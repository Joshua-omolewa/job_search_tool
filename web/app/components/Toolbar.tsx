import {MY_STATUS_LABEL, MY_STATUS_VALUES} from "@/app/constants";

type ToolbarProps = {
  aiStatusFilter: string;
  onAiStatusFilterChange: (value: string) => void;
  myStatusFilter: string;
  onMyStatusFilterChange: (value: string) => void;
  search: string;
  onSearchChange: (value: string) => void;
  count: number;
  total: number;
  onRefresh: () => void;
};

const Toolbar = ({
  aiStatusFilter, onAiStatusFilterChange,
  myStatusFilter, onMyStatusFilterChange,
  search, onSearchChange,
  count, total, onRefresh,
}: ToolbarProps) => {
  return (
    <div className="toolbar">
      <label>
        AI status
        <select value={aiStatusFilter} onChange={(e) => onAiStatusFilterChange(e.target.value)}>
          <option value="all">All</option>
          <option value="apply">Apply</option>
          <option value="consider">Consider</option>
          <option value="skip">Skip</option>
          <option value="not_evaluated">Not evaluated</option>
        </select>
      </label>
      <label>
        My status
        <select value={myStatusFilter} onChange={(e) => onMyStatusFilterChange(e.target.value)}>
          <option value="all">All</option>
          <option value="none">No status</option>
          {MY_STATUS_VALUES.map((v) => (
            <option key={v} value={v}>{MY_STATUS_LABEL[v]}</option>
          ))}
        </select>
      </label>
      <label>
        Search
        <input
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="company or title..."
        />
      </label>
      <div className="spacer" />
      <span className="count-badge">{count} / {total}</span>
      <button className="btn secondary" onClick={onRefresh}>↻ Refresh</button>
    </div>
  );
};

export default Toolbar;
