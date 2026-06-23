import type { TeacherLoad } from "../types/api";

interface Props {
  rows: TeacherLoad[];
}

export default function TeacherLoadTable({ rows }: Props) {
  const days = rows[0] ? Object.keys(rows[0].by_day) : [];
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Teacher</th>
            <th>Total</th>
            {days.map((day) => <th key={day}>{day}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.teacher_id}>
              <td>{row.teacher_name} ({row.teacher_id})</td>
              <td>{row.total_lessons}/{row.max_lessons_per_week}</td>
              {days.map((day) => (
                <td key={day}>{row.by_day[day] ?? 0}/{row.max_lessons_per_day}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
