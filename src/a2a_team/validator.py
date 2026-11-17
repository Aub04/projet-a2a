"""Validateur de planning : contraintes dures et scores souples (section 3 de docs/SPEC.md).

Le planning est toujours vérifié contre le scénario final, c'est-à-dire après application de tous les imprévus.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from pydantic import BaseModel, Field

from a2a_team.models import Plan, Scenario, apply_events, parse_slot, present_members

VIOLATION_TYPES = ["format", "capacite", "competence", "echeance", "dependance", "max_taches", "reunion"]


class Violation(BaseModel):
    type: str
    detail: str


class Report(BaseModel):
    valid: bool
    violations: list[Violation] = Field(default_factory=list)
    violation_counts: dict[str, int] = Field(default_factory=dict)
    assigned_ratio: float = 0.0
    preference_ratio: float = 0.0     # (préférées - évitées) / attribuées
    dislike_ratio: float = 0.0        # évitées / attribuées
    load_balance: float = 0.0         # coefficient de variation du taux d'occupation (plus bas = mieux)
    meetings_ratio: float = 0.0
    escalation_correct: bool | None = None   # seulement pour les scénarios « impossible »
    silent_failure: bool = False      # tâches ou réunions manquantes sans aucune escalade


def total_capacity(scenario: Scenario) -> float:
    return sum(sum(m.capacity_hours.get(w, 0) for w in scenario.weeks) for m in scenario.members)


def validate(scenario: Scenario, plan: Plan, apply_scenario_events: bool = True) -> Report:
    s = apply_events(scenario) if apply_scenario_events else scenario
    v: list[Violation] = []
    tasks = {t.id: t for t in s.tasks}
    members = {m.id: m for m in s.members}

    # Format : identifiants connus, une seule affectation par tâche, semaine connue.
    seen: set[str] = set()
    ok_assign = []
    for a in plan.assignments:
        if a.task_id not in tasks:
            v.append(Violation(type="format", detail=f"tâche inconnue {a.task_id}"))
            continue
        if a.member_id not in members:
            v.append(Violation(type="format", detail=f"membre inconnu {a.member_id} pour {a.task_id}"))
            continue
        if a.week not in s.weeks:
            v.append(Violation(type="format", detail=f"semaine inconnue {a.week} pour {a.task_id}"))
            continue
        if a.task_id in seen:
            v.append(Violation(type="format", detail=f"tâche {a.task_id} attribuée plusieurs fois"))
            continue
        seen.add(a.task_id)
        ok_assign.append(a)

    week_of = {a.task_id: a.week for a in ok_assign}
    hours = defaultdict(float)
    count = defaultdict(int)
    for a in ok_assign:
        t, m = tasks[a.task_id], members[a.member_id]
        hours[(m.id, a.week)] += t.hours
        count[(m.id, a.week)] += 1
        if m.level(t.skill) < t.min_level:
            v.append(Violation(type="competence",
                               detail=f"{a.task_id} demande {t.skill}>={t.min_level}, {m.id} a {m.level(t.skill)}"))
        if s.week_index(a.week) > s.week_index(t.deadline):
            v.append(Violation(type="echeance", detail=f"{a.task_id} en {a.week} après l'échéance {t.deadline}"))
        for d in t.depends_on:
            if d in week_of and s.week_index(a.week) < s.week_index(week_of[d]):
                v.append(Violation(type="dependance",
                                   detail=f"{a.task_id} en {a.week} avant sa dépendance {d} ({week_of[d]})"))

    for (mid, w), h in sorted(hours.items()):
        cap = members[mid].capacity_hours.get(w, 0)
        if h > cap + 1e-9:
            v.append(Violation(type="capacite", detail=f"{mid} en {w} : {h:g} h pour {cap:g} h disponibles"))
    for (mid, w), c in sorted(count.items()):
        mx = members[mid].max_tasks_per_week
        if mx is not None and c > mx:
            v.append(Violation(type="max_taches", detail=f"{mid} en {w} : {c} tâches pour {mx} max"))

    good_meetings = set()
    for slot in plan.meetings:
        try:
            week, _, _ = parse_slot(slot)
        except ValueError:
            v.append(Violation(type="format", detail=f"créneau mal formé {slot!r}"))
            continue
        if week not in s.weeks:
            v.append(Violation(type="format", detail=f"créneau hors période {slot}"))
            continue
        absent = [m.id for m in present_members(s, week) if slot not in m.meeting_slots]
        if absent:
            v.append(Violation(type="reunion", detail=f"{slot} impossible pour {', '.join(absent)}"))
        elif slot in good_meetings:
            v.append(Violation(type="format", detail=f"créneau {slot} retenu deux fois"))
        else:
            good_meetings.add(slot)

    # Scores souples
    n_assigned = len(ok_assign)
    pref = sum(1 for a in ok_assign if tasks[a.task_id].skill in members[a.member_id].preferences)
    disl = sum(1 for a in ok_assign if tasks[a.task_id].skill in members[a.member_id].dislikes)
    occupation = []
    for m in s.members:
        cap = sum(m.capacity_hours.get(w, 0) for w in s.weeks)
        used = sum(hours[(m.id, w)] for w in s.weeks)
        occupation.append(used / cap if cap > 0 else 0.0)
    mean = statistics.fmean(occupation) if occupation else 0.0
    load_balance = statistics.pstdev(occupation) / mean if mean > 0 else 0.0

    counts = {t: 0 for t in VIOLATION_TYPES}
    for x in v:
        counts[x.type] = counts.get(x.type, 0) + 1

    missing = len(seen) < len(tasks) or len(good_meetings) < s.meetings_needed
    return Report(
        valid=not v,
        violations=v,
        violation_counts=counts,
        assigned_ratio=len(seen) / len(tasks) if tasks else 1.0,
        preference_ratio=(pref - disl) / n_assigned if n_assigned else 0.0,
        dislike_ratio=disl / n_assigned if n_assigned else 0.0,
        load_balance=load_balance,
        meetings_ratio=min(len(good_meetings), s.meetings_needed) / s.meetings_needed if s.meetings_needed else 1.0,
        escalation_correct=(len(plan.escalations) > 0) if s.difficulty == "impossible" else None,
        silent_failure=missing and not plan.escalations,
    )
