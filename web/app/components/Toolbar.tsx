import {MY_STATUS_LABEL, MY_STATUS_VALUES} from "@/app/constants";

type ToolbarProps = {
  aiStatusFilter: string;
  onAiStatusFilterChange: (value: string) => void;
  myStatusFilter: string;
  onMyStatusFilterChange: (value: string) => void;
  locationFilter: string;
  onLocationFilterChange: (value: string) => void;
  sourceFilter: string;
  onSourceFilterChange: (value: string) => void;
  sourceOptions: string[];
  search: string;
  onSearchChange: (value: string) => void;
  datePostedFilter: string;
  onDatePostedFilterChange: (value: string) => void;
  minScoreFilter: string;
  onMinScoreFilterChange: (value: string) => void;
  count: number;
  total: number;
  onRefresh: () => void;
};

const Toolbar = ({
  aiStatusFilter, onAiStatusFilterChange,
  myStatusFilter, onMyStatusFilterChange,
  locationFilter, onLocationFilterChange,
  sourceFilter, onSourceFilterChange, sourceOptions,
  search, onSearchChange,
  datePostedFilter, onDatePostedFilterChange,
  minScoreFilter, onMinScoreFilterChange,
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
        Min score
        <input
          type="number"
          min={0}
          max={100}
          value={minScoreFilter}
          onChange={(e) => onMinScoreFilterChange(e.target.value)}
          placeholder="0-100"
        />
      </label>
      <label>
        Location
        <input
          type="search"
          value={locationFilter}
          onChange={(e) => onLocationFilterChange(e.target.value)}
          placeholder="city, state, remote..."
        />
      </label>
      <label>
        Date posted
        <select value={datePostedFilter} onChange={(e) => onDatePostedFilterChange(e.target.value)}>
          <option value="all">Any time</option>
          <option value="today">Today</option>
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
        </select>
      </label>
      <label>
        Source
        <select value={sourceFilter} onChange={(e) => onSourceFilterChange(e.target.value)}>
          <option value="all">All</option>
          {sourceOptions.map((v) => (
            <option key={v} value={v}>{v}</option>
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
