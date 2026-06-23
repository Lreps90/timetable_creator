import type {
  ConflictsResponse,
  SolveSettings,
  SolveStatus,
  SummaryResponse,
  TimetableResponse,
  TimetableView,
  UploadResponse,
  ValidationResponse
} from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function uploadProject(files: File[]): Promise<UploadResponse> {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  return request<UploadResponse>("/projects/upload", {
    method: "POST",
    body: formData
  });
}

export function getValidation(projectId: string): Promise<ValidationResponse> {
  return request<ValidationResponse>(`/projects/${projectId}/validation`);
}

export function startSolve(projectId: string, settings: SolveSettings): Promise<SolveStatus> {
  return request<SolveStatus>(`/projects/${projectId}/solve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings)
  });
}

export function getSolveStatus(projectId: string): Promise<SolveStatus> {
  return request<SolveStatus>(`/projects/${projectId}/solve-status`);
}

export function getTimetable(projectId: string, view: TimetableView, id?: string): Promise<TimetableResponse> {
  const params = new URLSearchParams({ view });
  if (id) params.set("id", id);
  return request<TimetableResponse>(`/projects/${projectId}/timetable?${params.toString()}`);
}

export function getConflicts(projectId: string): Promise<ConflictsResponse> {
  return request<ConflictsResponse>(`/projects/${projectId}/conflicts`);
}

export function getSummary(projectId: string): Promise<SummaryResponse> {
  return request<SummaryResponse>(`/projects/${projectId}/summary`);
}

export function exportUrl(projectId: string): string {
  return `${API_BASE}/projects/${projectId}/export`;
}

export async function resetProject(projectId: string): Promise<void> {
  await request(`/projects/${projectId}`, { method: "DELETE" });
}
