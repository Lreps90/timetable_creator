import { Download } from "lucide-react";
import { exportUrl } from "../api/client";

interface Props {
  projectId: string;
  disabled?: boolean;
}

export default function ExportPanel({ projectId, disabled }: Props) {
  return (
    <div className="panel export-panel">
      <div>
        <h2>Exports</h2>
        <p className="muted">CSV ZIP output for lessons, teacher timetables, rooms, groups, conflicts and summaries.</p>
      </div>
      <a className={disabled ? "primary-button disabled" : "primary-button"} href={disabled ? undefined : exportUrl(projectId)}>
        <Download size={16} aria-hidden="true" />
        Download ZIP
      </a>
    </div>
  );
}
