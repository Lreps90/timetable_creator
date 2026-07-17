from __future__ import annotations

from collections import Counter
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.app.data.csv_loader import load_project_from_files, load_project_from_zip_bytes
from backend.app.exports.csv_exporter import build_export_zip
from backend.app.models.entities import SolveResult, SolveSettings, SolveStatus
from backend.app.models.project import ProjectData
from backend.app.solver.heuristic_solver import solve_project
from backend.app.state import (
    ProjectSolveAlreadyActiveError,
    ProjectSolveValidationError,
    claim_project_solve,
    complete_project_solve,
    fail_project_solve,
    get_project,
    mark_project_solve_running,
    reset_project,
    store_project,
    update_project_solve_progress,
)


router = APIRouter(prefix="/api")


@router.post("/projects/upload")
async def upload_project(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one CSV file or a ZIP file.")

    project_id = str(uuid4())
    zip_file = next((file for file in files if (file.filename or "").lower().endswith(".zip")), None)
    if zip_file is not None:
        content = await zip_file.read()
        source_name = Path(zip_file.filename or "uploaded.zip").stem
        project = load_project_from_zip_bytes(content, project_id=project_id, source_scenario=source_name)
    else:
        raw_files: dict[str, bytes] = {}
        for file in files:
            filename = Path(file.filename or "").name
            if filename:
                raw_files[filename] = await file.read()
        project = load_project_from_files(raw_files, project_id=project_id, source_scenario="uploaded_files")

    store_project(project)
    return _project_payload(project)


@router.get("/projects/{project_id}/validation")
def validation(project_id: str) -> dict:
    project = _project_or_404(project_id)
    return {
        "project_id": project.project_id,
        "issues": [issue.model_dump() for issue in project.validation_issues],
        "summary": _validation_summary(project),
        "can_solve": not project.fatal_validation_issues,
    }


@router.post("/projects/{project_id}/solve")
def start_solve(project_id: str, settings: SolveSettings, background_tasks: BackgroundTasks) -> SolveStatus:
    try:
        project = claim_project_solve(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except ProjectSolveValidationError as exc:
        raise HTTPException(status_code=400, detail="Fix fatal validation errors before solving.")
    except ProjectSolveAlreadyActiveError as exc:
        raise HTTPException(
            status_code=409,
            detail="A timetable solve is already queued or running for this project.",
        ) from exc

    background_tasks.add_task(_run_solver, project_id, settings)
    return project.solve_status


@router.get("/projects/{project_id}/solve-status")
def solve_status(project_id: str) -> SolveStatus:
    project = _project_or_404(project_id)
    return project.solve_status


@router.get("/projects/{project_id}/timetable")
def timetable(project_id: str, view: str = "group", id: str | None = None) -> dict:
    project = _project_or_404(project_id)
    if project.solve_result is None:
        raise HTTPException(status_code=400, detail="Solve the project before requesting timetables.")

    assignments = project.solve_result.assignments
    options = _view_options(project, view)
    selected_id = id or (options[0]["id"] if options else "")
    filtered = [assignment for assignment in assignments if _matches_view(assignment, view, selected_id)]
    cells: dict[str, list[dict]] = {}
    for assignment in filtered:
        key = f"{assignment.day}-{assignment.period}"
        cells.setdefault(key, []).append(assignment.model_dump())

    return {
        "project_id": project.project_id,
        "view": view,
        "selected_id": selected_id,
        "days": project.school.days,
        "periods": project.school.periods,
        "options": options,
        "assignments": [assignment.model_dump() for assignment in filtered],
        "cells": cells,
    }


@router.get("/projects/{project_id}/conflicts")
def conflicts(project_id: str) -> dict:
    project = _project_or_404(project_id)
    result = project.solve_result
    return {
        "project_id": project.project_id,
        "validation_fatals": [issue.model_dump() for issue in project.validation_issues if issue.severity == "fatal"],
        "validation_warnings": [issue.model_dump() for issue in project.validation_issues if issue.severity == "warning"],
        "unscheduled_lessons": [issue.model_dump() for issue in result.unscheduled_lessons] if result else [],
        "broken_hard_constraints": [issue.model_dump() for issue in result.broken_hard_constraints] if result else [],
        "soft_penalties": [issue.model_dump() for issue in result.soft_penalties] if result else [],
    }


@router.get("/projects/{project_id}/summary")
def summary(project_id: str) -> dict:
    project = _project_or_404(project_id)
    return build_summary(project)


@router.get("/projects/{project_id}/export")
def export(project_id: str) -> StreamingResponse:
    project = _project_or_404(project_id)
    result = project.solve_result
    if result is None:
        raise HTTPException(status_code=400, detail="Solve the project before exporting.")
    _ensure_result_is_exportable(result, project.solve_status)
    zip_buffer = build_export_zip(project)
    filename = f"{project.source_scenario or project.project_id}_timetable_exports.zip"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ensure_result_is_exportable(result: SolveResult, solve_status: SolveStatus) -> None:
    reasons: list[str] = []
    if solve_status.status != "feasible":
        reasons.append(f"the recorded solve status is {solve_status.status}")
    if result.status != "feasible":
        reasons.append(f"the solve result is {result.status}")
    if result.unscheduled_lessons:
        count = len(result.unscheduled_lessons)
        reasons.append(f"{count} lesson{' remains' if count == 1 else 's remain'} unscheduled")
    if result.broken_hard_constraints:
        count = len(result.broken_hard_constraints)
        reasons.append(f"{count} verified hard-constraint violation{' remains' if count == 1 else 's remain'}")

    if reasons:
        raise HTTPException(
            status_code=409,
            detail=(
                "The timetable cannot be exported because "
                f"{' and '.join(reasons)}. Review the conflict report before exporting."
            ),
        )


@router.delete("/projects/{project_id}")
def reset(project_id: str) -> dict:
    reset_project(project_id)
    return {"status": "reset", "project_id": project_id}


def _run_solver(project_id: str, settings: SolveSettings) -> None:
    try:
        project = get_project(project_id)
        mark_project_solve_running(project_id)

        def progress(progress_value: float, messages: list[str]) -> None:
            update_project_solve_progress(project_id, progress_value, messages)

        result = solve_project(project, settings, progress_callback=progress)
    except Exception as exc:  # pragma: no cover - defensive status path
        fail_project_solve(project_id, str(exc))
        raise

    complete_project_solve(project_id, result)


def build_summary(project: ProjectData) -> dict:
    result = project.solve_result
    assignments = result.assignments if result else []
    teacher_day_counts: Counter[tuple[str, str]] = Counter((item.teacher_id, item.day) for item in assignments)
    room_counts: Counter[str] = Counter(item.room_id for item in assignments)
    warning_count = len([issue for issue in project.validation_issues if issue.severity == "warning"])
    fatal_count = len([issue for issue in project.validation_issues if issue.severity == "fatal"])

    teacher_load = []
    for teacher in sorted(project.teachers.values(), key=lambda item: item.teacher_id):
        by_day = {day: teacher_day_counts[(teacher.teacher_id, day)] for day in project.school.days}
        teacher_load.append({
            "teacher_id": teacher.teacher_id,
            "teacher_name": teacher.name,
            "department": teacher.department,
            "total_lessons": sum(by_day.values()),
            "max_lessons_per_week": teacher.max_lessons_per_week,
            "max_lessons_per_day": teacher.max_lessons_per_day,
            "by_day": by_day,
        })

    room_utilisation = []
    for room in sorted(project.rooms.values(), key=lambda item: item.room_id):
        available_days = room.available_days or project.school.days
        available_slots = len(available_days) * len(project.school.periods)
        scheduled = room_counts[room.room_id]
        room_utilisation.append({
            "room_id": room.room_id,
            "room_name": room.room_name,
            "room_type": room.room_type,
            "capacity": room.capacity,
            "has_computers": room.has_computers,
            "computer_count": room.computer_count,
            "scheduled_lessons": scheduled,
            "available_slots": available_slots,
            "utilisation_percent": round((scheduled / available_slots) * 100, 1) if available_slots else 0.0,
        })

    return {
        "project_id": project.project_id,
        "source_scenario": project.source_scenario,
        "solve_status": project.solve_status.status,
        "optimisation_score": result.score if result else None,
        "soft_penalty_total": result.total_penalty if result else 0,
        "teacher_load": teacher_load,
        "room_utilisation": room_utilisation,
        "scheduled_lessons": len(assignments),
        "unscheduled_lessons": len(result.unscheduled_lessons) if result else 0,
        "warnings": warning_count,
        "fatal_errors": fatal_count,
    }


def _project_or_404(project_id: str) -> ProjectData:
    try:
        return get_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


def _project_payload(project: ProjectData) -> dict:
    return {
        "project_id": project.project_id,
        "source_scenario": project.source_scenario,
        "files_detected": project.files_detected,
        "validation": _validation_summary(project),
        "can_solve": not project.fatal_validation_issues,
    }


def _validation_summary(project: ProjectData) -> dict:
    counts = Counter(issue.severity for issue in project.validation_issues)
    by_category = Counter(issue.category for issue in project.validation_issues)
    return {
        "fatal": counts["fatal"],
        "warning": counts["warning"],
        "info": counts["info"],
        "by_category": dict(sorted(by_category.items())),
    }


def _matches_view(assignment, view: str, selected_id: str) -> bool:
    if not selected_id:
        return True
    if view == "group":
        return assignment.group_id == selected_id
    if view == "teacher":
        return assignment.teacher_id == selected_id
    if view == "room":
        return assignment.room_id == selected_id
    if view == "subject":
        return assignment.subject == selected_id
    return assignment.group_id == selected_id


def _view_options(project: ProjectData, view: str) -> list[dict[str, str]]:
    if view == "teacher":
        return [
            {"id": teacher.teacher_id, "label": f"{teacher.name} ({teacher.teacher_id})"}
            for teacher in sorted(project.teachers.values(), key=lambda item: item.name)
        ]
    if view == "room":
        return [
            {"id": room.room_id, "label": f"{room.room_name} ({room.room_id})"}
            for room in sorted(project.rooms.values(), key=lambda item: item.room_name)
        ]
    if view == "subject":
        return [{"id": subject.subject, "label": subject.subject} for subject in sorted(project.subjects.values(), key=lambda item: item.subject)]
    return [
        {"id": group.group_id, "label": f"{group.group_id} - {group.subject}"}
        for group in sorted(project.teaching_groups.values(), key=lambda item: item.group_id)
    ]
