export type SortKey = "company" | "title" | "location" | "match_score" | "status" | "my_status" | "posted_at";
export type SortDir = "asc" | "desc";
export type SaveState = "idle" | "saving" | "saved" | "error";

export type Job = {
    url: string;
    company: string;
    title: string;
    location: string;
    posted_at: string | null;
    description: string;
    match_score: number | null;
    recommendation: string | null;
    genuine_gaps: string | null;
    transferable_strengths: string | null;
    risk_factors: string | null;
    my_status: string | null;
    notes: string | null;
    status: string; // apply | consider | skip | not_evaluated
};