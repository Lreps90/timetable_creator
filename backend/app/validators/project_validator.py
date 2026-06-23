from __future__ import annotations

from collections import defaultdict

from backend.app.models.entities import (
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
    issues.extend(_validate_group_feasibility(project))
    issues.extend(_validate_teacher_load_capacity(project))
    issues.extend(_validate_option_blocks(project))
    issues.extend(_validate_fixed_events(project))
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

    for event in project.fixed_events:
        for teacher_id in event.required_teacher_ids:
            if teacher_id not in teacher_ids:
                issues.append(
                    _fatal(
                        "fixed_events.csv",
                        "required_teacher_ids",
                        "invalid_reference",
                        f"Fixed event {event.event_id} references unknown teacher {teacher_id}.",
                    )
                )
        for room_id in event.required_room_ids:
            if room_id not in room_ids:
                issues.append(
                    _fatal(
                        "fixed_events.csv",
                        "required_room_ids",
                        "invalid_reference",
                        f"Fixed event {event.event_id} references unknown room {room_id}.",
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


def _validate_fixed_events(project: ProjectData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    valid_days = set(project.school.days)
    valid_periods = set(project.school.periods)
    period_index = {period: index for index, period in enumerate(project.school.periods)}
    known_applies_to = set(project.teachers) | set(project.rooms) | {group.group_id for group in project.teaching_groups.values()}
    known_applies_to |= {"ALL_STAFF", "SLT"}
    known_applies_to |= {f"Y{year}" for year in range(7, 14)}
    known_applies_to |= {str(year) for year in range(7, 14)}

    for event in project.fixed_events:
        if event.day not in valid_days:
            issues.append(
                _fatal(
                    "fixed_events.csv",
                    "day",
                    "invalid_reference",
                    f"Fixed event {event.event_id} uses unknown day {event.day}.",
                )
            )
        if event.period not in valid_periods:
            issues.append(
                _fatal(
                    "fixed_events.csv",
                    "period",
                    "invalid_reference",
                    f"Fixed event {event.event_id} uses unknown period {event.period}.",
                )
            )
        elif period_index[event.period] + event.duration_periods > len(project.school.periods):
            issues.append(
                _fatal(
                    "fixed_events.csv",
                    "duration_periods",
                    "impossible_constraint",
                    f"Fixed event {event.event_id} extends beyond the school day.",
                )
            )
        if event.applies_to and event.applies_to not in known_applies_to:
            issues.append(
                ValidationIssue(
                    file="fixed_events.csv",
                    field="applies_to",
                    severity="warning",
                    category="invalid_reference",
                    message=f"Fixed event {event.event_id} applies_to value {event.applies_to} was not recognised.",
                )
            )
    return issues


def candidate_teachers(project: ProjectData, group: TeachingGroup) -> list[Teacher]:
    return [teacher for teacher, _priority, _explicit in _candidate_teacher_records(project, group.subject, group.year_group, group.allowed_teachers)]


def candidate_rooms(project: ProjectData, group: TeachingGroup) -> list[Room]:
    rooms = [room for room in project.rooms.values() if room.capacity >= group.class_size]
    requirement = project.subject_room_requirements.get(group.subject)
    if requirement:
        required = requirement.required_room_type
        if requirement.allow_general_room:
            rooms = sorted(rooms, key=lambda room: 0 if room.room_type == required else 1)
        else:
            rooms = [room for room in rooms if room.room_type == required]
    else:
        subject = project.subjects.get(group.subject)
        if subject and subject.default_room_type:
            default_type = subject.default_room_type
            rooms = sorted(rooms, key=lambda room: 0 if room.room_type == default_type else 1)
    if group_requires_computers(project, group):
        rooms = [room for room in rooms if room.has_computers and room.computer_count >= group.class_size]
    return rooms


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


def _fatal(file: str, field: str, category: str, message: str) -> ValidationIssue:
    return ValidationIssue(file=file, field=field, severity="fatal", category=category, message=message)
