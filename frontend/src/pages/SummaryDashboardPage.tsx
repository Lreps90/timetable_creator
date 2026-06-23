import { RefreshCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { getSummary } from "../api/client";
import ExportPanel from "../components/ExportPanel";
import RoomUtilisationTable from "../components/RoomUtilisationTable";
import TeacherLoadTable from "../components/TeacherLoadTable";
import type { SummaryResponse } from "../types/api";

interface Props {
  projectId: string;
}

export default function SummaryDashboardPage({ projectId }: Props) {
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    setError("");
    getSummary(projectId).then(setSummary).catch((exc) => setError(exc instanceof Error ? exc.message : "Could not load summary."));
  };

  useEffect(load, [projectId]);

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Outcome</p>
          <h1>Summary</h1>
        </div>
        <button className="secondary-button" onClick={load}>
          <RefreshCcw size={16} aria-hidden="true" />
          Refresh
        </button>
      </header>

      {error ? <p className="error-text">{error}</p> : null}

      <div className="metric-grid">
        <div className="metric-tile"><span>Status</span><strong>{summary?.solve_status ?? "not_started"}</strong></div>
        <div className="metric-tile"><span>Score</span><strong>{summary?.optimisation_score ?? "-"}</strong></div>
        <div className="metric-tile"><span>Scheduled</span><strong>{summary?.scheduled_lessons ?? 0}</strong></div>
        <div className="metric-tile danger"><span>Unscheduled</span><strong>{summary?.unscheduled_lessons ?? 0}</strong></div>
      </div>

      <ExportPanel projectId={projectId} disabled={!summary || summary.solve_status === "not_started"} />

      <div className="panel">
        <h2>Teacher Load</h2>
        {summary ? <TeacherLoadTable rows={summary.teacher_load} /> : <p className="muted">Loading.</p>}
      </div>

      <div className="panel">
        <h2>Room Use</h2>
        {summary ? <RoomUtilisationTable rows={summary.room_utilisation} /> : <p className="muted">Loading.</p>}
      </div>
    </section>
  );
}
