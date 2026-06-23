import { Play, SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { getSolveStatus, startSolve } from "../api/client";
import type { SolveSettings, SolveStatus } from "../types/api";

interface Props {
  projectId: string;
  onSolved: () => void;
}

const terminalStates = new Set(["feasible", "infeasible", "failed"]);

export default function SolverSettingsPage({ projectId, onSolved }: Props) {
  const [settings, setSettings] = useState<SolveSettings>({
    mode: "balanced",
    time_limit_seconds: 30,
    soft_constraints_enabled: true
  });
  const [status, setStatus] = useState<SolveStatus | null>(null);
  const [error, setError] = useState("");

  const solve = async () => {
    setError("");
    try {
      const response = await startSolve(projectId, settings);
      setStatus(response);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Solve failed.");
    }
  };

  useEffect(() => {
    if (!status || terminalStates.has(status.status)) return undefined;
    const timer = window.setInterval(async () => {
      const next = await getSolveStatus(projectId);
      setStatus(next);
      if (terminalStates.has(next.status)) {
        window.clearInterval(timer);
      }
    }, 900);
    return () => window.clearInterval(timer);
  }, [projectId, status]);

  const progressPercent = Math.round((status?.progress ?? 0) * 100);

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Solver</p>
          <h1>Settings</h1>
        </div>
        <button className="primary-button" onClick={solve} disabled={status?.status === "running" || status?.status === "queued"}>
          <Play size={16} aria-hidden="true" />
          Start Solve
        </button>
      </header>

      <div className="settings-layout">
        <div className="panel">
          <h2><SlidersHorizontal size={18} aria-hidden="true" /> Mode</h2>
          <div className="segmented">
            {(["quick", "balanced", "deep"] as const).map((mode) => (
              <button
                key={mode}
                className={settings.mode === mode ? "selected" : ""}
                onClick={() => setSettings((current) => ({ ...current, mode }))}
              >
                {mode}
              </button>
            ))}
          </div>
          <label className="field-row">
            <span>Time limit</span>
            <input
              type="number"
              min={1}
              max={600}
              value={settings.time_limit_seconds}
              onChange={(event) => setSettings((current) => ({ ...current, time_limit_seconds: Number(event.target.value) }))}
            />
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={settings.soft_constraints_enabled}
              onChange={(event) => setSettings((current) => ({ ...current, soft_constraints_enabled: event.target.checked }))}
            />
            <span>Soft constraints</span>
          </label>
        </div>

        <div className="panel">
          <h2>Status</h2>
          <div className="progress-shell" aria-label="Solve progress">
            <div className="progress-bar" style={{ width: `${progressPercent}%` }} />
          </div>
          <div className="status-line">
            <span className={`badge ${status?.status ?? "info"}`}>{status?.status ?? "not_started"}</span>
            <strong>{progressPercent}%</strong>
            {status?.score != null ? <span>Score {status.score}</span> : null}
          </div>
          <div className="message-list">
            {(status?.messages ?? ["No solve has run yet."]).map((message, index) => (
              <span key={`${message}-${index}`}>{message}</span>
            ))}
          </div>
          {terminalStates.has(status?.status ?? "") ? (
            <button className="secondary-button" onClick={onSolved}>Open Timetables</button>
          ) : null}
          {error ? <p className="error-text">{error}</p> : null}
        </div>
      </div>
    </section>
  );
}
