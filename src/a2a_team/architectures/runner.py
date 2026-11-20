"""Exécution d'une architecture (p2p, orchestrator, baseline) sur un scénario, avec imprévus et mesures.

Déroulé commun aux deux architectures LLM :
1. négociation initiale sur le scénario sans imprévus ;
2. injection des imprévus un par un (ordre de `trigger_round`), réparation par l'architecture, mesure ;
3. planning final validé contre le scénario avec tous les imprévus.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from a2a_team import baseline
from a2a_team.a2a_layer.client import HttpTransport, InMemoryTransport, Transport
from a2a_team.a2a_layer.launcher import MEMBER_BASE_PORT, ORCHESTRATOR_PORT, ServerGroup, url_for
from a2a_team.a2a_layer.server import build_app, make_card
from a2a_team.a2a_layer.trace import SYSTEM, Tracer
from a2a_team.agents.member import MemberAgent
from a2a_team.agents.orchestrator import ORCH_ID, OrchestratorAgent
from a2a_team.llm import BudgetExceeded, LLMClient
from a2a_team.models import Assignment, Plan, Scenario, apply_events
from a2a_team.validator import Report, validate

ARCHS = ["p2p", "orchestrator", "baseline"]


class EventResult(BaseModel):
    type: str
    valid_after: bool
    assigned_ratio_after: float
    messages: int
    llm_calls: int


class RunResult(BaseModel):
    arch: str
    scenario_id: str
    difficulty: str
    repeat: int = 0
    plan_initial: Plan
    report_initial: Report
    plan: Plan
    report: Report
    events: list[EventResult] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    duration: float = 0.0
    error: str | None = None


def _plan_from_agents(agents: dict[str, MemberAgent], task_ids: list[str], meetings: list[str],
                      tracer: Tracer) -> Plan:
    assignments = [Assignment(task_id=tid, member_id=a.id, week=c.week)
                   for a in agents.values() for tid, c in a.commitments.items()]
    done = {x.task_id for x in assignments}
    return Plan(assignments=sorted(assignments, key=lambda x: x.task_id), meetings=list(meetings),
                unassigned=[t for t in task_ids if t not in done], escalations=list(tracer.escalations))


class Session:
    """Agents, serveurs A2A et transport pour une exécution."""

    def __init__(self, arch: str, scenario: Scenario, llm: LLMClient, tracer: Tracer, max_rounds: int,
                 transport: str = "http", port_offset: int = 0, orchestrator_hints: bool = True):
        self.orchestrator_hints = orchestrator_hints
        self.arch, self.scenario, self.llm, self.tracer = arch, scenario, llm, tracer
        self.kind, self.port_offset = transport, port_offset
        self.agents = {m.id: MemberAgent(m, scenario.weeks, llm, tracer, max_rounds) for m in scenario.members}
        ids = list(self.agents)
        names = {m.id: m.name for m in scenario.members}
        for a in self.agents.values():
            a.peers = [i for i in ids if i != a.id]
            a.peer_names = names
            a.mode = "orchestrator" if arch == "orchestrator" else "p2p"
        self.group = ServerGroup()
        self.transport: Transport
        self.orch: OrchestratorAgent | None = None
        self.max_rounds = max_rounds

    async def __aenter__(self):
        handlers = {mid: a.handle for mid, a in self.agents.items()}
        if self.kind == "http":
            urls = {mid: url_for(MEMBER_BASE_PORT + self.port_offset + i) for i, mid in enumerate(self.agents)}
            if self.arch == "orchestrator":
                urls[ORCH_ID] = url_for(ORCHESTRATOR_PORT + self.port_offset)
            self.transport = HttpTransport(self.tracer, urls)
        else:
            self.transport = InMemoryTransport(self.tracer, handlers)
        for a in self.agents.values():
            a.transport = self.transport
        if self.arch == "orchestrator":
            self.orch = OrchestratorAgent(self.llm, self.tracer, self.transport, list(self.agents),
                                          self.scenario.weeks, self.max_rounds, self.orchestrator_hints)
            handlers[ORCH_ID] = self.orch.handle
            if isinstance(self.transport, InMemoryTransport):
                self.transport.handlers[ORCH_ID] = self.orch.handle
        if self.kind == "http":
            apps = {}
            for i, (mid, a) in enumerate(self.agents.items()):
                url = url_for(MEMBER_BASE_PORT + self.port_offset + i)
                apps[MEMBER_BASE_PORT + self.port_offset + i] = build_app(
                    make_card(f"Agent de {a.name}", f"Représente {a.name} dans la négociation du planning", url),
                    a.handle)
            if self.orch:
                url = url_for(ORCHESTRATOR_PORT + self.port_offset)
                apps[ORCHESTRATOR_PORT + self.port_offset] = build_app(
                    make_card("Orchestrateur", "Coordonne le planning du groupe", url, "coordinate_planning",
                              "Coordonner le planning"), self.orch.handle)
            await self.group.start(apps)
        return self

    async def __aexit__(self, *exc):
        await self.transport.close()
        await self.group.stop()

    # ------------------------------------------------------------------ négociation initiale
    async def negotiate(self, scenario: Scenario) -> Plan:
        tr, ids = self.transport, list(self.agents)
        if self.arch == "p2p":
            self.rr = 0
            for t in baseline.task_order(scenario):
                announcer = ids[self.rr % len(ids)]
                self.rr += 1
                try:
                    await tr.send(SYSTEM, announcer, "ANNOUNCE", {"task": t.model_dump()})
                except BudgetExceeded:
                    self.tracer.escalate(SYSTEM, f"Budget d'appels LLM épuisé avant la tâche {t.id}")
                    break
            self.meetings = await self.p2p_meetings(scenario)
            return _plan_from_agents(self.agents, [t.id for t in scenario.tasks], self.meetings, self.tracer)
        r = await tr.send(SYSTEM, ORCH_ID, "START", {"tasks": [t.model_dump() for t in scenario.tasks],
                                                      "meetings_needed": scenario.meetings_needed})
        plan = Plan.model_validate(r["content"])
        plan.escalations = list(self.tracer.escalations)
        return plan

    async def p2p_meetings(self, scenario: Scenario) -> list[str]:
        ids, chosen = list(self.agents), []
        k = 0
        for index in range(1, scenario.meetings_needed + 1):
            slot = None
            for _ in range(min(len(ids), self.max_rounds)):
                initiator = ids[k % len(ids)]
                k += 1
                try:
                    r = await self.transport.send(SYSTEM, initiator, "START_POLL", {"index": index, "exclude": chosen})
                except BudgetExceeded:
                    break
                slot = r["content"].get("slot")
                if slot:
                    chosen.append(slot)
                    break
            if not slot:
                self.tracer.escalate(SYSTEM, f"Aucun créneau commun trouvé pour la réunion n°{index}")
        return chosen

    # ------------------------------------------------------------------ imprévus
    def concerned(self, ev: dict, plan: Plan) -> str:
        ids = list(self.agents)
        if ev["type"] == "absence":
            return ev["member_id"]
        if ev["type"] == "deadline_change":
            holder = plan.by_task().get(ev["task_id"])
            if holder:
                return holder.member_id
        if self.arch == "p2p":
            who = ids[self.rr % len(ids)]
            self.rr += 1
            return who
        return ids[0]

    async def inject(self, ev: dict, plan: Plan, scenario: Scenario) -> Plan:
        who = self.concerned(ev, plan)
        if ev["type"] == "deadline_change":   # l'échéance est une information publique : tous la connaissent
            for a in self.agents.values():
                if ev["task_id"] in a.tasks_seen:
                    a.tasks_seen[ev["task_id"]].deadline = ev["new_deadline"]
        try:
            r = await self.transport.send(SYSTEM, who, "EVENT", {"event": ev})
        except BudgetExceeded:
            self.tracer.escalate(SYSTEM, "Budget d'appels LLM épuisé pendant un imprévu")
        if self.arch == "p2p":
            return _plan_from_agents(self.agents, [t.id for t in scenario.tasks], self.meetings, self.tracer)
        plan = self.orch.plan.model_copy(deep=True)
        plan.escalations = list(self.tracer.escalations)
        return plan


async def run_llm_arch(arch: str, scenario: Scenario, llm: LLMClient, tracer: Tracer, max_rounds: int = 6,
                       transport: str = "http", port_offset: int = 0,
                       orchestrator_hints: bool = True) -> tuple[Plan, Plan, list[EventResult]]:
    base = scenario.model_copy(deep=True, update={"events": []})
    async with Session(arch, base, llm, tracer, max_rounds, transport, port_offset, orchestrator_hints) as s:
        plan0 = await s.negotiate(base)
        plan, current, results = plan0, base, []
        for k, ev in enumerate(sorted(scenario.events, key=lambda e: e.trigger_round)):
            s.transport.phase = f"imprevu_{k + 1}"
            before_msgs, before_llm = len(tracer.counted()), len(tracer.llm_calls)
            current = apply_events(current, [ev])
            plan = await s.inject(ev.model_dump(), plan, current)
            rep = validate(current, plan, apply_scenario_events=False)
            results.append(EventResult(type=ev.type, valid_after=rep.valid, assigned_ratio_after=rep.assigned_ratio,
                                       messages=len(tracer.counted()) - before_msgs,
                                       llm_calls=len(tracer.llm_calls) - before_llm))
    return plan0, plan, results


def run(arch: str, scenario: Scenario, llm: LLMClient | None = None, run_dir: Path | None = None,
        max_rounds: int = 6, transport: str = "http", repeat: int = 0,
        on_event: Callable[[dict], None] | None = None, port_offset: int = 0,
        orchestrator_hints: bool = True) -> RunResult:
    tracer = Tracer(run_dir, on_event)
    t0 = time.time()
    error = None
    events: list[EventResult] = []
    if arch == "baseline":
        base = scenario.model_copy(deep=True, update={"events": []})
        plan0 = baseline.solve(base)
        plan = baseline.solve(scenario)
        current = base
        for ev in sorted(scenario.events, key=lambda e: e.trigger_round):
            current = apply_events(current, [ev])
            rep = validate(current, baseline.solve(current, False), apply_scenario_events=False)
            events.append(EventResult(type=ev.type, valid_after=rep.valid, assigned_ratio_after=rep.assigned_ratio,
                                      messages=0, llm_calls=0))
    else:
        assert llm is not None
        llm.reset(arch=arch, scenario=scenario.id, repeat=repeat)
        llm.log = tracer.llm
        try:
            plan0, plan, events = asyncio.run(run_llm_arch(arch, scenario, llm, tracer, max_rounds, transport,
                                                           port_offset, orchestrator_hints))
        except Exception as e:  # une exécution ratée est un résultat : on la garde avec son erreur
            error = f"{type(e).__name__}: {e}"
            plan0 = plan = Plan(unassigned=[t.id for t in scenario.tasks], escalations=list(tracer.escalations))
    base = scenario.model_copy(deep=True, update={"events": []})
    result = RunResult(
        arch=arch, scenario_id=scenario.id, difficulty=scenario.difficulty, repeat=repeat,
        plan_initial=plan0, report_initial=validate(base, plan0), plan=plan, report=validate(scenario, plan),
        events=events, stats=tracer.stats(), duration=round(time.time() - t0, 2), error=error)
    if run_dir:
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        (Path(run_dir) / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
