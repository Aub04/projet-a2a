"""Campagne de mesures, aides de révision de l'orchestrateur, vote par refus, scénario de démo tendu."""

import json

from a2a_team.a2a_layer.trace import Tracer
from a2a_team.agents.member import MemberAgent
from a2a_team.agents.orchestrator import OrchestratorAgent
from a2a_team.baseline import solve
from a2a_team.eval import run as camp_run
from a2a_team.eval.metrics import jaccard, stability
from a2a_team.llm import FakeLLM
from a2a_team.models import Assignment, Plan
from a2a_team.scenarios.generator import load_split


def test_plan_runs_repetition_par_repetition_et_baseline_une_fois():
    scen = load_split("dev")[:4]
    plan = camp_run.plan_runs(scen, ["p2p", "orchestrator", "baseline"], 3)
    assert len(plan) == 4 + 4 * 2 * 3
    reps = [k for _, a, k in plan if a != "baseline"]
    assert reps == sorted(reps)                      # toute la répétition 1 avant la 2
    assert all(k == 0 for _, a, k in plan if a == "baseline")


def test_interleave_alterne_les_difficultes():
    order = [s.difficulty for s in camp_run.interleave(load_split("dev"))]
    assert order[:3] == ["tendu", "confortable", "impossible"]


def test_jaccard_et_stabilite():
    assert jaccard(["a", "b"], ["a", "b"]) == 1.0
    assert jaccard(["a", "b"], ["a", "c"]) == 1 / 3
    rows = [{"scenario_id": "s", "arch": "p2p", "assignments": x} for x in (["a"], ["a"], ["b"])]
    assert abs(stability(rows)[("s", "p2p")] - 1 / 3) < 1e-9


def test_erreur_infra_detectee():
    assert camp_run.is_infra_error("ConnectError: [WinError 10061] connexion refusée")
    assert not camp_run.is_infra_error("LLMError: JSON invalide après 3 tentatives")
    assert not camp_run.is_infra_error(None)


def test_campagne_fake_reprise_et_resume(tmp_path):
    args = ["--split", "dev", "--scenarios", "chantier_dev_3000", "chantier_dev_3002", "--runs", "2", "--fake-llm",
            "--transport", "memory", "--results-dir", str(tmp_path), "--campagne", "c"]
    assert camp_run.main(args) == 0
    rows = [json.loads(x) for x in (tmp_path / "c" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 * 2 + 2 * 2 * 2       # baseline et oracle une fois, p2p et orchestrateur 2 fois
    assert len({r["key"] for r in rows}) == len(rows)
    assert (tmp_path / "c" / "summary.csv").exists() and (tmp_path / "c" / "resume.md").exists()
    assert (tmp_path / "c" / "figures" / "complete.png").exists()
    # reprise : rien n'est refait
    assert camp_run.main(args) == 0
    assert len((tmp_path / "c" / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == len(rows)
    meta = json.loads((tmp_path / "c" / "meta.json").read_text(encoding="utf-8"))
    assert len(meta["sessions"]) == 2 and meta["sessions"][1]["already_done"] == len(rows)


def test_graine_differente_par_repetition():
    llm = FakeLLM(seed=42)
    llm.reset(repeat=0)
    s0 = llm.run_seed
    llm.reset(repeat=2)
    assert (s0, llm.run_seed) == (42, 44)


async def test_vote_par_refus(small_scenario):
    llm = FakeLLM(overrides={"meeting_vote": lambda d, m: {"refused": [m["options"][0]], "reason": "profil"}})
    a = MemberAgent(small_scenario.members[0], small_scenario.weeks, llm, Tracer())
    slots = a.available_slots(a.profile.meeting_slots)
    r = await a.handle({"type": "MEETING_POLL", "sender": "m2", "content": {"slots": slots, "index": 1}})
    assert r["content"]["accepted"] == slots[1:]


def test_aides_orchestrateur_charge_et_options(small_scenario):
    s = small_scenario
    o = OrchestratorAgent(FakeLLM(), Tracer(), None, [m.id for m in s.members], s.weeks)
    o.tasks = {t.id: t for t in s.tasks}
    o.infos = {m.id: {"name": m.name, "skills": m.skills, "capacity_hours": dict(m.capacity_hours),
                      "max_tasks_per_week": m.max_tasks_per_week} for m in s.members}
    t = s.tasks[0]
    m = s.members[0]
    w = s.weeks[0]
    plan = Plan(assignments=[Assignment(task_id=x.id, member_id=m.id, week=w) for x in s.tasks])
    txt = o.hints_text(plan, {m.id: [{"item": t.id, "constraint": "capacité", "reason": "trop d'heures"}]})
    assert "calculée par le programme" in txt and f"- {t.id} :" in txt
    for mid, wk, _ in o.options_for(plan, t.id):   # chaque option proposée respecte compétence et échéance
        assert o.infos[mid]["skills"].get(t.skill, 0) >= t.min_level
        assert s.weeks.index(wk) <= s.weeks.index(t.deadline)


def test_chantier_infos_privees():
    """Le coordinateur central ne voit que les déclarations ; les agents ne transmettent que les déclarations."""
    from a2a_team.models import public_view
    s = next(x for x in load_split("dev") if any(m.capacity_hours != m.declared_capacity_hours for m in x.members))
    pub = public_view(s)
    for m, p in zip(s.members, pub.members):
        assert p.capacity_hours == m.declared_capacity_hours and p.hard_limits == []
        assert all(m.capacity_hours[w] <= m.declared_capacity_hours[w] for w in s.weeks)


async def test_info_reply_ne_revele_que_les_declarations():
    s = load_split("dev")[0]
    m = next(x for x in s.members if x.hard_limits)
    a = MemberAgent(m, s.weeks, FakeLLM(), Tracer())
    r = await a.handle({"type": "INFO_REQUEST", "sender": "orchestrateur", "content": {}})
    assert r["content"]["capacity_hours"] == m.declared_capacity_hours
    assert r["content"]["hard_limits"] == []


def test_agents_respectent_les_vraies_heures_et_pas_le_central(tmp_path):
    from a2a_team.architectures.runner import run
    s = load_split("dev")[0]
    central = run("baseline", s, None, tmp_path / "b", transport="memory")
    oracle = run("oracle", s, None, tmp_path / "o", transport="memory")
    p2p = run("p2p", s, FakeLLM(), tmp_path / "p", transport="memory")
    assert not central.report.valid and oracle.report.valid
    assert p2p.report.valid      # chaque agent connaît ses vraies heures : aucune violation
