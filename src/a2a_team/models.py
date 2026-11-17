"""Modèle de données commun (section 2 de docs/SPEC.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

SKILLS = ["dev_python", "ia_llm", "front_demo", "redaction", "presentation", "test_eval", "devops"]
DAYS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


class MemberProfile(BaseModel):
    id: str
    name: str
    skills: dict[str, int]
    capacity_hours: dict[str, float]
    max_tasks_per_week: int | None = None
    preferences: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    hard_limits: list[str] = Field(default_factory=list)
    meeting_slots: list[str] = Field(default_factory=list)

    def level(self, skill: str) -> int:
        return int(self.skills.get(skill, 0))


class Task(BaseModel):
    id: str
    title: str
    skill: str
    min_level: int
    hours: float
    deadline: str
    depends_on: list[str] = Field(default_factory=list)


class Event(BaseModel):
    trigger_round: int
    type: Literal["absence", "new_task", "deadline_change"]
    member_id: str | None = None
    weeks: list[str] = Field(default_factory=list)
    task: Task | None = None
    task_id: str | None = None
    new_deadline: str | None = None


class Scenario(BaseModel):
    id: str
    split: Literal["dev", "test", "demo"]
    seed: int
    weeks: list[str]
    members: list[MemberProfile]
    tasks: list[Task]
    meetings_needed: int
    events: list[Event] = Field(default_factory=list)
    difficulty: Literal["confortable", "tendu", "impossible"]

    def member(self, member_id: str) -> MemberProfile:
        return next(m for m in self.members if m.id == member_id)

    def task(self, task_id: str) -> Task:
        return next(t for t in self.tasks if t.id == task_id)

    def week_index(self, week: str) -> int:
        return self.weeks.index(week)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


class Assignment(BaseModel):
    task_id: str
    member_id: str
    week: str


class Plan(BaseModel):
    assignments: list[Assignment] = Field(default_factory=list)
    meetings: list[str] = Field(default_factory=list)
    unassigned: list[str] = Field(default_factory=list)
    escalations: list[str] = Field(default_factory=list)

    def by_task(self) -> dict[str, Assignment]:
        return {a.task_id: a for a in self.assignments}


# --- Créneaux de réunion ----------------------------------------------------------------------

def parse_slot(slot: str) -> tuple[str, str, int]:
    """'S47-mar-14' -> ('S47', 'mar', 14). Lève ValueError si le format est faux."""
    week, day, hour = slot.split("-")
    if day not in DAYS:
        raise ValueError(f"jour inconnu dans {slot!r}")
    return week, day, int(hour)


def slot_sort_key(slot: str, weeks: list[str]) -> tuple[int, int, int]:
    week, day, hour = parse_slot(slot)
    return (weeks.index(week) if week in weeks else 99, DAYS.index(day), hour)


# --- Application des imprévus -----------------------------------------------------------------

def apply_event(scenario: Scenario, event: Event) -> Scenario:
    """Retourne une copie du scénario où l'événement a pris effet."""
    s = scenario.model_copy(deep=True)
    if event.type == "absence":
        m = s.member(event.member_id)
        for w in event.weeks:
            m.capacity_hours[w] = 0.0
    elif event.type == "new_task":
        if event.task and all(t.id != event.task.id for t in s.tasks):
            s.tasks.append(event.task.model_copy(deep=True))
    elif event.type == "deadline_change":
        s.task(event.task_id).deadline = event.new_deadline
    return s


def apply_events(scenario: Scenario, events: list[Event] | None = None) -> Scenario:
    events = scenario.events if events is None else events
    s = scenario
    for e in sorted(events, key=lambda e: e.trigger_round):
        s = apply_event(s, e)
    return s


def present_members(scenario: Scenario, week: str) -> list[MemberProfile]:
    """Membres présents une semaine donnée (capacité > 0)."""
    return [m for m in scenario.members if m.capacity_hours.get(week, 0) > 0]
