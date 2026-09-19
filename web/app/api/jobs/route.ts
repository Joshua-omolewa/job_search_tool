import { NextResponse } from "next/server";
import { unstable_noStore as noStore } from "next/cache";
import { getJobs } from "@/lib/db";

// This reads from a local SQLite file that main.py / ai_evaluate.py / the
// status API keep changing — never let Next.js cache/prerender it. Belt and
// suspenders: `dynamic`/`revalidate` opt the route out of the Full Route
// Cache at build time, noStore() opts out at request time, and the
// Cache-Control header stops any HTTP-level cache (proxy, browser) from
// reusing a stale response too. Route Handler GET caching is easy to end
// up half-disabled in Next 14 — verified by curl that writes via POST
// /api/status actually show up on the very next GET before trusting this.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  noStore();
  try {
    const jobs = getJobs();
    return NextResponse.json(jobs, {
      headers: { "Cache-Control": "no-store, must-revalidate" },
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: `Failed to read from SQLite: ${err.message}` },
      { status: 500 }
    );
  }
}
