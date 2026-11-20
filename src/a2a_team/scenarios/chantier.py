"""Générateur de scénarios « chantier » : coordination de sous-traitants avec informations privées.

    uv run python -m a2a_team.scenarios.chantier --split dev     # 20 scénarios, graines 3000 à 3019
    uv run python -m a2a_team.scenarios.chantier --split test    # 50 scénarios, graines 4000 à 4049 (figé ensuite)
    uv run python -m a2a_team.scenarios.chantier --split demo    # chantier_demo
    uv run python -m a2a_team.scenarios.chantier --check         # baseline, oracle et solution de référence

Situation : plusieurs entreprises indépendantes interviennent sur un même chantier de rénovation (plomberie,
électricité, plâtrerie...). Chaque entreprise déclare ses heures d'équipe par semaine, mais ne dit pas qu'elle est
engagée sur d'autres chantiers certaines semaines (clients et prix confidentiels). Ses vraies heures disponibles sont
donc plus faibles que ses déclarations ces semaines-là. Seul l'agent de l'entreprise les connaît.

- Le validateur vérifie le planning contre les vraies heures.
- La baseline « coordinateur central » planifie avec les heures déclarées (c'est tout ce qu'elle peut savoir).
- L'oracle planifie avec les vraies heures (ce que donnerait la centralisation si les entreprises partageaient tout).
- Les agents (p2p et orchestrateur) connaissent chacun leurs vraies heures et ne transmettent que leurs déclarations.

Comme pour le thème d'origine, chaque scénario est construit autour d'une solution cachée valide (sous les vraies
heures). Aucun scénario n'est choisi ou rejeté selon le résultat de la baseline ou des agents (sauf chantier_demo,
choisi pour la démonstration et signalé comme tel).
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from a2a_team.baseline import solve
from a2a_team.models import DAYS, Assignment, Event, MemberProfile, Plan, Scenario, Task, public_view
from a2a_team.scenarios.generator import SCEN_DIR, WEEKS, difficulty_for, ratio, reference_ok, split_hash
from a2a_team.validator import validate

SPLITS = {"dev": range(3000, 3020), "test": range(4000, 4050)}
DEMO_SEEDS = range(5000, 5200)
RATIO_BANDS = {"confortable": (0.45, 0.68), "tendu": (0.85, 0.99), "impossible": (1.18, 1.4)}
# Corps de métier et rang dans l'ordre d'intervention (réseaux, puis cloisons, puis menuiseries et sols, puis finitions)
TRADES = {"plomberie": 0, "electricite": 0, "chauffage_ventilation": 0, "platrerie": 1, "menuiserie": 2,
          "carrelage": 2, "peinture": 3}
LABEL = {"plomberie": "Plomberie", "electricite": "Électricité", "chauffage_ventilation": "Chauffage et ventilation",
         "platrerie": "Plâtrerie", "menuiserie": "Menuiserie", "carrelage": "Carrelage", "peinture": "Peinture"}
SURNAMES = ["Garnier", "Moreau", "Lefèvre", "Roux", "Fournier", "Girard", "Bonnet", "Dupuis", "Lambert", "Fontaine",
            "Rousseau", "Vincent", "Muller", "Leroy", "Chevalier", "Robin", "Gauthier", "Perrin", "Morel", "Mercier"]
ZONES = ["logement A1", "logement A2", "logement B1", "logement B2", "logement C1", "parties communes", "local vélos",
         "hall d'entrée", "logement C2", "cage d'escalier"]
SLOT_TIMES = [(d, h) for d in DAYS[:5] for h in (8, 11, 14, 17)]


def _company(rng: random.Random, i: int, main: str, slot_pool: list[str], used_names: set) -> MemberProfile:
    trades = list(TRADES)
    skills = {t: 0 for t in trades}
    skills[main] = rng.randint(4, 5)
    for t in rng.sample([t for t in trades if t != main], rng.choice([1, 1, 2])):
        skills[t] = rng.randint(2, 3)
    declared = float(rng.choice([20, 25, 30, 35, 40]))
    n_hidden = rng.choices([0, 1, 2], weights=[2, 6, 2])[0]
    hidden_weeks = rng.sample(WEEKS, n_hidden)
    true_cap = {w: (float(round(declared * rng.uniform(0.15, 0.6))) if w in hidden_weeks else declared) for w in WEEKS}
    limits = [f"Équipe engagée sur un autre chantier en {w} : {true_cap[w]:g} h réellement disponibles sur "
              f"{declared:g} h déclarées (information confidentielle)" for w in hidden_weeks]
    secondary = [t for t in trades if 0 < skills[t] < 4]
    name = f"{LABEL[main]} {SURNAMES[(i * 7 + rng.randint(0, 19)) % 20]}"
    while name in used_names:
        name = f"{LABEL[main]} {SURNAMES[(SURNAMES.index(name.split()[-1]) + 1) % 20]}"
    used_names.add(name)
    return MemberProfile(
        id=f"e{i + 1}", name=name, skills=skills, capacity_hours=true_cap, declared_capacity_hours={w: declared for w in WEEKS},
        max_tasks_per_week=None if rng.random() < 0.6 else rng.randint(2, 4),
        preferences=[main], dislikes=rng.sample(secondary, 1) if secondary and rng.random() < 0.5 else [],
        hard_limits=limits, meeting_slots=sorted(s for s in slot_pool if rng.random() < 0.72))


def _draw(seed: int, attempt: int, split: str, difficulty: str) -> tuple[Scenario, Plan]:
    rng = random.Random(seed * 1000 + attempt)
    n = rng.choices([4, 5, 6, 7], weights=[2, 5, 3, 1])[0]
    slot_pool = [f"{w}-{d}-{h}" for w in WEEKS for d, h in rng.sample(SLOT_TIMES, 8)]
    mains = list(TRADES)
    rng.shuffle(mains)
    mains = (mains * 2)[:n]
    used: set = set()
    members = [_company(rng, i, mains[i], slot_pool, used) for i in range(n)]

    n_tasks = rng.randint(8, 25)
    target = rng.uniform(*RATIO_BANDS[difficulty])
    cap_total = sum(sum(m.capacity_hours.values()) for m in members)
    cells = [(m, w) for m in members for w in WEEKS if m.capacity_hours[w] > 0]
    pieces = {(m.id, w): 0 for m, w in cells}
    for _ in range(n_tasks):
        allowed = [(m, w) for m, w in cells
                   if (m.max_tasks_per_week is None or pieces[(m.id, w)] < m.max_tasks_per_week)
                   and pieces[(m.id, w)] < m.capacity_hours[w]]
        weights = [m.capacity_hours[w] / (pieces[(m.id, w)] + 1) for m, w in allowed]
        m, w = rng.choices(allowed, weights=weights)[0]
        pieces[(m.id, w)] += 1
    used_cells = [(m, w) for m, w in cells if pieces[(m.id, w)]]
    scale = target * cap_total / sum(m.capacity_hours[w] for m, w in used_cells)
    if difficulty != "impossible" and scale > 1:
        scale = 1.0
    zones = rng.sample(ZONES, max(2, min(len(ZONES), n_tasks // 3)))
    planted = []   # (entreprise, semaine, métier, heures, zone) : solution cachée
    for m, w in used_cells:
        k = pieces[(m.id, w)]
        budget = max(k, int(m.capacity_hours[w] * scale))
        cuts = sorted(rng.sample(range(1, budget), k - 1)) if k > 1 else []
        doable = [t for t in TRADES if m.level(t) >= 2]
        for h in [b - a for a, b in zip([0] + cuts, cuts + [budget])]:
            planted.append((m.id, w, rng.choice(doable), h, rng.choice(zones)))
    rng.shuffle(planted)
    planted.sort(key=lambda x: (WEEKS.index(x[1]), TRADES[x[2]]))
    tasks: list[Task] = []
    for k, (mid, w, trade, h, zone) in enumerate(planted):
        m = next(x for x in members if x.id == mid)
        deadline = rng.choice(WEEKS[WEEKS.index(w):]) if rng.random() < 0.5 else w
        before = [t for t, p in zip(tasks, planted) if WEEKS.index(p[1]) <= WEEKS.index(w)
                  and p[4] == zone and TRADES[p[2]] < TRADES[trade]]
        deps = [max(before, key=lambda t: TRADES[t.skill]).id] if before and rng.random() < 0.8 else []
        tasks.append(Task(id=f"T{k + 1:02d}", title=f"{LABEL[trade]}, {zone}", skill=trade,
                          min_level=rng.randint(1, min(4, m.level(trade))), hours=float(h),
                          deadline=deadline, depends_on=deps))
    hidden = [(t.id, p[0], p[1]) for t, p in zip(tasks, planted)]

    events = []
    for r in sorted(rng.sample([1, 2, 3], rng.choices([0, 1, 2, 3], weights=[1, 3, 3, 1])[0])):
        kind = rng.choice(["absence", "new_task", "deadline_change"])
        if kind == "absence":
            events.append(Event(trigger_round=r, type="absence", member_id=rng.choice(members).id,
                                weeks=[rng.choice(WEEKS)]))
        elif kind == "new_task":
            trade = rng.choice(list(TRADES))
            events.append(Event(trigger_round=r, type="new_task", task=Task(
                id=f"U{r}", title=f"Réserve à lever : {LABEL[trade].lower()}, {rng.choice(zones)}", skill=trade,
                min_level=min(2, max(m.level(trade) for m in members)), hours=float(rng.randint(2, 6)),
                deadline=rng.choice(WEEKS[1:]))))
        else:
            movable = [t for t in tasks if WEEKS.index(t.deadline) > 0]
            if movable:
                t = rng.choice(movable)
                events.append(Event(trigger_round=r, type="deadline_change", task_id=t.id,
                                    new_deadline=WEEKS[WEEKS.index(t.deadline) - 1]))
    scen = Scenario(id=f"chantier_{split}_{seed}", split=split, seed=seed, weeks=list(WEEKS), members=members,
                    tasks=tasks, meetings_needed=rng.randint(1, 3), events=events, difficulty=difficulty)
    return scen, Plan(assignments=[Assignment(task_id=t, member_id=m, week=w) for t, m, w in hidden])


def generate(seed: int, split: str, difficulty: str, max_attempts: int = 2000) -> tuple[Scenario, Plan]:
    lo, hi = RATIO_BANDS[difficulty]
    for attempt in range(max_attempts):
        s, ref = _draw(seed, attempt, split, difficulty)
        if not lo <= ratio(s) <= hi:
            continue
        if difficulty != "impossible" and not reference_ok(s, ref):
            continue
        return s, ref
    raise RuntimeError(f"aucun scénario {difficulty} pour la graine {seed}")


def complete(s: Scenario, plan: Plan, with_events: bool = False) -> bool:
    r = validate(s, plan, apply_scenario_events=with_events)
    return r.valid and r.assigned_ratio == 1.0 and r.meetings_ratio == 1.0


def baseline_ok(s: Scenario) -> tuple[bool, bool]:
    """(coordinateur central sur déclarations, oracle) : planning complet et valide avant imprévus."""
    return complete(s, solve(public_view(s), False)), complete(s, solve(s, False))


def write(s: Scenario, ref: Plan, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "ref").mkdir(exist_ok=True)
    s.save(out / f"{s.id}.json")
    (out / "ref" / f"{s.id}.json").write_text(ref.model_dump_json(indent=2), encoding="utf-8")


def generate_split(split: str) -> list[Scenario]:
    out = SCEN_DIR / split
    if any(out.glob("*.json")):
        raise SystemExit(f"{out} contient déjà des scénarios : un split figé n'est jamais régénéré.")
    res = []
    for i, seed in enumerate(SPLITS[split]):
        s, ref = generate(seed, split, difficulty_for(i))
        write(s, ref, out)
        res.append(s)
    return res


def generate_demo() -> Scenario:
    """Premier scénario « tendu » à 5 entreprises où le coordinateur central échoue et l'oracle réussit.
    Choisi pour illustrer la situation en démonstration ; il n'entre pas dans les mesures."""
    for seed in DEMO_SEEDS:
        s, ref = generate(seed, "demo", "tendu")
        if len(s.members) == 5 and 12 <= len(s.tasks) <= 18 and baseline_ok(s) == (False, True):
            s.id = "chantier_demo"
            write(s, ref, SCEN_DIR / "demo")
            return s
    raise SystemExit("aucun scénario de démo trouvé")


def check_report(scenarios: list[Scenario]) -> str:
    lines = ["| scénario | difficulté | entreprises | interventions | ratio | central (déclarations) | oracle |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for s in scenarios:
        c, o = baseline_ok(s)
        lines.append(f"| {s.id} | {s.difficulty} | {len(s.members)} | {len(s.tasks)} | {ratio(s):.2f} | "
                     f"{'complet' if c else 'échec'} | {'complet' if o else 'échec'} |")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--split", choices=["dev", "test", "demo"])
    p.add_argument("--check", action="store_true")
    a = p.parse_args()
    if a.split == "demo":
        s = generate_demo()
        print(f"{s.id} écrit (graine {s.seed}, {len(s.members)} entreprises, {len(s.tasks)} interventions).")
    elif a.split:
        res = generate_split(a.split)
        print(f"{len(res)} scénarios écrits dans {SCEN_DIR / a.split}, SHA-256 {split_hash(a.split)}")
    if a.check:
        for split in ("dev", "test"):
            scen = [Scenario.load(f) for f in sorted((SCEN_DIR / split).glob("*.json"))]
            print(f"\n## {split}\n\n{check_report(scen)}")


if __name__ == "__main__":
    main()
