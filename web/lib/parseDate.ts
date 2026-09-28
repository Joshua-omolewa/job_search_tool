// posted_at is usually an ISO-8601 string, but some ATS sources hand back
// a raw epoch timestamp (seconds or millis) instead — shared by
// JobsTable's "3 days ago" display and the toolbar's date-posted filter
// so both agree on what a given posted_at value actually means.
export function parsePostedDate(postedAt: string | null): Date | null {
  if (!postedAt) return null;
  let date: Date;
  if (/^\d+$/.test(postedAt)) {
    const n = Number(postedAt);
    const ms = n < 1e12 ? n * 1000 : n;
    date = new Date(ms);
  } else {
    date = new Date(postedAt);
  }
  return isNaN(date.getTime()) ? null : date;
}

// Whole calendar days between a posted_at date and now (0 = today), so a
// job posted this morning still counts as "today" this evening rather
// than off by a fraction of a day.
export function daysAgo(date: Date): number {
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((startOfDay(new Date()) - startOfDay(date)) / 86400000);
}
