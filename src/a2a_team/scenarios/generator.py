"""Générateur de scénarios fictifs (section 11 de docs/SPEC.md).

Usage :
    uv run python -m a2a_team.scenarios.generator --split dev
    uv run python -m a2a_team.scenarios.generator --split test
    uv run python -m a2a_team.scenarios.generator --check      # vérifie les fichiers existants avec la baseline

Chaque scénario est entièrement déterminé par sa graine. Les scénarios « confortable » et « tendu » sont retirés
jusqu'à ce que la baseline trouve un planning valide et complet (tâches et réunions) avant imprévus.
"""

from __future__ import annotations

import argparse
import hashlib
import random
from pathlib import Path

from a2a_team.baseline import solve
from a2a_team.baseline import common_slots
from a2a_team.models import DAYS, SKILLS, Assignment, Event, MemberProfile, Plan, Scenario, Task
from a2a_team.validator import total_capacity, validate

WEEKS = ["S47", "S48", "S49"]
SPLITS = {"dev": range(1000, 1020), "test": range(2000, 2050)}
RATIO_BANDS = {"confortable": (0.45, 0.68), "tendu": (0.85, 0.99), "impossible": (1.18, 1.4)}
FIRST_NAMES = ["Alice", "Bastien", "Chloé", "David", "Emma", "Farid", "Gaëlle", "Hugo", "Inès", "Jules",
               "Karim", "Léa", "Mathis", "Nina", "Oscar", "Pauline", "Quentin", "Rose", "Samuel", "Tess"]
ROOT = Path(__file__).resolve().parents[3]
SCEN_DIR = ROOT / "data" / "scenarios"


def difficulty_for(index: int) -> str:
    """Répartition fixe : 2/5 confortable, 2/5 tendu, 1/5 impossible."""
    return ["confortable", "confortable", "tendu", "tendu", "impossible"][index % 5]


def _member(rng: random.Random, i: int, slot_pool: list[str]) -> MemberProfile:
    skills = {s: rng.randint(1, 3) for s in SKILLS}
    strong = rng.sample(SKILLS, 2)
    for s in strong:
        skills[s] = rng.randint(4, 5)
    weak = [s for s in SKILLS if skills[s] <= 2] or [min(SKILLS, key=skills.get)]
    if rng.random() < 0.6:   # une semaine forte (école), deux faibles (entreprise)
        strong_weeks = [WEEKS[0]]
    else:                    # l'inverse
        strong_weeks = rng.sample(WEEKS, 2)
    cap = {w: float(rng.randint(15, 25) if w in strong_weeks else rng.randint(3, 8)) for w in WEEKS}
    max_tasks = None if rng.random() < 0.6 else rng.randint(2, 4)
    limits = [f"Pas plus de {cap[w]:g} heures de projet en {w}" for w in WEEKS if w not in strong_weeks]
    return MemberProfile(
        id=f"m{i + 1}", name=FIRST_NAMES[(i + rng.randint(0, 19)) % 20],
        skills=skills, capacity_hours=cap, max_tasks_per_week=max_tasks,
        preferences=rng.sample(strong, rng.randint(1, 2)),
        dislikes=rng.sample(weak, 1),
        hard_limits=limits,
        meeting_slots=sorted(s for s in slot_pool if rng.random() < 0.72),
    )


def _draw(seed: int, attempt: int, split: str, difficulty: str) -> tuple[Scenario, Plan]:
    rng = random.Random(seed * 1000 + attempt)
    n_members = rng.choices([3, 4, 5, 6, 7], weights=[1, 2, 6, 2, 1])[0]
    slot_pool = [f"{w}-{d}-{h}" for w in WEEKS
                 for d, h in rng.sample([(d, h) for d in DAYS for h in (10, 14, 18, 20)], 8)]
    members = [_member(rng, i, slot_pool) for i in range(n_members)]
    # prénoms uniques
    used = set()
    for m in members:
        while m.name in used:
            m.name = FIRST_NAMES[(FIRST_NAMES.index(m.name) + 1) % 20]
        used.add(m.name)

    n_tasks = rng.randint(8, 25)
    lo, hi = RATIO_BANDS[difficulty]
    target = rng.uniform(lo, hi)
    cap_total = sum(sum(m.capacity_hours.values()) for m in members)
    # 1) répartition des tâches dans des cellules (membre, semaine), en respectant max_tasks_per_week
    cells = [(m, w) for m in members for w in WEEKS]
    pieces = {(m.id, w): 0 for m, w in cells}
    for _ in range(n_tasks):
        allowed = [(m, w) for m, w in cells
                   if (m.max_tasks_per_week is None or pieces[(m.id, w)] < m.max_tasks_per_week)
                   and pieces[(m.id, w)] < m.capacity_hours[w]]
        weights = [m.capacity_hours[w] / (pieces[(m.id, w)] + 1) for m, w in allowed]
        m, w = rng.choices(allowed, weights=weights)[0]
        pieces[(m.id, w)] += 1
    # 2) budget d'heures par cellule occupée : proportionnel à la capacité, total visé = target * capacité
    used_cells = [(m, w) for m, w in cells if pieces[(m.id, w)]]
    used_cap = sum(m.capacity_hours[w] for m, w in used_cells)
    scale = target * cap_total / used_cap
    if difficulty != "impossible" and scale > 1:
        scale = 1.0   # cellules occupées pleines : le ratio visé n'est pas atteignable, rejeté plus bas
    # 3) découpage de chaque budget en tâches (solution cachée)
    planted: list[tuple[str, str, str, int]] = []   # (membre, semaine, compétence, heures)
    for m, w in used_cells:
        k = pieces[(m.id, w)]
        budget = max(k, int(m.capacity_hours[w] * scale))
        cuts = sorted(rng.sample(range(1, budget), k - 1)) if k > 1 else []
        parts = [b - a for a, b in zip([0] + cuts, cuts + [budget])]
        good = [sk for sk in SKILLS if m.level(sk) >= 2] or SKILLS
        for h in parts:
            planted.append((m.id, w, rng.choice(good), h))
    rng.shuffle(planted)
    planted.sort(key=lambda x: WEEKS.index(x[1]))
    tasks: list[Task] = []
    for k, (mid, w, skill, h) in enumerate(planted):
        m = next(x for x in members if x.id == mid)
        deadline = rng.choice(WEEKS[WEEKS.index(w):]) if rng.random() < 0.5 else w
        earlier = [t for t, p in zip(tasks, planted) if WEEKS.index(p[1]) <= WEEKS.index(w)]
        deps = [rng.choice(earlier).id] if earlier and rng.random() < 0.3 else []
        tasks.append(Task(id=f"T{k + 1:02d}", title=f"Tâche {k + 1} ({skill})", skill=skill,
                          min_level=rng.randint(1, min(4, m.level(skill))), hours=float(h),
                          deadline=deadline, depends_on=deps))
    # La solution cachée est rangée à part, elle n'est jamais montrée aux agents.
    hidden = [(t.id, p[0], p[1]) for t, p in zip(tasks, planted)]

    events = []
    for r in sorted(rng.sample([1, 2, 3], rng.choices([0, 1, 2, 3], weights=[1, 3, 3, 1])[0])):
        kind = rng.choice(["absence", "new_task", "deadline_change"])
        if kind == "absence":
            events.append(Event(trigger_round=r, type="absence", member_id=rng.choice(members).id,
                                weeks=[rng.choice(WEEKS)]))
        elif kind == "new_task":
            skill = rng.choice(SKILLS)
            events.append(Event(trigger_round=r, type="new_task", task=Task(
                id=f"U{r}", title=f"Tâche urgente {r} ({skill})", skill=skill,
                min_level=min(2, max(m.level(skill) for m in members)), hours=float(rng.randint(2, 5)),
                deadline=rng.choice(WEEKS[1:]))))
        else:
            movable = [t for t in tasks if WEEKS.index(t.deadline) > 0]
            if movable:
                t = rng.choice(movable)
                events.append(Event(trigger_round=r, type="deadline_change", task_id=t.id,
                                    new_deadline=WEEKS[WEEKS.index(t.deadline) - 1]))

    scen = Scenario(id=f"{split}_{seed}", split=split, seed=seed, weeks=list(WEEKS), members=members,
                    tasks=tasks, meetings_needed=rng.randint(1, 3), events=events, difficulty=difficulty)
    return scen, Plan(assignments=[Assignment(task_id=t, member_id=m, week=w) for t, m, w in hidden])


def ratio(s: Scenario) -> float:
    return sum(t.hours for t in s.tasks) / total_capacity(s)


def baseline_complete(s: Scenario, with_events: bool = False) -> bool:
    plan = solve(s, apply_scenario_events=with_events)
    r = validate(s, plan, apply_scenario_events=with_events)
    return r.valid and r.assigned_ratio == 1.0 and r.meetings_ratio == 1.0


def reference_ok(s: Scenario, ref: Plan) -> bool:
    """La solution cachée est valide et complète, et il y a assez de créneaux communs."""
    ref = ref.model_copy(deep=True)
    ref.meetings = common_slots(s)[: s.meetings_needed]
    r = validate(s, ref, apply_scenario_events=False)
    return r.valid and r.assigned_ratio == 1.0 and r.meetings_ratio == 1.0


def generate(seed: int, split: str, difficulty: str, max_attempts: int = 2000) -> tuple[Scenario, Plan]:
    lo, hi = RATIO_BANDS[difficulty]
    for attempt in range(max_attempts):
        s, ref = _draw(seed, attempt, split, difficulty)
        if not lo <= ratio(s) <= hi:
            continue
        if difficulty == "impossible" or reference_ok(s, ref):
            return s, ref
    raise RuntimeError(f"aucun scénario {difficulty} soluble pour la graine {seed}")


def generate_split(split: str, out_dir: Path = SCEN_DIR) -> list[Scenario]:
    out = out_dir / split
    scenarios = []
    for i, seed in enumerate(SPLITS[split]):
        s, ref = generate(seed, split, difficulty_for(i))
        s.save(out / f"{s.id}.json")
        (out / "ref").mkdir(parents=True, exist_ok=True)
        (out / "ref" / f"{s.id}.json").write_text(ref.model_dump_json(indent=2), encoding="utf-8")
        scenarios.append(s)
    return scenarios


def split_hash(split: str, out_dir: Path = SCEN_DIR) -> str:
    h = hashlib.sha256()
    for f in sorted((out_dir / split).glob("*.json")):
        h.update(f.name.encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def load_split(split: str, out_dir: Path = SCEN_DIR) -> list[Scenario]:
    return [Scenario.load(f) for f in sorted((out_dir / split).glob("*.json"))]


def check_report(scenarios: list[Scenario]) -> str:
    lines = ["| scénario | difficulté | membres | tâches | ratio | baseline complète | après imprévus |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for s in scenarios:
        lines.append(f"| {s.id} | {s.difficulty} | {len(s.members)} | {len(s.tasks)} | {ratio(s):.2f} | "
                     f"{'oui' if baseline_complete(s) else 'non'} | "
                     f"{'oui' if baseline_complete(s, True) else 'non'} |")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--split", choices=list(SPLITS))
    p.add_argument("--check", action="store_true")
    p.add_argument("--force", action="store_true", help="écrase un split existant (interdit pour test)")
    a = p.parse_args()
    if a.split:
        existing = list((SCEN_DIR / a.split).glob("*.json"))
        if existing and not a.force:
            raise SystemExit(f"{a.split} existe déjà ({len(existing)} fichiers). Le split test est figé.")
        generate_split(a.split)
        print(f"{a.split} : sha256 = {split_hash(a.split)}")
    if a.check:
        for split in SPLITS:
            if (SCEN_DIR / split).exists():
                print(f"\n## {split} (sha256 {split_hash(split)})\n")
                print(check_report(load_split(split)))


if __name__ == "__main__":
    main()
