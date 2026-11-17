from a2a_team.baseline import common_slots, solve, task_order
from a2a_team.models import Event, Task
from a2a_team.validator import validate


def test_ordre_respecte_dependances(small_scenario):
    small_scenario.tasks.insert(0, Task(id="T0", title="x", skill="redaction", min_level=1, hours=1,
                                        deadline="S47", depends_on=["T2"]))
    order = [t.id for t in task_order(small_scenario)]
    assert order.index("T1") < order.index("T2") < order.index("T0")


def test_baseline_valide_et_complete(small_scenario):
    plan = solve(small_scenario)
    r = validate(small_scenario, plan)
    assert r.valid, r.violations
    assert r.assigned_ratio == 1.0 and r.meetings_ratio == 1.0
    assert plan.by_task()["T1"].member_id == "m1"
    assert plan.by_task()["T2"].member_id == "m2"   # seule à avoir le niveau en rédaction


def test_baseline_escalade_si_impossible(small_scenario):
    small_scenario.tasks.append(Task(id="T4", title="énorme", skill="dev_python", min_level=3, hours=50,
                                     deadline="S49"))
    plan = solve(small_scenario)
    assert "T4" in plan.unassigned and plan.escalations
    assert validate(small_scenario, plan).valid


def test_baseline_tient_compte_des_imprevus(small_scenario):
    small_scenario.events = [Event(trigger_round=1, type="absence", member_id="m1", weeks=["S47"])]
    plan = solve(small_scenario)
    assert "T1" in plan.unassigned       # seul m1 sait faire T1, absent en S47
    assert validate(small_scenario, plan).valid


def test_creneaux_communs(small_scenario):
    small_scenario.members[1].meeting_slots = ["S48-mer-20"]
    assert common_slots(small_scenario) == ["S48-mer-20"]
