"""Agent orchestrateur (section 10 de docs/SPEC.md), utilisé seulement dans l'architecture orchestrateur.

Le code vérifie le format du planning produit par le LLM (identifiants connus, une seule affectation par tâche),
pas les contraintes : ce sont les agents membres qui détectent les erreurs et objectent.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field, create_model

from a2a_team.a2a_layer.client import Transport
from a2a_team.a2a_layer.trace import Tracer
from a2a_team.agents.member import lit, prompt
from a2a_team.llm import BudgetExceeded, LLMClient, LLMError
from a2a_team.models import Assignment, Plan, Task, parse_slot

ORCH_ID = "orchestrateur"


class OrchestratorAgent:
    id = ORCH_ID

    def __init__(self, llm: LLMClient, tracer: Tracer, transport: Transport, member_ids: list[str],
                 weeks: list[str], max_rounds: int = 6):
        self.llm = llm
        self.tracer = tracer
        self.transport = transport
        self.member_ids = member_ids
        self.weeks = weeks
        self.max_rounds = max_rounds
        self.infos: dict[str, dict] = {}
        self.tasks: dict[str, Task] = {}
        self.meetings_needed = 0
        self.plan = Plan()
        self.accepted: dict[str, dict] = {}   # membre -> partie acceptée (pour ne pas redemander si inchangée)
        self.revision = 0

    # ------------------------------------------------------------------ point d'entrée A2A
    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind, c = payload.get("type"), payload.get("content", {})
        if kind == "START":
            self.tasks = {t["id"]: Task.model_validate(t) for t in c["tasks"]}
            self.meetings_needed = c["meetings_needed"]
            plan = await self.run()
            return {"type": "PLAN_FINAL", "sender": self.id, "content": plan.model_dump()}
        if kind == "EVENT":
            plan = await self.on_event(payload.get("sender", "?"), c)
            return {"type": "PLAN_FINAL", "sender": self.id, "content": plan.model_dump()}
        return {"type": "ERROR", "sender": self.id, "content": {"detail": f"type inconnu {kind}"}}

    # ------------------------------------------------------------------ protocole
    async def collect(self) -> None:
        replies = await asyncio.gather(*[self.transport.send(self.id, m, "INFO_REQUEST", {})
                                         for m in self.member_ids])
        self.infos = {r["content"]["member_id"]: r["content"] for r in replies}

    async def run(self, context: str = "") -> Plan:
        if not self.infos:
            await self.collect()
        objections: dict[str, list] = {}
        previous: Plan | None = self.plan if self.plan.assignments else None
        plan = Plan()
        for _ in range(self.max_rounds):
            rev = self.revision
            self.revision += 1
            try:
                plan = await self.draft(previous, objections, context)
            except (LLMError, BudgetExceeded) as e:
                self.tracer.escalate(self.id, f"Planning impossible à produire : {e}")
                plan = previous or Plan(unassigned=list(self.tasks))
                break
            objections = await self.consult(plan, rev)
            if not objections:
                self.plan = plan
                return plan
            previous = plan
        self.plan = self.finalize(plan, objections)
        await self.notify_final()
        return self.plan

    def _members_text(self) -> str:
        out = []
        for mid, i in self.infos.items():
            caps = ", ".join(f"{w} {i['capacity_hours'].get(w, 0):g} h" for w in self.weeks)
            skills = ", ".join(f"{k} {v}" for k, v in i["skills"].items())
            mx = i["max_tasks_per_week"] if i["max_tasks_per_week"] is not None else "pas de limite"
            out.append(f"- {mid} ({i['name']}) : compétences {skills} ; heures disponibles {caps} ; tâches par "
                       f"semaine max {mx} ; aime {', '.join(i['preferences']) or '-'} ; évite "
                       f"{', '.join(i['dislikes']) or '-'} ; limites : {'; '.join(i['hard_limits']) or '-'} ; "
                       f"créneaux de réunion : {', '.join(i['meeting_slots']) or 'aucun'}")
        return "\n".join(out)

    def _tasks_text(self) -> str:
        return "\n".join(f"- {t.id} « {t.title} » : {t.skill} niveau >= {t.min_level}, {t.hours:g} h, échéance "
                         f"{t.deadline}, dépend de : {', '.join(t.depends_on) or 'aucune'}"
                         for t in self.tasks.values())

    def schema(self) -> type[BaseModel]:
        task_ids, members = list(self.tasks), list(self.infos)
        slots = sorted({s for i in self.infos.values() for s in i["meeting_slots"]})
        A = create_model("Affectation", task_id=(lit(task_ids), ...), member_id=(lit(members), ...),
                         week=(lit(self.weeks), ...))
        return create_model("PlanningLLM", assignments=(list[A], Field(default_factory=list)),
                            meetings=(list[lit(slots)] if slots else list, Field(default_factory=list)),
                            unassigned=(list[lit(task_ids)], Field(default_factory=list)), comment=(str, ""))

    async def draft(self, previous: Plan | None, objections: dict[str, list], context: str) -> Plan:
        ctx = context
        if previous is not None:
            lines = "\n".join(f"- {a.task_id} -> {a.member_id} en {a.week}" for a in previous.assignments)
            obj = "\n".join(f"- {m} : {o['item']} ({o['constraint']}) : {o['reason']}"
                            for m, lst in objections.items() for o in lst) or "aucune"
            ctx += (f"\nTon planning précédent :\n{lines}\nRéunions : {', '.join(previous.meetings) or 'aucune'}\n"
                    f"Objections reçues des agents :\n{obj}\nCorrige le planning pour lever ces objections, en "
                    f"gardant ce qui a été accepté autant que possible.\n")
        system = prompt("orchestrator_system", weeks=", ".join(self.weeks))
        user = prompt("orchestrator_plan", members=self._members_text(), tasks=self._tasks_text(),
                      meetings_needed=self.meetings_needed, context=ctx)
        raw = await self.llm.adecide(system, user, self.schema(), {
            "agent": self.id, "decision": "orchestrator_plan", "infos": self.infos, "tasks": self.tasks,
            "weeks": self.weeks, "meetings_needed": self.meetings_needed, "previous": previous,
            "objections": objections, "fake_plan": lambda: _fake_greedy(self)})
        return self.check_format(raw)

    def check_format(self, raw: BaseModel) -> Plan:
        """Vérification de format seulement : doublons supprimés, tâches oubliées ajoutées aux non attribuées."""
        seen, assignments = set(), []
        for a in raw.assignments:
            if a.task_id in seen:
                continue
            seen.add(a.task_id)
            assignments.append(Assignment(task_id=a.task_id, member_id=a.member_id, week=a.week))
        unassigned = [t for t in self.tasks if t not in seen]
        meetings = list(dict.fromkeys(raw.meetings))
        return Plan(assignments=assignments, meetings=meetings, unassigned=unassigned)

    def part_for(self, plan: Plan, mid: str) -> dict:
        week_of = {a.task_id: a.week for a in plan.assignments}
        mine = [a for a in plan.assignments if a.member_id == mid]
        deps = {d: week_of[d] for a in mine for d in self.tasks[a.task_id].depends_on if d in week_of}
        return {"assignments": [{"task": self.tasks[a.task_id].model_dump(), "week": a.week} for a in mine],
                "meetings": plan.meetings, "dependency_weeks": deps}

    async def consult(self, plan: Plan, rev: int) -> dict[str, list]:
        parts = {m: self.part_for(plan, m) for m in self.member_ids}
        to_ask = [m for m in self.member_ids if self.accepted.get(m) != parts[m]]
        replies = await asyncio.gather(*[
            self.transport.send(self.id, m, "PLAN_PROPOSAL", {**parts[m], "revision": rev}) for m in to_ask])
        objections = {}
        for m, r in zip(to_ask, replies):
            if r["content"]["decision"] == "accept":
                self.accepted[m] = parts[m]
            else:
                self.accepted.pop(m, None)
                objections[m] = r["content"]["objections"]
        return objections

    def finalize(self, plan: Plan, objections: dict[str, list]) -> Plan:
        """Après le nombre maximal de révisions : ce qui reste contesté est retiré et remonté aux humains."""
        bad_tasks, bad_slots = set(), set()
        for m, lst in objections.items():
            for o in lst:
                for item in str(o["item"]).split(","):
                    if item in self.tasks:
                        bad_tasks.add((m, item))
                    elif item in plan.meetings:
                        bad_slots.add(item)
                    elif item in self.weeks:   # trop de tâches dans la semaine : on retire toutes celles du membre
                        bad_tasks |= {(m, a.task_id) for a in plan.assignments if a.member_id == m and a.week == item}
        keep = [a for a in plan.assignments if (a.member_id, a.task_id) not in bad_tasks]
        removed = [a.task_id for a in plan.assignments if (a.member_id, a.task_id) in bad_tasks]
        final = Plan(assignments=keep, meetings=[s for s in plan.meetings if s not in bad_slots],
                     unassigned=plan.unassigned + removed)
        for tid in removed:
            self.tracer.escalate(self.id, f"Tâche {tid} retirée du planning : objection non levée après "
                                          f"{self.max_rounds} révisions")
        for s in bad_slots:
            self.tracer.escalate(self.id, f"Réunion {s} retirée : objection non levée")
        # Les membres concernés reçoivent leur partie définitive (sans les éléments retirés).
        for m in {m for m, _ in bad_tasks} | (set(self.member_ids) if bad_slots else set()):
            self.accepted.pop(m, None)
        return final

    async def notify_final(self) -> None:
        """Envoie la partie définitive (PLAN_FINAL, sans décision à prendre) aux membres dont elle a changé."""
        parts = {m: self.part_for(self.plan, m) for m in self.member_ids}
        changed = [m for m in self.member_ids if self.accepted.get(m) != parts[m]]
        await asyncio.gather(*[self.transport.send(self.id, m, "PLAN_FINAL", parts[m]) for m in changed])
        for m in changed:
            self.accepted[m] = parts[m]

    async def on_event(self, sender: str, c: dict) -> Plan:
        ev = c["event"]
        if ev["type"] == "absence":
            for w in ev.get("weeks", []):
                self.infos[ev["member_id"]]["capacity_hours"][w] = 0
        elif ev["type"] == "new_task":
            t = Task.model_validate(ev["task"])
            self.tasks[t.id] = t
        elif ev["type"] == "deadline_change" and ev["task_id"] in self.tasks:
            self.tasks[ev["task_id"]].deadline = ev["new_deadline"]
        ctx = f"\nImprévu signalé par {sender} : {describe_event(ev)}. Adapte le planning.\n"
        return await self.run(context=ctx)


def describe_event(ev: dict) -> str:
    if ev["type"] == "absence":
        return f"{ev['member_id']} est absent en {', '.join(ev['weeks'])} (0 heure disponible)"
    if ev["type"] == "new_task":
        t = ev["task"]
        return f"nouvelle tâche urgente {t['id']} « {t['title']} » ({t['skill']}, {t['hours']} h, échéance {t['deadline']})"
    return f"l'échéance de {ev['task_id']} est avancée à {ev['new_deadline']}"


def _fake_greedy(o: OrchestratorAgent) -> dict:
    """Réponse du FakeLLM pour l'orchestrateur : glouton simple sur les informations reçues (tests seulement)."""
    from collections import defaultdict
    used = defaultdict(float)
    count = defaultdict(int)
    out, week_of = [], {}
    for t in o.tasks.values():
        lo = max([o.weeks.index(week_of[d]) for d in t.depends_on if d in week_of], default=0)
        done = False
        for w in o.weeks[lo:o.weeks.index(t.deadline) + 1]:
            for mid, i in o.infos.items():
                mx = i["max_tasks_per_week"]
                if i["skills"].get(t.skill, 0) >= t.min_level and \
                        i["capacity_hours"].get(w, 0) - used[(mid, w)] >= t.hours and \
                        (mx is None or count[(mid, w)] < mx):
                    used[(mid, w)] += t.hours
                    count[(mid, w)] += 1
                    out.append({"task_id": t.id, "member_id": mid, "week": w})
                    week_of[t.id] = w
                    done = True
                    break
            if done:
                break
    slots = [s for s in sorted({s for i in o.infos.values() for s in i["meeting_slots"]})
             if all(s in i["meeting_slots"] or i["capacity_hours"].get(parse_slot(s)[0], 0) <= 0
                    for i in o.infos.values())]
    return {"assignments": out, "meetings": slots[: o.meetings_needed], "unassigned": [], "comment": ""}
