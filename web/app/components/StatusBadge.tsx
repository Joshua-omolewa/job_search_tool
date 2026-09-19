import {AI_STATUS_LABEL} from "@/app/constants";

export default function StatusBadge({status}: {status: string}) {
  return <span className={`badge ${status}`}>{AI_STATUS_LABEL[status] ?? status}</span>;
}
