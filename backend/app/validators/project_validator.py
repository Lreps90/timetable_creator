from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from backend.app.constraint_policy import CONSTRAINT_DEFINITIONS
from backend.app.models.entities import (
    CurriculumRow,
    FixedEvent,
    Room,
    Teacher,
    TeachingGroup,
    ValidationIssue,
)
from backend.app.models.project import ProjectData


def validate_project(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    issues.extend(_validate_school_structure(project))
    issues.extend(_validate_required_ids(project))
    issues.extend(_validate_references(project))
    issues.extend(_validate_curriculum_alignment(project))
    issues.extend(_validate_group_feasibility(project))
    issues.extend(_validate_teacher_load_capacity(project))
    issues.extend(_validate_option_blocks(project))
    issues.extend(_validate_fixed_events(project))
    issues.extend(_validate_constraints(project))
    return issues


def _validate_curriculum_alignment(project: ProjectData) -> list[ValidationIssue]:
    """Keep solver demand from teaching_groups.csv aligned with the curriculum plan."""
    # teaching_groups.csv remains the operational source of solver demand. curriculum.csv
    # validates that demand; this milestone does not create groups from curriculum rows.
    issues: list[ValidationIssue] = []
    curriculum_by_key: dict[tuple[int, str], list[tuple[int, CurriculumRow]]] = defaultdict(list)
    groups_by_key: dict[tuple[int, str], list[tuple[int, TeachingGroup]]] = defaultdict(list)

    for row_number, curriculum in enumerate(project.curriculum, start=2):
        curriculum_by_key[(curriculum.year_group, curriculum.subject)].append((row_number, curriculum))
    for row_number, group in enumerate(project.teaching_groups.values(), start=2):
        groups_by_key[(group.year_group, group.subject)].append((row_number, group))

    for (year_group, subject), rows in curriculum_by_key.items():
        if len(rows) > 1:
            first_row = rows[0][0]
            for row_number, curriculum in rows[1:]:
                issues.append(
                    _fatal(
                        "curriculum.csv",
                        "subject",
                        "curriculum_mismatch",
                        f"Duplicate curriculum row for Year {year_group} {subject} at row {row_number}; "
                        f"first declared at row {first_row}. Expected {curriculum.lessons_per_week} lessons per week per group.",
                        row=row_number,
                    )
                )

        row_number, curriculum = rows[0]
        matching_groups = groups_by_key.get((year_group, subject), [])
        if not matching_groups:
            issues.append(
                _fatal(
                    "curriculum.csv",
                    "lessons_per_week",
                    "curriculum_mismatch",
                    f"Year {year_group} {subject} requires {curriculum.lessons_per_week} lessons per week, "
                    "but no matching teaching groups exist.",
                    row=row_number,
                )
            )
            continue

        total_lessons = sum(group.lessons_per_week for _group_row, group in matching_groups)
        for group_row, group in matching_groups:
            if group.lessons_per_week != curriculum.lessons_per_week:
                issues.append(
                    _fatal(
                        "teaching_groups.csv",
                        "lessons_per_week",
                        "curriculum_mismatch",
                        f"Year {year_group} {subject} curriculum expects {curriculum.lessons_per_week} lessons per week "
                        f"per group, but {group.group_id} requests {group.lessons_per_week} (matching groups total "
                        f"{total_lessons} lessons per week).",
                        row=group_row,
                    )
                )

    for (year_group, subject), groups in groups_by_key.items():
        if (year_group, subject) in curriculum_by_key:
            continue
        for _row_number, group in groups:
            issues.append(
                _fatal(
                    "teaching_groups.csv",
                    "subject",
                    "curriculum_mismatch",
                    f"Teaching group {group.group_id} requests {group.lessons_per_week} lessons per week for "
                    f"Year {year_group} {subject}, but no matching curriculum row exists.",
                    row=_row_number,
                )
            )
    return issues


def _validate_school_structure(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if project.school.cycle_weeks != 1:
        issues.append(
            ValidationIssue(
                file="school_structure.csv",
                field="cycle_weeks",
                severity="warning",
                category="unsupported_feature",
                message="v1 solves a one-week timetable. cycle_weeks is stored but only week 1 is scheduled.",
            )
        )
    if len(project.school.days) != project.school.days_per_week:
        issues.append(
            ValidationIssue(
                file="school_structure.csv",
                field="days",
                severity="warning",
                category="school_structure",
                message="days_per_week does not match the number of day labels; the explicit day labels will be used.",
            )
        )
    if len(project.school.periods) != project.school.periods_per_day:
        issues.append(
            ValidationIssue(
                file="school_structure.csv",
                field="periods",
                severity="warning",
                category="school_structure",
                message="periods_per_day does not match the number of period labels; the explicit period labels will be used.",
            )
        )
    return issues


def _validate_required_ids(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for teacher in project.teachers.values():
        if not teacher.teacher_id:
            issues.append(_fatal("teachers.csv", "teacher_id", "schema", "Teacher rows must include teacher_id."))
        if not teacher.working_days:
            issues.append(
                ValidationIssue(
                    file="teachers.csv",
                    field="working_days",
                    severity="warning",
                    category="teacher_availability",
                    message=f"Teacher {teacher.teacher_id} has no working_days; they will be treated as unavailable.",
                )
            )
    for subject in project.subjects.values():
        if not subject.subject:
            issues.append(_fatal("subjects.csv", "subject", "schema", "Subject rows must include subject."))
    for group in project.teaching_groups.values():
        if not group.group_id:
            issues.append(_fatal("teaching_groups.csv", "group_id", "schema", "Teaching group rows must include group_id."))
        if group.lessons_per_week <= 0:
            issues.append(
                _fatal(
                    "teaching_groups.csv",
                    "lessons_per_week",
                    "impossible_constraint",
                    f"Teaching group {group.group_id} must request at least one lesson per week.",
                )
            )
    for room in project.rooms.values():
        if not room.room_id:
            issues.append(_fatal("rooms.csv", "room_id", "schema", "Room rows must include room_id."))
        if room.capacity <= 0:
            issues.append(
                _fatal(
                    "rooms.csv",
                    "capacity",
                    "room_capacity",
                    f"Room {room.room_id} must have positive capacity.",
                )
            )
        if room.has_computers and room.computer_count <= 0:
            issues.append(
                _fatal(
                    "rooms.csv",
                    "computer_count",
                    "computer_rooming",
                    f"Room {room.room_id} is marked as having computers but computer_count is not positive.",
                )
            )
        if room.computer_count > room.capacity:
            issues.append(
                ValidationIssue(
                    file="rooms.csv",
                    field="computer_count",
                    severity="warning",
                    category="computer_rooming",
                    message=f"Room {room.room_id} has computer_count {room.computer_count} above capacity {room.capacity}.",
                )
            )
    return issues


def _validate_references(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    teacher_ids = set(project.teachers)
    subject_ids = set(project.subjects)
    group_ids = set(project.teaching_groups)
    room_ids = set(project.rooms)

    for teacher_subject in project.teacher_subjects:
        if teacher_subject.teacher_id not in teacher_ids:
            issues.append(
                _fatal(
                    "teacher_subjects.csv",
                    "teacher_id",
                    "invalid_reference",
                    f"Teacher subject row references unknown teacher {teacher_subject.teacher_id}.",
                )
            )
        if teacher_subject.subject not in subject_ids:
            issues.append(
                _fatal(
                    "teacher_subjects.csv",
                    "subject",
                    "invalid_reference",
                    f"Teacher subject row references unknown subject {teacher_subject.subject}.",
                )
            )
        if teacher_subject.priority not in {1, 2, 3}:
            issues.append(
                _fatal(
                    "teacher_subjects.csv",
                    "priority",
                    "schema",
                    f"Teacher {teacher_subject.teacher_id} subject {teacher_subject.subject} has priority {teacher_subject.priority}; use 1, 2 or 3.",
                )
            )

    for curriculum in project.curriculum:
        if curriculum.subject not in subject_ids:
            issues.append(
                _fatal(
                    "curriculum.csv",
                    "subject",
                    "invalid_reference",
                    f"Curriculum references unknown subject {curriculum.subject}.",
                )
            )

    for group in project.teaching_groups.values():
        if group.subject not in subject_ids:
            issues.append(
                _fatal(
                    "teaching_groups.csv",
                    "subject",
                    "invalid_reference",
                    f"Teaching group {group.group_id} references unknown subject {group.subject}.",
                )
            )
        for teacher_id in group.allowed_teachers:
            if teacher_id not in teacher_ids:
                issues.append(
                    _fatal(
                        "teaching_groups.csv",
                        "allowed_teachers",
                        "invalid_reference",
                        f"Teaching group {group.group_id} allows unknown teacher {teacher_id}.",
                    )
                )
        if group.preferred_teacher and group.preferred_teacher not in teacher_ids:
            issues.append(
                _fatal(
                    "teaching_groups.csv",
                    "preferred_teacher",
                    "invalid_reference",
                    f"Teaching group {group.group_id} has unknown preferred teacher {group.preferred_teacher}.",
                )
            )

    for requirement in project.subject_room_requirements.values():
        if requirement.subject not in subject_ids:
            issues.append(
                _fatal(
                    "subject_room_requirements.csv",
                    "subject",
                    "invalid_reference",
                    f"Room requirement references unknown subject {requirement.subject}.",
                )
            )

    for option in project.option_blocks:
        if option.group_id not in group_ids:
            issues.append(
                _fatal(
                    "option_blocks.csv",
                    "group_id",
                    "invalid_reference",
                    f"Option block {option.block} references unknown group {option.group_id}.",
                )
            )
        if option.subject not in subject_ids:
            issues.append(
                _fatal(
                    "option_blocks.csv",
                    "subject",
                    "invalid_reference",
                    f"Option block {option.block} references unknown subject {option.subject}.",
                )
            )
        group = project.teaching_groups.get(option.group_id)
        if group and (group.year_group != option.year_group or group.subject != option.subject):
            issues.append(
                _fatal(
                    "option_blocks.csv",
                    "group_id",
                    "option_block",
                    f"Option block row for {option.group_id} does not match the teaching group year/subject.",
                )
            )

    return issues


def _validate_group_feasibility(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    total_slots = len(project.school.days) * len(project.school.periods)

    for group in project.teaching_groups.values():
        if group.lessons_per_week > total_slots:
            issues.append(
                _fatal(
                    "teaching_groups.csv",
                    "lessons_per_week",
                    "impossible_constraint",
                    f"Group {group.group_id} requests {group.lessons_per_week} lessons but only {total_slots} slots exist.",
                )
            )

        teachers = candidate_teachers(project, group)
        if not teachers:
            issues.append(
                _fatal(
                    "teaching_groups.csv",
                    "allowed_teachers",
                    "teacher_qualification",
                    f"Group {group.group_id} has no qualified or explicitly allowed teacher for {group.subject} Year {group.year_group}.",
                )
            )

        rooms = candidate_rooms(project, group)
        if not rooms:
            requirement = project.subject_room_requirements.get(group.subject)
            if group_requires_computers(project, group):
                category = "computer_rooming"
            elif requirement and not requirement.allow_general_room:
                category = "specialist_room"
            else:
                category = "room_capacity"
            issues.append(
                _fatal(
                    "rooms.csv",
                    "capacity",
                    category,
                    f"Group {group.group_id} has no suitable room for {group.subject} with capacity {group.class_size} and required resources.",
                )
            )
    return issues


def _validate_teacher_load_capacity(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    demand_by_teacher: dict[str, int] = defaultdict(int)
    flexible_demand_by_subject_year: dict[tuple[str, int], int] = defaultdict(int)

    for group in project.teaching_groups.values():
        if group.allowed_teachers:
            for teacher_id in group.allowed_teachers:
                demand_by_teacher[teacher_id] += group.lessons_per_week
        else:
            flexible_demand_by_subject_year[(group.subject, group.year_group)] += group.lessons_per_week

    for teacher_id, demand in demand_by_teacher.items():
        teacher = project.teachers.get(teacher_id)
        if teacher and demand > teacher.max_lessons_per_week:
            issues.append(
                ValidationIssue(
                    file="teaching_groups.csv",
                    field="allowed_teachers",
                    severity="warning",
                    category="teacher_load",
                    message=f"Explicitly constrained demand for {teacher_id} is {demand}, above max_lessons_per_week {teacher.max_lessons_per_week}.",
                )
            )

    for (subject, year_group), demand in flexible_demand_by_subject_year.items():
        capacity = 0
        for teacher, _priority, _explicit in _candidate_teacher_records(project, subject, year_group, []):
            capacity += teacher.max_lessons_per_week
        if capacity and demand > capacity:
            issues.append(
                ValidationIssue(
                    file="teacher_subjects.csv",
                    field="max_lessons_per_week",
                    severity="warning",
                    category="teacher_load",
                    message=f"{subject} Year {year_group} demand is {demand}, above the combined weekly load {capacity} of listed teachers.",
                )
            )
    return issues


def _validate_option_blocks(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    groups_by_key: dict[tuple[int, str], list[str]] = defaultdict(list)

    for option in project.option_blocks:
        if option.simultaneous_required:
            groups_by_key[(option.year_group, option.block)].append(option.group_id)

    for group in project.teaching_groups.values():
        if group.option_block:
            key = (group.year_group, group.option_block)
            if group.group_id not in groups_by_key.get(key, []):
                issues.append(
                    ValidationIssue(
                        file="option_blocks.csv",
                        field="group_id",
                        severity="warning",
                        category="option_block",
                        message=f"Group {group.group_id} has option_block {group.option_block} but no matching simultaneous option_blocks.csv row.",
                    )
                )

    for (year_group, block), group_ids in groups_by_key.items():
        groups = [project.teaching_groups[group_id] for group_id in group_ids if group_id in project.teaching_groups]
        lesson_counts = {group.lessons_per_week for group in groups}
        if len(lesson_counts) > 1:
            issues.append(
                _fatal(
                    "option_blocks.csv",
                    "simultaneous_required",
                    "option_block",
                    f"Year {year_group} option block {block} has simultaneous groups with different lessons_per_week values.",
                )
            )
        if len(groups) > len(project.rooms):
            issues.append(
                _fatal(
                    "option_blocks.csv",
                    "block",
                    "option_block",
                    f"Year {year_group} option block {block} has {len(groups)} simultaneous groups but only {len(project.rooms)} rooms exist.",
                )
            )
        if groups and not _has_distinct_room_matching(project, groups):
            room_counts = [f"{group.group_id}: {len(candidate_rooms(project, group))} suitable room(s)" for group in groups]
            issues.append(
                _fatal(
                    "option_blocks.csv",
                    "block",
                    "option_block_rooming",
                    f"Year {year_group} option block {block} cannot assign distinct suitable rooms to every simultaneous group. "
                    f"Check room type, capacity and computer_count. {'; '.join(room_counts)}.",
                )
            )
    return issues


@dataclass(frozen=True)
class FixedEventTargets:
    teacher_ids: frozenset[str]
    room_ids: frozenset[str]
    group_ids: frozenset[str]
    year_groups: frozenset[int]


def resolve_fixed_event_targets(project: ProjectData, event: FixedEvent) -> FixedEventTargets | None:
    teacher_ids = set(event.required_teacher_ids)
    room_ids = set(event.required_room_ids)
    group_ids: set[str] = set()
    year_groups: set[int] = set()
    applies_to = event.applies_to

    if applies_to == "ALL_STAFF":
        teacher_ids.update(project.teachers)
    elif applies_to == "SLT":
        teacher_ids.update(
            teacher.teacher_id
            for teacher in project.teachers.values()
            if "slt" in teacher.role.lower() or "senior" in teacher.role.lower()
        )
    elif applies_to:
        matches: list[tuple[str, str | int]] = []
        if applies_to in project.teachers:
            matches.append(("teacher", applies_to))
        if applies_to in project.rooms:
            matches.append(("room", applies_to))
        if applies_to in project.teaching_groups:
            matches.append(("group", applies_to))
        year_groups_in_project = {group.year_group for group in project.teaching_groups.values()}
        if applies_to.startswith("Y") and applies_to[1:].isdigit():
            year_group = int(applies_to[1:])
            if year_group in year_groups_in_project:
                matches.append(("year_group", year_group))
        elif applies_to.isdigit():
            year_group = int(applies_to)
            if year_group in year_groups_in_project:
                matches.append(("year_group", year_group))
        if len(matches) != 1:
            return None
        target_type, target_id = matches[0]
        if target_type == "teacher":
            teacher_ids.add(str(target_id))
        elif target_type == "room":
            room_ids.add(str(target_id))
        elif target_type == "group":
            group_ids.add(str(target_id))
        else:
            year_groups.add(int(target_id))

    return FixedEventTargets(
        teacher_ids=frozenset(teacher_ids),
        room_ids=frozenset(room_ids),
        group_ids=frozenset(group_ids),
        year_groups=frozenset(year_groups),
    )


def fixed_event_periods(project: ProjectData, event: FixedEvent) -> tuple[str, ...]:
    period_index = {period: index for index, period in enumerate(project.school.periods)}
    start = period_index.get(event.period)
    if start is None or event.duration_periods <= 0:
        return ()
    end = start + event.duration_periods
    if end > len(project.school.periods):
        return ()
    return tuple(project.school.periods[start:end])


def _validate_fixed_events(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    valid_days = set(project.school.days)
    valid_periods = set(project.school.periods)
    period_index = {period: index for index, period in enumerate(project.school.periods)}

    for event in project.fixed_events:
        row = event.source_row
        if not event.event_id:
            issues.append(_fatal("fixed_events.csv", "event_id", "schema", "Fixed event rows must include event_id.", row=row))
        if event.day not in valid_days:
            issues.append(
                _fatal(
                    "fixed_events.csv",
                    "day",
                    "invalid_reference",
                    f"Fixed event {event.event_id} uses unknown day {event.day}.",
                    row=row,
                )
            )
        if event.period not in valid_periods:
            issues.append(
                _fatal(
                    "fixed_events.csv",
                    "period",
                    "invalid_reference",
                    f"Fixed event {event.event_id} uses unknown period {event.period}.",
                    row=row,
                )
            )
        elif event.duration_periods <= 0:
            issues.append(
                _fatal(
                    "fixed_events.csv",
                    "duration_periods",
                    "fixed_event_duration",
                    f"Fixed event {event.event_id} must have a positive duration_periods value; got {event.duration_periods}.",
                    row=row,
                )
            )
        elif period_index[event.period] + event.duration_periods > len(project.school.periods):
            issues.append(
                _fatal(
                    "fixed_events.csv",
                    "duration_periods",
                    "fixed_event_duration",
                    f"Fixed event {event.event_id} starts at {event.period} for {event.duration_periods} period(s), "
                    f"which extends beyond the configured {len(project.school.periods)} periods.",
                    row=row,
                )
            )
        targets = resolve_fixed_event_targets(project, event)
        if event.applies_to and targets is None:
            issues.append(
                _fatal(
                    "fixed_events.csv",
                    "applies_to",
                    "fixed_event_target",
                    f"Fixed event {event.event_id} targets {event.applies_to!r}, but this is not a recognised teacher, "
                    "room, group, year-group or reserved target.",
                    row=row,
                )
            )
        for field, values, known_ids, label in (
            ("required_teacher_ids", event.required_teacher_ids, set(project.teachers), "teacher"),
            ("required_room_ids", event.required_room_ids, set(project.rooms), "room"),
        ):
            for value in values:
                if value not in known_ids:
                    issues.append(
                        _fatal(
                            "fixed_events.csv",
                            field,
                            "invalid_reference",
                            f"Fixed event {event.event_id} references unknown {label} {value}.",
                            row=row,
                        )
                    )
            for value in _duplicate_values(values):
                issues.append(
                    _fatal(
                        "fixed_events.csv",
                        field,
                        "duplicate_reference",
                        f"Fixed event {event.event_id} repeats {label} {value} in {field}.",
                        row=row,
                    )
                )
        if targets is not None and not (
            targets.teacher_ids or targets.room_ids or targets.group_ids or targets.year_groups
        ):
            if event.applies_to == "SLT":
                issues.append(
                    ValidationIssue(
                        file="fixed_events.csv",
                        row=row,
                        field="applies_to",
                        severity="warning",
                        category="fixed_event_no_matches",
                        message=(
                            f"Fixed event {event.event_id} targets SLT, but no teachers currently have an SLT or "
                            "Senior role. The event has no blocking effect in this scenario."
                        ),
                    )
                )
            else:
                issues.append(
                    _fatal(
                        "fixed_events.csv",
                        "applies_to",
                        "fixed_event_target",
                        f"Fixed event {event.event_id} has no recognised target or required resource to block.",
                        row=row,
                    )
                )
    return issues


def _duplicate_values(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def candidate_teachers(project: ProjectData, group: TeachingGroup) -> list[Teacher]:
    return [teacher for teacher, _priority, _explicit in _candidate_teacher_records(project, group.subject, group.year_group, group.allowed_teachers)]


def candidate_rooms(project: ProjectData, group: TeachingGroup) -> list[Room]:
    rooms = [
        room
        for room in project.rooms.values()
        if room_satisfies_group_requirements(project, group, room)
    ]
    requirement = project.subject_room_requirements.get(group.subject)
    if requirement:
        required = requirement.required_room_type
        rooms = sorted(rooms, key=lambda room: 0 if room.room_type == required else 1)
    else:
        subject = project.subjects.get(group.subject)
        if subject and subject.default_room_type:
            default_type = subject.default_room_type
            rooms = sorted(rooms, key=lambda room: 0 if room.room_type == default_type else 1)
    return rooms


def room_satisfies_group_requirements(project: ProjectData, group: TeachingGroup, room: Room) -> bool:
    if room.capacity < group.class_size:
        return False

    requirement = project.subject_room_requirements.get(group.subject)
    if requirement:
        required_type = requirement.required_room_type
        if room.room_type != required_type:
            if not requirement.allow_general_room or room.room_type != "General":
                return False

    if group_requires_computers(project, group):
        return room.has_computers and room.computer_count >= group.class_size
    return True


def group_requires_computers(project: ProjectData, group: TeachingGroup) -> bool:
    subject = project.subjects.get(group.subject)
    requirement = project.subject_room_requirements.get(group.subject)
    signals = [group.subject]
    if subject:
        signals.append(subject.default_room_type)
    if requirement:
        signals.append(requirement.required_room_type)
    return any(_computer_resource_signal(value) for value in signals)


def _computer_resource_signal(value: str) -> bool:
    normalized = value.strip().lower()
    return any(token in normalized for token in ("computer", "computing", "ict", "it suite", "pc"))


def _has_distinct_room_matching(project: ProjectData, groups: list[TeachingGroup]) -> bool:
    room_options = {group.group_id: [room.room_id for room in candidate_rooms(project, group)] for group in groups}
    if any(not room_ids for room_ids in room_options.values()):
        return False

    ordered_groups = sorted(groups, key=lambda group: len(room_options[group.group_id]))
    matched_room_to_group: dict[str, str] = {}

    def assign(index: int) -> bool:
        if index == len(ordered_groups):
            return True
        group = ordered_groups[index]
        for room_id in room_options[group.group_id]:
            if room_id in matched_room_to_group:
                continue
            matched_room_to_group[room_id] = group.group_id
            if assign(index + 1):
                return True
            matched_room_to_group.pop(room_id)
        return False

    return assign(0)


def _candidate_teacher_records(
    project: ProjectData,
    subject: str,
    year_group: int,
    allowed_teachers: list[str],
) -> list[tuple[Teacher, int, bool]]:
    records: list[tuple[Teacher, int, bool]] = []
    if allowed_teachers:
        for teacher_id in allowed_teachers:
            teacher = project.teachers.get(teacher_id)
            if teacher:
                priority = _priority_for(project, teacher_id, subject, year_group) or 2
                records.append((teacher, priority, True))
        return records

    for teacher_subject in project.teacher_subjects:
        if teacher_subject.subject != subject:
            continue
        if teacher_subject.can_teach_years and year_group not in teacher_subject.can_teach_years:
            continue
        teacher = project.teachers.get(teacher_subject.teacher_id)
        if teacher:
            records.append((teacher, teacher_subject.priority, False))
    return sorted(records, key=lambda record: (record[1], record[0].teacher_id))


def _priority_for(project: ProjectData, teacher_id: str, subject: str, year_group: int) -> int | None:
    for teacher_subject in project.teacher_subjects:
        if teacher_subject.teacher_id != teacher_id or teacher_subject.subject != subject:
            continue
        if teacher_subject.can_teach_years and year_group not in teacher_subject.can_teach_years:
            continue
        return teacher_subject.priority
    return None


def _validate_constraints(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for constraint in project.constraints.values():
        definition = CONSTRAINT_DEFINITIONS.get(constraint.constraint_name)
        row = constraint.source_row
        if definition is None:
            issues.append(
                _fatal(
                    "constraints.csv",
                    "constraint_name",
                    "unsupported_constraint",
                    f"Constraint {constraint.constraint_name!r} is not supported and cannot be enabled or retained as a disabled row.",
                    row=row,
                )
            )
            continue

        if constraint.constraint_type != definition.constraint_type:
            issues.append(
                _fatal(
                    "constraints.csv",
                    "constraint_type",
                    "constraint_type",
                    f"Constraint {constraint.constraint_name!r} must be declared as {definition.constraint_type}, not {constraint.constraint_type}.",
                    row=row,
                )
            )

        if definition.constraint_type == "HARD" and not constraint.enabled:
            issues.append(
                _fatal(
                    "constraints.csv",
                    "enabled",
                    "mandatory_constraint",
                    f"Mandatory hard constraint {constraint.constraint_name!r} cannot be disabled.",
                    row=row,
                )
            )
        if definition.constraint_type == "SOFT" and constraint.weight < 0:
            issues.append(
                _fatal(
                    "constraints.csv",
                    "weight",
                    "invalid_constraint_weight",
                    f"Soft constraint {constraint.constraint_name!r} must have a non-negative weight.",
                    row=row,
                )
            )
    return issues


def _fatal(file: str, field: str, category: str, message: str, row: int | None = None) -> ValidationIssue:
    return ValidationIssue(file=file, row=row, field=field, severity="fatal", category=category, message=message)
