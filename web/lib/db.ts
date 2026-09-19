import Database from "better-sqlite3";
import path from "node:path";

// Points at the SAME sqlite file the Python pipeline (dedup.py) writes to
// — this app is a read/write viewer on top of it, not a separate data
// store. Override with DB_PATH if your job_search_pipeline checkout lives
// somewhere other than the parent of this web/ folder.
const DB_PATH = process.env.DB_PATH || path.join(process.cwd(), "..", "data", "seen_jobs.sqlite3");

export const MY_STATUS_VALUES = ["applied", "interview", "rejected", "skipped", "silence"] as const;
export type MyStatus = (typeof MY_STATUS_VALUES)[number];

export type JobRow = {
  url: string;
  company: string;
  title: string;
  location: string;
  posted_at: string | null;
  description: string;
  passed_filters: number;
  match_score: number | null;
  recommendation: string | null;
  genuine_gaps: string | null;
  transferable_strengths: string | null;
  risk_factors: string | null;
  my_status: MyStatus | null;
  notes: string | null;
  // computed
  status: string;
};

let db: Database.Database | null = null;

function getDb(): Database.Database {
  if (db) return db;
  db = new Database(DB_PATH);
  // Mirrors dedup.py's SCHEMA for the tables this app touches — if the
  // Python pipeline hasn't run yet, `npm run dev` still starts cleanly
  // instead of erroring on a missing table.
  db.exec(`
    CREATE TABLE IF NOT EXISTS job_details (
      url TEXT PRIMARY KEY,
      company TEXT,
      title TEXT,
      location TEXT,
      posted_at TEXT,
      description TEXT,
      passed_filters INTEGER,
      fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS ai_evaluations (
      url TEXT PRIMARY KEY,
      match_score INTEGER,
      recommendation TEXT,
      genuine_gaps TEXT,
      transferable_strengths TEXT,
      risk_factors TEXT,
      model TEXT,
      evaluated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS user_status (
      url TEXT PRIMARY KEY,
      my_status TEXT,
      notes TEXT,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
  `);
  return db;
}

export function getJobs(): JobRow[] {
  const rows = getDb()
    .prepare(
      `
      SELECT jd.url, jd.company, jd.title, jd.location, jd.posted_at,
             jd.description, jd.passed_filters,
             ae.match_score, ae.recommendation, ae.genuine_gaps,
             ae.transferable_strengths, ae.risk_factors,
             us.my_status, us.notes
      FROM job_details jd
      LEFT JOIN ai_evaluations ae ON jd.url = ae.url
      LEFT JOIN user_status us ON jd.url = us.url
      WHERE jd.passed_filters = 1
      ORDER BY ae.match_score DESC
      `
    )
    .all() as Omit<JobRow, "status">[];

  return rows.map((r) => ({
    ...r,
    status: r.recommendation ?? "not_evaluated", // apply | consider | skip | not_evaluated
  }));
}

export function saveUserStatus(url: string, myStatus: string | null, notes: string | null): void {
  getDb()
    .prepare(
      `
      INSERT INTO user_status (url, my_status, notes, updated_at)
      VALUES (?, ?, ?, CURRENT_TIMESTAMP)
      ON CONFLICT(url) DO UPDATE SET
        my_status = excluded.my_status,
        notes = excluded.notes,
        updated_at = CURRENT_TIMESTAMP
      `
    )
    .run(url, myStatus || null, notes || null);
}
