export type Severity = "fatal" | "warning" | "info";
export type SolveState = "not_started" | "queued" | "running" | "feasible" | "infeasible" | "failed";
export type TimetableView = "group" | "teacher" | "room" | "subject";

export interface ValidationIssue {
  file?: string | null;
  row?: number | null;
  field?: string | null;
  severity: Severity;
  category: string;
  message: string;
}

export interface UploadResponse {
  project_id: string;
  source_scenario: string;
  files_detected: string[];
  validation: ValidationSummary;
  can_solve: boolean;
}

export interface ValidationSummary {
  fatal: number;
  warning: number;
  info: number;
  by_category: Record<string, number>;
}

export interface ValidationResponse {
  project_id: string;
  issues: ValidationIssue[];
  summary: ValidationSummary;
  can_solve: boolean;
}

export interface SolveSettings {
  mode: "quick" | "balanced" | "deep";
  time_limit_seconds: number;
  soft_constraints_enabled: boolean;
}

export interface SolveStatus {
  status: SolveState;
  progress: number;
  score?: number | null;
  messages: string[];
}

export interface LessonAssignment {
  lesson_id: string;
  day: string;
  period: string;
  group_id: string;
  year_group: number;
  subject: string;
  teacher_id: string;
  teacher_name: string;
  room_id: string;
  room_name: string;
  source_scenario: string;
  option_block?: string;
}

export interface ViewOption {
  id: string;
  label: string;
}

export interface TimetableResponse {
  project_id: string;
  view: TimetableView;
  selected_id: string;
  days: string[];
  periods: string[];
  options: ViewOption[];
  assignments: LessonAssignment[];
  cells: Record<string, LessonAssignment[]>;
}

export interface ConflictIssue {
  lesson_id?: string | null;
  group_id?: string | null;
  subject?: string | null;
  severity: Severity;
  category: string;
  message: string;
  reasons: string[];
}

export interface ConflictsResponse {
  project_id: string;
  validation_fatals: ValidationIssue[];
  validation_warnings: ValidationIssue[];
  unscheduled_lessons: ConflictIssue[];
  broken_hard_constraints: ConflictIssue[];
  soft_penalties: ConflictIssue[];
}

export interface TeacherLoad {
  teacher_id: string;
  teacher_name: string;
  department: string;
  total_lessons: number;
  max_lessons_per_week: number;
  max_lessons_per_day: number;
  by_day: Record<string, number>;
}

export interface RoomUtilisation {
  room_id: string;
  room_name: string;
  room_type: string;
  capacity: number;
  has_computers: boolean;
  computer_count: number;
  scheduled_lessons: number;
  available_slots: number;
  utilisation_percent: number;
}

export interface SummaryResponse {
  project_id: string;
  source_scenario: string;
  solve_status: SolveState;
  optimisation_score?: number | null;
  soft_penalty_total: number;
  teacher_load: TeacherLoad[];
  room_utilisation: RoomUtilisation[];
  scheduled_lessons: number;
  unscheduled_lessons: number;
  warnings: number;
  fatal_errors: number;
}
