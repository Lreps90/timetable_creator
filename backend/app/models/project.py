from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .entities import (
    Constraint,
    CurriculumRow,
    FixedEvent,
    LessonPattern,
    OptionBlock,
    Room,
    SchoolStructure,
    SolveResult,
    SolveStatus,
    Subject,
    SubjectRoomRequirement,
    Teacher,
    TeacherSubject,
    TeachingGroup,
    ValidationIssue,
)


class ProjectData(BaseModel):
    project_id: str
    source_scenario: str = "uploaded"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    files_detected: list[str] = Field(default_factory=list)
    school: SchoolStructure = Field(default_factory=SchoolStructure)
    teachers: dict[str, Teacher] = Field(default_factory=dict)
    teacher_subjects: list[TeacherSubject] = Field(default_factory=list)
    subjects: dict[str, Subject] = Field(default_factory=dict)
    curriculum: list[CurriculumRow] = Field(default_factory=list)
    teaching_groups: dict[str, TeachingGroup] = Field(default_factory=dict)
    rooms: dict[str, Room] = Field(default_factory=dict)
    subject_room_requirements: dict[str, SubjectRoomRequirement] = Field(default_factory=dict)
    option_blocks: list[OptionBlock] = Field(default_factory=list)
    fixed_events: list[FixedEvent] = Field(default_factory=list)
    lesson_patterns: list[LessonPattern] = Field(default_factory=list)
    constraints: dict[str, Constraint] = Field(default_factory=dict)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    solve_status: SolveStatus = Field(default_factory=SolveStatus)
    solve_result: SolveResult | None = None

    @property
    def fatal_validation_issues(self) -> list[ValidationIssue]:
        return [issue for issue in self.validation_issues if issue.severity == "fatal"]

