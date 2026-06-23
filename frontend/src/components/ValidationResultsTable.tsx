import type { ValidationIssue } from "../types/api";

interface Props {
  issues: ValidationIssue[];
}

export default function ValidationResultsTable({ issues }: Props) {
  if (!issues.length) {
    return <p className="empty-state">No validation issues found.</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Severity</th>
            <th>Category</th>
            <th>File</th>
            <th>Row</th>
            <th>Field</th>
            <th>Explanation</th>
          </tr>
        </thead>
        <tbody>
          {issues.map((issue, index) => (
            <tr key={`${issue.file}-${issue.field}-${index}`}>
              <td><span className={`badge ${issue.severity}`}>{issue.severity}</span></td>
              <td>{issue.category}</td>
              <td>{issue.file ?? ""}</td>
              <td>{issue.row ?? ""}</td>
              <td>{issue.field ?? ""}</td>
              <td>{issue.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
