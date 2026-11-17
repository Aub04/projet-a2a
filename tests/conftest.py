import pytest

from a2a_team.models import MemberProfile, Scenario, Task


def make_member(mid, **kw):
    base = dict(
        id=mid, name=mid.upper(),
        skills={"dev_python": 3, "redaction": 2},
        capacity_hours={"S47": 10, "S48": 4, "S49": 4},
        max_tasks_per_week=None, preferences=["dev_python"], dislikes=["redaction"],
        hard_limits=[], meeting_slots=["S47-mar-14", "S48-mer-20"],
    )
    base.update(kw)
    return MemberProfile(**base)


@pytest.fixture
def small_scenario():
    members = [
        make_member("m1"),
        make_member("m2", skills={"dev_python": 1, "redaction": 4}, preferences=["redaction"], dislikes=[]),
    ]
    tasks = [
        Task(id="T1", title="code", skill="dev_python", min_level=3, hours=6, deadline="S47"),
        Task(id="T2", title="doc", skill="redaction", min_level=2, hours=3, deadline="S48", depends_on=["T1"]),
        Task(id="T3", title="suite", skill="dev_python", min_level=2, hours=4, deadline="S49", depends_on=["T1"]),
    ]
    return Scenario(id="petit", split="dev", seed=0, weeks=["S47", "S48", "S49"], members=members,
                    tasks=tasks, meetings_needed=1, difficulty="confortable")
