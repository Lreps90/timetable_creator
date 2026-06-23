import type { RoomUtilisation } from "../types/api";

interface Props {
  rows: RoomUtilisation[];
}

export default function RoomUtilisationTable({ rows }: Props) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Room</th>
            <th>Type</th>
            <th>Capacity</th>
            <th>Computers</th>
            <th>Lessons</th>
            <th>Use</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.room_id}>
              <td>{row.room_name} ({row.room_id})</td>
              <td>{row.room_type}</td>
              <td>{row.capacity}</td>
              <td>{row.has_computers ? row.computer_count : "None"}</td>
              <td>{row.scheduled_lessons}/{row.available_slots}</td>
              <td>
                <div className="mini-bar"><span style={{ width: `${Math.min(100, row.utilisation_percent)}%` }} /></div>
                {row.utilisation_percent}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
