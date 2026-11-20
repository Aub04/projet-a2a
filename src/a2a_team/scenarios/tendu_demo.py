"""Variante « tendue » du scénario notre_groupe, pour la démonstration.

    uv run python -m a2a_team.scenarios.tendu_demo

Le scénario notre_groupe est confortable (ratio heures / capacité 0,59) : la baseline gloutonne le résout seule.
Cette variante garde les mêmes membres, tâches et créneaux, et réduit les heures disponibles de tous les membres
d'un même facteur jusqu'à ce que :
1. une solution complète et valide existe encore (prouvé par une recherche exhaustive, `exact_solve`) ;
2. la baseline gloutonne n'en trouve pas.
Le facteur retenu est le plus grand qui remplit ces deux conditions avec un ratio d'au moins 0,70 (seuil « tendu »
de from_profiles.py). Au-delà d'un ratio de 0,72 environ, la recherche exhaustive ne trouve plus de solution.
Sortie : data/scenarios/demo/notre_groupe_tendu.json et la solution de référence dans data/scenarios/demo/ref/
(jamais montrée aux agents). Ce scénario sert à la démo, pas à la campagne test (split figé).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from a2a_team.baseline import solve
from a2a_team.models import Assignment, Plan, Scenario
from a2a_team.scenarios.generator import common_slots, ratio
from a2a_team.validator import validate

DEMO = Path(__file__).resolve().parents[3] / "data" / "scenarios" / "demo"


def exact_solve(s: Scenario, node_limit: int = 2_000_000) -> Plan | None:
    """Recherche exhaustive (retour arrière) d'une affectation complète respectant les contraintes dures :
    compétence minimale, échéance, dépendances (même semaine ou après), heures et nombre de tâches par semaine."""
    weeks = s.weeks
    tasks = {t.id: t for t in s.tasks}
    order, seen = [], set()

    def visit(tid: str) -> None:
        if tid in seen:
            return
        seen.add(tid)
        for d in tasks[tid].depends_on:
            if d in tasks:
                visit(d)
        order.append(tid)

    for t in sorted(s.tasks, key=lambda t: (weeks.index(t.deadline), t.id)):
        visit(t.id)
    free = {(m.id, w): m.capacity_hours.get(w, 0) for m in s.members for w in weeks}
    count = {(m.id, w): 0 for m in s.members for w in weeks}
    week_of: dict[str, int] = {}
    out: list[Assignment] = []
    nodes = 0

    def rec(i: int) -> bool:
        nonlocal nodes
        nodes += 1
        if nodes > node_limit:
            raise TimeoutError
        if i == len(order):
            return True
        t = tasks[order[i]]
        lo = max([week_of[d] for d in t.depends_on if d in week_of], default=0)
        hi = weeks.index(t.deadline)
        for wi in range(lo, hi + 1):
            w = weeks[wi]
            for m in sorted(s.members, key=lambda m: -free[(m.id, w)]):
                k = (m.id, w)
                if m.skills.get(t.skill, 0) < t.min_level or free[k] < t.hours:
                    continue
                if m.max_tasks_per_week is not None and count[k] >= m.max_tasks_per_week:
                    continue
                free[k] -= t.hours
                count[k] += 1
                week_of[t.id] = wi
                out.append(Assignment(task_id=t.id, member_id=m.id, week=w))
                if rec(i + 1):
                    return True
                out.pop()
                del week_of[t.id]
                free[k] += t.hours
                count[k] -= 1
        return False

    try:
        ok = rec(0)
    except TimeoutError:
        return None
    if not ok:
        return None
    return Plan(assignments=sorted(out, key=lambda a: a.task_id), meetings=common_slots(s)[: s.meetings_needed])


def scaled(base: Scenario, f: float) -> Scenario:
    s = base.model_copy(deep=True)
    for m in s.members:
        m.capacity_hours = {w: math.floor(h * f * 2) / 2 for w, h in m.capacity_hours.items()}
    s.id = "notre_groupe_tendu"
    return s


def complete(s: Scenario, plan: Plan) -> bool:
    r = validate(s, plan, apply_scenario_events=False)
    return r.valid and r.assigned_ratio == 1.0 and r.meetings_ratio == 1.0


def build() -> tuple[Scenario, Plan, float]:
    base = Scenario.load(DEMO / "notre_groupe.json")
    best = None
    for k in range(100, 40, -1):
        f = k / 100
        s = scaled(base, f)
        r = ratio(s)
        if r > 1.0:
            break
        if r < 0.70:   # seuil « tendu » de from_profiles.py
            continue
        if complete(s, solve(s, False)):
            continue
        ref = exact_solve(s)
        if ref is not None and complete(s, ref):
            best = (s, ref, f)
            break
    if best is None:
        raise SystemExit("Aucun facteur ne donne un scénario tendu soluble où la baseline échoue.")
    s, ref, f = best
    s.difficulty = "tendu"
    return s, ref, f


def main() -> None:
    s, ref, f = build()
    out = DEMO / "notre_groupe_tendu.json"
    s.save(out)
    (DEMO / "ref").mkdir(exist_ok=True)
    (DEMO / "ref" / "notre_groupe_tendu.json").write_text(ref.model_dump_json(indent=2), encoding="utf-8")
    b = validate(s, solve(s, False), apply_scenario_events=False)
    print(f"{out.name} écrit : heures disponibles x{f:.2f}, ratio {ratio(s):.2f}.")
    print(f"Solution de référence complète : oui. Baseline : {b.assigned_ratio:.0%} des tâches, "
          f"réunions {b.meetings_ratio:.0%}, valide {b.valid}.")


if __name__ == "__main__":
    main()
