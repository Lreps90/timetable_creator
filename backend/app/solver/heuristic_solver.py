from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable

from backend.app.constraint_policy import (
    SOFT_RULE_BALANCE_TEACHER_LOAD,
    SOFT_RULE_CONSECUTIVE_TEACHER,
    SOFT_RULE_EMERGENCY_STAFF,
    SOFT_RULE_OPTION_END,
    SOFT_RULE_PREFERRED_TEACHER,
    SOFT_RULE_PRIORITY_TEACHER,
    SOFT_RULE_ROOM_PREFERENCE,
    SOFT_RULE_SAME_SUBJECT,
    SOFT_RULE_SPREAD_GROUP_LESSONS,
    effective_soft_weight,
)
from backend.app.models.entities import (
    ConflictIssue,
    LessonAssignment,
    Room,
    SolveResult,
    SolveSettings,
    Teacher,
    TeachingGroup,
)
from backend.app.models.project import ProjectData
from backend.app.validators.project_validator import (
    candidate_rooms,
    fixed_event_periods,
    group_requires_computers,
    resolve_fixed_event_targets,
)
from backend.app.solver.verification import apply_verification


ProgressCallback = Callable[[float, list[str]], None]


@dataclass(frozen=True)
class TeacherCandidate:
    teacher: Teacher
    priority: int
    explicit_override: bool
    subject_max: int | None


@dataclass
class Task:
    task_id: str
    groups: list[TeachingGroup]
    option_block: str = ""


@dataclass
class TentativeAssignment:
    group: TeachingGroup
    teacher: Teacher
    room: Room
    priority: int
    explicit_override: bool
    penalty: int
    penalty_messages: list[str]


class HeuristicTimetableSolver:
    """Deterministic single-period solver with small backtracking inside option blocks."""

    def __init__(self, project: ProjectData, settings: SolveSettings):
        self.project = project
        self.settings = settings
        self.days = project.school.days
        self.periods = project.school.periods
        self.slots = [(day, period) for day in self.days for period in self.periods]
        self.teacher_slot: dict[tuple[str, str, str], str] = {}
        self.room_slot: dict[tuple[str, str, str], str] = {}
        self.group_slot: dict[tuple[str, str, str], str] = {}
        self.fixed_teacher_slots: set[tuple[str, str, str]] = set()
        self.fixed_room_slots: set[tuple[str, str, str]] = set()
        self.fixed_group_slots: set[tuple[str, str, str]] = set()
        self.fixed_year_slots: set[tuple[int, str, str]] = set()
        self.teacher_week_load: Counter[str] = Counter()
        self.teacher_day_load: Counter[tuple[str, str]] = Counter()
        self.teacher_subject_load: Counter[tuple[str, str]] = Counter()
        self.group_subject_day_load: Counter[tuple[str, str, str]] = Counter()
        self.assignments: list[LessonAssignment] = []
        self.unscheduled: list[ConflictIssue] = []
        self.soft_penalties: list[ConflictIssue] = []
        self.total_penalty = 0
        self._lesson_sequence = 1

    def solve(self, progress_callback: ProgressCallback | None = None) -> SolveResult:
        start = time.monotonic()
        fatal_count = len([issue for issue in self.project.validation_issues if issue.severity == "fatal"])
        if fatal_count:
            return SolveResult(
                status="failed",
                score=0,
                messages=[f"Solving was not started because validation found {fatal_count} fatal issue(s)."],
                broken_hard_constraints=[
                    ConflictIssue(
                        severity="fatal",
                        category=issue.category,
                        message=issue.message,
                        reasons=[issue.field or issue.file or "validation"],
                    )
                    for issue in self.project.validation_issues
                    if issue.severity == "fatal"
                ],
            )

        self._apply_fixed_events()
        tasks = self._build_tasks()
        if not tasks:
            return SolveResult(status="feasible", score=1000, messages=["No lessons were requested."])

        for index, task in enumerate(tasks, start=1):
            if time.monotonic() - start > self.settings.time_limit_seconds:
                self._record_unscheduled(task, ["time limit reached before the task could be placed"])
                continue

            placed = self._place_task(task)
            if not placed:
                reasons = self._explain_task_failure(task)
                self._record_unscheduled(task, reasons)

            if progress_callback:
                progress = round(index / len(tasks), 3)
                progress_callback(progress, [f"Scheduled {index - len(self.unscheduled)} of {len(tasks)} lesson task(s)."])

        status = "feasible" if not self.unscheduled else "infeasible"
        score = max(0, 1000 - self.total_penalty - (len(self.unscheduled) * 100))
        return SolveResult(
            status=status,
            assignments=self._sorted_assignments(),
            unscheduled_lessons=self.unscheduled,
            broken_hard_constraints=[],
            soft_penalties=self.soft_penalties,
            score=score,
            total_penalty=self.total_penalty,
            constraint_report=self._constraint_report(status, score),
            messages=[
                f"Placed {len(self.assignments)} lesson assignment(s).",
                f"{len(self.unscheduled)} lesson task(s) could not be scheduled.",
            ],
        )

    def _build_tasks(self) -> list[Task]:
        option_groups: dict[tuple[int, str], list[TeachingGroup]] = defaultdict(list)
        for option in self.project.option_blocks:
            if not option.simultaneous_required:
                continue
            group = self.project.teaching_groups.get(option.group_id)
            if group:
                option_groups[(option.year_group, option.block)].append(group)

        tasks: list[Task] = []
        option_group_ids: set[str] = set()
        sequence = 1
        for (year_group, block), groups in sorted(option_groups.items()):
            if not groups:
                continue
            lessons = min(group.lessons_per_week for group in groups)
            option_group_ids.update(group.group_id for group in groups)
            for _ in range(lessons):
                tasks.append(Task(task_id=f"OB{year_group}{block}-{sequence:03d}", groups=groups, option_block=block))
                sequence += 1

        for group in sorted(self.project.teaching_groups.values(), key=lambda item: item.group_id):
            if group.group_id in option_group_ids:
                continue
            for lesson_index in range(group.lessons_per_week):
                tasks.append(Task(task_id=f"{group.group_id}-{lesson_index + 1:02d}", groups=[group]))

        return sorted(tasks, key=self._task_sort_key)

    def _task_sort_key(self, task: Task) -> tuple[int, int, str]:
        candidate_product = 1
        for group in task.groups:
            candidate_product *= max(1, len(self._teacher_candidates(group)) * len(candidate_rooms(self.project, group)))
        return (-len(task.groups), candidate_product, task.task_id)

    def _place_task(self, task: Task) -> bool:
        best_slot: tuple[str, str] | None = None
        best_assignments: list[TentativeAssignment] | None = None
        best_penalty: int | None = None

        for day, period in self._ordered_slots(task):
            tentative = self._assign_groups_for_slot(task, day, period)
            if tentative is None:
                continue
            penalty = sum(item.penalty for item in tentative)
            if best_penalty is None or penalty < best_penalty:
                best_slot = (day, period)
                best_assignments = tentative
                best_penalty = penalty
                if self.settings.mode == "quick" and penalty == 0:
                    break

        if best_slot is None or best_assignments is None:
            return False

        self._commit_assignments(task, best_slot[0], best_slot[1], best_assignments)
        return True

    def _ordered_slots(self, task: Task) -> list[tuple[str, str]]:
        slots = list(self.slots)
        if self.settings.mode == "quick":
            return slots
        if self.settings.mode == "deep":
            return sorted(slots, key=lambda slot: self._slot_pressure(slot[0], slot[1], task))
        return sorted(slots, key=lambda slot: (self._day_index(slot[0]) % 2, self._slot_pressure(slot[0], slot[1], task), self._period_index(slot[1])))

    def _slot_pressure(self, day: str, period: str, task: Task) -> int:
        pressure = 0
        for group in task.groups:
            pressure += self.group_subject_day_load[(group.group_id, group.subject, day)]
        pressure += self._period_index(period)
        return pressure

    def _assign_groups_for_slot(self, task: Task, day: str, period: str) -> list[TentativeAssignment] | None:
        for group in task.groups:
            if self._group_blocked(group, day, period):
                return None

        best: list[TentativeAssignment] | None = None
        best_penalty: int | None = None

        def backtrack(
            index: int,
            used_teachers: set[str],
            used_rooms: set[str],
            chosen: list[TentativeAssignment],
        ) -> None:
            nonlocal best, best_penalty
            if index == len(task.groups):
                penalty = sum(item.penalty for item in chosen)
                if best_penalty is None or penalty < best_penalty:
                    best = list(chosen)
                    best_penalty = penalty
                return

            group = task.groups[index]
            teachers = [
                candidate
                for candidate in self._teacher_candidates(group)
                if candidate.teacher.teacher_id not in used_teachers
                and self._teacher_available(candidate, group, day, period)
            ]
            rooms = [
                room
                for room in candidate_rooms(self.project, group)
                if room.room_id not in used_rooms and self._room_available(room, day, period)
            ]
            if not teachers or not rooms:
                return

            teachers.sort(key=lambda candidate: self._teacher_order(candidate, group, day))
            rooms.sort(key=lambda room: (0 if self._preferred_room_type(group, room) else 1, self.room_slot.get((room.room_id, day, period), "")))

            for candidate in teachers:
                for room in rooms:
                    penalty, messages = self._soft_penalty(group, candidate, room, day, period, bool(task.option_block))
                    if best_penalty is not None and sum(item.penalty for item in chosen) + penalty >= best_penalty:
                        continue
                    chosen.append(
                        TentativeAssignment(
                            group=group,
                            teacher=candidate.teacher,
                            room=room,
                            priority=candidate.priority,
                            explicit_override=candidate.explicit_override,
                            penalty=penalty,
                            penalty_messages=messages,
                        )
                    )
                    used_teachers.add(candidate.teacher.teacher_id)
                    used_rooms.add(room.room_id)
                    backtrack(index + 1, used_teachers, used_rooms, chosen)
                    used_rooms.remove(room.room_id)
                    used_teachers.remove(candidate.teacher.teacher_id)
                    chosen.pop()

        backtrack(0, set(), set(), [])
        return best

    def _teacher_order(self, candidate: TeacherCandidate, group: TeachingGroup, day: str) -> tuple[int, int, int, str]:
        preferred = 0 if group.preferred_teacher and candidate.teacher.teacher_id == group.preferred_teacher else 1
        return (
            preferred,
            candidate.priority,
            self.teacher_day_load[(candidate.teacher.teacher_id, day)],
            candidate.teacher.teacher_id,
        )

    def _teacher_candidates(self, group: TeachingGroup) -> list[TeacherCandidate]:
        candidates: list[TeacherCandidate] = []
        if group.allowed_teachers:
            for teacher_id in group.allowed_teachers:
                teacher = self.project.teachers.get(teacher_id)
                if not teacher:
                    continue
                priority, subject_max = self._teacher_subject_limits(teacher_id, group.subject, group.year_group)
                candidates.append(TeacherCandidate(teacher, priority or 2, True, subject_max))
            return candidates

        for teacher_subject in self.project.teacher_subjects:
            if teacher_subject.subject != group.subject:
                continue
            if teacher_subject.can_teach_years and group.year_group not in teacher_subject.can_teach_years:
                continue
            teacher = self.project.teachers.get(teacher_subject.teacher_id)
            if teacher:
                candidates.append(
                    TeacherCandidate(
                        teacher=teacher,
                        priority=teacher_subject.priority,
                        explicit_override=False,
                        subject_max=teacher_subject.max_lessons_in_subject,
                    )
                )
        return sorted(candidates, key=lambda candidate: (candidate.priority, candidate.teacher.teacher_id))

    def _teacher_subject_limits(self, teacher_id: str, subject: str, year_group: int) -> tuple[int | None, int | None]:
        for teacher_subject in self.project.teacher_subjects:
            if teacher_subject.teacher_id != teacher_id or teacher_subject.subject != subject:
                continue
            if teacher_subject.can_teach_years and year_group not in teacher_subject.can_teach_years:
                continue
            return teacher_subject.priority, teacher_subject.max_lessons_in_subject
        return None, None

    def _teacher_available(self, candidate: TeacherCandidate, group: TeachingGroup, day: str, period: str) -> bool:
        teacher = candidate.teacher
        if not teacher.working_days or day not in teacher.working_days:
            return False
        if _slot_code(day, period) in teacher.unavailable_periods:
            return False
        if (teacher.teacher_id, day, period) in self.fixed_teacher_slots:
            return False
        if (teacher.teacher_id, day, period) in self.teacher_slot:
            return False
        if self.teacher_week_load[teacher.teacher_id] >= teacher.max_lessons_per_week:
            return False
        if self.teacher_day_load[(teacher.teacher_id, day)] >= teacher.max_lessons_per_day:
            return False
        if candidate.subject_max is not None and self.teacher_subject_load[(teacher.teacher_id, group.subject)] >= candidate.subject_max:
            return False
        return True

    def _room_available(self, room: Room, day: str, period: str) -> bool:
        if room.available_days and day not in room.available_days:
            return False
        if _slot_code(day, period) in room.unavailable_periods:
            return False
        if (room.room_id, day, period) in self.fixed_room_slots:
            return False
        if (room.room_id, day, period) in self.room_slot:
            return False
        return True

    def _group_blocked(self, group: TeachingGroup, day: str, period: str) -> bool:
        if (group.group_id, day, period) in self.group_slot:
            return True
        if (group.group_id, day, period) in self.fixed_group_slots:
            return True
        if (group.year_group, day, period) in self.fixed_year_slots:
            return True
        return False

    def _commit_assignments(self, task: Task, day: str, period: str, tentative: list[TentativeAssignment]) -> None:
        for item in tentative:
            lesson_id = f"L{self._lesson_sequence:04d}"
            self._lesson_sequence += 1
            group = item.group
            teacher = item.teacher
            room = item.room

            assignment = LessonAssignment(
                lesson_id=lesson_id,
                day=day,
                period=period,
                group_id=group.group_id,
                year_group=group.year_group,
                subject=group.subject,
                teacher_id=teacher.teacher_id,
                teacher_name=teacher.name,
                room_id=room.room_id,
                room_name=room.room_name,
                source_scenario=self.project.source_scenario,
                option_block=task.option_block or group.option_block,
            )
            self.assignments.append(assignment)
            self.teacher_slot[(teacher.teacher_id, day, period)] = lesson_id
            self.room_slot[(room.room_id, day, period)] = lesson_id
            self.group_slot[(group.group_id, day, period)] = lesson_id
            self.teacher_week_load[teacher.teacher_id] += 1
            self.teacher_day_load[(teacher.teacher_id, day)] += 1
            self.teacher_subject_load[(teacher.teacher_id, group.subject)] += 1
            self.group_subject_day_load[(group.group_id, group.subject, day)] += 1
            self.total_penalty += item.penalty

            for message in item.penalty_messages:
                self.soft_penalties.append(
                    ConflictIssue(
                        lesson_id=lesson_id,
                        group_id=group.group_id,
                        subject=group.subject,
                        severity="warning",
                        category="soft_constraint",
                        message=message,
                        reasons=[f"{day}-{period}"],
                    )
                )

    def _soft_penalty(
        self,
        group: TeachingGroup,
        candidate: TeacherCandidate,
        room: Room,
        day: str,
        period: str,
        is_option_block: bool,
    ) -> tuple[int, list[str]]:
        if not self.settings.soft_constraints_enabled:
            return 0, []

        penalty = 0
        messages: list[str] = []
        teacher = candidate.teacher

        if group.preferred_teacher and teacher.teacher_id != group.preferred_teacher:
            value = self._constraint_weight(SOFT_RULE_PREFERRED_TEACHER)
            penalty += value
            messages.append(f"{group.group_id} was not assigned to preferred teacher {group.preferred_teacher}.")

        if candidate.priority == 2:
            value = self._constraint_weight(SOFT_RULE_PRIORITY_TEACHER)
            penalty += value
            messages.append(f"{teacher.teacher_id} is priority 2 for {group.subject}.")
        elif candidate.priority >= 3:
            value = self._constraint_weight(SOFT_RULE_EMERGENCY_STAFF)
            penalty += value
            messages.append(f"{teacher.teacher_id} is emergency priority for {group.subject}.")

        max_same_day = self._max_same_subject_per_day(group)
        existing_subject_day = self.group_subject_day_load[(group.group_id, group.subject, day)]
        if existing_subject_day >= max_same_day:
            value = self._constraint_weight(SOFT_RULE_SAME_SUBJECT)
            penalty += value * (existing_subject_day + 1)
            messages.append(f"{group.group_id} has {group.subject} more than {max_same_day} time(s) on {day}.")
        elif existing_subject_day > 0:
            value = self._constraint_weight(SOFT_RULE_SPREAD_GROUP_LESSONS)
            penalty += value
            messages.append(f"{group.group_id} has another {group.subject} lesson on {day}.")

        adjacent = self._teacher_adjacent_count(teacher.teacher_id, day, period)
        if adjacent:
            value = self._constraint_weight(SOFT_RULE_CONSECUTIVE_TEACHER)
            penalty += value * adjacent
            messages.append(f"{teacher.teacher_id} has adjacent teaching around {day}-{period}.")

        average_day_load = max(1, self.teacher_week_load[teacher.teacher_id] // max(1, len(self.days)))
        if self.teacher_day_load[(teacher.teacher_id, day)] > average_day_load:
            value = self._constraint_weight(SOFT_RULE_BALANCE_TEACHER_LOAD)
            penalty += value
            messages.append(f"{teacher.teacher_id} load is heavier on {day}.")

        if is_option_block and self._period_index(period) >= len(self.periods) - 1:
            value = self._constraint_weight(SOFT_RULE_OPTION_END)
            penalty += value
            messages.append(f"Option block lesson was placed at the end of {day}.")

        if not self._preferred_room_type(group, room):
            value = self._constraint_weight(SOFT_RULE_ROOM_PREFERENCE)
            penalty += value
            messages.append(f"{room.room_id} is suitable but not the preferred room type for {group.subject}.")

        return penalty, messages

    def _constraint_weight(self, penalty_key: str) -> int:
        return effective_soft_weight(self.project.constraints, penalty_key)

    def _preferred_room_type(self, group: TeachingGroup, room: Room) -> bool:
        requirement = self.project.subject_room_requirements.get(group.subject)
        if requirement:
            return room.room_type == requirement.required_room_type
        subject = self.project.subjects.get(group.subject)
        if subject and subject.default_room_type:
            return room.room_type == subject.default_room_type
        return True

    def _max_same_subject_per_day(self, group: TeachingGroup) -> int:
        for pattern in self.project.lesson_patterns:
            if pattern.subject == group.subject and pattern.year_group == group.year_group:
                return max(1, pattern.max_same_subject_per_day)
        return 1

    def _teacher_adjacent_count(self, teacher_id: str, day: str, period: str) -> int:
        count = 0
        index = self._period_index(period)
        for adjacent_index in (index - 1, index + 1):
            if 0 <= adjacent_index < len(self.periods):
                adjacent_period = self.periods[adjacent_index]
                if (teacher_id, day, adjacent_period) in self.teacher_slot:
                    count += 1
        return count

    def _apply_fixed_events(self) -> None:
        for event in self.project.fixed_events:
            targets = resolve_fixed_event_targets(self.project, event)
            blocked_periods = fixed_event_periods(self.project, event)
            if event.day not in self.days or targets is None or not blocked_periods:
                continue

            for blocked_period in blocked_periods:
                for teacher_id in targets.teacher_ids:
                    self.fixed_teacher_slots.add((teacher_id, event.day, blocked_period))
                for room_id in targets.room_ids:
                    self.fixed_room_slots.add((room_id, event.day, blocked_period))
                for group_id in targets.group_ids:
                    self.fixed_group_slots.add((group_id, event.day, blocked_period))
                for year_group in targets.year_groups:
                    self.fixed_year_slots.add((year_group, event.day, blocked_period))

    def _explain_task_failure(self, task: Task) -> list[str]:
        reasons: Counter[str] = Counter()
        for group in task.groups:
            if not self._teacher_candidates(group):
                reasons["no qualified teacher"] += 1
            if not candidate_rooms(self.project, group):
                if group_requires_computers(self.project, group):
                    reasons["no suitable computer room with enough computers and capacity"] += 1
                else:
                    reasons["no suitable room with enough capacity and required type"] += 1

        for day, period in self.slots:
            for group in task.groups:
                if self._group_blocked(group, day, period):
                    reasons["group or year group blocked in available slots"] += 1
                    continue
                if not [
                    candidate
                    for candidate in self._teacher_candidates(group)
                    if self._teacher_available(candidate, group, day, period)
                ]:
                    reasons["no available teacher in remaining slots"] += 1
                if not [room for room in candidate_rooms(self.project, group) if self._room_available(room, day, period)]:
                    if group_requires_computers(self.project, group):
                        reasons["no available computer room in remaining slots"] += 1
                    else:
                        reasons["no available room in remaining slots"] += 1
        if not reasons:
            reasons["option block could not be placed with distinct teachers and rooms"] += 1
        return [reason for reason, _count in reasons.most_common(5)]

    def _record_unscheduled(self, task: Task, reasons: list[str]) -> None:
        labels = ", ".join(group.group_id for group in task.groups)
        subjects = ", ".join(sorted({group.subject for group in task.groups}))
        self.unscheduled.append(
            ConflictIssue(
                lesson_id=task.task_id,
                group_id=labels,
                subject=subjects,
                severity="fatal",
                category="unscheduled_lesson",
                message=f"Could not schedule {labels} ({subjects}).",
                reasons=reasons,
            )
        )

    def _constraint_report(self, status: str, score: int) -> list[dict[str, object]]:
        report: list[dict[str, object]] = [
            {
                "category": "solve_status",
                "severity": "info",
                "message": f"Solve finished with status {status}.",
                "score": score,
            }
        ]
        for issue in self.project.validation_issues:
            report.append(issue.model_dump())
        for issue in self.unscheduled + self.soft_penalties:
            report.append(issue.model_dump())
        return report

    def _sorted_assignments(self) -> list[LessonAssignment]:
        return sorted(
            self.assignments,
            key=lambda assignment: (
                self._day_index(assignment.day),
                self._period_index(assignment.period),
                assignment.group_id,
            ),
        )

    def _day_index(self, day: str) -> int:
        try:
            return self.days.index(day)
        except ValueError:
            return 99

    def _period_index(self, period: str) -> int:
        try:
            return self.periods.index(period)
        except ValueError:
            return 99


def solve_project(
    project: ProjectData,
    settings: SolveSettings,
    progress_callback: ProgressCallback | None = None,
) -> SolveResult:
    solver = HeuristicTimetableSolver(project, settings)
    result = solver.solve(progress_callback=progress_callback)
    return apply_verification(project, result)


def _slot_code(day: str, period: str) -> str:
    return f"{day}-{period}"
