from a2a_team.models import Assignment, Event, Plan
from a2a_team.validator import validate


def good_plan():
    return Plan(assignments=[
        Assignment(task_id="T1", member_id="m1", week="S47"),
        Assignment(task_id="T2", member_id="m2", week="S47"),
        Assignment(task_id="T3", member_id="m1", week="S48"),
    ], meetings=["S47-mar-14"])


def test_plan_valide(small_scenario):
    r = validate(small_scenario, good_plan())
    assert r.valid, r.violations
    assert r.assigned_ratio == 1.0
    assert r.meetings_ratio == 1.0
    # m1 : 2 tâches dev préférées ; m2 : rédaction préférée
    assert r.preference_ratio == 1.0
    assert r.silent_failure is False


def _types(r):
    return {v.type for v in r.violations}


def test_capacite(small_scenario):
    p = good_plan()
    p.assignments[2].week = "S47"   # m1 : 6 + 4 > 10 ? non, 10 = 10 ; on force avec T2
    p.assignments[1] = Assignment(task_id="T2", member_id="m1", week="S47")
    r = validate(small_scenario, p)
    assert "capacite" in _types(r)


def test_competence(small_scenario):
    p = good_plan()
    p.assignments[0].member_id = "m2"
    assert "competence" in _types(validate(small_scenario, p))


def test_echeance(small_scenario):
    p = good_plan()
    p.assignments[1].week = "S49"
    assert _types(validate(small_scenario, p)) == {"echeance"}


def test_dependance(small_scenario):
    p = good_plan()
    p.assignments[0].week = "S47"
    p.assignments[2] = Assignment(task_id="T3", member_id="m1", week="S47")
    p.assignments[0] = Assignment(task_id="T1", member_id="m1", week="S47")
    p.assignments[1] = Assignment(task_id="T2", member_id="m2", week="S47")
    # T1 déplacée en S48 : T2 (S47) devient avant sa dépendance
    p.assignments[0].week = "S48"
    p.assignments[2].week = "S48"
    r = validate(small_scenario, p)
    assert "dependance" in _types(r)


def test_max_taches(small_scenario):
    small_scenario.members[0].max_tasks_per_week = 1
    p = good_plan()
    p.assignments[2].week = "S47"
    p.assignments[2].member_id = "m1"
    small_scenario.members[0].capacity_hours["S47"] = 20
    assert "max_taches" in _types(validate(small_scenario, p))


def test_reunion(small_scenario):
    small_scenario.members[1].meeting_slots = ["S48-mer-20"]
    assert _types(validate(small_scenario, good_plan())) == {"reunion"}


def test_reunion_membre_absent_ignore(small_scenario):
    small_scenario.members[1].meeting_slots = ["S48-mer-20"]
    small_scenario.members[1].capacity_hours["S47"] = 0
    p = good_plan()
    p.assignments[1].week = "S48"
    assert validate(small_scenario, p).valid


def test_format(small_scenario):
    p = good_plan()
    p.assignments.append(Assignment(task_id="T9", member_id="m1", week="S47"))
    p.assignments.append(Assignment(task_id="T1", member_id="m1", week="S47"))
    r = validate(small_scenario, p)
    assert r.violation_counts["format"] == 2


def test_evenement_absence_applique(small_scenario):
    small_scenario.events = [Event(trigger_round=1, type="absence", member_id="m1", weeks=["S48"])]
    r = validate(small_scenario, good_plan())
    assert "capacite" in _types(r)


def test_tache_manquante_sans_escalade(small_scenario):
    p = good_plan()
    p.assignments.pop()
    r = validate(small_scenario, p)
    assert r.valid and r.assigned_ratio < 1 and r.silent_failure


def test_escalade_scenario_impossible(small_scenario):
    small_scenario.difficulty = "impossible"
    p = good_plan()
    assert validate(small_scenario, p).escalation_correct is False
    p.escalations.append("manque de temps")
    assert validate(small_scenario, p).escalation_correct is True
