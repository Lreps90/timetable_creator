import { useState } from "react";
import type { LessonAssignment, TimetableResponse, TimetableView } from "../types/api";

interface Props {
  data: TimetableResponse;
  view: TimetableView;
}

export default function TimetableGrid({ data, view }: Props) {
  const [selected, setSelected] = useState<LessonAssignment | null>(null);

  return (
    <div className="grid-with-details">
      <div className="timetable-grid" style={{ gridTemplateColumns: `110px repeat(${data.days.length}, minmax(130px, 1fr))` }}>
        <div className="grid-corner">Period</div>
        {data.days.map((day) => (
          <div className="grid-heading" key={day}>{day}</div>
        ))}
        {data.periods.map((period) => (
          <div className="grid-row" key={period}>
            <div className="period-label">{period}</div>
            {data.days.map((day) => {
              const lessons = data.cells[`${day}-${period}`] ?? [];
              return (
                <button
                  key={`${day}-${period}`}
                  className={lessons.length ? "grid-cell filled" : "grid-cell"}
                  onClick={() => setSelected(lessons[0] ?? null)}
                >
                  {lessons.length ? lessons.map((lesson) => <CellText key={lesson.lesson_id} lesson={lesson} view={view} />) : <span className="empty-cell">Empty</span>}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <aside className="details-panel">
        <h2>Lesson Details</h2>
        {selected ? (
          <dl>
            <dt>Group</dt><dd>{selected.group_id}</dd>
            <dt>Subject</dt><dd>{selected.subject}</dd>
            <dt>Teacher</dt><dd>{selected.teacher_name} ({selected.teacher_id})</dd>
            <dt>Room</dt><dd>{selected.room_name} ({selected.room_id})</dd>
            <dt>Slot</dt><dd>{selected.day} {selected.period}</dd>
            <dt>Option block</dt><dd>{selected.option_block || "None"}</dd>
          </dl>
        ) : (
          <p className="muted">Select a scheduled cell.</p>
        )}
      </aside>
    </div>
  );
}

function CellText({ lesson, view }: { lesson: LessonAssignment; view: TimetableView }) {
  if (view === "teacher") {
    return <span><strong>{lesson.subject}</strong><small>{lesson.group_id} · {lesson.room_id}</small></span>;
  }
  if (view === "room") {
    return <span><strong>{lesson.subject}</strong><small>{lesson.group_id} · {lesson.teacher_id}</small></span>;
  }
  if (view === "subject") {
    return <span><strong>{lesson.group_id}</strong><small>{lesson.teacher_id} · {lesson.room_id}</small></span>;
  }
  return <span><strong>{lesson.subject}</strong><small>{lesson.teacher_id} · {lesson.room_id}</small></span>;
}
