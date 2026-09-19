import { NextResponse } from "next/server";
import { saveUserStatus, MY_STATUS_VALUES } from "@/lib/db";

export async function POST(req: Request) {
  const body = await req.json().catch(() => null);
  if (!body || typeof body.url !== "string" || !body.url) {
    return NextResponse.json({ error: "Missing 'url' in request body" }, { status: 400 });
  }
  const { url, my_status, notes } = body;
  if (my_status && !MY_STATUS_VALUES.includes(my_status)) {
    return NextResponse.json(
      { error: `Invalid my_status '${my_status}', expected one of ${MY_STATUS_VALUES.join(", ")}` },
      { status: 400 }
    );
  }
  try {
    saveUserStatus(url, my_status ?? null, notes ?? null);
    return NextResponse.json({ ok: true });
  } catch (err: any) {
    return NextResponse.json({ error: `Failed to write to SQLite: ${err.message}` }, { status: 500 });
  }
}
