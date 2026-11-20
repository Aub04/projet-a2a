"""Agent membre, commun aux deux architectures (section 8 de docs/SPEC.md).

Principe : le code calcule les options faisables (capacité, compétence, échéance, dépendances connues, nombre maximal
de tâches), le LLM choisit parmi elles ou refuse. Sans option faisable, la réponse est un refus sans appel au LLM.

Les rôles propres au pair-à-pair (annoncer une tâche, lancer un sondage de réunion) sont aussi portés par cet agent,
puisque dans cette architecture ce sont les agents eux-mêmes qui coordonnent. Ils ne sont jamais utilisés dans
l'architecture orchestrateur.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from a2a_team.a2a_layer.client import Transport
from a2a_team.a2a_layer.trace import Tracer
from a2a_team.llm import LLMClient, LLMError
from a2a_team.models import MemberProfile, Task, parse_slot

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def prompt(_file: str, **kw) -> str:
    return (PROMPTS / f"{_file}.txt").read_text(encoding="utf-8").format(**kw)


def lit(values: list[str]):
    """Type Literal construit à partir d'options : le schéma JSON envoyé au LLM contient alors un `enum`."""
    return Literal[tuple(values)]  # type: ignore[valid-type]


def profile_text(p: MemberProfile, weeks: list[str]) -> str:
    skills = ", ".join(f"{k} {v}" for k, v in p.skills.items())
    caps = ", ".join(f"{w} {p.capacity_hours.get(w, 0):g} h" for w in weeks)
    mx = f"{p.max_tasks_per_week}" if p.max_tasks_per_week is not None else "pas de limite"
    limits = "; ".join(p.hard_limits) or "aucune"
    return (f"- compétences (niveau 1 à 5) : {skills}\n- heures disponibles : {caps}\n"
            f"- tâches par semaine au maximum : {mx}\n- aime : {', '.join(p.preferences) or 'rien de précisé'}\n"
            f"- préfère éviter : {', '.join(p.dislikes) or 'rien de précisé'}\n- limites : {limits}")


class Commitment(BaseModel):
    task: Task
    week: str


class MemberAgent:
    def __init__(self, profile: MemberProfile, weeks: list[str], llm: LLMClient, tracer: Tracer,
                 max_rounds: int = 6):
        self.profile = profile.model_copy(deep=True)
        self.weeks = weeks
        self.llm = llm
        self.tracer = tracer
        self.max_rounds = max_rounds
        self.commitments: dict[str, Commitment] = {}
        self.board: dict[str, dict] = {}         # affectations publiques connues : task_id -> {member_id, week}
        self.tasks_seen: dict[str, Task] = {}    # tâches connues (annoncées ou reçues)
        self.transport: Transport | None = None  # pour les rôles pair-à-pair
        self.peers: list[str] = []
        self.peer_names: dict[str, str] = {}
        self.mode = "p2p"                        # "p2p" ou "orchestrator"
        self.orchestrator_id = "orchestrateur"

    @property
    def id(self) -> str:
        return self.profile.id

    @property
    def name(self) -> str:
        return self.profile.name

    # ------------------------------------------------------------------ calculs faits par le code
    def used(self, commitments: dict[str, Commitment] | None = None) -> tuple[dict, dict]:
        hours, count = defaultdict(float), defaultdict(int)
        for c in (self.commitments if commitments is None else commitments).values():
            hours[c.week] += c.task.hours
            count[c.week] += 1
        return hours, count

    def feasible_weeks(self, task: Task, weeks: list[str]) -> tuple[list[str], str]:
        """Semaines possibles pour cette tâche, et raison du refus s'il n'y en a aucune."""
        p = self.profile
        if p.level(task.skill) < task.min_level:
            return [], f"niveau {p.level(task.skill)} en {task.skill}, {task.min_level} demandé"
        hours, count = self.used()
        ok, reasons = [], []
        for w in weeks:
            if w in self.weeks and self.weeks.index(w) > self.weeks.index(task.deadline):
                reasons.append(f"{w} après l'échéance")
            elif p.capacity_hours.get(w, 0) - hours[w] < task.hours - 1e-9:
                reasons.append(f"{w} : {p.capacity_hours.get(w, 0) - hours[w]:g} h libres")
            elif p.max_tasks_per_week is not None and count[w] >= p.max_tasks_per_week:
                reasons.append(f"{w} : déjà {count[w]} tâches")
            else:
                ok.append(w)
        return ok, "; ".join(reasons) or "aucune semaine possible"

    def hard_check(self, items: list[tuple[Task, str]], meetings: list[str],
                   dep_weeks: dict[str, str]) -> list[dict]:
        """Objections dures sur une proposition de planning complète pour ce membre."""
        p, obj = self.profile, []
        hours, count = defaultdict(float), defaultdict(int)
        for t, w in items:
            hours[w] += t.hours
            count[w] += 1
            if p.level(t.skill) < t.min_level:
                obj.append({"item": t.id, "constraint": "competence",
                            "reason": f"niveau {p.level(t.skill)} en {t.skill}, {t.min_level} demandé"})
            if w not in self.weeks or self.weeks.index(w) > self.weeks.index(t.deadline):
                obj.append({"item": t.id, "constraint": "echeance", "reason": f"{w} après l'échéance {t.deadline}"})
            for d in t.depends_on:
                if d in dep_weeks and w in self.weeks and self.weeks.index(w) < self.weeks.index(dep_weeks[d]):
                    obj.append({"item": t.id, "constraint": "dependance",
                                "reason": f"placée en {w} avant sa dépendance {d} ({dep_weeks[d]})"})
        for w, h in hours.items():
            cap = p.capacity_hours.get(w, 0)
            if h > cap + 1e-9:
                ids = [t.id for t, ww in items if ww == w]
                obj.append({"item": ",".join(ids), "constraint": "capacite",
                            "reason": f"{h:g} h en {w} pour {cap:g} h disponibles"})
            if p.max_tasks_per_week is not None and count[w] > p.max_tasks_per_week:
                obj.append({"item": w, "constraint": "max_taches",
                            "reason": f"{count[w]} tâches en {w}, {p.max_tasks_per_week} au maximum"})
        for slot in meetings:
            try:
                week = parse_slot(slot)[0]
            except ValueError:
                obj.append({"item": slot, "constraint": "reunion", "reason": "créneau mal formé"})
                continue
            if p.capacity_hours.get(week, 0) > 0 and slot not in p.meeting_slots:
                obj.append({"item": slot, "constraint": "reunion", "reason": f"{self.name} n'est pas libre"})
        return obj

    def available_slots(self, slots: list[str]) -> list[str]:
        out = []
        for s in slots:
            try:
                week = parse_slot(s)[0]
            except ValueError:
                continue
            if s in self.profile.meeting_slots or self.profile.capacity_hours.get(week, 0) <= 0:
                out.append(s)    # un membre absent cette semaine-là n'est pas requis
        return out

    # ------------------------------------------------------------------ appels au LLM
    def system_prompt(self) -> str:
        if self.commitments:
            lines = [f"- {c.task.id} « {c.task.title} » en {c.week} ({c.task.hours:g} h, {c.task.skill})"
                     for c in self.commitments.values()]
            hours, _ = self.used()
            lines.append("Heures restantes : " + ", ".join(
                f"{w} {self.profile.capacity_hours.get(w, 0) - hours[w]:g} h" for w in self.weeks))
            com = "\n".join(lines)
        else:
            com = "aucun pour l'instant"
        return prompt("member_system", name=self.name, profile=profile_text(self.profile, self.weeks),
                      commitments=com)

    async def ask(self, decision: str, user: str, schema: type[BaseModel], **meta) -> BaseModel | None:
        try:
            return await self.llm.adecide(self.system_prompt(), user, schema,
                                          {"agent": self.id, "decision": decision, **meta})
        except LLMError as e:
            self.tracer.llm({"agent": self.id, "decision": decision, "error": f"abandon : {e}", "valid": False,
                             "fallback": True})
            return None

    # ------------------------------------------------------------------ point d'entrée A2A
    async def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = payload.get("type")
        c = payload.get("content", {})
        sender = payload.get("sender", "?")
        fn = getattr(self, f"on_{kind.lower()}", None) if kind else None
        if fn is None:
            return self.reply("ERROR", {"detail": f"type de message inconnu : {kind}"})
        return await fn(sender, c)

    def reply(self, type_: str, content: dict) -> dict:
        return {"type": type_, "sender": self.id, "content": content}

    # --- Contract Net : réponse à un appel à candidatures
    async def on_cfp(self, sender: str, c: dict) -> dict:
        task = Task.model_validate(c["task"])
        self.tasks_seen[task.id] = task
        options, why = self.feasible_weeks(task, c["weeks"])
        if not options:
            return self.reply("REFUSAL", {"task_id": task.id, "reason": why, "hard": True})
        relance = c.get("relance", 0)
        relance_txt = (f"Personne ne s'est proposé au tour précédent (relance n°{relance}). Reconsidère ta réponse "
                       f"si c'est possible pour {self.name}.") if relance else ""
        schema = create_model("Bid", decision=(Literal["propose", "refuse"], ...),
                              week=(lit(options) | None, None), justification=(str, ""))
        user = prompt("member_bid", sender=sender, task_id=task.id, title=task.title, skill=task.skill,
                      min_level=task.min_level, hours=f"{task.hours:g}", deadline=task.deadline,
                      relance=relance_txt, name=self.name, options=", ".join(options))
        d = await self.ask("bid", user, schema, options=options, task_id=task.id, relance=relance)
        if d is None or d.decision == "refuse" or d.week is None:
            reason = "erreur du LLM" if d is None else (d.justification or "ne souhaite pas")
            return self.reply("REFUSAL", {"task_id": task.id, "reason": reason, "hard": False})
        return self.reply("PROPOSAL", {"task_id": task.id, "week": d.week, "justification": d.justification})

    async def on_award(self, sender: str, c: dict) -> dict:
        task = Task.model_validate(c["task"])
        week = c["week"]
        options, why = self.feasible_weeks(task, [week])
        if not options:
            return self.reply("DECLINE", {"task_id": task.id, "reason": why})
        schema = create_model("AwardAnswer", decision=(Literal["confirm", "decline"], ...), reason=(str, ""))
        user = prompt("member_award", sender=sender, task_id=task.id, title=task.title, skill=task.skill,
                      hours=f"{task.hours:g}", name=self.name, week=week)
        d = await self.ask("confirm_award", user, schema, task_id=task.id, week=week)
        if d is not None and d.decision == "decline":
            return self.reply("DECLINE", {"task_id": task.id, "reason": d.reason})
        self.commitments[task.id] = Commitment(task=task, week=week)
        return self.reply("CONFIRM", {"task_id": task.id, "week": week})

    async def on_award_notice(self, sender: str, c: dict) -> dict:
        if c.get("member_id"):
            self.board[c["task_id"]] = {"member_id": c["member_id"], "week": c["week"]}
        else:
            self.board.pop(c["task_id"], None)
        return self.reply("ACK", {})

    async def on_meeting_poll(self, sender: str, c: dict) -> dict:
        options = self.available_slots(c["slots"])
        if not options:
            return self.reply("MEETING_VOTE", {"accepted": [], "index": c.get("index")})
        # Vote par refus : un créneau compatible est accepté sauf si le LLM le refuse avec une raison tirée du profil
        # (voir DECISIONS.md, 2026-11-20). Le vote par liste d'acceptation donnait des refus arbitraires.
        schema = create_model("Vote", refused=(list[lit(options)], Field(default_factory=list)), reason=(str, ""))
        user = prompt("member_vote", sender=sender, index=c.get("index", 1), slots=", ".join(c["slots"]),
                      options=", ".join(options), name=self.name)
        d = await self.ask("meeting_vote", user, schema, options=options)
        refused = set() if d is None else set(d.refused)
        accepted = [s for s in options if s not in refused]
        return self.reply("MEETING_VOTE", {"accepted": accepted, "index": c.get("index")})

    # --- Architecture orchestrateur
    async def on_info_request(self, sender: str, c: dict) -> dict:
        p = self.profile
        return self.reply("INFO_REPLY", {
            "member_id": p.id, "name": p.name, "skills": p.skills, "capacity_hours": p.capacity_hours,
            "max_tasks_per_week": p.max_tasks_per_week, "preferences": p.preferences, "dislikes": p.dislikes,
            "hard_limits": p.hard_limits, "meeting_slots": p.meeting_slots})

    async def on_plan_proposal(self, sender: str, c: dict) -> dict:
        items = [(Task.model_validate(a["task"]), a["week"]) for a in c["assignments"]]
        for t, _ in items:
            self.tasks_seen[t.id] = t
        meetings = c.get("meetings", [])
        hard = self.hard_check(items, meetings, c.get("dependency_weeks", {}))
        if hard:
            return self.reply("PLAN_FEEDBACK", {"decision": "object", "objections": hard, "hard": True})
        ids = [t.id for t, _ in items] + list(meetings)
        lines = "\n".join(f"- {t.id} « {t.title} » en {w} ({t.hours:g} h, {t.skill})" for t, w in items) or \
            "- aucune tâche"
        schema = create_model(
            "Feedback", decision=(Literal["accept", "object"], ...),
            objections=(list[create_model("Objection", item=(lit(ids), ...), reason=(str, ""))] if ids else list,
                        Field(default_factory=list)))
        user = prompt("member_plan_feedback", sender=sender, revision=c.get("revision", 0), name=self.name,
                      items=lines, meetings=", ".join(meetings) or "aucune")
        d = await self.ask("plan_feedback", user, schema, items=ids, revision=c.get("revision", 0))
        if d is not None and d.decision == "object" and d.objections:
            obj = [{"item": o.item, "constraint": "preference", "reason": o.reason} for o in d.objections]
            return self.reply("PLAN_FEEDBACK", {"decision": "object", "objections": obj, "hard": False})
        self.commitments = {t.id: Commitment(task=t, week=w) for t, w in items}
        return self.reply("PLAN_FEEDBACK", {"decision": "accept", "objections": []})

    async def on_plan_final(self, sender: str, c: dict) -> dict:
        """Partie définitive après les révisions (éléments contestés retirés) : simple prise en compte."""
        self.commitments = {a["task"]["id"]: Commitment(task=Task.model_validate(a["task"]), week=a["week"])
                            for a in c["assignments"]}
        return self.reply("ACK", {})

    async def on_event(self, sender: str, c: dict) -> dict:
        """Imprévu reçu du système. Pair-à-pair : l'agent libère ce qu'il ne peut plus tenir et le ré-annonce.
        Orchestrateur : l'agent prévient l'orchestrateur, qui replanifie."""
        ev = c["event"]
        released = self.apply_event(ev)
        if self.mode == "orchestrator":
            await self.transport.send(self.id, self.orchestrator_id, "EVENT", {"event": ev, "released": released})
            return self.reply("EVENT_DONE", {"released": released})
        to_announce = [self.tasks_seen[t] for t in released]
        for tid in released:
            self.board.pop(tid, None)
            await asyncio.gather(*[self.transport.send(self.id, p, "AWARD_NOTICE",
                                                       {"task_id": tid, "member_id": None, "week": None})
                                   for p in self.peers])
        if ev["type"] == "new_task":
            to_announce.append(Task.model_validate(ev["task"]))
        results = []
        for t in to_announce:
            self.tasks_seen[t.id] = t
            results.append(await self.announce(t))
        return self.reply("EVENT_DONE", {"released": released, "results": results})

    # --- Imprévus (les deux architectures)
    def apply_event(self, event: dict) -> list[str]:
        """Applique un imprévu au profil ou aux tâches connues ; renvoie les tâches que l'agent ne peut plus tenir."""
        released = []
        if event["type"] == "new_task" and event.get("task"):
            self.tasks_seen[event["task"]["id"]] = Task.model_validate(event["task"])
        if event["type"] == "absence" and event.get("member_id") == self.id:
            for w in event.get("weeks", []):
                self.profile.capacity_hours[w] = 0.0
            released = [tid for tid, c in self.commitments.items() if c.week in event.get("weeks", [])]
            # les réunions déjà calées restent valides : un membre absent n'est pas requis
        elif event["type"] == "deadline_change":
            tid = event["task_id"]
            if tid in self.tasks_seen:
                self.tasks_seen[tid].deadline = event["new_deadline"]
            if tid in self.commitments:
                c = self.commitments[tid]
                c.task.deadline = event["new_deadline"]
                if self.weeks.index(c.week) > self.weeks.index(c.task.deadline):
                    released.append(tid)
        for tid in released:
            self.commitments.pop(tid, None)
        return released

    # ------------------------------------------------------------------ rôles pair-à-pair
    def cfp_weeks(self, task: Task) -> list[str]:
        """Fenêtre de semaines de l'annonce : après les dépendances connues, avant l'échéance et avant les tâches
        déjà placées qui dépendent de celle-ci."""
        lo = max([self.weeks.index(self.board[d]["week"]) for d in task.depends_on if d in self.board], default=0)
        hi = self.weeks.index(task.deadline)
        for tid, info in self.board.items():
            t = self.tasks_seen.get(tid)
            if t and task.id in t.depends_on:
                hi = min(hi, self.weeks.index(info["week"]))
        return self.weeks[lo:hi + 1]

    async def on_announce(self, sender: str, c: dict) -> dict:
        """Ordre de l'horloge du système : c'est au tour de cet agent d'annoncer une tâche."""
        task = Task.model_validate(c["task"])
        self.tasks_seen[task.id] = task
        result = await self.announce(task)
        return self.reply("ANNOUNCE_RESULT", result)

    async def _self_bid(self, content: dict) -> dict:
        r = await self.on_cfp(self.id, content)
        return {**r, "sender": self.id}

    async def announce(self, task: Task) -> dict:
        assert self.transport is not None
        tr = self.transport
        weeks = self.cfp_weeks(task)
        for relance in range(self.max_rounds):
            content = {"task": task.model_dump(), "weeks": weeks, "relance": relance}
            replies = await asyncio.gather(self._self_bid(content),
                                           *[tr.send(self.id, p, "CFP", content) for p in self.peers])
            proposals = [r for r in replies if r["type"] == "PROPOSAL"]
            while proposals:
                winner = await self.choose(task, proposals)
                prop = next(p for p in proposals if p["sender"] == winner)
                award = {"task": task.model_dump(), "week": prop["content"]["week"]}
                if winner == self.id:
                    ans = await self.on_award(self.id, award)
                else:
                    ans = await tr.send(self.id, winner, "AWARD", award)
                if ans["type"] == "CONFIRM":
                    week = prop["content"]["week"]
                    self.board[task.id] = {"member_id": winner, "week": week}
                    notice = {"task_id": task.id, "member_id": winner, "week": week}
                    await asyncio.gather(*[tr.send(self.id, p, "AWARD_NOTICE", notice) for p in self.peers])
                    return {"task_id": task.id, "member_id": winner, "week": week, "rounds": relance + 1}
                proposals = [p for p in proposals if p["sender"] != winner]
            if all(r["content"].get("hard") for r in replies):
                break    # personne ne peut la prendre : inutile de relancer
        reason = "aucun membre disponible et compétent" if not weeks else "aucune proposition acceptée"
        self.tracer.escalate(self.id, f"Tâche {task.id} non attribuée après annonce par {self.id} : {reason}")
        return {"task_id": task.id, "member_id": None, "week": None}

    async def choose(self, task: Task, proposals: list[dict]) -> str:
        ids = [p["sender"] for p in proposals]
        if len(ids) == 1:
            return ids[0]
        lines = "\n".join(f"- {p['sender']} ({self.peer_names.get(p['sender'], p['sender'])}) : semaine "
                          f"{p['content']['week']}, « {p['content'].get('justification', '')} »" for p in proposals)
        schema = create_model("Choice", winner=(lit(ids), ...), reason=(str, ""))
        user = prompt("announcer_choose", task_id=task.id, title=task.title, skill=task.skill,
                      min_level=task.min_level, hours=f"{task.hours:g}", deadline=task.deadline, proposals=lines)
        d = await self.ask("choose_award", user, schema, options=ids, task_id=task.id)
        return ids[0] if d is None else d.winner

    async def on_start_poll(self, sender: str, c: dict) -> dict:
        """Ordre du système : cet agent lance un sondage pour une réunion avec ses propres créneaux."""
        assert self.transport is not None
        excluded = set(c.get("exclude", []))
        slots = [s for s in self.available_slots(self.profile.meeting_slots) if s not in excluded]
        slots = [s for s in slots if parse_slot(s)[0] in self.weeks]
        if not slots:
            return self.reply("POLL_RESULT", {"slot": None})
        content = {"slots": slots, "index": c.get("index", 1)}
        votes = await asyncio.gather(*[self.transport.send(self.id, p, "MEETING_POLL", content) for p in self.peers])
        common = [s for s in slots if all(s in v["content"]["accepted"] for v in votes)]
        return self.reply("POLL_RESULT", {"slot": common[0] if common else None})
