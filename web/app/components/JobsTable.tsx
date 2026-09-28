import {MY_STATUS_LABEL, MY_STATUS_VALUES} from "@/app/constants";
import {Job, SortDir, SortKey} from "@/app/types";
import StatusBadge from "@/app/components/StatusBadge";
import {daysAgo, parsePostedDate} from "@/lib/parseDate";

const COLUMNS: [SortKey, string][] = [
  ["company", "Company"], ["title", "Title"], ["location", "Location"],
  ["salary", "Salary"], ["match_score", "Score"], ["status", "AI status"],
  ["my_status", "My status"], ["posted_at", "Posted"], ["source", "Source"],
];

// "Today", "3 days ago", "1 month ago", ... — see lib/parseDate.ts for how
// posted_at (ISO string or raw epoch) turns into a real Date and a
// calendar-day count, shared with the toolbar's date-posted filter so
// both agree on what a given value means.
function formatRelativeDate(postedAt: string | null): string {
  const date = parsePostedDate(postedAt);
  if (!date) return "";
  const diffDays = daysAgo(date);

  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "1 day ago";
  if (diffDays < 30) return `${diffDays} days ago`;
  const diffMonths = Math.round(diffDays / 30);
  if (diffMonths < 12) return diffMonths === 1 ? "1 month ago" : `${diffMonths} months ago`;
  const diffYears = Math.round(diffMonths / 12);
  return diffYears === 1 ? "1 year ago" : `${diffYears} years ago`;
}

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
            <td className="salary-cell">{j.salary ?? "—"}</td>
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
            <td className="loc-cell">{formatRelativeDate(j.posted_at)}</td>
            <td className="loc-cell">{j.source ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
