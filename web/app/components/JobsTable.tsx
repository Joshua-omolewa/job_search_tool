import {MY_STATUS_LABEL, MY_STATUS_VALUES} from "@/app/constants";
import {Job, SortDir, SortKey} from "@/app/types";
import StatusBadge from "@/app/components/StatusBadge";

const COLUMNS: [SortKey, string][] = [
  ["company", "Company"], ["title", "Title"], ["location", "Location"],
  ["match_score", "Score"], ["status", "AI status"], ["my_status", "My status"],
  ["posted_at", "Date"],
];

type JobsTableProps = {
  rows: Job[];
  sortKey: SortKey;
  sortDir: SortDir;
  onToggleSort: (key: SortKey) => void;
  onSelectJob: (job: Job) => void;
  onQuickStatusChange: (job: Job, status: string) => void;
};

export default function JobsTable({rows, sortKey, sortDir, onToggleSort, onSelectJob, onQuickStatusChange}: JobsTableProps) {
  return (
    <table>
      <thead>
        <tr>
          {COLUMNS.map(([key, label]) => (
            <th key={key} className={sortKey === key ? "active-sort" : ""} onClick={() => onToggleSort(key)}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((j) => (
          <tr key={j.url} onClick={() => onSelectJob(j)}>
            <td className="company-cell">{j.company}</td>
            <td>{j.title}</td>
            <td className="loc-cell">{j.location}</td>
            <td className="score-cell">{j.match_score ?? "—"}</td>
            <td><StatusBadge status={j.status} /></td>
            <td onClick={(e) => e.stopPropagation()}>
              <select
                className="my-status-select"
                value={j.my_status ?? ""}
                onChange={(e) => onQuickStatusChange(j, e.target.value)}
              >
                <option value="">—</option>
                {MY_STATUS_VALUES.map((v) => (
                  <option key={v} value={v}>{MY_STATUS_LABEL[v]}</option>
                ))}
              </select>
            </td>
            <td className="loc-cell">{j.posted_at ? j.posted_at.slice(0, 10) : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
