import { Play, RefreshCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { getValidation } from "../api/client";
import ValidationResultsTable from "../components/ValidationResultsTable";
import type { ValidationResponse } from "../types/api";

interface Props {
  projectId: string;
  onSolve: () => void;
}

export default function ValidationPage({ projectId, onSolve }: Props) {
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    getValidation(projectId).then(setValidation).catch((exc) => setError(exc instanceof Error ? exc.message : "Could not load validation."));
  };

  useEffect(load, [projectId]);

  const fatals = validation?.issues.filter((issue) => issue.severity === "fatal") ?? [];
  const warnings = validation?.issues.filter((issue) => issue.severity !== "fatal") ?? [];

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Data quality</p>
          <h1>Validation</h1>
        </div>
        <div className="toolbar">
          <button className="secondary-button" onClick={load}>
            <RefreshCcw size={16} aria-hidden="true" />
            Refresh
          </button>
          <button className="primary-button" disabled={!validation?.can_solve} onClick={onSolve}>
            <Play size={16} aria-hidden="true" />
            Solve
          </button>
        </div>
      </header>

      {error ? <p className="error-text">{error}</p> : null}

      <div className="metric-grid">
        <div className="metric-tile danger"><span>Fatal</span><strong>{validation?.summary.fatal ?? 0}</strong></div>
        <div className="metric-tile warning"><span>Warnings</span><strong>{validation?.summary.warning ?? 0}</strong></div>
        <div className="metric-tile"><span>Categories</span><strong>{Object.keys(validation?.summary.by_category ?? {}).length}</strong></div>
      </div>

      <div className="panel">
        <h2>Fatal Errors</h2>
        <ValidationResultsTable issues={fatals} />
      </div>
      <div className="panel">
        <h2>Warnings</h2>
        <ValidationResultsTable issues={warnings} />
      </div>
    </section>
  );
}
