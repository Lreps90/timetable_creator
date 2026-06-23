from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter, defaultdict

from backend.app.models.entities import LessonAssignment
from backend.app.models.project import ProjectData


EXPORT_FILENAMES = [
    "timetable_by_lesson.csv",
    "teacher_timetables.csv",
    "room_timetables.csv",
    "group_timetables.csv",
    "unscheduled_lessons.csv",
    "constraint_report.csv",
    "teacher_load_summary.csv",
    "room_utilisation_summary.csv",
]


def build_export_zip(project: ProjectData) -> io.BytesIO:
    if project.solve_result is None:
        raise ValueError("Project has not been solved yet.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("timetable_by_lesson.csv", _csv_text(_lesson_rows(project.solve_result.assignments), [
            "day",
            "period",
            "group_id",
            "year_group",
            "subject",
            "teacher_id",
            "teacher_name",
            "room_id",
            "room_name",
            "source_scenario",
        ]))
        archive.writestr("teacher_timetables.csv", _csv_text(_teacher_rows(project.solve_result.assignments), [
            "teacher_id",
            "teacher_name",
            "day",
            "period",
            "subject",
            "group_id",
            "room_id",
            "room_name",
        ]))
        archive.writestr("room_timetables.csv", _csv_text(_room_rows(project.solve_result.assignments), [
            "room_id",
            "room_name",
            "day",
            "period",
            "subject",
            "group_id",
            "teacher_id",
            "teacher_name",
        ]))
        archive.writestr("group_timetables.csv", _csv_text(_group_rows(project.solve_result.assignments), [
            "group_id",
            "year_group",
            "day",
            "period",
            "subject",
            "teacher_id",
            "teacher_name",
            "room_id",
            "room_name",
        ]))
        archive.writestr("unscheduled_lessons.csv", _csv_text([
            {
                "lesson_id": issue.lesson_id or "",
                "group_id": issue.group_id or "",
                "subject": issue.subject or "",
                "severity": issue.severity,
                "category": issue.category,
                "message": issue.message,
                "reasons": "|".join(issue.reasons),
            }
            for issue in project.solve_result.unscheduled_lessons
        ], ["lesson_id", "group_id", "subject", "severity", "category", "message", "reasons"]))
        archive.writestr("constraint_report.csv", _csv_text(_constraint_rows(project), [
            "severity",
            "category",
            "file",
            "field",
            "lesson_id",
            "group_id",
            "subject",
            "message",
            "reasons",
        ]))
        archive.writestr("teacher_load_summary.csv", _csv_text(_teacher_load_rows(project), [
            "teacher_id",
            "teacher_name",
            "total_lessons",
            "max_lessons_per_week",
            *project.school.days,
        ]))
        archive.writestr("room_utilisation_summary.csv", _csv_text(_room_utilisation_rows(project), [
            "room_id",
            "room_name",
            "room_type",
            "capacity",
            "has_computers",
            "computer_count",
            "scheduled_lessons",
            "available_slots",
            "utilisation_percent",
        ]))
    buffer.seek(0)
    return buffer


def _lesson_rows(assignments: list[LessonAssignment]) -> list[dict[str, object]]:
    return [
        {
            "day": item.day,
            "period": item.period,
            "group_id": item.group_id,
            "year_group": item.year_group,
            "subject": item.subject,
            "teacher_id": item.teacher_id,
            "teacher_name": item.teacher_name,
            "room_id": item.room_id,
            "room_name": item.room_name,
            "source_scenario": item.source_scenario,
        }
        for item in assignments
    ]


def _teacher_rows(assignments: list[LessonAssignment]) -> list[dict[str, object]]:
    return [
        {
            "teacher_id": item.teacher_id,
            "teacher_name": item.teacher_name,
            "day": item.day,
            "period": item.period,
            "subject": item.subject,
            "group_id": item.group_id,
            "room_id": item.room_id,
            "room_name": item.room_name,
        }
        for item in assignments
    ]


def _room_rows(assignments: list[LessonAssignment]) -> list[dict[str, object]]:
    return [
        {
            "room_id": item.room_id,
            "room_name": item.room_name,
            "day": item.day,
            "period": item.period,
            "subject": item.subject,
            "group_id": item.group_id,
            "teacher_id": item.teacher_id,
            "teacher_name": item.teacher_name,
        }
        for item in assignments
    ]


def _group_rows(assignments: list[LessonAssignment]) -> list[dict[str, object]]:
    return [
        {
            "group_id": item.group_id,
            "year_group": item.year_group,
            "day": item.day,
            "period": item.period,
            "subject": item.subject,
            "teacher_id": item.teacher_id,
            "teacher_name": item.teacher_name,
            "room_id": item.room_id,
            "room_name": item.room_name,
        }
        for item in assignments
    ]


def _constraint_rows(project: ProjectData) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if project.solve_result is None:
        return rows
    for issue in project.validation_issues:
        rows.append({
            "severity": issue.severity,
            "category": issue.category,
            "file": issue.file or "",
            "field": issue.field or "",
            "lesson_id": "",
            "group_id": "",
            "subject": "",
            "message": issue.message,
            "reasons": "",
        })
    for issue in project.solve_result.unscheduled_lessons + project.solve_result.broken_hard_constraints + project.solve_result.soft_penalties:
        rows.append({
            "severity": issue.severity,
            "category": issue.category,
            "file": "",
            "field": "",
            "lesson_id": issue.lesson_id or "",
            "group_id": issue.group_id or "",
            "subject": issue.subject or "",
            "message": issue.message,
            "reasons": "|".join(issue.reasons),
        })
    return rows


def _teacher_load_rows(project: ProjectData) -> list[dict[str, object]]:
    if project.solve_result is None:
        return []
    by_teacher_day: Counter[tuple[str, str]] = Counter()
    for assignment in project.solve_result.assignments:
        by_teacher_day[(assignment.teacher_id, assignment.day)] += 1

    rows: list[dict[str, object]] = []
    for teacher in sorted(project.teachers.values(), key=lambda item: item.teacher_id):
        row: dict[str, object] = {
            "teacher_id": teacher.teacher_id,
            "teacher_name": teacher.name,
            "total_lessons": sum(by_teacher_day[(teacher.teacher_id, day)] for day in project.school.days),
            "max_lessons_per_week": teacher.max_lessons_per_week,
        }
        for day in project.school.days:
            row[day] = by_teacher_day[(teacher.teacher_id, day)]
        rows.append(row)
    return rows


def _room_utilisation_rows(project: ProjectData) -> list[dict[str, object]]:
    if project.solve_result is None:
        return []
    room_counts: Counter[str] = Counter(assignment.room_id for assignment in project.solve_result.assignments)
    slots = len(project.school.days) * len(project.school.periods)
    rows: list[dict[str, object]] = []
    for room in sorted(project.rooms.values(), key=lambda item: item.room_id):
        available_slots = (len(room.available_days) if room.available_days else len(project.school.days)) * len(project.school.periods)
        available_slots = min(available_slots, slots)
        count = room_counts[room.room_id]
        utilisation = round((count / available_slots) * 100, 1) if available_slots else 0.0
        rows.append({
            "room_id": room.room_id,
            "room_name": room.room_name,
            "room_type": room.room_type,
            "capacity": room.capacity,
            "has_computers": room.has_computers,
            "computer_count": room.computer_count,
            "scheduled_lessons": count,
            "available_slots": available_slots,
            "utilisation_percent": utilisation,
        })
    return rows


def _csv_text(rows: list[dict[str, object]], fieldnames: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()
