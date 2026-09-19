// Job descriptions arrive in three different shapes depending on the ATS/
// aggregator they came from (see ats_clients.py / aggregator_clients.py):
// real HTML (Remotive), HTML that got entity-escaped somewhere upstream —
// literal "&lt;div&gt;..." text instead of actual tags (Greenhouse), or
// plain text with \n newlines (Ashby). Rendering all three the same way
// via dangerouslySetInnerHTML either shows garbled literal tags or
// collapses paragraph breaks, so detect which shape we actually have.

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ",
};

function decodeEntities(text: string): string {
  return text.replace(/&(#\d+|#x[0-9a-f]+|[a-z]+);/gi, (match, entity: string) => {
    if (entity[0] === "#") {
      const code = entity[1].toLowerCase() === "x"
        ? parseInt(entity.slice(2), 16)
        : parseInt(entity.slice(1), 10);
      return Number.isNaN(code) ? match : String.fromCodePoint(code);
    }
    const lower = entity.toLowerCase();
    return lower in NAMED_ENTITIES ? NAMED_ENTITIES[lower] : match;
  });
}

const HTML_TAG_RE = /<[a-z][\s\S]*?>/i;

export type FormattedDescription =
  | {kind: "html"; html: string}
  | {kind: "text"; text: string};

export function formatDescription(raw: string): FormattedDescription {
  if (!raw) return {kind: "text", text: ""};
  // Already real HTML — leave it alone (decoding here would mangle
  // intentional entities inside genuine markup, e.g. "&amp;" in text).
  if (HTML_TAG_RE.test(raw)) return {kind: "html", html: raw};

  // No raw tags — try decoding entities in case this is escaped HTML.
  const decoded = decodeEntities(raw);
  if (HTML_TAG_RE.test(decoded)) return {kind: "html", html: decoded};

  // Still no tags after decoding — genuinely plain text.
  return {kind: "text", text: raw};
}
