from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError

from backend.app.models.entities import (
    Constraint,
    CurriculumRow,
    FixedEvent,
    LessonPattern,
    OptionBlock,
    Room,
    SchoolStructure,
    Subject,
    SubjectRoomRequirement,
    Teacher,
    TeacherSubject,
    TeachingGroup,
    ValidationIssue,
)
from backend.app.models.project import ProjectData
from backend.app.validators.project_validator import validate_project


EXPECTED_FILES = [
    "school_structure.csv",
    "teachers.csv",
    "teacher_subjects.csv",
    "subjects.csv",
    "curriculum.csv",
    "teaching_groups.csv",
    "rooms.csv",
    "subject_room_requirements.csv",
    "option_blocks.csv",
    "fixed_events.csv",
    "lesson_patterns.csv",
    "constraints.csv",
]


REQUIRED_COLUMNS: dict[str, list[str]] = {
    "school_structure.csv": ["key", "value"],
    "teachers.csv": [
        "teacher_id",
        "name",
        "role",
        "department",
        "working_days",
        "max_lessons_per_week",
        "max_lessons_per_day",
        "unavailable_periods",
        "notes",
    ],
    "teacher_subjects.csv": [
        "teacher_id",
        "subject",
        "priority",
        "max_lessons_in_subject",
        "can_teach_years",
    ],
    "subjects.csv": ["subject", "department", "is_core", "default_room_type"],
    "curriculum.csv": ["year_group", "subject", "lessons_per_week", "notes"],
    "teaching_groups.csv": [
        "group_id",
        "year_group",
        "subject",
        "lessons_per_week",
        "class_size",
        "group_type",
        "option_block",
        "allowed_teachers",
        "preferred_teacher",
        "notes",
    ],
    "rooms.csv": [
        "room_id",
        "room_name",
        "room_type",
        "capacity",
        "available_days",
        "unavailable_periods",
        "notes",
    ],
    "subject_room_requirements.csv": [
        "subject",
        "required_room_type",
        "allow_general_room",
        "notes",
    ],
    "option_blocks.csv": [
        "year_group",
        "block",
        "group_id",
        "subject",
        "simultaneous_required",
        "notes",
    ],
    "fixed_events.csv": [
        "event_id",
        "event_name",
        "event_type",
        "applies_to",
        "day",
        "period",
        "duration_periods",
        "required_teacher_ids",
        "required_room_ids",
        "notes",
    ],
    "lesson_patterns.csv": [
        "subject",
        "year_group",
        "lessons_per_week",
        "allowed_patterns",
        "double_lessons_allowed",
        "max_same_subject_per_day",
        "notes",
    ],
    "constraints.csv": [
        "constraint_name",
        "constraint_type",
        "weight",
        "enabled",
        "description",
    ],
}


@dataclass(frozen=True)
class _InvalidPrimitive:
    value: str
    expected_type: str


def load_project_from_folder(folder: str | Path, project_id: str | None = None) -> ProjectData:
    root = Path(folder)
    raw_files: dict[str, bytes] = {}
    for file_path in root.iterdir():
        if file_path.is_file() and file_path.name in EXPECTED_FILES:
            raw_files[file_path.name] = file_path.read_bytes()
    return load_project_from_files(
        raw_files,
        project_id=project_id or str(uuid4()),
        source_scenario=root.name,
    )


def load_project_from_zip_bytes(zip_bytes: bytes, project_id: str | None = None, source_scenario: str = "uploaded_zip") -> ProjectData:
    raw_files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for name in archive.namelist():
            basename = Path(name).name
            if basename in EXPECTED_FILES and not name.endswith("/"):
                raw_files[basename] = archive.read(name)
    return load_project_from_files(
        raw_files,
        project_id=project_id or str(uuid4()),
        source_scenario=source_scenario,
    )


def load_project_from_files(
    raw_files: dict[str, bytes],
    project_id: str | None = None,
    source_scenario: str = "uploaded",
) -> ProjectData:
    project = ProjectData(
        project_id=project_id or str(uuid4()),
        source_scenario=source_scenario,
        files_detected=sorted(raw_files.keys()),
    )
    rows_by_file: dict[str, list[dict[str, str]]] = {}

    for expected_file in EXPECTED_FILES:
        content = raw_files.get(expected_file)
        if content is None:
            project.validation_issues.append(
                ValidationIssue(
                    file=expected_file,
                    severity="fatal",
                    category="missing_file",
                    message=f"Required file {expected_file} was not provided.",
                )
            )
            continue
        rows_by_file[expected_file] = _read_csv(expected_file, content, project.validation_issues)

    _load_school_structure(project, rows_by_file.get("school_structure.csv", []))
    _load_teachers(project, rows_by_file.get("teachers.csv", []))
    _load_teacher_subjects(project, rows_by_file.get("teacher_subjects.csv", []))
    _load_subjects(project, rows_by_file.get("subjects.csv", []))
    _load_curriculum(project, rows_by_file.get("curriculum.csv", []))
    _load_teaching_groups(project, rows_by_file.get("teaching_groups.csv", []))
    _load_rooms(project, rows_by_file.get("rooms.csv", []))
    _load_subject_room_requirements(project, rows_by_file.get("subject_room_requirements.csv", []))
    _load_option_blocks(project, rows_by_file.get("option_blocks.csv", []))
    _load_fixed_events(project, rows_by_file.get("fixed_events.csv", []))
    _load_lesson_patterns(project, rows_by_file.get("lesson_patterns.csv", []))
    _load_constraints(project, rows_by_file.get("constraints.csv", []))

    project.validation_issues.extend(validate_project(project))
    return project


def _read_csv(filename: str, content: bytes, issues: list[ValidationIssue]) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        issues.append(
            ValidationIssue(
                file=filename,
                severity="fatal",
                category="csv_decode",
                message=f"Could not decode {filename} as UTF-8: {exc}.",
            )
        )
        return []

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames or []
    missing_columns = [column for column in REQUIRED_COLUMNS[filename] if column not in fieldnames]
    for column in missing_columns:
        issues.append(
            ValidationIssue(
                file=filename,
                field=column,
                severity="fatal",
                category="missing_column",
                message=f"Missing required column {column} in {filename}.",
            )
        )

    rows: list[dict[str, str]] = []
    try:
        for index, row in enumerate(reader, start=2):
            clean_row = {key: (value or "").strip() for key, value in row.items() if key is not None}
            clean_row["__row__"] = str(index)
            rows.append(clean_row)
    except csv.Error as exc:
        issues.append(
            ValidationIssue(
                file=filename,
                severity="fatal",
                category="csv_parse",
                message=f"Could not parse {filename}: {exc}.",
            )
        )
    return rows


def _load_school_structure(project: ProjectData, rows: list[dict[str, str]]) -> None:
    values = {row.get("key", "").strip(): row.get("value", "").strip() for row in rows if row.get("key")}
    rows_by_key = {row.get("key", "").strip(): row for row in rows if row.get("key")}
    days = _split_pipe(values.get("days")) or ["Mon", "Tue", "Wed", "Thu", "Fri"]
    days_per_week = _int(values.get("days_per_week"), len(days))
    periods_per_day = _int(values.get("periods_per_day"), 5)
    cycle_weeks = _int(values.get("cycle_weeks"), 1)
    primitive_values = {
        "days_per_week": days_per_week,
        "periods_per_day": periods_per_day,
        "cycle_weeks": cycle_weeks,
    }
    for field, invalid in _invalid_primitives(primitive_values):
        _append_invalid_primitive_issue(project, "school_structure.csv", rows_by_key.get(field, {}), field, invalid)

    safe_periods_per_day = periods_per_day if isinstance(periods_per_day, int) else 5
    periods = _split_pipe(values.get("periods")) or [f"P{i}" for i in range(1, safe_periods_per_day + 1)]
    project.school = SchoolStructure(
        days_per_week=days_per_week if isinstance(days_per_week, int) else len(days),
        periods_per_day=periods_per_day if isinstance(periods_per_day, int) else len(periods),
        cycle_weeks=cycle_weeks if isinstance(cycle_weeks, int) else 1,
        days=days,
        periods=periods,
    )


def _load_teachers(project: ProjectData, rows: list[dict[str, str]]) -> None:
    seen_keys: dict[str, int] = {}
    for row in rows:
        _add_model(
            project,
            "teachers.csv",
            row,
            Teacher,
            {
                "teacher_id": row.get("teacher_id", ""),
                "name": row.get("name", ""),
                "role": row.get("role", ""),
                "department": row.get("department", ""),
                "working_days": _split_pipe(row.get("working_days")),
                "max_lessons_per_week": _int(row.get("max_lessons_per_week"), 25),
                "max_lessons_per_day": _int(row.get("max_lessons_per_day"), 5),
                "unavailable_periods": _split_pipe(row.get("unavailable_periods")),
                "notes": row.get("notes", ""),
            },
            lambda model: project.teachers.__setitem__(model.teacher_id, model),
            key_getter=lambda model: model.teacher_id,
            seen_keys=seen_keys,
            key_field="teacher_id",
        )


def _load_teacher_subjects(project: ProjectData, rows: list[dict[str, str]]) -> None:
    for row in rows:
        _add_model(
            project,
            "teacher_subjects.csv",
            row,
            TeacherSubject,
            {
                "teacher_id": row.get("teacher_id", ""),
                "subject": row.get("subject", ""),
                "priority": _int(row.get("priority"), 1),
                "max_lessons_in_subject": _optional_int(row.get("max_lessons_in_subject")),
                "can_teach_years": [_int(value, 0) for value in _split_pipe(row.get("can_teach_years")) if value],
            },
            project.teacher_subjects.append,
        )


def _load_subjects(project: ProjectData, rows: list[dict[str, str]]) -> None:
    seen_keys: dict[str, int] = {}
    for row in rows:
        _add_model(
            project,
            "subjects.csv",
            row,
            Subject,
            {
                "subject": row.get("subject", ""),
                "department": row.get("department", ""),
                "is_core": _bool(row.get("is_core")),
                "default_room_type": row.get("default_room_type", "General") or "General",
            },
            lambda model: project.subjects.__setitem__(model.subject, model),
            key_getter=lambda model: model.subject,
            seen_keys=seen_keys,
            key_field="subject",
        )


def _load_curriculum(project: ProjectData, rows: list[dict[str, str]]) -> None:
    for row in rows:
        _add_model(
            project,
            "curriculum.csv",
            row,
            CurriculumRow,
            {
                "year_group": _int(row.get("year_group"), 0),
                "subject": row.get("subject", ""),
                "lessons_per_week": _int(row.get("lessons_per_week"), 0),
                "notes": row.get("notes", ""),
            },
            project.curriculum.append,
        )


def _load_teaching_groups(project: ProjectData, rows: list[dict[str, str]]) -> None:
    seen_keys: dict[str, int] = {}
    for row in rows:
        _add_model(
            project,
            "teaching_groups.csv",
            row,
            TeachingGroup,
            {
                "group_id": row.get("group_id", ""),
                "year_group": _int(row.get("year_group"), 0),
                "subject": row.get("subject", ""),
                "lessons_per_week": _int(row.get("lessons_per_week"), 0),
                "class_size": _int(row.get("class_size"), 0),
                "group_type": row.get("group_type", ""),
                "option_block": row.get("option_block", ""),
                "allowed_teachers": _split_pipe(row.get("allowed_teachers")),
                "preferred_teacher": row.get("preferred_teacher", ""),
                "notes": row.get("notes", ""),
            },
            lambda model: project.teaching_groups.__setitem__(model.group_id, model),
            key_getter=lambda model: model.group_id,
            seen_keys=seen_keys,
            key_field="group_id",
        )


def _load_rooms(project: ProjectData, rows: list[dict[str, str]]) -> None:
    seen_keys: dict[str, int] = {}
    for row in rows:
        room_type = row.get("room_type", "General") or "General"
        capacity = _int(row.get("capacity"), 0)
        inferred_has_computers = _room_type_has_computers(room_type)
        has_computers = _bool(row.get("has_computers"), inferred_has_computers)
        computer_count = _optional_int(row.get("computer_count"))
        if computer_count is None:
            computer_count = capacity if has_computers else 0
        _add_model(
            project,
            "rooms.csv",
            row,
            Room,
            {
                "room_id": row.get("room_id", ""),
                "room_name": row.get("room_name", ""),
                "room_type": room_type,
                "capacity": capacity,
                "has_computers": has_computers,
                "computer_count": computer_count,
                "available_days": _split_pipe(row.get("available_days")),
                "unavailable_periods": _split_pipe(row.get("unavailable_periods")),
                "notes": row.get("notes", ""),
            },
            lambda model: project.rooms.__setitem__(model.room_id, model),
            key_getter=lambda model: model.room_id,
            seen_keys=seen_keys,
            key_field="room_id",
        )


def _load_subject_room_requirements(project: ProjectData, rows: list[dict[str, str]]) -> None:
    seen_keys: dict[str, int] = {}
    for row in rows:
        if not row.get("subject"):
            continue
        _add_model(
            project,
            "subject_room_requirements.csv",
            row,
            SubjectRoomRequirement,
            {
                "subject": row.get("subject", ""),
                "required_room_type": row.get("required_room_type", "General") or "General",
                "allow_general_room": _bool(row.get("allow_general_room"), True),
                "notes": row.get("notes", ""),
            },
            lambda model: project.subject_room_requirements.__setitem__(model.subject, model),
            key_getter=lambda model: model.subject,
            seen_keys=seen_keys,
            key_field="subject",
        )


def _load_option_blocks(project: ProjectData, rows: list[dict[str, str]]) -> None:
    for row in rows:
        if not row.get("group_id"):
            continue
        _add_model(
            project,
            "option_blocks.csv",
            row,
            OptionBlock,
            {
                "year_group": _int(row.get("year_group"), 0),
                "block": row.get("block", ""),
                "group_id": row.get("group_id", ""),
                "subject": row.get("subject", ""),
                "simultaneous_required": _bool(row.get("simultaneous_required"), True),
                "notes": row.get("notes", ""),
            },
            project.option_blocks.append,
        )


def _load_fixed_events(project: ProjectData, rows: list[dict[str, str]]) -> None:
    seen_keys: dict[str, int] = {}
    for row in rows:
        _add_model(
            project,
            "fixed_events.csv",
            row,
            FixedEvent,
            {
                "event_id": row.get("event_id", ""),
                "event_name": row.get("event_name", ""),
                "event_type": row.get("event_type", ""),
                "applies_to": row.get("applies_to", ""),
                "day": row.get("day", ""),
                "period": row.get("period", ""),
                "duration_periods": _int(row.get("duration_periods"), 1),
                "required_teacher_ids": _split_pipe(row.get("required_teacher_ids")),
                "required_room_ids": _split_pipe(row.get("required_room_ids")),
                "source_row": _row_number(row),
                "notes": row.get("notes", ""),
            },
            project.fixed_events.append,
            key_getter=lambda model: model.event_id,
            seen_keys=seen_keys,
            key_field="event_id",
        )


def _load_lesson_patterns(project: ProjectData, rows: list[dict[str, str]]) -> None:
    for row in rows:
        if not row.get("subject"):
            continue
        _add_model(
            project,
            "lesson_patterns.csv",
            row,
            LessonPattern,
            {
                "subject": row.get("subject", ""),
                "year_group": _int(row.get("year_group"), 0),
                "lessons_per_week": _int(row.get("lessons_per_week"), 0),
                "allowed_patterns": _split_pipe(row.get("allowed_patterns")),
                "double_lessons_allowed": _bool(row.get("double_lessons_allowed")),
                "max_same_subject_per_day": _int(row.get("max_same_subject_per_day"), 1),
                "notes": row.get("notes", ""),
            },
            project.lesson_patterns.append,
        )


def _load_constraints(project: ProjectData, rows: list[dict[str, str]]) -> None:
    seen_keys: dict[str, int] = {}
    for row in rows:
        if not row.get("constraint_name"):
            continue
        constraint_type = (row.get("constraint_type") or "SOFT").upper()
        _add_model(
            project,
            "constraints.csv",
            row,
            Constraint,
            {
                "constraint_name": row.get("constraint_name", ""),
                "constraint_type": constraint_type,
                "weight": _int(row.get("weight"), 1),
                "enabled": _bool(row.get("enabled"), True),
                "source_row": _row_number(row),
                "description": row.get("description", ""),
            },
            lambda model: project.constraints.__setitem__(model.constraint_name, model),
            key_getter=lambda model: model.constraint_name,
            seen_keys=seen_keys,
            key_field="constraint_name",
        )


def _add_model(
    project: ProjectData,
    filename: str,
    row: dict[str, str],
    model_type,
    values: dict[str, Any],
    add: Callable[[Any], None],
    key_getter: Callable[[Any], str] | None = None,
    seen_keys: dict[str, int] | None = None,
    key_field: str | None = None,
) -> None:
    for field, invalid in _invalid_primitives(values):
        _append_invalid_primitive_issue(project, filename, row, field, invalid)
    if any(True for _field, _invalid in _invalid_primitives(values)):
        return

    try:
        model = model_type(**values)
    except ValidationError as exc:
        project.validation_issues.append(
            ValidationIssue(
                file=filename,
                row=_row_number(row),
                severity="fatal",
                category="schema",
                message=f"Invalid row in {filename}: {exc.errors()[0]['msg']}.",
            )
        )
        return

    if key_getter is not None and seen_keys is not None and key_field is not None:
        key = key_getter(model)
        previous_row = seen_keys.get(key)
        if previous_row is not None:
            project.validation_issues.append(
                ValidationIssue(
                    file=filename,
                    row=_row_number(row),
                    field=key_field,
                    severity="fatal",
                    category="duplicate_identifier",
                    message=(
                        f"Duplicate {key_field} {key!r} in {filename} at row "
                        f"{_row_number(row)}; first declared at row {previous_row}."
                    ),
                )
            )
            return
        seen_keys[key] = _row_number(row) or 0
    add(model)


def _invalid_primitives(values: dict[str, Any]):
    for field, value in values.items():
        if isinstance(value, _InvalidPrimitive):
            yield field, value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, _InvalidPrimitive):
                    yield field, item


def _append_invalid_primitive_issue(
    project: ProjectData,
    filename: str,
    row: dict[str, str],
    field: str,
    invalid: _InvalidPrimitive,
) -> None:
    project.validation_issues.append(
        ValidationIssue(
            file=filename,
            row=_row_number(row),
            field=field,
            severity="fatal",
            category="invalid_primitive",
            message=f"Invalid {invalid.expected_type} value {invalid.value!r} for {field} in {filename}.",
        )
    )


def _split_pipe(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = value.replace(",", "|")
    return [part.strip() for part in normalized.split("|") if part.strip()]


def _bool(value: str | None, default: bool = False) -> bool | _InvalidPrimitive:
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "t"}:
        return True
    if normalized in {"0", "false", "no", "n", "f"}:
        return False
    return _InvalidPrimitive(value=value, expected_type="boolean")


def _int(value: str | None, default: int) -> int | _InvalidPrimitive:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return _InvalidPrimitive(value=value, expected_type="integer")


def _optional_int(value: str | None) -> int | None | _InvalidPrimitive:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return _InvalidPrimitive(value=value, expected_type="integer")


def _room_type_has_computers(room_type: str) -> bool:
    normalized = room_type.strip().lower()
    return normalized in {"ict", "it", "computer", "computing", "pc", "computer lab", "ict suite"}


def _row_number(row: dict[str, str]) -> int | None:
    value = row.get("__row__")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
