from __future__ import annotations

import io
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from backend.app.api import routes as api_routes
from backend.app.constraint_policy import (
    CONSTRAINT_DEFINITIONS,
    SOFT_RULE_PREFERRED_TEACHER,
    SOFT_RULE_PRIORITY_TEACHER,
    SOFT_RULE_ROOM_PREFERENCE,
    effective_soft_weight,
)
from backend.app.data.csv_loader import EXPECTED_FILES, load_project_from_files, load_project_from_folder
from backend.app.exports.csv_exporter import EXPORT_FILENAMES, build_export_zip
from backend.app.main import app
from backend.app.models.entities import (
    ConflictIssue,
    LessonAssignment,
    Room,
    SolveResult,
    SolveSettings,
    SolveStatus,
    Subject,
    SubjectRoomRequirement,
    Teacher,
    TeacherSubject,
    TeachingGroup,
)
from backend.app.models.project import ProjectData
from backend.app.solver.heuristic_solver import HeuristicTimetableSolver, TeacherCandidate, solve_project
from backend.app.solver.verification import apply_verification, verify_solve_result
from backend.app.state import (
    ProjectSolveAlreadyActiveError,
    claim_project_solve,
    get_project,
    reset_project,
    store_project,
)
from backend.app.validators.project_validator import (
    candidate_rooms,
    fixed_event_periods,
    resolve_fixed_event_targets,
)


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_ROOT = ROOT / "sample_data"


def _minimal_scenario_files() -> dict[str, bytes]:
    scenario = SAMPLE_ROOT / "scenario_01_minimal_ks3_core"
    return {path.name: path.read_bytes() for path in scenario.iterdir() if path.is_file()}


def _project_with_fixed_event_rows(*rows: bytes) -> ProjectData:
    raw_files = _minimal_scenario_files()
    raw_files["fixed_events.csv"] = (
        b"event_id,event_name,event_type,applies_to,day,period,duration_periods,required_teacher_ids,required_room_ids,notes\n"
        + b"".join(rows)
    )
    return load_project_from_files(raw_files, project_id="fixed-event-test")


def _fatal_issues(project, category: str) -> list:
    return [
        issue
        for issue in project.validation_issues
        if issue.severity == "fatal" and issue.category == category
    ]


def _project_with_constraint_row(row: str) -> ProjectData:
    raw_files = _minimal_scenario_files()
    constraint_name = row.split(",", 1)[0]
    lines = raw_files["constraints.csv"].decode("utf-8").splitlines(keepends=True)
    for index, existing_row in enumerate(lines[1:], start=1):
        if existing_row.split(",", 1)[0] == constraint_name:
            lines[index] = row
            break
    else:
        lines.append(row)
    raw_files["constraints.csv"] = "".join(lines).encode("utf-8")
    return load_project_from_files(raw_files, project_id="constraint-policy-test")


def _assignment(
    project,
    group_id: str,
    teacher_id: str,
    room_id: str,
    day: str = "Tue",
    period: str = "P1",
    lesson_id: str = "L9001",
) -> LessonAssignment:
    group = project.teaching_groups[group_id]
    teacher = project.teachers[teacher_id]
    room = project.rooms[room_id]
    return LessonAssignment(
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
    )


def _extra_assignment_in_free_slot(
    project: ProjectData,
    assignments: list[LessonAssignment],
    group_id: str,
    lesson_id: str,
) -> LessonAssignment:
    template = next(item for item in assignments if item.group_id == group_id)
    teacher = project.teachers[template.teacher_id]
    room = project.rooms[template.room_id]
    group = project.teaching_groups[group_id]

    for day in project.school.days:
        for period in project.school.periods:
            if day not in teacher.working_days or f"{day}-{period}" in teacher.unavailable_periods:
                continue
            if room.available_days and day not in room.available_days:
                continue
            if f"{day}-{period}" in room.unavailable_periods:
                continue
            if any(
                item.day == day
                and item.period == period
                and (item.teacher_id == teacher.teacher_id or item.room_id == room.room_id or item.group_id == group_id)
                for item in assignments
            ):
                continue
            if any(
                day == event.day
                and period in fixed_event_periods(project, event)
                and (targets := resolve_fixed_event_targets(project, event)) is not None
                and (
                    teacher.teacher_id in targets.teacher_ids
                    or room.room_id in targets.room_ids
                    or group_id in targets.group_ids
                    or group.year_group in targets.year_groups
                )
                for event in project.fixed_events
            ):
                continue
            return template.model_copy(update={"lesson_id": lesson_id, "day": day, "period": period})
    raise AssertionError(f"No free slot found for fabricated assignment to {group_id}.")


def _computer_room_project(allow_general_room: bool, general_has_computers: bool = True) -> ProjectData:
    project = ProjectData(project_id="room-suitability-test")
    project.subjects = {
        "Computer Science": Subject(subject="Computer Science", default_room_type="Computing")
    }
    project.teachers = {
        "T1": Teacher(teacher_id="T1", name="Teacher One", working_days=["Mon"])
    }
    project.teacher_subjects = [TeacherSubject(teacher_id="T1", subject="Computer Science")]
    project.teaching_groups = {
        "10A_CS": TeachingGroup(
            group_id="10A_CS",
            year_group=10,
            subject="Computer Science",
            lessons_per_week=1,
            class_size=20,
        )
    }
    project.subject_room_requirements = {
        "Computer Science": SubjectRoomRequirement(
            subject="Computer Science",
            required_room_type="Computing",
            allow_general_room=allow_general_room,
        )
    }
    project.rooms = {
        "COMP1": Room(room_id="COMP1", room_name="Computing Room", room_type="Computing", capacity=30, has_computers=True, computer_count=30),
        "GEN1": Room(room_id="GEN1", room_name="General Room", room_type="General", capacity=30, has_computers=general_has_computers, computer_count=30 if general_has_computers else 0),
        "SCI1": Room(room_id="SCI1", room_name="Science Lab", room_type="Science", capacity=30, has_computers=True, computer_count=30),
    }
    return project


def _upload_csv_files(client: TestClient, raw_files: dict[str, bytes]) -> dict:
    response = client.post(
        "/api/projects/upload",
        files=[("files", (filename, content, "text/csv")) for filename, content in raw_files.items()],
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_terminal_status(client: TestClient, project_id: str) -> dict:
    for _ in range(20):
        response = client.get(f"/api/projects/{project_id}/solve-status")
        assert response.status_code == 200
        status = response.json()
        if status["status"] in {"feasible", "infeasible", "failed"}:
            return status
        time.sleep(0.05)
    raise AssertionError("Solve did not complete within the polling window.")


def _assert_invalid_project_cannot_solve(client: TestClient, raw_files: dict[str, bytes], category: str) -> None:
    upload = _upload_csv_files(client, raw_files)
    project_id = upload["project_id"]
    assert upload["can_solve"] is False

    validation = client.get(f"/api/projects/{project_id}/validation")
    assert validation.status_code == 200
    assert any(issue["severity"] == "fatal" and issue["category"] == category for issue in validation.json()["issues"])

    rejected = client.post(
        f"/api/projects/{project_id}/solve",
        json={"mode": "balanced", "time_limit_seconds": 30, "soft_constraints_enabled": True},
    )
    assert rejected.status_code == 400
    assert "fatal validation errors" in rejected.json()["detail"].lower()

    status = client.get(f"/api/projects/{project_id}/solve-status")
    assert status.status_code == 200
    assert status.json()["status"] == "not_started"

    conflicts = client.get(f"/api/projects/{project_id}/conflicts")
    assert conflicts.status_code == 200
    assert any(issue["category"] == category for issue in conflicts.json()["validation_fatals"])

    summary = client.get(f"/api/projects/{project_id}/summary")
    assert summary.status_code == 200
    assert summary.json()["solve_status"] == "not_started"


def test_all_sample_scenarios_detect_expected_files_and_validate_without_fatals() -> None:
    scenarios = sorted(path for path in SAMPLE_ROOT.iterdir() if path.is_dir())
    assert len(scenarios) == 5

    for scenario in scenarios:
        project = load_project_from_folder(scenario)
        assert set(EXPECTED_FILES).issubset(set(project.files_detected))
        assert project.fatal_validation_issues == [], scenario.name


def test_all_sample_scenarios_solve_feasibly() -> None:
    for scenario in sorted(path for path in SAMPLE_ROOT.iterdir() if path.is_dir()):
        project = load_project_from_folder(scenario)
        result = solve_project(project, SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True))

        assert result.status == "feasible", scenario.name
        assert result.unscheduled_lessons == [], scenario.name
        assert result.broken_hard_constraints == [], scenario.name


def test_all_supplied_constraints_use_exact_known_definitions() -> None:
    for scenario in sorted(path for path in SAMPLE_ROOT.iterdir() if path.is_dir()):
        project = load_project_from_folder(scenario)
        for constraint in project.constraints.values():
            definition = CONSTRAINT_DEFINITIONS.get(constraint.constraint_name)
            assert definition is not None, (scenario.name, constraint.constraint_name)
            assert definition.constraint_type == constraint.constraint_type


@pytest.mark.parametrize("enabled", ["TRUE", "FALSE"])
def test_unknown_constraint_rows_are_fatal_even_when_disabled(enabled: str) -> None:
    project = _project_with_constraint_row(
        f"preferred_teacher_extra,SOFT,1,{enabled},This row must not silently do nothing\n"
    )

    issues = _fatal_issues(project, "unsupported_constraint")
    assert len(issues) == 1
    assert issues[0].file == "constraints.csv"
    assert issues[0].row == 10
    assert "preferred_teacher_extra" in issues[0].message


def test_mandatory_hard_constraint_cannot_be_disabled() -> None:
    project = _project_with_constraint_row("teacher_double_booking,HARD,1,FALSE,Always enforced\n")

    issues = _fatal_issues(project, "mandatory_constraint")
    assert len(issues) == 1
    assert issues[0].row == 10
    assert "teacher_double_booking" in issues[0].message


@pytest.mark.parametrize(
    ("row", "expected_category", "expected_row"),
    [
        ("teacher_double_booking,SOFT,1,TRUE,Wrong type\n", "constraint_type", 10),
        ("preferred_teacher,HARD,1,TRUE,Wrong type\n", "constraint_type", 5),
        ("preferred_teacher,SOFT,-1,TRUE,Negative weight\n", "invalid_constraint_weight", 5),
    ],
)
def test_constraint_type_and_soft_weight_rules_are_fatal(
    row: str, expected_category: str, expected_row: int
) -> None:
    project = _project_with_constraint_row(row)

    issues = _fatal_issues(project, expected_category)
    assert len(issues) == 1
    assert issues[0].row == expected_row


def test_disabled_soft_constraint_has_zero_effective_weight() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    project.constraints["preferred_teacher"].enabled = False

    assert effective_soft_weight(project.constraints, SOFT_RULE_PREFERRED_TEACHER) == 0
    assert effective_soft_weight(project.constraints, SOFT_RULE_PRIORITY_TEACHER) == 2


def test_exact_soft_weight_changes_only_its_mapped_penalty() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    group = project.teaching_groups["7A_ENG"]
    group.preferred_teacher = "T002"
    candidate = TeacherCandidate(project.teachers["T001"], priority=1, explicit_override=False, subject_max=None)
    room = project.rooms["G101"]
    settings = SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True)

    initial_penalty, _messages = HeuristicTimetableSolver(project, settings)._soft_penalty(
        group, candidate, room, "Mon", "P1", False
    )
    project.constraints["preferred_teacher"].weight = 9
    changed_penalty, _messages = HeuristicTimetableSolver(project, settings)._soft_penalty(
        group, candidate, room, "Mon", "P1", False
    )

    assert initial_penalty == 4
    assert changed_penalty == 9
    assert effective_soft_weight(project.constraints, SOFT_RULE_PRIORITY_TEACHER) == 2


def test_description_keywords_and_missing_rows_do_not_change_soft_rule_mapping() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    project.constraints.pop("preferred_teacher")
    project.constraints["spread_group_lessons"].description = "preferred_teacher priority emergency room_preference"

    assert effective_soft_weight(project.constraints, SOFT_RULE_PREFERRED_TEACHER) == 3
    assert effective_soft_weight(project.constraints, SOFT_RULE_ROOM_PREFERENCE) == 1


def test_invalid_references_are_caught() -> None:
    raw_files = _minimal_scenario_files()
    text = raw_files["teaching_groups.csv"].decode("utf-8")
    text = text.replace("7A_HIS,7,History", "7A_HIS,7,Dance", 1)
    raw_files["teaching_groups.csv"] = text.encode("utf-8")

    project = load_project_from_files(raw_files, project_id="invalid-reference-test")
    fatals = [issue for issue in project.validation_issues if issue.severity == "fatal"]

    assert any(issue.category == "invalid_reference" and "Dance" in issue.message for issue in fatals)


def test_curriculum_row_without_matching_groups_is_fatal() -> None:
    raw_files = _minimal_scenario_files()
    groups = raw_files["teaching_groups.csv"].decode("utf-8")
    groups = "\n".join(line for line in groups.splitlines() if ",History," not in line) + "\n"
    raw_files["teaching_groups.csv"] = groups.encode("utf-8")

    project = load_project_from_files(raw_files, project_id="curriculum-without-groups")
    issues = _fatal_issues(project, "curriculum_mismatch")

    assert any(
        issue.file == "curriculum.csv"
        and issue.field == "lessons_per_week"
        and "Year 7 History" in issue.message
        and "1 lessons per week" in issue.message
        for issue in issues
    )


def test_teaching_group_without_curriculum_row_is_fatal() -> None:
    raw_files = _minimal_scenario_files()
    curriculum = raw_files["curriculum.csv"].decode("utf-8")
    curriculum = "\n".join(line for line in curriculum.splitlines() if line != "7,History,1,") + "\n"
    raw_files["curriculum.csv"] = curriculum.encode("utf-8")

    project = load_project_from_files(raw_files, project_id="groups-without-curriculum")
    issues = _fatal_issues(project, "curriculum_mismatch")

    assert any(
        issue.file == "teaching_groups.csv"
        and issue.field == "subject"
        and "Year 7 History" in issue.message
        and "7A_HIS" in issue.message
        for issue in issues
    )


def test_curriculum_and_group_lesson_count_mismatch_is_fatal_with_details() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["curriculum.csv"] = raw_files["curriculum.csv"].replace(b"7,English,2,", b"7,English,4,", 1)

    project = load_project_from_files(raw_files, project_id="curriculum-lesson-mismatch")
    issues = _fatal_issues(project, "curriculum_mismatch")

    assert any(
        issue.file == "teaching_groups.csv"
        and issue.field == "lessons_per_week"
        and issue.row is not None
        and "Year 7 English" in issue.message
        and "4 lessons per week" in issue.message
        and "requests 2" in issue.message
        for issue in issues
    )


def test_multiple_matching_groups_with_the_curriculum_lesson_count_are_allowed() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")

    matching_groups = [
        group
        for group in project.teaching_groups.values()
        if group.year_group == 7 and group.subject == "English"
    ]

    assert len(matching_groups) == 2
    assert not _fatal_issues(project, "curriculum_mismatch")


def test_duplicate_curriculum_year_and_subject_is_fatal() -> None:
    raw_files = _minimal_scenario_files()
    curriculum = raw_files["curriculum.csv"].decode("utf-8")
    lines = curriculum.splitlines()
    lines.insert(lines.index("7,English,2,") + 1, "7,English,2,")
    raw_files["curriculum.csv"] = ("\n".join(lines) + "\n").encode("utf-8")

    project = load_project_from_files(raw_files, project_id="duplicate-curriculum")
    issues = _fatal_issues(project, "curriculum_mismatch")

    assert any(
        issue.file == "curriculum.csv"
        and issue.field == "subject"
        and issue.row is not None
        and "Duplicate curriculum row" in issue.message
        and "Year 7 English" in issue.message
        for issue in issues
    )


def test_duplicate_teacher_id_is_fatal_and_keeps_first_row() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["teachers.csv"] = raw_files["teachers.csv"].replace(
        b"T002,Ben Patel", b"T001,Ben Patel", 1
    )

    project = load_project_from_files(raw_files, project_id="duplicate-teacher")
    issues = _fatal_issues(project, "duplicate_identifier")

    assert any(
        issue.file == "teachers.csv"
        and issue.field == "teacher_id"
        and issue.row == 3
        and "T001" in issue.message
        and "row 2" in issue.message
        for issue in issues
    )
    assert project.teachers["T001"].name == "Alice Green"


def test_duplicate_group_id_is_fatal() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["teaching_groups.csv"] = raw_files["teaching_groups.csv"].replace(
        b"7B_HIS,7,History", b"7A_HIS,7,History", 1
    )

    project = load_project_from_files(raw_files, project_id="duplicate-group")

    assert any(
        issue.file == "teaching_groups.csv" and issue.field == "group_id" and issue.row == 9
        for issue in _fatal_issues(project, "duplicate_identifier")
    )


def test_duplicate_room_id_is_fatal() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["rooms.csv"] = raw_files["rooms.csv"].replace(
        b"G102,Room G102", b"G101,Room G102", 1
    )

    project = load_project_from_files(raw_files, project_id="duplicate-room")

    assert any(
        issue.file == "rooms.csv" and issue.field == "room_id" and issue.row == 3
        for issue in _fatal_issues(project, "duplicate_identifier")
    )


def test_malformed_required_integer_is_fatal_with_row_and_field() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["rooms.csv"] = raw_files["rooms.csv"].replace(
        b"G101,Room G101,General,32", b"G101,Room G101,General,abc", 1
    )

    project = load_project_from_files(raw_files, project_id="invalid-required-integer")

    assert any(
        issue.file == "rooms.csv"
        and issue.field == "capacity"
        and issue.row == 2
        and "abc" in issue.message
        for issue in _fatal_issues(project, "invalid_primitive")
    )


def test_malformed_optional_integer_is_fatal_with_row_and_field() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["teacher_subjects.csv"] = raw_files["teacher_subjects.csv"].replace(
        b"T001,English,1,,7|8|9|10|11", b"T001,English,1,abc,7|8|9|10|11", 1
    )

    project = load_project_from_files(raw_files, project_id="invalid-optional-integer")

    assert any(
        issue.file == "teacher_subjects.csv"
        and issue.field == "max_lessons_in_subject"
        and issue.row == 2
        and "abc" in issue.message
        for issue in _fatal_issues(project, "invalid_primitive")
    )


def test_malformed_boolean_is_fatal_with_row_and_field() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["subjects.csv"] = raw_files["subjects.csv"].replace(
        b"English,English,TRUE,General", b"English,English,maybe,General", 1
    )

    project = load_project_from_files(raw_files, project_id="invalid-boolean")

    assert any(
        issue.file == "subjects.csv"
        and issue.field == "is_core"
        and issue.row == 2
        and "maybe" in issue.message
        for issue in _fatal_issues(project, "invalid_primitive")
    )


def test_simultaneous_computing_block_requires_enough_distinct_pc_rooms() -> None:
    raw_files = {
        "school_structure.csv": b"key,value\n"
        b"days_per_week,5\n"
        b"periods_per_day,5\n"
        b"cycle_weeks,1\n"
        b"days,Mon|Tue|Wed|Thu|Fri\n"
        b"periods,P1|P2|P3|P4|P5\n",
        "teachers.csv": b"teacher_id,name,role,department,working_days,max_lessons_per_week,max_lessons_per_day,unavailable_periods,notes\n"
        b"T001,Comp One,Teacher,Computing,Mon|Tue|Wed|Thu|Fri,10,5,,\n"
        b"T002,Comp Two,Teacher,Computing,Mon|Tue|Wed|Thu|Fri,10,5,,\n"
        b"T003,Comp Three,Teacher,Computing,Mon|Tue|Wed|Thu|Fri,10,5,,\n",
        "teacher_subjects.csv": b"teacher_id,subject,priority,max_lessons_in_subject,can_teach_years\n"
        b"T001,Computer Science,1,,10\n"
        b"T002,Computer Science,1,,10\n"
        b"T003,Computer Science,1,,10\n",
        "subjects.csv": b"subject,department,is_core,default_room_type\nComputer Science,Computing,FALSE,Computing\n",
        "curriculum.csv": b"year_group,subject,lessons_per_week,notes\n10,Computer Science,2,\n",
        "teaching_groups.csv": b"group_id,year_group,subject,lessons_per_week,class_size,group_type,option_block,allowed_teachers,preferred_teacher,notes\n"
        b"10A_CS1,10,Computer Science,2,20,option,A,,,\n"
        b"10A_CS2,10,Computer Science,2,20,option,A,,,\n"
        b"10A_CS3,10,Computer Science,2,20,option,A,,,\n",
        "rooms.csv": b"room_id,room_name,room_type,capacity,has_computers,computer_count,available_days,unavailable_periods,notes\n"
        b"COMP1,Computing Room 1,Computing,30,TRUE,30,Mon|Tue|Wed|Thu|Fri,,\n"
        b"COMP2,Computing Room 2,Computing,30,TRUE,30,Mon|Tue|Wed|Thu|Fri,,\n"
        b"SCI1,Science Lab,Science,30,TRUE,30,Mon|Tue|Wed|Thu|Fri,,\n",
        "subject_room_requirements.csv": b"subject,required_room_type,allow_general_room,notes\nComputer Science,Computing,TRUE,\n",
        "option_blocks.csv": b"year_group,block,group_id,subject,simultaneous_required,notes\n"
        b"10,A,10A_CS1,Computer Science,TRUE,\n"
        b"10,A,10A_CS2,Computer Science,TRUE,\n"
        b"10,A,10A_CS3,Computer Science,TRUE,\n",
        "fixed_events.csv": b"event_id,event_name,event_type,applies_to,day,period,duration_periods,required_teacher_ids,required_room_ids,notes\n",
        "lesson_patterns.csv": b"subject,year_group,lessons_per_week,allowed_patterns,double_lessons_allowed,max_same_subject_per_day,notes\n"
        b"Computer Science,10,2,1+1,FALSE,1,\n",
        "constraints.csv": b"constraint_name,constraint_type,weight,enabled,description\n",
    }

    project = load_project_from_files(raw_files, project_id="pc-room-shortage")
    fatals = [issue for issue in project.validation_issues if issue.severity == "fatal"]

    assert any(issue.category == "option_block_rooming" for issue in fatals)
    assert any("computer_count" in issue.message for issue in fatals)


def test_candidate_rooms_allow_only_required_type_or_general_fallback() -> None:
    fallback_project = _computer_room_project(allow_general_room=True)
    group = fallback_project.teaching_groups["10A_CS"]

    assert {room.room_id for room in candidate_rooms(fallback_project, group)} == {"COMP1", "GEN1"}

    specialist_only_project = _computer_room_project(allow_general_room=False)
    group = specialist_only_project.teaching_groups["10A_CS"]
    assert {room.room_id for room in candidate_rooms(specialist_only_project, group)} == {"COMP1"}


def test_general_computer_room_fallback_requires_sufficient_equipment() -> None:
    project = _computer_room_project(allow_general_room=True, general_has_computers=False)
    group = project.teaching_groups["10A_CS"]

    assert {room.room_id for room in candidate_rooms(project, group)} == {"COMP1"}


def test_verifier_rejects_unrelated_specialist_room_with_general_fallback() -> None:
    project = _computer_room_project(allow_general_room=True)
    group = project.teaching_groups["10A_CS"]
    teacher = project.teachers["T1"]
    room = project.rooms["SCI1"]
    assignment = LessonAssignment(
        lesson_id="L1",
        day="Mon",
        period="P1",
        group_id=group.group_id,
        year_group=group.year_group,
        subject=group.subject,
        teacher_id=teacher.teacher_id,
        teacher_name=teacher.name,
        room_id=room.room_id,
        room_name=room.room_name,
    )

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))
    assert any(issue.category == "room_suitability" for issue in issues)


def test_web_upload_route_accepts_zip() -> None:
    scenario = SAMPLE_ROOT / "scenario_01_minimal_ks3_core"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in scenario.iterdir():
            if path.is_file():
                archive.write(path, arcname=f"{scenario.name}/{path.name}")
    buffer.seek(0)

    client = TestClient(app)
    response = client.post(
        "/api/projects/upload",
        files={"files": ("scenario.zip", buffer.getvalue(), "application/zip")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_solve"] is True
    assert set(EXPECTED_FILES).issubset(set(payload["files_detected"]))


def test_valid_project_completes_the_api_upload_validation_solve_and_export_flow() -> None:
    client = TestClient(app)
    upload = _upload_csv_files(client, _minimal_scenario_files())
    project_id = upload["project_id"]
    assert upload["can_solve"] is True

    validation = client.get(f"/api/projects/{project_id}/validation")
    assert validation.status_code == 200
    assert validation.json()["summary"]["fatal"] == 0
    assert validation.json()["can_solve"] is True

    solve = client.post(
        f"/api/projects/{project_id}/solve",
        json={"mode": "deep", "time_limit_seconds": 30, "soft_constraints_enabled": True},
    )
    assert solve.status_code == 200
    status = _wait_for_terminal_status(client, project_id)
    assert status["status"] == "feasible"

    summary = client.get(f"/api/projects/{project_id}/summary")
    assert summary.status_code == 200
    assert summary.json()["solve_status"] == "feasible"
    assert summary.json()["scheduled_lessons"] > 0

    conflicts = client.get(f"/api/projects/{project_id}/conflicts")
    assert conflicts.status_code == 200
    assert conflicts.json()["validation_fatals"] == []
    assert conflicts.json()["unscheduled_lessons"] == []
    assert conflicts.json()["broken_hard_constraints"] == []

    export = client.get(f"/api/projects/{project_id}/export")
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        assert set(EXPORT_FILENAMES).issubset(set(archive.namelist()))


def test_api_blocks_export_for_infeasible_result_with_unscheduled_lessons() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["teachers.csv"] = raw_files["teachers.csv"].replace(
        b"Mon|Tue|Wed|Thu|Fri", b"Mon", 4
    )
    raw_files["fixed_events.csv"] += (
        b"E_BLOCK,Whole staff briefing,meeting,ALL_STAFF,Mon,P1,5,,,\n"
    )

    client = TestClient(app)
    project_id = _upload_csv_files(client, raw_files)["project_id"]
    solve = client.post(
        f"/api/projects/{project_id}/solve",
        json={"mode": "balanced", "time_limit_seconds": 30, "soft_constraints_enabled": True},
    )
    assert solve.status_code == 200
    assert _wait_for_terminal_status(client, project_id)["status"] == "infeasible"

    conflicts = client.get(f"/api/projects/{project_id}/conflicts")
    assert conflicts.status_code == 200
    assert conflicts.json()["unscheduled_lessons"]

    export = client.get(f"/api/projects/{project_id}/export")
    assert export.status_code == 409
    assert "infeasible" in export.json()["detail"].lower()
    assert "unscheduled" in export.json()["detail"].lower()


def test_api_blocks_export_for_verified_hard_constraint_violation() -> None:
    client = TestClient(app)
    project_id = _upload_csv_files(client, _minimal_scenario_files())["project_id"]
    project = get_project(project_id)
    result = apply_verification(
        project,
        SolveResult(
            status="feasible",
            assignments=[
                _assignment(project, "7A_ENG", "T001", "G101", lesson_id="L1"),
                _assignment(project, "7B_ENG", "T001", "G102", lesson_id="L2"),
            ],
        ),
    )
    project.solve_result = result
    project.solve_status = SolveStatus(status=result.status, progress=1.0)

    conflicts = client.get(f"/api/projects/{project_id}/conflicts")
    assert conflicts.status_code == 200
    assert any(issue["category"] == "teacher_double_booking" for issue in conflicts.json()["broken_hard_constraints"])

    export = client.get(f"/api/projects/{project_id}/export")
    assert export.status_code == 409
    assert "hard-constraint" in export.json()["detail"].lower()


def test_api_blocks_export_when_feasible_result_has_unscheduled_lessons() -> None:
    client = TestClient(app)
    project_id = _upload_csv_files(client, _minimal_scenario_files())["project_id"]
    project = get_project(project_id)
    project.solve_result = SolveResult(
        status="feasible",
        unscheduled_lessons=[
            ConflictIssue(category="unscheduled_lesson", message="Controlled incomplete result.")
        ],
    )
    project.solve_status = SolveStatus(status="feasible", progress=1.0)

    export = client.get(f"/api/projects/{project_id}/export")
    assert export.status_code == 409
    assert "unscheduled" in export.json()["detail"].lower()


def test_api_blocks_export_for_unsolved_project() -> None:
    client = TestClient(app)
    project_id = _upload_csv_files(client, _minimal_scenario_files())["project_id"]

    export = client.get(f"/api/projects/{project_id}/export")
    assert export.status_code == 400
    assert "solve the project" in export.json()["detail"].lower()


def test_claim_project_solve_is_atomic_for_active_project() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core", project_id="atomic-claim")
    store_project(project)
    try:
        claimed = claim_project_solve(project.project_id)
        assert claimed.solve_status.status == "queued"
        assert claimed.solve_result is None

        with pytest.raises(ProjectSolveAlreadyActiveError):
            claim_project_solve(project.project_id)

        assert get_project(project.project_id).solve_status.status == "queued"
    finally:
        reset_project(project.project_id)


def test_api_rejects_duplicate_solve_without_starting_second_background_task(monkeypatch) -> None:
    client = TestClient(app)
    project_id = _upload_csv_files(client, _minimal_scenario_files())["project_id"]
    started = Event()
    release = Event()
    invocation_count = 0

    def controlled_solve(*_args, **_kwargs) -> SolveResult:
        nonlocal invocation_count
        invocation_count += 1
        started.set()
        assert release.wait(timeout=2)
        return SolveResult(status="feasible", messages=["Controlled solve completed."])

    monkeypatch.setattr(api_routes, "solve_project", controlled_solve)
    first_response: list = []

    def start_first_solve() -> None:
        first_response.append(
            TestClient(app).post(
                f"/api/projects/{project_id}/solve",
                json={"mode": "balanced", "time_limit_seconds": 30, "soft_constraints_enabled": True},
            )
        )

    thread = Thread(target=start_first_solve)
    thread.start()
    try:
        assert started.wait(timeout=2)
        second = client.post(
            f"/api/projects/{project_id}/solve",
            json={"mode": "balanced", "time_limit_seconds": 30, "soft_constraints_enabled": True},
        )
        assert second.status_code == 409
        assert "already queued or running" in second.json()["detail"].lower()
        assert invocation_count == 1
        assert get_project(project_id).solve_status.status == "running"
    finally:
        release.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert first_response[0].status_code == 200
    assert get_project(project_id).solve_status.status == "feasible"


def test_completed_project_can_be_solved_again(monkeypatch) -> None:
    client = TestClient(app)
    project_id = _upload_csv_files(client, _minimal_scenario_files())["project_id"]
    invocation_count = 0

    def controlled_solve(*_args, **_kwargs) -> SolveResult:
        nonlocal invocation_count
        invocation_count += 1
        return SolveResult(status="feasible", messages=[f"Run {invocation_count} completed."])

    monkeypatch.setattr(api_routes, "solve_project", controlled_solve)
    settings = {"mode": "balanced", "time_limit_seconds": 30, "soft_constraints_enabled": True}

    assert client.post(f"/api/projects/{project_id}/solve", json=settings).status_code == 200
    assert _wait_for_terminal_status(client, project_id)["status"] == "feasible"
    assert client.post(f"/api/projects/{project_id}/solve", json=settings).status_code == 200
    assert _wait_for_terminal_status(client, project_id)["status"] == "feasible"
    assert invocation_count == 2


def test_failed_project_can_be_retried(monkeypatch) -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core", project_id="failed-retry")
    store_project(project)
    try:
        claim_project_solve(project.project_id)

        def failing_solve(*_args, **_kwargs) -> SolveResult:
            raise RuntimeError("Controlled solver failure.")

        monkeypatch.setattr(api_routes, "solve_project", failing_solve)
        with pytest.raises(RuntimeError, match="Controlled solver failure"):
            api_routes._run_solver(project.project_id, SolveSettings())

        assert get_project(project.project_id).solve_status.status == "failed"
        assert get_project(project.project_id).solve_status.messages == ["Controlled solver failure."]

        tasks = BackgroundTasks()
        accepted = api_routes.start_solve(project.project_id, SolveSettings(), tasks)
        assert accepted.status == "queued"
        assert len(tasks.tasks) == 1
    finally:
        reset_project(project.project_id)


def test_active_solves_are_independent_per_project() -> None:
    first = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core", project_id="independent-a")
    second = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core", project_id="independent-b")
    store_project(first)
    store_project(second)
    try:
        first_tasks = BackgroundTasks()
        second_tasks = BackgroundTasks()

        assert api_routes.start_solve(first.project_id, SolveSettings(), first_tasks).status == "queued"
        assert api_routes.start_solve(second.project_id, SolveSettings(), second_tasks).status == "queued"
        assert len(first_tasks.tasks) == 1
        assert len(second_tasks.tasks) == 1
    finally:
        reset_project(first.project_id)
        reset_project(second.project_id)


def test_duplicate_identifier_project_can_be_inspected_but_cannot_solve_through_api() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["teachers.csv"] = raw_files["teachers.csv"].replace(
        b"T002,Ben Patel", b"T001,Ben Patel", 1
    )

    _assert_invalid_project_cannot_solve(TestClient(app), raw_files, "duplicate_identifier")


def test_malformed_primitive_project_can_be_inspected_but_cannot_solve_through_api() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["rooms.csv"] = raw_files["rooms.csv"].replace(
        b"G101,Room G101,General,32", b"G101,Room G101,General,abc", 1
    )

    _assert_invalid_project_cannot_solve(TestClient(app), raw_files, "invalid_primitive")


def test_curriculum_mismatch_project_can_be_inspected_but_cannot_solve_through_api() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["curriculum.csv"] = raw_files["curriculum.csv"].replace(b"7,English,2,", b"7,English,4,", 1)

    _assert_invalid_project_cannot_solve(TestClient(app), raw_files, "curriculum_mismatch")


def test_solver_prevents_double_booking_and_respects_limits() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_04_gcse_options", project_id="solver-hard-constraints")
    result = solve_project(project, SolveSettings(mode="deep", time_limit_seconds=30, soft_constraints_enabled=True))

    assert result.status == "feasible"
    assert not result.unscheduled_lessons

    teacher_slots: set[tuple[str, str, str]] = set()
    room_slots: set[tuple[str, str, str]] = set()
    group_slots: set[tuple[str, str, str]] = set()
    teacher_week_load: Counter[str] = Counter()
    teacher_day_load: Counter[tuple[str, str]] = Counter()

    for assignment in result.assignments:
        teacher_key = (assignment.teacher_id, assignment.day, assignment.period)
        room_key = (assignment.room_id, assignment.day, assignment.period)
        group_key = (assignment.group_id, assignment.day, assignment.period)
        assert teacher_key not in teacher_slots
        assert room_key not in room_slots
        assert group_key not in group_slots
        teacher_slots.add(teacher_key)
        room_slots.add(room_key)
        group_slots.add(group_key)

        teacher = project.teachers[assignment.teacher_id]
        room = project.rooms[assignment.room_id]
        group = project.teaching_groups[assignment.group_id]
        assert assignment.day in teacher.working_days
        assert f"{assignment.day}-{assignment.period}" not in teacher.unavailable_periods
        assert f"{assignment.day}-{assignment.period}" not in room.unavailable_periods
        assert room.capacity >= group.class_size
        teacher_week_load[assignment.teacher_id] += 1
        teacher_day_load[(assignment.teacher_id, assignment.day)] += 1

    for teacher_id, load in teacher_week_load.items():
        assert load <= project.teachers[teacher_id].max_lessons_per_week
    for (teacher_id, _day), load in teacher_day_load.items():
        assert load <= project.teachers[teacher_id].max_lessons_per_day


def test_option_blocks_are_scheduled_simultaneously() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_04_gcse_options", project_id="option-block-test")
    result = solve_project(project, SolveSettings(mode="deep", time_limit_seconds=30, soft_constraints_enabled=True))

    slots_by_group: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for assignment in result.assignments:
        slots_by_group[assignment.group_id].add((assignment.day, assignment.period))

    blocks: dict[tuple[int, str], list[str]] = defaultdict(list)
    for option in project.option_blocks:
        if option.simultaneous_required:
            blocks[(option.year_group, option.block)].append(option.group_id)

    for group_ids in blocks.values():
        expected = slots_by_group[group_ids[0]]
        for group_id in group_ids[1:]:
            assert slots_by_group[group_id] == expected


def test_output_zip_contains_requested_csvs() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_02_years_7_to_11_core", project_id="export-test")
    project.solve_result = solve_project(project, SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True))

    zip_buffer = build_export_zip(project)
    with zipfile.ZipFile(zip_buffer) as archive:
        assert set(EXPORT_FILENAMES).issubset(set(archive.namelist()))
        lesson_csv = archive.read("timetable_by_lesson.csv").decode("utf-8")
        assert "day,period,group_id,year_group,subject,teacher_id,teacher_name,room_id,room_name,source_scenario" in lesson_csv


def test_valid_sample_solve_has_no_broken_hard_constraints() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_04_gcse_options", project_id="verification-valid")

    result = solve_project(project, SolveSettings(mode="deep", time_limit_seconds=30, soft_constraints_enabled=True))

    assert result.status == "feasible"
    assert result.broken_hard_constraints == []


def test_verifier_detects_teacher_double_booking() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    assignments = [
        _assignment(project, "7A_ENG", "T001", "G101", lesson_id="L1"),
        _assignment(project, "7B_ENG", "T001", "G102", lesson_id="L2"),
    ]

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=assignments))

    assert any(issue.category == "teacher_double_booking" for issue in issues)


def test_verifier_detects_room_double_booking() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    assignments = [
        _assignment(project, "7A_ENG", "T001", "G101", lesson_id="L1"),
        _assignment(project, "7A_MAT", "T002", "G101", lesson_id="L2"),
    ]

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=assignments))

    assert any(issue.category == "room_double_booking" for issue in issues)


def test_verifier_detects_group_double_booking() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    assignments = [
        _assignment(project, "7A_ENG", "T001", "G101", lesson_id="L1"),
        _assignment(project, "7A_ENG", "T001", "G102", lesson_id="L2"),
    ]

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=assignments))

    assert any(issue.category == "group_double_booking" for issue in issues)


def test_verifier_detects_teacher_unavailability() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    assignment = _assignment(project, "7A_ENG", "T001", "G101", day="Wed", period="P5")

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))

    assert any(issue.category == "teacher_unavailable" for issue in issues)


def test_verifier_detects_room_capacity_violation() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    project.rooms["G101"].capacity = 20
    assignment = _assignment(project, "7A_ENG", "T001", "G101")

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))

    assert any(issue.category == "room_capacity" for issue in issues)


def test_verifier_detects_room_suitability_violation() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    assignment = _assignment(project, "7A_SCI", "T003", "G101")

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))

    assert any(issue.category == "room_suitability" for issue in issues)


def test_verifier_detects_fixed_event_violation() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    assignment = _assignment(project, "7A_ENG", "T001", "G101", day="Mon", period="P1")

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))

    assert any(issue.category == "fixed_event_conflict" for issue in issues)


def test_duplicate_fixed_event_id_is_fatal_with_source_rows() -> None:
    project = _project_with_fixed_event_rows(
        b"FE_DUP,First event,meeting,T001,Tue,P1,1,,,\n",
        b"FE_DUP,Second event,meeting,T002,Tue,P2,1,,,\n",
    )

    issues = _fatal_issues(project, "duplicate_identifier")
    assert any(
        issue.file == "fixed_events.csv"
        and issue.field == "event_id"
        and issue.row == 3
        and "FE_DUP" in issue.message
        and "row 2" in issue.message
        for issue in issues
    )


def test_unknown_fixed_event_target_is_fatal() -> None:
    project = _project_with_fixed_event_rows(
        b"FE_UNKNOWN,Unknown target,meeting,ALL-STAFF,Tue,P1,1,,,\n",
    )

    assert any(
        issue.file == "fixed_events.csv"
        and issue.row == 2
        and issue.field == "applies_to"
        and issue.category == "fixed_event_target"
        and "ALL-STAFF" in issue.message
        for issue in _fatal_issues(project, "fixed_event_target")
    )


@pytest.mark.parametrize(
    ("target", "teacher_id", "room_id"),
    [
        ("T001", "T001", "G101"),
        ("G101", "T001", "G101"),
        ("7A_ENG", "T001", "G101"),
        ("Y7", "T001", "G101"),
        ("ALL_STAFF", "T001", "G101"),
    ],
)
def test_valid_fixed_event_targets_are_accepted_and_block_assignments(
    target: str,
    teacher_id: str,
    room_id: str,
) -> None:
    project = _project_with_fixed_event_rows(
        f"FE_TARGET,Target test,meeting,{target},Tue,P1,1,,,\n".encode("utf-8"),
    )
    assert project.fatal_validation_issues == []

    assignment = _assignment(project, "7A_ENG", teacher_id, room_id, day="Tue", period="P1")
    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))
    assert any(issue.category == "fixed_event_conflict" for issue in issues)


def test_slt_fixed_event_blocks_teachers_with_existing_slt_role_convention() -> None:
    raw_files = _minimal_scenario_files()
    raw_files["teachers.csv"] = raw_files["teachers.csv"].replace(
        b"T001,Alice Green,Teacher", b"T001,Alice Green,Senior Leadership Team", 1
    )
    raw_files["fixed_events.csv"] = (
        b"event_id,event_name,event_type,applies_to,day,period,duration_periods,required_teacher_ids,required_room_ids,notes\n"
        b"FE_SLT,SLT meeting,meeting,SLT,Tue,P1,1,,,\n"
    )
    project = load_project_from_files(raw_files, project_id="slt-fixed-event-test")
    assert project.fatal_validation_issues == []

    assignment = _assignment(project, "7A_ENG", "T001", "G101", day="Tue", period="P1")
    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))
    assert any(issue.category == "fixed_event_conflict" for issue in issues)


def test_slt_fixed_event_without_matching_staff_is_a_warning_and_blocks_nobody() -> None:
    project = _project_with_fixed_event_rows(
        b"FE_SLT_EMPTY,Conditional SLT meeting,meeting,SLT,Tue,P1,1,,,\n",
    )

    assert project.fatal_validation_issues == []
    assert any(
        issue.row == 2
        and issue.category == "fixed_event_no_matches"
        and "FE_SLT_EMPTY" in issue.message
        and "SLT" in issue.message
        for issue in project.validation_issues
    )
    assert "source_row" not in project.fixed_events[0].model_dump()

    assignment = _assignment(project, "7A_ENG", "T001", "G101", day="Tue", period="P1")
    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))
    assert not any(issue.category == "fixed_event_conflict" for issue in issues)


@pytest.mark.parametrize("target", ["T999", "R999", "UNKNOWN_GROUP", "Y12"])
def test_missing_exact_or_year_fixed_event_target_is_fatal(target: str) -> None:
    project = _project_with_fixed_event_rows(
        f"FE_MISSING,Missing target,meeting,{target},Tue,P1,1,,,\n".encode("utf-8"),
    )

    assert any(
        issue.row == 2
        and issue.field == "applies_to"
        and issue.category == "fixed_event_target"
        and target in issue.message
        for issue in _fatal_issues(project, "fixed_event_target")
    )


@pytest.mark.parametrize(
    ("field", "value", "label"),
    [
        ("required_teacher_ids", "T999", "teacher"),
        ("required_room_ids", "R999", "room"),
    ],
)
def test_unknown_fixed_event_required_resource_is_fatal(field: str, value: str, label: str) -> None:
    required_teachers = value if field == "required_teacher_ids" else ""
    required_rooms = value if field == "required_room_ids" else ""
    project = _project_with_fixed_event_rows(
        f"FE_UNKNOWN_RESOURCE,Unknown resource,meeting,,Tue,P1,1,{required_teachers},{required_rooms},\n".encode("utf-8"),
    )

    assert any(
        issue.row == 2
        and issue.field == field
        and issue.category == "invalid_reference"
        and label in issue.message
        for issue in _fatal_issues(project, "invalid_reference")
    )


def test_duplicate_fixed_event_required_resource_is_fatal() -> None:
    project = _project_with_fixed_event_rows(
        b"FE_REPEAT,Repeated resource,meeting,,Tue,P1,1,T001|T001,,\n",
    )

    assert any(
        issue.row == 2 and issue.category == "duplicate_reference"
        for issue in _fatal_issues(project, "duplicate_reference")
    )


@pytest.mark.parametrize("duration", ["0", "-1"])
def test_non_positive_fixed_event_duration_is_fatal(duration: str) -> None:
    project = _project_with_fixed_event_rows(
        f"FE_DURATION,Duration test,meeting,T001,Tue,P1,{duration},,,\n".encode("utf-8"),
    )

    assert any(
        issue.row == 2 and issue.field == "duration_periods" and issue.category == "fixed_event_duration"
        for issue in _fatal_issues(project, "fixed_event_duration")
    )


def test_fixed_event_extending_beyond_final_period_is_fatal() -> None:
    project = _project_with_fixed_event_rows(
        b"FE_OVERRUN,Overrun,meeting,T001,Tue,P5,2,,,\n",
    )

    assert any(
        issue.row == 2
        and issue.field == "duration_periods"
        and issue.category == "fixed_event_duration"
        and "P5" in issue.message
        for issue in _fatal_issues(project, "fixed_event_duration")
    )


def test_fixed_event_without_effective_target_or_resource_is_fatal() -> None:
    project = _project_with_fixed_event_rows(
        b"FE_EMPTY,Empty event,meeting,,Tue,P1,1,,,\n",
    )

    assert any(
        issue.row == 2 and issue.category == "fixed_event_target"
        for issue in _fatal_issues(project, "fixed_event_target")
    )


def test_verifier_detects_multi_period_fixed_event_conflict() -> None:
    project = _project_with_fixed_event_rows(
        b"FE_MULTI,Multi-period meeting,meeting,T001,Tue,P2,2,,,\n",
    )
    assert project.fatal_validation_issues == []

    assignment = _assignment(project, "7A_ENG", "T001", "G101", day="Tue", period="P3")
    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=[assignment]))
    assert any(issue.category == "fixed_event_conflict" for issue in issues)


def test_verifier_detects_option_block_simultaneity_violation() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_04_gcse_options")
    result = solve_project(project, SolveSettings(mode="deep", time_limit_seconds=30, soft_constraints_enabled=True))
    assignment = next(item for item in result.assignments if item.group_id == "10A_CS")
    assignment.day = "Fri"
    assignment.period = "P5"

    issues = verify_solve_result(project, result)

    assert any(issue.category == "option_block_simultaneity" for issue in issues)


def test_verification_prevents_feasible_status_when_hard_constraints_are_broken() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    result = SolveResult(
        status="feasible",
        assignments=[
            _assignment(project, "7A_ENG", "T001", "G101", lesson_id="L1"),
            _assignment(project, "7B_ENG", "T001", "G102", lesson_id="L2"),
        ],
    )

    verified = apply_verification(project, result)

    assert verified.status == "infeasible"
    assert any(issue.category == "teacher_double_booking" for issue in verified.broken_hard_constraints)


def test_valid_sample_has_exact_configured_assignment_count_for_each_group() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_04_gcse_options")
    result = solve_project(project, SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True))
    counts = Counter(assignment.group_id for assignment in result.assignments)

    assert result.status == "feasible"
    assert all(counts[group.group_id] == group.lessons_per_week for group in project.teaching_groups.values())
    assert not any(issue.category in {"missing_required_lessons", "incorrect_lesson_count"} for issue in result.broken_hard_constraints)


def test_verifier_reports_missing_lessons_with_expected_and_actual_counts() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    result = solve_project(project, SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True))
    assignments = list(result.assignments)
    assignments.remove(next(item for item in assignments if item.group_id == "7A_ENG"))

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=assignments))
    missing = next(issue for issue in issues if issue.category == "missing_required_lessons" and issue.group_id == "7A_ENG")

    assert "English" in missing.message
    assert "Year 7" in missing.message
    assert "requires 2 lessons" in missing.message
    assert "1 were scheduled" in missing.message
    assert "1 missing" in missing.message


def test_excess_assignment_is_a_hard_violation_and_makes_result_infeasible() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    solved = solve_project(project, SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True))
    assignments = list(solved.assignments)
    assignments.append(_extra_assignment_in_free_slot(project, assignments, "7A_ENG", "L_EXTRA"))

    verified = apply_verification(project, SolveResult(status="feasible", assignments=assignments))
    excess = next(
        issue
        for issue in verified.broken_hard_constraints
        if issue.category == "incorrect_lesson_count" and issue.group_id == "7A_ENG"
    )

    assert verified.status == "infeasible"
    assert "English" in excess.message
    assert "Year 7" in excess.message
    assert "requires 2 lessons" in excess.message
    assert "3 were scheduled" in excess.message
    assert "1 excess assignment" in excess.message


def test_api_exposes_and_blocks_verified_excess_assignment_result() -> None:
    client = TestClient(app)
    project_id = _upload_csv_files(client, _minimal_scenario_files())["project_id"]
    project = get_project(project_id)
    solved = solve_project(project, SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True))
    assignments = list(solved.assignments)
    assignments.append(_extra_assignment_in_free_slot(project, assignments, "7A_ENG", "L_EXTRA_API"))
    result = apply_verification(project, SolveResult(status="feasible", assignments=assignments))
    project.solve_result = result
    project.solve_status = SolveStatus(status=result.status, progress=1.0, messages=result.messages)

    conflicts = client.get(f"/api/projects/{project_id}/conflicts")
    assert conflicts.status_code == 200
    assert any(
        issue["category"] == "incorrect_lesson_count" and issue["group_id"] == "7A_ENG"
        for issue in conflicts.json()["broken_hard_constraints"]
    )

    export = client.get(f"/api/projects/{project_id}/export")
    assert export.status_code == 409
    assert "hard-constraint" in export.json()["detail"].lower()


def test_option_block_group_excess_assignment_is_counted_individually() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_04_gcse_options")
    solved = solve_project(project, SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True))
    assignments = list(solved.assignments)
    assignments.append(_extra_assignment_in_free_slot(project, assignments, "10A_CS", "L_OPTION_EXTRA"))

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=assignments))
    assert any(issue.category == "incorrect_lesson_count" and issue.group_id == "10A_CS" for issue in issues)


def test_unknown_group_assignment_is_not_counted_as_excess_for_known_groups() -> None:
    project = load_project_from_folder(SAMPLE_ROOT / "scenario_01_minimal_ks3_core")
    solved = solve_project(project, SolveSettings(mode="balanced", time_limit_seconds=30, soft_constraints_enabled=True))
    assignments = list(solved.assignments)
    extra = _extra_assignment_in_free_slot(project, assignments, "7A_ENG", "L_UNKNOWN_GROUP")
    assignments.append(extra.model_copy(update={"group_id": "UNKNOWN_GROUP"}))

    issues = verify_solve_result(project, SolveResult(status="feasible", assignments=assignments))
    assert any(issue.category == "unknown_group" and issue.group_id == "UNKNOWN_GROUP" for issue in issues)
    assert not any(issue.category == "incorrect_lesson_count" for issue in issues)
