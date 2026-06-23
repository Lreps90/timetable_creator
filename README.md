# School Timetable Creator

Browser-based v1 timetabling app for secondary school Years 7-11.

## What Is Included

- FastAPI backend with Pydantic validation models.
- CSV and ZIP scenario upload.
- In-memory project state for local v1 use.
- Deterministic single-period heuristic solver with hard-constraint checks.
- GCSE option-block simultaneity handling.
- Validation, conflict, summary and export APIs.
- React + TypeScript + Vite frontend.
- Five sample scenario folders under `sample_data/`.
- Backend tests covering loading, upload, validation, solver constraints, option blocks and exports.

The solver is isolated under `backend/app/solver/` so it can be replaced with an OR-Tools CP-SAT implementation later. The current implementation uses the modular heuristic fallback because OR-Tools is optional and not required for v1.

## Project Structure

```text
backend/
  app/
    api/
    data/
    exports/
    models/
    solver/
    validators/
  tests/
frontend/
  src/
    api/
    components/
    pages/
    styles/
    types/
sample_data/
```

## Backend Setup

Quick Windows launcher:

```powershell
.\run_app.bat
```

Run backend tests only:

```powershell
.\run_app.bat -TestOnly
```

The batch launcher uses a per-process PowerShell execution-policy bypass, so it does not change your Windows execution policy.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

The API will run at:

```text
http://127.0.0.1:8000
```

Health check:

```text
http://127.0.0.1:8000/api/health
```

## Frontend Setup

Install Node.js first if `node` and `npm` are not available on PATH.

```powershell
cd frontend
npm install
npm run dev
```

The UI will run at:

```text
http://127.0.0.1:5173
```

Vite proxies `/api` to the FastAPI backend.

## Running Tests

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests
```

## Input Files

Each scenario folder must include:

- `school_structure.csv`
- `teachers.csv`
- `teacher_subjects.csv`
- `subjects.csv`
- `curriculum.csv`
- `teaching_groups.csv`
- `rooms.csv`
- `subject_room_requirements.csv`
- `option_blocks.csv`
- `fixed_events.csv`
- `lesson_patterns.csv`
- `constraints.csv`

The app accepts either a ZIP containing these files or individual CSV files selected in the browser.

`rooms.csv` also supports these optional columns:

- `has_computers`
- `computer_count`

If these columns are omitted, rooms with an ICT/computer-style `room_type` are treated as having computers equal to their room capacity, and other rooms are treated as having no computers. Computer Science and ICT-style subjects require a suitable room with both enough total capacity and enough computers. Simultaneous option blocks must have a distinct feasible room for every group in the block.

## API Endpoints

- `POST /api/projects/upload`
- `GET /api/projects/{project_id}/validation`
- `POST /api/projects/{project_id}/solve`
- `GET /api/projects/{project_id}/solve-status`
- `GET /api/projects/{project_id}/timetable?view=group&id=7A_MAT`
- `GET /api/projects/{project_id}/conflicts`
- `GET /api/projects/{project_id}/summary`
- `GET /api/projects/{project_id}/export`
- `DELETE /api/projects/{project_id}`

## Exported CSVs

The export ZIP includes:

- `timetable_by_lesson.csv`
- `teacher_timetables.csv`
- `room_timetables.csv`
- `group_timetables.csv`
- `unscheduled_lessons.csv`
- `constraint_report.csv`
- `teacher_load_summary.csv`
- `room_utilisation_summary.csv`

## v1 Solver Scope

The solver schedules one-week single-period lessons. It enforces teacher, room and group double-booking constraints; teacher availability and load limits; room availability and capacity; computer availability for ICT/computing rooms; specialist room requirements; teacher qualification; fixed events; and GCSE option-block simultaneity.

Soft constraints are scored as penalties and exposed in the conflict viewer and export report. Double lessons and two-week cycles are represented in the data model but not scheduled as multi-period sessions in v1.
