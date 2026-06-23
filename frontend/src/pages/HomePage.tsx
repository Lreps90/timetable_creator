import { ArrowRight, Database, FileUp, ShieldCheck } from "lucide-react";
import type { UploadResponse } from "../types/api";

interface Props {
  project: UploadResponse | null;
  onUpload: () => void;
  onOpenSummary: () => void;
}

export default function HomePage({ project, onUpload, onOpenSummary }: Props) {
  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">School timetable workspace</p>
          <h1>Dashboard</h1>
        </div>
        {project ? (
          <button className="primary-button" onClick={onOpenSummary}>
            <Database size={18} aria-hidden="true" />
            Open Summary
          </button>
        ) : (
          <button className="primary-button" onClick={onUpload}>
            <FileUp size={18} aria-hidden="true" />
            Upload Scenario
          </button>
        )}
      </header>

      <div className="metric-grid">
        <div className="metric-tile">
          <ShieldCheck aria-hidden="true" />
          <span>Validation</span>
          <strong>{project ? `${project.validation.fatal} fatal` : "Waiting"}</strong>
        </div>
        <div className="metric-tile">
          <Database aria-hidden="true" />
          <span>Files</span>
          <strong>{project ? project.files_detected.length : 0}</strong>
        </div>
        <div className="metric-tile">
          <ArrowRight aria-hidden="true" />
          <span>Status</span>
          <strong>{project ? (project.can_solve ? "Ready" : "Blocked") : "New"}</strong>
        </div>
      </div>

      <div className="panel">
        <h2>Workflow</h2>
        <div className="step-row">
          {["Upload", "Validate", "Solve", "Review", "Export"].map((step) => (
            <span key={step}>{step}</span>
          ))}
        </div>
      </div>
    </section>
  );
}
