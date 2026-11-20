"""Export pour la notation à l'aveugle (section 12 de docs/SPEC.md).

    uv run python -m a2a_team.eval.notation results/campagne_demo --scenario notre_groupe

Écrit dans results/notation_aveugle/<scénario>/ : planning_A.md, planning_B.md (p2p et orchestrateur tirés au hasard),
notes.csv à remplir par chaque membre (note de 1 à 5) et correspondance.json (qui est A, qui est B : à ne pas ouvrir
avant d'avoir noté).
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

from a2a_team.models import Scenario
from a2a_team.run_demo import find_scenario

ROOT = Path(__file__).resolve().parents[3]


def plan_md(result: dict, scenario: Scenario, label: str) -> str:
    names = {m.id: m.name for m in scenario.members}
    titles = {t.id: t.title for t in scenario.tasks}
    plan = result["plan"]
    lines = [f"# {label} ({scenario.id})", "", "| semaine | tâche | membre |", "| --- | --- | --- |"]
    for a in sorted(plan["assignments"], key=lambda a: (a["week"], a["task_id"])):
        lines.append(f"| {a['week']} | {a['task_id']} {titles.get(a['task_id'], '')} | "
                     f"{names.get(a['member_id'], a['member_id'])} |")
    lines += ["", f"Réunions : {', '.join(plan['meetings']) or 'aucune'}",
              f"Tâches non attribuées : {', '.join(plan['unassigned']) or 'aucune'}", ""]
    if plan["escalations"]:
        lines += ["Points remontés au groupe :", ""] + [f"- {e}" for e in plan["escalations"]] + [""]
    return "\n".join(lines)


def export(camp: Path, scenario_id: str, out_root: Path | None = None, seed: int | None = None) -> Path:
    scenario = find_scenario(scenario_id)
    res = {}
    for arch in ("p2p", "orchestrator"):
        f = camp / "runs" / f"{scenario_id}__{arch}__r0" / "result.json"
        if not f.exists():
            raise SystemExit(f"Résultat manquant : {f}")
        res[arch] = json.loads(f.read_text(encoding="utf-8"))
    out = (out_root or ROOT / "results" / "notation_aveugle") / scenario_id
    if (out / "correspondance.json").exists():   # déjà exporté : on ne retire pas au sort (notes peut-être en cours)
        return out
    archs = ["p2p", "orchestrator"]
    random.Random(seed).shuffle(archs)
    out.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for label, arch in zip(("A", "B"), archs):
        (out / f"planning_{label}.md").write_text(plan_md(res[arch], scenario, f"Planning {label}"), encoding="utf-8")
        mapping[f"Planning {label}"] = arch
    (out / "correspondance.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    with open(out / "notes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["membre", "planning", "note_1_a_5", "commentaire"])
        for m in scenario.members:
            for label in ("A", "B"):
                w.writerow([m.name, f"Planning {label}", "", ""])
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("campagne", type=Path)
    p.add_argument("--scenario", default="notre_groupe")
    a = p.parse_args()
    print(f"Export écrit dans {export(a.campagne, a.scenario)}")


if __name__ == "__main__":
    main()
