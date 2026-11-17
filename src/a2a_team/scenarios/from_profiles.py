"""Construit le scénario de démo « notre_groupe » depuis data/profiles/ et data/project_tasks.yaml.

Usage : uv run python -m a2a_team.scenarios.from_profiles
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from a2a_team.models import MemberProfile, Scenario, Task
from a2a_team.scenarios.generator import SCEN_DIR

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"


class ProfilesNotFilled(RuntimeError):
    pass


def load_profiles(profiles_dir: Path = DATA / "profiles", allow_templates: bool = False) -> list[MemberProfile]:
    members, missing = [], []
    for f in sorted(profiles_dir.glob("m*.yaml")):
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        if raw.pop("A_REMPLIR", False):
            missing.append(f.name)
        members.append(MemberProfile.model_validate(raw))
    if missing and not allow_templates:
        raise ProfilesNotFilled(f"profils encore à remplir : {', '.join(missing)}")
    return members


def build(allow_templates: bool = False, data_dir: Path = DATA) -> Scenario:
    members = load_profiles(data_dir / "profiles", allow_templates)
    raw = yaml.safe_load((data_dir / "project_tasks.yaml").read_text(encoding="utf-8"))
    tasks = [Task.model_validate(t) for t in raw["tasks"]]
    weeks = raw["weeks"]
    cap = sum(sum(m.capacity_hours.get(w, 0) for w in weeks) for m in members)
    r = sum(t.hours for t in tasks) / cap if cap else 99
    difficulty = "confortable" if r < 0.7 else "tendu" if r <= 1.15 else "impossible"
    return Scenario(id="notre_groupe", split="demo", seed=0, weeks=weeks, members=members, tasks=tasks,
                    meetings_needed=raw.get("meetings_needed", 1), events=[], difficulty=difficulty)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--allow-templates", action="store_true",
                   help="accepte des profils non remplis (essais techniques seulement, rien n'est écrit dans data/)")
    a = p.parse_args()
    s = build(a.allow_templates)
    if a.allow_templates:
        out = ROOT / "runs" / "notre_groupe_MODELES.json"
    else:
        out = SCEN_DIR / "demo" / "notre_groupe.json"
    s.save(out)
    print(f"{out} écrit ({len(s.members)} membres, {len(s.tasks)} tâches, difficulté {s.difficulty})")


if __name__ == "__main__":
    main()
