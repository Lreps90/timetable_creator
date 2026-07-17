from __future__ import annotations

from threading import Lock

from .models.entities import SolveResult, SolveStatus
from .models.project import ProjectData


PROJECTS: dict[str, ProjectData] = {}
PROJECT_LOCK = Lock()


class ProjectSolveAlreadyActiveError(RuntimeError):
    pass


class ProjectSolveValidationError(RuntimeError):
    pass


def store_project(project: ProjectData) -> None:
    with PROJECT_LOCK:
        PROJECTS[project.project_id] = project


def get_project(project_id: str) -> ProjectData:
    with PROJECT_LOCK:
        project = PROJECTS.get(project_id)
    if project is None:
        raise KeyError(project_id)
    return project


def reset_project(project_id: str) -> None:
    with PROJECT_LOCK:
        PROJECTS.pop(project_id, None)


def claim_project_solve(project_id: str) -> ProjectData:
    with PROJECT_LOCK:
        project = _get_project_locked(project_id)
        if project.fatal_validation_issues:
            raise ProjectSolveValidationError(project_id)
        if project.solve_status.status in {"queued", "running"}:
            raise ProjectSolveAlreadyActiveError(project_id)
        project.solve_status = SolveStatus(status="queued", progress=0.0, messages=["Solve queued."])
        project.solve_result = None
        return project


def mark_project_solve_running(project_id: str) -> None:
    with PROJECT_LOCK:
        project = _get_project_locked(project_id)
        project.solve_status = SolveStatus(status="running", progress=0.01, messages=["Solving started."])


def update_project_solve_progress(project_id: str, progress: float, messages: list[str]) -> None:
    with PROJECT_LOCK:
        project = _get_project_locked(project_id)
        project.solve_status = SolveStatus(status="running", progress=progress, messages=messages)


def complete_project_solve(project_id: str, result: SolveResult) -> None:
    with PROJECT_LOCK:
        project = _get_project_locked(project_id)
        project.solve_result = result
        project.solve_status = SolveStatus(
            status=result.status,
            progress=1.0,
            score=result.score,
            messages=result.messages,
        )


def fail_project_solve(project_id: str, message: str) -> None:
    with PROJECT_LOCK:
        project = PROJECTS.get(project_id)
        if project is not None:
            project.solve_status = SolveStatus(status="failed", progress=1.0, messages=[message])


def _get_project_locked(project_id: str) -> ProjectData:
    project = PROJECTS.get(project_id)
    if project is None:
        raise KeyError(project_id)
    return project

