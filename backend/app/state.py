from __future__ import annotations

from threading import Lock

from .models.project import ProjectData


PROJECTS: dict[str, ProjectData] = {}
PROJECT_LOCK = Lock()


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

