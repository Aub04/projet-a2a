import pytest
import yaml

from a2a_team.scenarios import from_profiles
from a2a_team.scenarios.generator import (RATIO_BANDS, SCEN_DIR, difficulty_for, generate, load_split, ratio,
                                          reference_ok, split_hash)
from a2a_team.validator import validate


@pytest.mark.parametrize("difficulty", ["confortable", "tendu", "impossible"])
def test_generation_deterministe_et_dans_la_bande(difficulty):
    s1, ref1 = generate(42, "dev", difficulty)
    s2, _ = generate(42, "dev", difficulty)
    assert s1 == s2
    lo, hi = RATIO_BANDS[difficulty]
    assert lo <= ratio(s1) <= hi
    assert 3 <= len(s1.members) <= 7 and len(s1.tasks) >= 8
    if difficulty != "impossible":
        assert reference_ok(s1, ref1)


def test_repartition_des_difficultes():
    assert [difficulty_for(i) for i in range(5)] == ["confortable", "confortable", "tendu", "tendu", "impossible"]


def test_split_test_fige():
    """Le split test ne doit jamais changer (hash noté dans docs/DECISIONS.md)."""
    if not (SCEN_DIR / "test").exists():
        pytest.skip("split test non généré")
    assert split_hash("test") == "0a8369c6a2996509cb6a8dc8f17b3d0607bc089d9bada209d324354c3ee89a78"
    assert len(load_split("test")) == 50


def test_solutions_de_reference_valides():
    import json
    from a2a_team.models import Plan
    for s in load_split("dev"):
        if s.difficulty == "impossible":
            continue
        ref = Plan.model_validate(json.loads((SCEN_DIR / "dev" / "ref" / f"{s.id}.json").read_text("utf-8")))
        assert reference_ok(s, ref)
        assert validate(s, ref, apply_scenario_events=False).valid


def _write_profiles(tmp_path, filled):
    (tmp_path / "profiles").mkdir()
    for i in range(1, 3):
        prof = {"A_REMPLIR": not filled, "id": f"m{i}", "name": f"P{i}", "skills": {"dev_python": 3},
                "capacity_hours": {"S47": 10, "S48": 5, "S49": 5}, "max_tasks_per_week": None,
                "preferences": [], "dislikes": [], "hard_limits": [], "meeting_slots": ["S47-mar-14"]}
        (tmp_path / "profiles" / f"m{i}.yaml").write_text(yaml.safe_dump(prof), encoding="utf-8")
    tasks = {"weeks": ["S47", "S48", "S49"], "meetings_needed": 1, "tasks": [
        {"id": "T01", "title": "x", "skill": "dev_python", "min_level": 2, "hours": 5, "deadline": "S47",
         "depends_on": []}]}
    (tmp_path / "project_tasks.yaml").write_text(yaml.safe_dump(tasks), encoding="utf-8")


def test_from_profiles_refuse_les_modeles(tmp_path):
    _write_profiles(tmp_path, filled=False)
    with pytest.raises(from_profiles.ProfilesNotFilled):
        from_profiles.build(data_dir=tmp_path)
    s = from_profiles.build(allow_templates=True, data_dir=tmp_path)
    assert s.id == "notre_groupe" and len(s.members) == 2


def test_from_profiles_ok(tmp_path):
    _write_profiles(tmp_path, filled=True)
    s = from_profiles.build(data_dir=tmp_path)
    assert s.difficulty == "confortable" and s.split == "demo"
