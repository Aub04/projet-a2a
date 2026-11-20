"""Mesures d'une exécution et stabilité entre répétitions (section 12 de docs/SPEC.md).

`run_row` aplatit un `RunResult` en une ligne de `runs.jsonl`. Les champs sont ceux de la spec : validité,
violations, taux d'attribution, préférences, équilibre de charge, réunions, escalade, coût, imprévus.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from statistics import mean

from a2a_team.architectures.runner import RunResult
from a2a_team.models import Scenario, apply_events


def assignment_keys(plan) -> list[str]:
    return sorted(f"{a.task_id}:{a.member_id}:{a.week}" for a in plan.assignments)


def run_row(r: RunResult, scenario: Scenario, campagne: str, run_dir: str = "") -> dict:
    rep, s = r.report, r.stats
    ev = r.events
    return {
        "key": run_key(scenario.id, r.arch, r.repeat),
        "campagne": campagne,
        "split": scenario.split,
        "scenario_id": scenario.id,
        "difficulty": scenario.difficulty,
        "arch": r.arch,
        "repeat": r.repeat,
        "n_members": len(scenario.members),
        "n_tasks": len(scenario.tasks) + sum(1 for e in scenario.events if e.task),
        "n_events": len(scenario.events),
        # qualité du planning final (après imprévus)
        "valid": rep.valid,
        "n_violations": len(rep.violations),
        "violation_types": dict(Counter(v.type for v in rep.violations)),
        "assigned_ratio": rep.assigned_ratio,
        "feasible_ratio": feasible_ratio(scenario, r.plan),
        "complete": rep.valid and rep.assigned_ratio == 1.0 and rep.meetings_ratio == 1.0,
        "preference_ratio": rep.preference_ratio,
        "dislike_ratio": rep.dislike_ratio,
        "load_balance": rep.load_balance,
        "meetings_ratio": rep.meetings_ratio,
        "escalation_correct": rep.escalation_correct,
        "silent_failure": rep.silent_failure,
        # planning initial (avant imprévus)
        "valid_initial": r.report_initial.valid,
        "assigned_ratio_initial": r.report_initial.assigned_ratio,
        "complete_initial": r.report_initial.valid and r.report_initial.assigned_ratio == 1.0
        and r.report_initial.meetings_ratio == 1.0,
        # coût
        "messages": s.get("messages", 0),
        "llm_calls": s.get("llm_calls", 0),
        "tokens_in": s.get("tokens_in", 0),
        "tokens_out": s.get("tokens_out", 0),
        "llm_seconds": s.get("llm_seconds", 0.0),
        "duration": r.duration,
        "error": r.error,
        # imprévus
        "events_valid_ratio": mean(e.valid_after for e in ev) if ev else None,
        "events_messages": mean(e.messages for e in ev) if ev else None,
        "events_llm_calls": mean(e.llm_calls for e in ev) if ev else None,
        "assignments": assignment_keys(r.plan),
        "n_escalations": len(r.plan.escalations),
        "run_dir": run_dir,
    }


def feasible_ratio(scenario: Scenario, plan) -> float:
    """Part des tâches attribuées de façon réalisable avec les vraies contraintes : qualification, échéance,
    dépendances respectées, et case (membre, semaine) sans dépassement d'heures ni du nombre de tâches.
    Pour un planning invalide, les tâches d'une case surchargée comptent toutes comme non réalisables."""
    s = apply_events(scenario)
    tasks, members = {t.id: t for t in s.tasks}, {m.id: m for m in s.members}
    week_of = {a.task_id: a.week for a in plan.assignments}
    hours, count = Counter(), Counter()
    for a in plan.assignments:
        if a.task_id in tasks and a.member_id in members:
            hours[(a.member_id, a.week)] += tasks[a.task_id].hours
            count[(a.member_id, a.week)] += 1
    ok = 0
    for a in plan.assignments:
        t, m = tasks.get(a.task_id), members.get(a.member_id)
        if not t or not m or a.week not in s.weeks:
            continue
        cell = (a.member_id, a.week)
        if m.level(t.skill) < t.min_level or s.week_index(a.week) > s.week_index(t.deadline):
            continue
        if any(d in week_of and s.week_index(a.week) < s.week_index(week_of[d]) for d in t.depends_on):
            continue
        if hours[cell] > m.capacity_hours.get(a.week, 0) + 1e-9:
            continue
        if m.max_tasks_per_week is not None and count[cell] > m.max_tasks_per_week:
            continue
        ok += 1
    return ok / len(tasks) if tasks else 1.0


def run_key(scenario_id: str, arch: str, repeat: int) -> str:
    return f"{scenario_id}__{arch}__r{repeat}"


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def stability(rows: list[dict]) -> dict[tuple[str, str], float]:
    """Jaccard moyen des affectations entre répétitions, par (scénario, architecture). Il faut au moins 2 répétitions."""
    groups: dict[tuple[str, str], list[list[str]]] = {}
    for r in rows:
        groups.setdefault((r["scenario_id"], r["arch"]), []).append(r["assignments"])
    return {k: mean(jaccard(a, b) for a, b in combinations(v, 2)) for k, v in groups.items() if len(v) >= 2}
