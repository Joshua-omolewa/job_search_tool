"use client";

import {useEffect, useMemo, useState} from "react";
import {Job, SaveState, SortKey} from "@/app/types";
import Toolbar from "@/app/components/Toolbar";
import JobsTable from "@/app/components/JobsTable";
import JobDetailPanel from "@/app/components/JobDetailPanel";
import {daysAgo, parsePostedDate} from "@/lib/parseDate";

export default function Page() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [aiStatusFilter, setAiStatusFilter] = useState("all");
  const [myStatusFilter, setMyStatusFilter] = useState("all");
  const [locationFilter, setLocationFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [datePostedFilter, setDatePostedFilter] = useState("all");
  const [minScoreFilter, setMinScoreFilter] = useState("");

  const [sortKey, setSortKey] = useState<SortKey>("match_score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const [selectedUrl, setSelectedUrl] = useState<string | null>(null);
  const [draftStatus, setDraftStatus] = useState("");
  const [draftNotes, setDraftNotes] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/jobs");
      if (!res.ok) throw new Error((await res.json()).error || res.statusText);
      setJobs(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "match_score" ? "desc" : "asc");
    }
  }

  const rows = useMemo(() => {
    let r = jobs.filter((j) => {
      if (aiStatusFilter !== "all" && j.status !== aiStatusFilter) return false;
      if (myStatusFilter !== "all") {
        if (myStatusFilter === "none" ? j.my_status : j.my_status !== myStatusFilter) return false;
      }
      if (locationFilter && !j.location.toLowerCase().includes(locationFilter.toLowerCase())) return false;
      if (sourceFilter !== "all" && j.source !== sourceFilter) return false;
      if (search) {
        const hay = `${j.company} ${j.title}`.toLowerCase();
        if (!hay.includes(search.toLowerCase())) return false;
      }
      if (datePostedFilter !== "all") {
        const date = parsePostedDate(j.posted_at);
        // No parseable date -> can't confirm it's within the window, same
        // as an empty location never matching a location search.
        if (!date) return false;
        const maxDaysAgo = datePostedFilter === "today" ? 0 : Number(datePostedFilter);
        if (daysAgo(date) > maxDaysAgo) return false;
      }
      if (minScoreFilter !== "") {
        const min = Number(minScoreFilter);
        if (j.match_score == null || j.match_score < min) return false;
      }
      return true;
    });

    r = [...r].sort((a, b) => {
      let av: any = a[sortKey];
      let bv: any = b[sortKey];
      if (sortKey === "match_score") {
        av = av ?? -1;
        bv = bv ?? -1;
      } else {
        av = (av ?? "").toString().toLowerCase();
        bv = (bv ?? "").toString().toLowerCase();
      }
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return r;
  }, [
    jobs, aiStatusFilter, myStatusFilter, locationFilter, sourceFilter, search,
    datePostedFilter, minScoreFilter, sortKey, sortDir,
  ]);

  // Sources are whatever companies.yaml/aggregators.yaml actually produced
  // (10+ ATS types, 7+ aggregator types and growing) — derived from the
  // real data instead of a hardcoded list, so a newly-added source shows
  // up in the filter automatically without a code change here.
  const sourceOptions = useMemo(
    () => Array.from(new Set(jobs.map((j) => j.source).filter((s): s is string => !!s))).sort(),
    [jobs]
  );

  // Derived from `jobs` by url (rather than a frozen snapshot object) so
  // the open panel reflects the latest score/description/status after a
  // background refresh instead of showing stale AI analysis.
  const selected = useMemo(
    () => jobs.find((j) => j.url === selectedUrl) ?? null,
    [jobs, selectedUrl]
  );

  function openPanel(j: Job) {
    setSelectedUrl(j.url);
    setDraftStatus(j.my_status ?? "");
    setDraftNotes(j.notes ?? "");
    setSaveState("idle");
  }

  async function saveStatus(url: string, myStatus: string, notes: string, isPanelSave: boolean) {
    if (isPanelSave) setSaveState("saving");
    try {
      const res = await fetch("/api/status", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({url, my_status: myStatus || null, notes: notes || null}),
      });
      if (!res.ok) throw new Error((await res.json()).error || res.statusText);
      setJobs((prev) =>
        prev.map((j) => (j.url === url ? {...j, my_status: myStatus || null, notes: notes || null} : j))
      );
      if (isPanelSave) setSaveState("saved");
    } catch (e) {
      if (isPanelSave) setSaveState("error");
      else alert("Failed to save status: " + (e as Error).message);
    }
  }

  return (
    <>
      <header>
        <h1>Job Search Board</h1>
        <div className="subtitle">
          Reads and writes data/seen_jobs.sqlite3 directly — no export/import step.
        </div>
      </header>

      <Toolbar
        aiStatusFilter={aiStatusFilter}
        onAiStatusFilterChange={setAiStatusFilter}
        myStatusFilter={myStatusFilter}
        onMyStatusFilterChange={setMyStatusFilter}
        locationFilter={locationFilter}
        onLocationFilterChange={setLocationFilter}
        sourceFilter={sourceFilter}
        onSourceFilterChange={setSourceFilter}
        sourceOptions={sourceOptions}
        search={search}
        onSearchChange={setSearch}
        datePostedFilter={datePostedFilter}
        onDatePostedFilterChange={setDatePostedFilter}
        minScoreFilter={minScoreFilter}
        onMinScoreFilterChange={setMinScoreFilter}
        count={rows.length}
        total={jobs.length}
        onRefresh={load}
      />

      <main>
        {loading && <div className="loading-state">Loading...</div>}
        {error && (
          <div className="error-state">
            Can't read db: {error}
            <br />
            Check that `make run` was run at least once.
          </div>
        )}
        {!loading && !error && (
          rows.length === 0 ? (
            <div className="empty-state">Nothing matches the current filter/search.</div>
          ) : (
            <JobsTable
              rows={rows}
              sortKey={sortKey}
              sortDir={sortDir}
              onToggleSort={toggleSort}
              onSelectJob={openPanel}
              onQuickStatusChange={(job, status) => saveStatus(job.url, status, job.notes ?? "", false)}
            />
          )
        )}
      </main>

      {selected && (
        <JobDetailPanel
          job={selected}
          draftStatus={draftStatus}
          onDraftStatusChange={setDraftStatus}
          draftNotes={draftNotes}
          onDraftNotesChange={setDraftNotes}
          saveState={saveState}
          onSave={() => saveStatus(selected.url, draftStatus, draftNotes, true)}
          onClose={() => setSelectedUrl(null)}
        />
      )}
    </>
  );
}
