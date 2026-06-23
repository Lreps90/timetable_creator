import { RefreshCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { getConflicts } from "../api/client";
import type { ConflictIssue, ConflictsResponse, ValidationIssue } from "../types/api";

interface Props {
  projectId: string;
}

export default function ConflictViewerPage({ projectId }: Props) {
  const [data, setData] = useState<ConflictsResponse | null>(null);
  const [error, setError] = useState("");

  const load = () => {
    setError("");
    getConflicts(projectId).then(setData).catch((exc) => setError(exc instanceof Error ? exc.message : "Could not load conflicts."));
  };

  useEffect(load, [projectId]);

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Constraint review</p>
          <h1>Conflicts</h1>
        </div>
        <button className="secondary-button" onClick={load}>
          <RefreshCcw size={16} aria-hidden="true" />
          Refresh
        </button>
      </header>
      {error ? <p className="error-text">{error}</p> : null}
      <IssuePanel title="Unscheduled Lessons" items={data?.unscheduled_lessons ?? []} />
      <IssuePanel title="Broken Hard Constraints" items={data?.broken_hard_constraints ?? []} />
      <IssuePanel title="Soft Penalties" items={data?.soft_penalties ?? []} />
      <ValidationPanel title="Validation Warnings" items={data?.validation_warnings ?? []} />
    </section>
  );
}

function IssuePanel({ title, items }: { title: string; items: ConflictIssue[] }) {
  return (
    <div className="panel">
      <h2>{title}</h2>
      {items.length ? (
        <div className="issue-list">
          {items.map((item, index) => (
            <article className="issue-item" key={`${item.lesson_id}-${index}`}>
              <span className={`badge ${item.severity}`}>{item.category}</span>
              <strong>{item.message}</strong>
              <small>{item.reasons.join(" | ")}</small>
            </article>
          ))}
        </div>
      ) : (
        <p className="empty-state">None.</p>
      )}
    </div>
  );
}

function ValidationPanel({ title, items }: { title: string; items: ValidationIssue[] }) {
  return (
    <div className="panel">
      <h2>{title}</h2>
      {items.length ? (
        <div className="issue-list">
          {items.map((item, index) => (
            <article className="issue-item" key={`${item.file}-${index}`}>
              <span className={`badge ${item.severity}`}>{item.category}</span>
              <strong>{item.message}</strong>
              <small>{[item.file, item.field].filter(Boolean).join(" · ")}</small>
            </article>
          ))}
        </div>
      ) : (
        <p className="empty-state">None.</p>
      )}
    </div>
  );
}
