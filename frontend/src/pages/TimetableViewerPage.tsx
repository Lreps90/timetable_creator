import { RefreshCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { getTimetable } from "../api/client";
import TimetableGrid from "../components/TimetableGrid";
import type { TimetableResponse, TimetableView } from "../types/api";

interface Props {
  projectId: string;
}

const views: TimetableView[] = ["group", "teacher", "room", "subject"];

export default function TimetableViewerPage({ projectId }: Props) {
  const [view, setView] = useState<TimetableView>("group");
  const [selectedId, setSelectedId] = useState<string>("");
  const [data, setData] = useState<TimetableResponse | null>(null);
  const [error, setError] = useState("");

  const load = (nextView = view, nextId = selectedId) => {
    setError("");
    getTimetable(projectId, nextView, nextId || undefined)
      .then((response) => {
        setData(response);
        setSelectedId(response.selected_id);
      })
      .catch((exc) => setError(exc instanceof Error ? exc.message : "Could not load timetable."));
  };

  useEffect(() => {
    load(view, selectedId);
  }, [projectId, view]);

  return (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Timetable viewer</p>
          <h1>Grid Views</h1>
        </div>
        <button className="secondary-button" onClick={() => load()}>
          <RefreshCcw size={16} aria-hidden="true" />
          Refresh
        </button>
      </header>

      <div className="toolbar wrap">
        <div className="segmented">
          {views.map((item) => (
            <button
              key={item}
              className={view === item ? "selected" : ""}
              onClick={() => {
                setSelectedId("");
                setView(item);
              }}
            >
              {item}
            </button>
          ))}
        </div>
        <select
          value={selectedId}
          onChange={(event) => {
            setSelectedId(event.target.value);
            load(view, event.target.value);
          }}
        >
          {(data?.options ?? []).map((option) => (
            <option key={option.id} value={option.id}>{option.label}</option>
          ))}
        </select>
      </div>

      {error ? <p className="error-text">{error}</p> : null}
      {data ? <TimetableGrid data={data} view={view} /> : <div className="panel"><p className="muted">No timetable loaded.</p></div>}
    </section>
  );
}
