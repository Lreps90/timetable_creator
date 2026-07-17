from __future__ import annotations

from collections import Counter, defaultdict

from backend.app.models.entities import ConflictIssue, LessonAssignment, SolveResult
from backend.app.models.project import ProjectData
from backend.app.validators.project_validator import (
    candidate_teachers,
    fixed_event_periods,
    resolve_fixed_event_targets,
    room_satisfies_group_requirements,
)


def verify_solve_result(project: ProjectData, result: SolveResult) -> list[ConflictIssue]:
    """Return hard-constraint violations found in a completed solver result."""
    issues: list[ConflictIssue] = []
    assignments = result.assignments
    valid_days = set(project.school.days)
    valid_periods = set(project.school.periods)
    teacher_loads: Counter[str] = Counter()
    teacher_day_loads: Counter[tuple[str, str]] = Counter()
    teacher_subject_loads: Counter[tuple[str, str]] = Counter()
    assignments_by_group: Counter[str] = Counter()

    for assignment in assignments:
        group = project.teaching_groups.get(assignment.group_id)
        teacher = project.teachers.get(assignment.teacher_id)
        room = project.rooms.get(assignment.room_id)

        if assignment.day not in valid_days or assignment.period not in valid_periods:
            issues.append(_issue(
                assignment,
                "invalid_slot",
                f"Lesson {assignment.lesson_id} uses invalid timetable slot {assignment.day} {assignment.period}.",
            ))
        if group is None:
            issues.append(_issue(
                assignment,
                "unknown_group",
                f"Lesson {assignment.lesson_id} references unknown teaching group {assignment.group_id}.",
            ))
        else:
            assignments_by_group[group.group_id] += 1
            if assignment.subject != group.subject or assignment.year_group != group.year_group:
                issues.append(_issue(
                    assignment,
                    "group_details_mismatch",
                    f"Lesson {assignment.lesson_id} does not match teaching group {group.group_id}'s subject or year group.",
                ))
        if teacher is None:
            issues.append(_issue(
                assignment,
                "unknown_teacher",
                f"Lesson {assignment.lesson_id} references unknown teacher {assignment.teacher_id}.",
            ))
        if room is None:
            issues.append(_issue(
                assignment,
                "unknown_room",
                f"Lesson {assignment.lesson_id} references unknown room {assignment.room_id}.",
            ))

        if teacher is not None:
            teacher_loads[teacher.teacher_id] += 1
            teacher_day_loads[(teacher.teacher_id, assignment.day)] += 1
            teacher_subject_loads[(teacher.teacher_id, assignment.subject)] += 1
            if assignment.day not in teacher.working_days:
                issues.append(_issue(
                    assignment,
                    "teacher_working_day",
                    f"Teacher {teacher.teacher_id} is assigned on {assignment.day}, which is not a working day.",
                ))
            if _slot_code(assignment) in teacher.unavailable_periods:
                issues.append(_issue(
                    assignment,
                    "teacher_unavailable",
                    f"Teacher {teacher.teacher_id} is assigned during unavailable period {assignment.day} {assignment.period}.",
                ))
            if group is not None and teacher.teacher_id not in {item.teacher_id for item in candidate_teachers(project, group)}:
                issues.append(_issue(
                    assignment,
                    "teacher_qualification",
                    f"Teacher {teacher.teacher_id} is not qualified or explicitly allowed for {group.group_id}.",
                ))

        if room is not None:
            if room.available_days and assignment.day not in room.available_days:
                issues.append(_issue(
                    assignment,
                    "room_unavailable",
                    f"Room {room.room_id} is not available on {assignment.day}.",
                ))
            if _slot_code(assignment) in room.unavailable_periods:
                issues.append(_issue(
                    assignment,
                    "room_unavailable",
                    f"Room {room.room_id} is unavailable during {assignment.day} {assignment.period}.",
                ))
            if group is not None and room.capacity < group.class_size:
                issues.append(_issue(
                    assignment,
                    "room_capacity",
                    f"Group {group.group_id} is scheduled in room {room.room_id}, but the room capacity is "
                    f"{room.capacity} and the group size is {group.class_size}.",
                ))
            if group is not None and not room_satisfies_group_requirements(project, group, room):
                issues.append(_issue(
                    assignment,
                    "room_suitability",
                    f"Room {room.room_id} does not meet the type, capacity, or equipment requirements for {group.group_id}.",
                ))

    _verify_slot_clashes(assignments, "teacher_id", "teacher", issues)
    _verify_slot_clashes(assignments, "room_id", "room", issues)
    _verify_slot_clashes(assignments, "group_id", "group", issues)
    _verify_teacher_loads(project, assignments, teacher_loads, teacher_day_loads, teacher_subject_loads, issues)
    _verify_fixed_events(project, assignments, issues)
    _verify_option_blocks(project, assignments, issues)
    _verify_required_lessons(project, assignments_by_group, issues)
    return issues


def apply_verification(project: ProjectData, result: SolveResult) -> SolveResult:
    """Attach verification failures and prevent a result being reported as feasible."""
    if result.status == "failed":
        return result
    issues = verify_solve_result(project, result)
    result.broken_hard_constraints = issues
    if issues:
        result.status = "infeasible"
        result.messages.append(f"Post-solve verification found {len(issues)} hard-constraint violation(s).")
        for report_item in result.constraint_report:
            if report_item.get("category") == "solve_status":
                report_item["message"] = "Solve finished with status infeasible after post-solve verification."
        result.constraint_report.extend(issue.model_dump() for issue in issues)
    return result


def _verify_slot_clashes(
    assignments: list[LessonAssignment],
    attribute: str,
    label: str,
    issues: list[ConflictIssue],
) -> None:
    by_slot: dict[tuple[str, str, str], list[LessonAssignment]] = defaultdict(list)
    for assignment in assignments:
        by_slot[(getattr(assignment, attribute), assignment.day, assignment.period)].append(assignment)
    for (resource_id, day, period), scheduled in by_slot.items():
        if len(scheduled) > 1:
            issues.append(_issue(
                scheduled[0],
                f"{label}_double_booking",
                f"{label.capitalize()} {resource_id} is assigned to more than one lesson on {day} {period}.",
                reasons=[item.lesson_id for item in scheduled],
            ))


def _verify_teacher_loads(
    project: ProjectData,
    assignments: list[LessonAssignment],
    weekly: Counter[str],
    daily: Counter[tuple[str, str]],
    subject: Counter[tuple[str, str]],
    issues: list[ConflictIssue],
) -> None:
    assignment_by_teacher = {assignment.teacher_id: assignment for assignment in assignments}
    for teacher_id, count in weekly.items():
        teacher = project.teachers.get(teacher_id)
        if teacher is None:
            continue
        exemplar = assignment_by_teacher[teacher_id]
        if count > teacher.max_lessons_per_week:
            issues.append(_issue(exemplar, "teacher_weekly_load", f"Teacher {teacher_id} exceeds the weekly maximum of {teacher.max_lessons_per_week}."))
        for day in project.school.days:
            day_count = daily[(teacher_id, day)]
            if day_count > teacher.max_lessons_per_day:
                issues.append(_issue(exemplar, "teacher_daily_load", f"Teacher {teacher_id} exceeds the daily maximum of {teacher.max_lessons_per_day} on {day}."))

    limits: dict[tuple[str, str], int] = {}
    for record in project.teacher_subjects:
        if record.max_lessons_in_subject is not None:
            limits[(record.teacher_id, record.subject)] = record.max_lessons_in_subject
    for key, count in subject.items():
        maximum = limits.get(key)
        if maximum is not None and count > maximum:
            issues.append(_issue(
                assignment_by_teacher[key[0]],
                "teacher_subject_load",
                f"Teacher {key[0]} exceeds the maximum of {maximum} lessons for {key[1]}.",
            ))


def _verify_fixed_events(project: ProjectData, assignments: list[LessonAssignment], issues: list[ConflictIssue]) -> None:
    for event in project.fixed_events:
        targets = resolve_fixed_event_targets(project, event)
        periods = fixed_event_periods(project, event)
        if targets is None or not periods:
            continue

        for assignment in assignments:
            if assignment.day != event.day or assignment.period not in periods:
                continue
            if (
                assignment.teacher_id in targets.teacher_ids
                or assignment.room_id in targets.room_ids
                or assignment.group_id in targets.group_ids
                or assignment.year_group in targets.year_groups
            ):
                issues.append(_issue(
                    assignment,
                    "fixed_event_conflict",
                    f"Lesson {assignment.lesson_id} conflicts with fixed event {event.event_id} on {event.day} {assignment.period}.",
                ))


def _verify_option_blocks(project: ProjectData, assignments: list[LessonAssignment], issues: list[ConflictIssue]) -> None:
    groups_by_block: dict[tuple[int, str], list[str]] = defaultdict(list)
    for option in project.option_blocks:
        if option.simultaneous_required:
            groups_by_block[(option.year_group, option.block)].append(option.group_id)

    slots_by_group: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for assignment in assignments:
        slots_by_group[assignment.group_id].add((assignment.day, assignment.period))

    for (year_group, block), group_ids in groups_by_block.items():
        expected = slots_by_group[group_ids[0]] if group_ids else set()
        if any(slots_by_group[group_id] != expected for group_id in group_ids[1:]):
            issues.append(ConflictIssue(
                group_id=", ".join(group_ids),
                severity="fatal",
                category="option_block_simultaneity",
                message=f"Year {year_group} option block {block} is not scheduled simultaneously.",
            ))

        block_assignments = [item for item in assignments if item.group_id in group_ids]
        for day, period in expected:
            at_slot = [item for item in block_assignments if (item.day, item.period) == (day, period)]
            for attribute, label in (("teacher_id", "teacher"), ("room_id", "room")):
                values = [getattr(item, attribute) for item in at_slot]
                if len(values) != len(set(values)):
                    issues.append(ConflictIssue(
                        group_id=", ".join(group_ids),
                        severity="fatal",
                        category="option_block_resource_clash",
                        message=f"Year {year_group} option block {block} uses the same {label} more than once on {day} {period}.",
                    ))


def _verify_required_lessons(project: ProjectData, scheduled: Counter[str], issues: list[ConflictIssue]) -> None:
    for group in project.teaching_groups.values():
        expected = group.lessons_per_week
        actual = scheduled[group.group_id]
        if actual < expected:
            missing = expected - actual
            issues.append(ConflictIssue(
                group_id=group.group_id,
                subject=group.subject,
                severity="fatal",
                category="missing_required_lessons",
                message=(
                    f"Group {group.group_id} ({group.subject}, Year {group.year_group}) requires {expected} lessons, "
                    f"but {actual} were scheduled ({missing} missing)."
                ),
            ))
        elif actual > expected:
            excess = actual - expected
            issues.append(ConflictIssue(
                group_id=group.group_id,
                subject=group.subject,
                severity="fatal",
                category="incorrect_lesson_count",
                message=(
                    f"Group {group.group_id} ({group.subject}, Year {group.year_group}) requires {expected} lessons, "
                    f"but {actual} were scheduled ({excess} excess assignment{'s' if excess != 1 else ''})."
                ),
            ))


def _issue(assignment: LessonAssignment, category: str, message: str, reasons: list[str] | None = None) -> ConflictIssue:
    return ConflictIssue(
        lesson_id=assignment.lesson_id,
        group_id=assignment.group_id,
        subject=assignment.subject,
        severity="fatal",
        category=category,
        message=message,
        reasons=reasons or [],
    )


def _slot_code(assignment: LessonAssignment) -> str:
    return f"{assignment.day}-{assignment.period}"
