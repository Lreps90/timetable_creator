import { BarChart3, CalendarDays, CheckSquare, FileUp, Home, RotateCcw, Settings, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { resetProject } from "./api/client";
import ConflictViewerPage from "./pages/ConflictViewerPage";
import HomePage from "./pages/HomePage";
import SolverSettingsPage from "./pages/SolverSettingsPage";
import SummaryDashboardPage from "./pages/SummaryDashboardPage";
import TimetableViewerPage from "./pages/TimetableViewerPage";
import UploadProjectPage from "./pages/UploadProjectPage";
import ValidationPage from "./pages/ValidationPage";
import type { UploadResponse } from "./types/api";

type Page = "home" | "upload" | "validation" | "solver" | "timetable" | "conflicts" | "summary";

const pages: Array<{ id: Page; label: string; icon: typeof Home; requiresProject?: boolean }> = [
  { id: "home", label: "Dashboard", icon: Home },
  { id: "upload", label: "Upload", icon: FileUp },
  { id: "validation", label: "Validation", icon: CheckSquare, requiresProject: true },
  { id: "solver", label: "Solve", icon: Settings, requiresProject: true },
  { id: "timetable", label: "Timetables", icon: CalendarDays, requiresProject: true },
  { id: "conflicts", label: "Conflicts", icon: TriangleAlert, requiresProject: true },
  { id: "summary", label: "Summary", icon: BarChart3, requiresProject: true }
];

export default function App() {
  const [page, setPage] = useState<Page>("home");
  const [project, setProject] = useState<UploadResponse | null>(null);

  const handleUploaded = (response: UploadResponse) => {
    setProject(response);
    setPage("validation");
  };

  const handleReset = async () => {
    if (project) {
      await resetProject(project.project_id).catch(() => undefined);
    }
    setProject(null);
    setPage("upload");
  };

  const renderPage = () => {
    if (!project && page !== "home" && page !== "upload") {
      return <UploadProjectPage onUploaded={handleUploaded} />;
    }
    switch (page) {
      case "upload":
        return <UploadProjectPage onUploaded={handleUploaded} />;
      case "validation":
        return project ? <ValidationPage projectId={project.project_id} onSolve={() => setPage("solver")} /> : null;
      case "solver":
        return project ? <SolverSettingsPage projectId={project.project_id} onSolved={() => setPage("timetable")} /> : null;
      case "timetable":
        return project ? <TimetableViewerPage projectId={project.project_id} /> : null;
      case "conflicts":
        return project ? <ConflictViewerPage projectId={project.project_id} /> : null;
      case "summary":
        return project ? <SummaryDashboardPage projectId={project.project_id} /> : null;
      default:
        return <HomePage project={project} onUpload={() => setPage("upload")} onOpenSummary={() => setPage("summary")} />;
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <CalendarDays aria-hidden="true" />
          <div>
            <strong>Timetable Creator</strong>
            <span>Years 7-11</span>
          </div>
        </div>
        <nav className="nav-list" aria-label="Main navigation">
          {pages.map((item) => {
            const Icon = item.icon;
            const disabled = item.requiresProject && !project;
            return (
              <button
                key={item.id}
                className={page === item.id ? "nav-item active" : "nav-item"}
                disabled={disabled}
                onClick={() => setPage(item.id)}
                title={item.label}
              >
                <Icon size={18} aria-hidden="true" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          {project ? (
            <>
              <span className="project-chip">{project.source_scenario}</span>
              <button className="secondary-button full" onClick={handleReset}>
                <RotateCcw size={16} aria-hidden="true" />
                Reset
              </button>
            </>
          ) : (
            <span className="muted">No project loaded</span>
          )}
        </div>
      </aside>
      <main className="main-panel">{renderPage()}</main>
    </div>
  );
}
