import DOMPurify from "dompurify";
import {MY_STATUS_LABEL, MY_STATUS_VALUES} from "@/app/constants";
import {Job, SaveState} from "@/app/types";
import StatusBadge from "@/app/components/StatusBadge";
import {formatDescription} from "@/lib/formatDescription";

type JobDetailPanelProps = {
  job: Job;
  draftStatus: string;
  onDraftStatusChange: (value: string) => void;
  draftNotes: string;
  onDraftNotesChange: (value: string) => void;
  saveState: SaveState;
  onSave: () => void;
  onClose: () => void;
};

export default function JobDetailPanel({
  job, draftStatus, onDraftStatusChange, draftNotes, onDraftNotesChange, saveState, onSave, onClose,
}: JobDetailPanelProps) {
  return (
    <div id="overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div id="panel">
        <div id="panel-header">
          <button id="panel-close" onClick={onClose} aria-label="Close">&times;</button>
          <h2>{job.title}</h2>
          <div className="panel-meta">
            {job.company} · {job.location} · <StatusBadge status={job.status} />
            {job.match_score != null && ` · score ${job.match_score}`}
          </div>
        </div>
        <div id="panel-body">
          <h3>My status</h3>
          <div className="panel-status-row">
            <select value={draftStatus} onChange={(e) => onDraftStatusChange(e.target.value)}>
              <option value="">— not set —</option>
              {MY_STATUS_VALUES.map((v) => (
                <option key={v} value={v}>{MY_STATUS_LABEL[v]}</option>
              ))}
            </select>
          </div>

          <h3>Notes</h3>
          <textarea
            value={draftNotes}
            onChange={(e) => onDraftNotesChange(e.target.value)}
            placeholder="e.g.: applied via referral, follow up by Friday"
          />

          <div className="panel-actions">
            <button className="btn" onClick={onSave}>Save</button>
            {saveState === "saving" && <span className="save-status">Saving...</span>}
            {saveState === "saved" && <span className="save-status">✓ Saved</span>}
            {saveState === "error" && <span className="save-status">Error</span>}
            <a className="btn secondary" href={job.url} target="_blank" rel="noopener noreferrer">
              Open job ↗
            </a>
          </div>
          {job.match_score != null && (
            <>
              <h3>Transferable strengths</h3>
              <div>{job.transferable_strengths || "—"}</div>
              <h3>Genuine gaps</h3>
              <div>{job.genuine_gaps || "—"}</div>
              <h3>Risk factors</h3>
              <div>{job.risk_factors || "—"}</div>
            </>
          )}

          <h3>Job description</h3>
          {job.description ? (
            (() => {
              const formatted = formatDescription(job.description);
              return formatted.kind === "html"
                ? <div className="desc" dangerouslySetInnerHTML={{__html: DOMPurify.sanitize(formatted.html)}} />
                : <div className="desc desc-plain">{formatted.text}</div>;
            })()
          ) : (
            <div className="desc"><em>No description.</em></div>
          )}
        </div>
      </div>
    </div>
  );
}
