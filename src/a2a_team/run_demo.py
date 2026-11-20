"""Commande unique de démonstration.

    uv run python -m a2a_team.run_demo --arch p2p --scenario notre_groupe
    uv run python -m a2a_team.run_demo --arch orchestrator --scenario dev_1001
    uv run python -m a2a_team.run_demo --arch baseline --scenario notre_groupe

Options : --fake-llm (sans Ollama, réponses scriptées), --transport memory (sans serveurs HTTP).
Résultats dans runs/<date>_<arch>_<scenario>/ : trace.jsonl, llm_calls.jsonl, result.json.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from a2a_team.architectures.runner import ARCHS, run
from a2a_team.llm import load_config, make_client
from a2a_team.models import Scenario

ROOT = Path(__file__).resolve().parents[2]


def find_scenario(name: str, allow_templates: bool = False) -> Scenario:
    p = Path(name)
    if p.exists():
        return Scenario.load(p)
    for split in ("demo", "dev", "test"):
        f = ROOT / "data" / "scenarios" / split / f"{name}.json"
        if f.exists():
            return Scenario.load(f)
    if name == "notre_groupe":
        from a2a_team.scenarios.from_profiles import ProfilesNotFilled, build
        try:
            return build(allow_templates)
        except ProfilesNotFilled as e:
            sys.exit(f"{e}\nRemplissez data/profiles/ (A_REMPLIR: false), ou relancez avec --allow-templates "
                     f"pour un essai technique sur les modèles.")
    sys.exit(f"scénario introuvable : {name}")


def print_result(r, scenario: Scenario) -> None:
    names = {m.id: m.name for m in scenario.members}
    titles = {t.id: t.title for t in scenario.tasks}
    for e in scenario.events:
        if e.task:
            titles[e.task.id] = e.task.title
    print(f"\n=== {r.arch} sur {r.scenario_id} ({r.difficulty}) en {r.duration:.1f} s ===")
    if r.error:
        print(f"ERREUR : {r.error}")
    print(f"{'tâche':6} {'semaine':8} {'membre':14} titre")
    for a in sorted(r.plan.assignments, key=lambda a: (a.week, a.task_id)):
        print(f"{a.task_id:6} {a.week:8} {names.get(a.member_id, a.member_id):14} {titles.get(a.task_id, '')}")
    print(f"Réunions : {', '.join(r.plan.meetings) or 'aucune'}")
    print(f"Non attribuées : {', '.join(r.plan.unassigned) or 'aucune'}")
    for e in r.plan.escalations:
        print(f"Escalade : {e}")
    rep = r.report
    print(f"\nValide : {rep.valid}  attribuées : {rep.assigned_ratio:.0%}  réunions : {rep.meetings_ratio:.0%}  "
          f"préférences : {rep.preference_ratio:+.2f}  équilibre (CV) : {rep.load_balance:.2f}")
    for v in rep.violations:
        print(f"  violation {v.type} : {v.detail}")
    s = r.stats
    print(f"Coût : {s['messages']} messages A2A, {s['llm_calls']} appels LLM, {s['tokens_in']}+{s['tokens_out']} "
          f"tokens")
    for e in r.events:
        print(f"Imprévu {e.type} : valide après = {e.valid_after}, {e.messages} messages, {e.llm_calls} appels LLM")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--arch", choices=ARCHS, required=True)
    p.add_argument("--scenario", default="notre_groupe")
    p.add_argument("--fake-llm", action="store_true")
    p.add_argument("--transport", choices=["http", "memory"], default="http")
    p.add_argument("--no-events", action="store_true", help="ignore les imprévus du scénario")
    p.add_argument("--allow-templates", action="store_true")
    p.add_argument("--config", default=None)
    a = p.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    cfg = load_config(a.config)
    scenario = find_scenario(a.scenario, a.allow_templates)
    if a.no_events:
        scenario.events = []
    run_dir = ROOT / "runs" / f"{datetime.now():%Y%m%d_%H%M%S}_{a.arch}_{scenario.id}"
    llm = None if a.arch in ("baseline", "oracle") else make_client(cfg, fake=a.fake_llm)

    def live(ev: dict) -> None:
        if ev.get("kind") == "a2a" and ev["type"] != "ACK":
            c = ev["content"]
            detail = c.get("task_id") or c.get("task", {}).get("id", "") if isinstance(c, dict) else ""
            print(f"[{ev['ts']:7.1f}s] {ev['sender']:>13} -> {ev['recipient']:<13} {ev['type']:<15} {detail}",
                  flush=True)

    r = run(a.arch, scenario, llm, run_dir, cfg["limits"]["max_rounds"], a.transport, on_event=live,
            orchestrator_hints=cfg["limits"].get("orchestrator_hints", True))
    print_result(r, scenario)
    print(f"\nTrace : {run_dir}")


if __name__ == "__main__":
    main()
