from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Severity = Literal["fatal", "warning", "info"]
SolveMode = Literal["quick", "balanced", "deep"]
SolveState = Literal["not_started", "queued", "running", "feasible", "infeasible", "failed"]


class ValidationIssue(BaseModel):
    file: str | None = None
    row: int | None = None
    field: str | None = None
    severity: Severity
    category: str
    message: str


class SchoolStructure(BaseModel):
    days_per_week: int = 5
    periods_per_day: int = 5
    cycle_weeks: int = 1
    days: list[str] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    periods: list[str] = Field(default_factory=lambda: ["P1", "P2", "P3", "P4", "P5"])


class Teacher(BaseModel):
    teacher_id: str
    name: str
    role: str = ""
    department: str = ""
    working_days: list[str] = Field(default_factory=list)
    max_lessons_per_week: int = 25
    max_lessons_per_day: int = 5
    unavailable_periods: list[str] = Field(default_factory=list)
    notes: str = ""


class TeacherSubject(BaseModel):
    teacher_id: str
    subject: str
    priority: int = 1
    max_lessons_in_subject: int | None = None
    can_teach_years: list[int] = Field(default_factory=list)


class Subject(BaseModel):
    subject: str
    department: str = ""
    is_core: bool = False
    default_room_type: str = "General"


class CurriculumRow(BaseModel):
    year_group: int
    subject: str
    lessons_per_week: int
    notes: str = ""


class TeachingGroup(BaseModel):
    group_id: str
    year_group: int
    subject: str
    lessons_per_week: int
    class_size: int
    group_type: str = ""
    option_block: str = ""
    allowed_teachers: list[str] = Field(default_factory=list)
    preferred_teacher: str = ""
    notes: str = ""


class Room(BaseModel):
    room_id: str
    room_name: str
    room_type: str = "General"
    capacity: int = 0
    has_computers: bool = False
    computer_count: int = 0
    available_days: list[str] = Field(default_factory=list)
    unavailable_periods: list[str] = Field(default_factory=list)
    notes: str = ""


class SubjectRoomRequirement(BaseModel):
    subject: str
    required_room_type: str
    allow_general_room: bool = True
    notes: str = ""


class OptionBlock(BaseModel):
    year_group: int
    block: str
    group_id: str
    subject: str
    simultaneous_required: bool = True
    notes: str = ""


class FixedEvent(BaseModel):
    event_id: str
    event_name: str
    event_type: str
    applies_to: str
    day: str
    period: str
    duration_periods: int = 1
    required_teacher_ids: list[str] = Field(default_factory=list)
    required_room_ids: list[str] = Field(default_factory=list)
    source_row: int | None = Field(default=None, exclude=True)
    notes: str = ""


class LessonPattern(BaseModel):
    subject: str
    year_group: int
    lessons_per_week: int
    allowed_patterns: list[str] = Field(default_factory=list)
    double_lessons_allowed: bool = False
    max_same_subject_per_day: int = 1
    notes: str = ""


class Constraint(BaseModel):
    constraint_name: str
    constraint_type: Literal["HARD", "SOFT"]
    weight: int = 1
    enabled: bool = True
    source_row: int | None = Field(default=None, exclude=True)
    description: str = ""


class LessonAssignment(BaseModel):
    lesson_id: str
    day: str
    period: str
    group_id: str
    year_group: int
    subject: str
    teacher_id: str
    teacher_name: str
    room_id: str
    room_name: str
    source_scenario: str = ""
    option_block: str = ""


class ConflictIssue(BaseModel):
    lesson_id: str | None = None
    group_id: str | None = None
    subject: str | None = None
    severity: Severity = "warning"
    category: str
    message: str
    reasons: list[str] = Field(default_factory=list)


class SolveSettings(BaseModel):
    mode: SolveMode = "balanced"
    time_limit_seconds: int = Field(default=30, ge=1, le=600)
    soft_constraints_enabled: bool = True


class SolveStatus(BaseModel):
    status: SolveState = "not_started"
    progress: float = 0.0
    score: int | None = None
    messages: list[str] = Field(default_factory=list)


class SolveResult(BaseModel):
    status: SolveState
    assignments: list[LessonAssignment] = Field(default_factory=list)
    unscheduled_lessons: list[ConflictIssue] = Field(default_factory=list)
    broken_hard_constraints: list[ConflictIssue] = Field(default_factory=list)
    soft_penalties: list[ConflictIssue] = Field(default_factory=list)
    score: int = 0
    total_penalty: int = 0
    constraint_report: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
