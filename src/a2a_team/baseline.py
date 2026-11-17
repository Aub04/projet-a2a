"""Baseline sans IA : algorithme glouton déterministe (section 4 de docs/SPEC.md).

La baseline voit le scénario final (imprévus déjà appliqués) : elle replanifie de zéro, sans message.
"""

from __future__ import annotations

from collections import defaultdict

from a2a_team.models import Assignment, Plan, Scenario, Task, apply_events, present_members, slot_sort_key


def task_order(scenario: Scenario, tasks: list[Task] | None = None) -> list[Task]:
    """Tri par échéance puis tri topologique : une tâche passe toujours après ses dépendances."""
    tasks = list(scenario.tasks if tasks is None else tasks)
    ids = {t.id for t in tasks}
    key = {t.id: (scenario.week_index(t.deadline), i) for i, t in enumerate(tasks)}
    done: set[str] = set()
    order: list[Task] = []
    remaining = sorted(tasks, key=lambda t: key[t.id])
    while remaining:
        for t in remaining:
            if all(d in done or d not in ids for d in t.depends_on):
                break
        else:  # cycle : on prend la première pour ne pas bloquer
            t = remaining[0]
        order.append(t)
        done.add(t.id)
        remaining.remove(t)
    return order


def common_slots(scenario: Scenario) -> list[str]:
    """Créneaux où tous les membres présents la semaine concernée sont disponibles, dans l'ordre chronologique."""
    all_slots = sorted({s for m in scenario.members for s in m.meeting_slots},
                       key=lambda s: slot_sort_key(s, scenario.weeks))
    result = []
    for slot in all_slots:
        week = slot.split("-")[0]
        if week not in scenario.weeks:
            continue
        present = present_members(scenario, week)
        if present and all(slot in m.meeting_slots for m in present):
            result.append(slot)
    return result


def solve(scenario: Scenario, apply_scenario_events: bool = True) -> Plan:
    s = apply_events(scenario) if apply_scenario_events else scenario
    used = defaultdict(float)
    count = defaultdict(int)
    week_of: dict[str, str] = {}
    plan = Plan()

    for t in task_order(s):
        earliest = max([s.week_index(week_of[d]) for d in t.depends_on if d in week_of], default=0)
        chosen = None
        for wi in range(earliest, s.week_index(t.deadline) + 1):
            w = s.weeks[wi]
            cands = []
            for m in s.members:
                if m.level(t.skill) < t.min_level:
                    continue
                rest = m.capacity_hours.get(w, 0) - used[(m.id, w)]
                if rest < t.hours - 1e-9:
                    continue
                if m.max_tasks_per_week is not None and count[(m.id, w)] >= m.max_tasks_per_week:
                    continue
                # plus de capacité restante, puis préférence, puis pas de rejet, puis identifiant
                cands.append((-rest, t.skill not in m.preferences, t.skill in m.dislikes, m.id))
            if cands:
                chosen = (min(cands)[3], w)
                break
        if chosen is None:
            plan.unassigned.append(t.id)
            plan.escalations.append(f"Tâche {t.id} sans membre disponible et compétent avant {t.deadline}")
            continue
        mid, w = chosen
        used[(mid, w)] += t.hours
        count[(mid, w)] += 1
        week_of[t.id] = w
        plan.assignments.append(Assignment(task_id=t.id, member_id=mid, week=w))

    slots = common_slots(s)
    plan.meetings = slots[: s.meetings_needed]
    if len(plan.meetings) < s.meetings_needed:
        plan.escalations.append(
            f"Seulement {len(plan.meetings)} créneau(x) commun(s) pour {s.meetings_needed} réunion(s)")
    return plan
