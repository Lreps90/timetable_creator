from __future__ import annotations

import io
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.data.csv_loader import EXPECTED_FILES, load_project_from_files, load_project_from_folder
from backend.app.exports.csv_exporter import EXPORT_FILENAMES, build_export_zip
from backend.app.main import app
from backend.app.models.entities import SolveSettings
from backend.app.solver.heuristic_solver import solve_project


ROOT = Path(__file__).resolve().parents[2]
SAMPLE_ROOT = ROOT / "sample_data"


def test_all_sample_scenarios_detect_expected_files_and_validate_without_fatals() -> None:
    scenarios = sorted(path for path in SAMPLE_ROOT.iterdir() if path.is_dir())
    assert len(scenarios) == 5

    for scenario in scenarios:
        project = load_project_from_folder(scenario)
        assert set(EXPECTED_FILES).issubset(set(project.files_detected))
        assert project.fatal_validation_issues == [], scenario.name


def test_invalid_references_are_caught() -> None:
    scenario = SAMPLE_ROOT / "scenario_01_minimal_ks3_core"
    raw_files = {path.name: path.read_bytes() for path in scenario.iterdir() if path.is_file()}
    text = raw_files["teaching_groups.csv"].decode("utf-8")
    text = text.replace("7A_HIS,7,History", "7A_HIS,7,Dance", 1)
    raw_files["teaching_groups.csv"] = text.encode("utf-8")

    project = load_project_from_files(raw_files, project_id="invalid-reference-test")
    fatals = [issue for issue in project.validation_issues if issue.severity == "fatal"]

    assert any(issue.category == "invalid_reference" and "Dance" in issue.message for issue in fatals)


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
        "subjects.csv": b"subject,department,is_core,default_room_type\nComputer Science,Computing,FALSE,ICT\n",
        "curriculum.csv": b"year_group,subject,lessons_per_week,notes\n10,Computer Science,2,\n",
        "teaching_groups.csv": b"group_id,year_group,subject,lessons_per_week,class_size,group_type,option_block,allowed_teachers,preferred_teacher,notes\n"
        b"10A_CS1,10,Computer Science,2,20,option,A,,,\n"
        b"10A_CS2,10,Computer Science,2,20,option,A,,,\n"
        b"10A_CS3,10,Computer Science,2,20,option,A,,,\n",
        "rooms.csv": b"room_id,room_name,room_type,capacity,has_computers,computer_count,available_days,unavailable_periods,notes\n"
        b"ICT1,ICT Suite 1,ICT,30,TRUE,30,Mon|Tue|Wed|Thu|Fri,,\n"
        b"ICT2,ICT Suite 2,ICT,30,TRUE,30,Mon|Tue|Wed|Thu|Fri,,\n"
        b"G101,Room G101,General,30,FALSE,0,Mon|Tue|Wed|Thu|Fri,,\n",
        "subject_room_requirements.csv": b"subject,required_room_type,allow_general_room,notes\nComputer Science,ICT,FALSE,\n",
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
