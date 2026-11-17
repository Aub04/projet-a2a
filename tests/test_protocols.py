import json

import pytest

from a2a_team.a2a_layer.trace import Tracer
from a2a_team.agents.member import MemberAgent
from a2a_team.architectures.runner import run
from a2a_team.llm import FakeLLM, LLMError
from a2a_team.models import Event, Task
from a2a_team.scenarios.generator import load_split


def agent(small_scenario, llm=None, mid="m1"):
    return MemberAgent(small_scenario.member(mid), small_scenario.weeks, llm or FakeLLM(), Tracer())


async def test_refus_sans_llm_si_aucune_option(small_scenario):
    llm = FakeLLM()
    a = agent(small_scenario, llm, "m2")   # m2 a dev_python 1
    t = small_scenario.task("T1")
    r = await a.handle({"type": "CFP", "sender": "m1", "content": {"task": t.model_dump(), "weeks": ["S47"]}})
    assert r["type"] == "REFUSAL" and r["content"]["hard"] is True
    assert llm.calls == 0


async def test_proposition_parmi_options_faisables(small_scenario):
    llm = FakeLLM()
    a = agent(small_scenario, llm)
    t = Task(id="X", title="x", skill="dev_python", min_level=2, hours=6, deadline="S49")
    r = await a.handle({"type": "CFP", "sender": "m2", "content": {"task": t.model_dump(),
                                                                    "weeks": ["S47", "S48", "S49"]}})
    assert r["type"] == "PROPOSAL" and r["content"]["week"] == "S47"     # 4 h seulement en S48 et S49
    assert llm.seen[0]["options"] == ["S47"]


async def test_llm_invalide_relances_puis_refus(small_scenario):
    llm = FakeLLM(overrides={"bid": lambda d, m: "pas du json"}, max_retries=2)
    a = agent(small_scenario, llm)
    t = small_scenario.task("T1")
    r = await a.handle({"type": "CFP", "sender": "m2", "content": {"task": t.model_dump(), "weeks": ["S47"]}})
    assert r["type"] == "REFUSAL" and llm.calls == 3


def test_llm_semaine_hors_options_rejetee():
    from a2a_team.agents.member import lit
    from pydantic import create_model
    llm = FakeLLM(overrides={"bid": lambda d, m: {"decision": "propose", "week": "S49", "justification": ""}},
                  max_retries=0)
    schema = create_model("B", decision=(str, ...), week=(lit(["S47"]) | None, None))
    with pytest.raises(LLMError):
        llm.decide("s", "u", schema, {"decision": "bid"})


async def test_award_reverifie_la_capacite(small_scenario):
    a = agent(small_scenario)
    t = small_scenario.task("T1")
    r = await a.handle({"type": "AWARD", "sender": "m2", "content": {"task": t.model_dump(), "week": "S47"}})
    assert r["type"] == "CONFIRM" and "T1" in a.commitments
    t2 = Task(id="T9", title="y", skill="dev_python", min_level=1, hours=5, deadline="S47")
    r = await a.handle({"type": "AWARD", "sender": "m2", "content": {"task": t2.model_dump(), "week": "S47"}})
    assert r["type"] == "DECLINE"     # 6 + 5 > 10


async def test_plan_proposal_objection_dure_sans_llm(small_scenario):
    llm = FakeLLM()
    a = agent(small_scenario, llm, "m2")
    t = small_scenario.task("T1")
    r = await a.handle({"type": "PLAN_PROPOSAL", "sender": "orchestrateur",
                        "content": {"assignments": [{"task": t.model_dump(), "week": "S47"}],
                                    "meetings": ["S47-mar-14"], "dependency_weeks": {}}})
    assert r["content"]["decision"] == "object" and r["content"]["hard"]
    assert {o["constraint"] for o in r["content"]["objections"]} == {"competence"}
    assert llm.calls == 0


def _small_events(small_scenario):
    small_scenario.events = [
        Event(trigger_round=1, type="absence", member_id="m2", weeks=["S48"]),
        Event(trigger_round=2, type="new_task", task=Task(id="U1", title="urgent", skill="dev_python",
                                                          min_level=2, hours=2, deadline="S49")),
    ]
    return small_scenario


@pytest.mark.parametrize("arch", ["p2p", "orchestrator"])
def test_architecture_de_bout_en_bout_memoire(arch, small_scenario, tmp_path):
    s = _small_events(small_scenario)
    r = run(arch, s, FakeLLM(), tmp_path, transport="memory")
    assert r.error is None
    assert r.report_initial.valid and r.report_initial.assigned_ratio == 1.0
    assert r.report.valid, r.report.violations
    assert "U1" in {a.task_id for a in r.plan.assignments}
    assert len(r.events) == 2 and all(e.valid_after for e in r.events)
    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    types = {json.loads(x)["type"] for x in lines}
    if arch == "p2p":
        assert {"CFP", "PROPOSAL", "AWARD", "CONFIRM", "MEETING_POLL", "MEETING_VOTE"} <= types
    else:
        assert {"INFO_REQUEST", "INFO_REPLY", "PLAN_PROPOSAL", "PLAN_FEEDBACK"} <= types
    assert (tmp_path / "llm_calls.jsonl").exists()


def test_orchestrateur_revise_apres_objection(small_scenario, tmp_path):
    """Le premier planning donne T1 à m2 (niveau insuffisant) : objection dure, puis révision correcte."""
    from a2a_team.agents.orchestrator import _fake_greedy
    state = {"n": 0}

    def plan(decision, meta):
        state["n"] += 1
        if state["n"] == 1:
            return {"assignments": [{"task_id": "T1", "member_id": "m2", "week": "S47"}],
                    "meetings": [], "unassigned": [], "comment": ""}
        return meta["fake_plan"]()

    r = run("orchestrator", small_scenario, FakeLLM(overrides={"orchestrator_plan": plan}), tmp_path,
            transport="memory")
    assert state["n"] == 2 and r.report.valid and r.report.assigned_ratio == 1.0


def test_orchestrateur_retire_les_objections_persistantes(small_scenario, tmp_path):
    bad = lambda d, m: {"assignments": [{"task_id": "T1", "member_id": "m2", "week": "S47"}],
                        "meetings": [], "unassigned": [], "comment": ""}
    r = run("orchestrator", small_scenario, FakeLLM(overrides={"orchestrator_plan": bad}), tmp_path,
            max_rounds=2, transport="memory")
    assert "T1" in r.plan.unassigned and r.plan.escalations and r.report.valid


def test_p2p_escalade_tache_impossible(small_scenario, tmp_path):
    small_scenario.tasks.append(Task(id="T9", title="énorme", skill="dev_python", min_level=3, hours=40,
                                     deadline="S49"))
    r = run("p2p", small_scenario, FakeLLM(), tmp_path, transport="memory")
    assert "T9" in r.plan.unassigned and any("T9" in e for e in r.plan.escalations)


def test_p2p_relance_si_refus_souples(small_scenario, tmp_path):
    """Au premier tour tout le monde refuse par préférence ; la relance permet l'attribution."""
    def bid(d, m):
        if m["relance"] == 0:
            return {"decision": "refuse", "week": None, "justification": "pas envie"}
        return {"decision": "propose", "week": m["options"][0], "justification": "ok"}
    r = run("p2p", small_scenario, FakeLLM(overrides={"bid": bid}), tmp_path, transport="memory")
    assert r.report.assigned_ratio == 1.0


@pytest.mark.parametrize("arch", ["p2p", "orchestrator", "baseline"])
def test_scenarios_dev_sans_erreur(arch, tmp_path):
    s = load_split("dev")[1]
    r = run(arch, s, None if arch == "baseline" else FakeLLM(), tmp_path, transport="memory")
    assert r.error is None
    assert r.report.valid, r.report.violations   # le code empêche toute violation dure côté agents
