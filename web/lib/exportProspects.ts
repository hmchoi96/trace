import type { Person, Profile } from "./api";
import {
  actorLabel,
  foundOnLabel,
  lastEvent,
  sentenceCase,
  shortDate,
  statusLabel,
  timestamp,
} from "./format";
import { hasStructuredResearch, researchSectionsHtml } from "./researchView";

const SORT_NAMES: Record<string, string> = {
  added: "date added",
  name: "name",
  company: "company",
  actor: "actor",
  status: "status",
  foundOn: "found on",
  signal: "latest signal",
  email: "email source",
  event: "last event",
};

export function prospectSortCaption(key: string, dir: "asc" | "desc"): string {
  const name = SORT_NAMES[key] ?? key;
  const dated = key === "added" || key === "signal";
  const order = dated
    ? dir === "desc"
      ? "newest first"
      : "oldest first"
    : dir === "asc"
      ? "A to Z"
      : "Z to A";
  return `${name}, ${order}`;
}

function esc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function text(value: string | null | undefined, fallback = ""): string {
  const raw = (value ?? "").trim();
  return esc(raw || fallback);
}

function emailHtml(address: string): string {
  const at = address.indexOf("@");
  if (at <= 0) return text(address);
  return `${text(address.slice(0, at + 1))}<wbr>${text(address.slice(at + 1))}`;
}

function href(url: string): string | null {
  const raw = url.trim();
  if (!/^https?:\/\//i.test(raw)) return null;
  return esc(raw);
}

function anchor(url: string, label?: string): string {
  const safe = href(url);
  if (!safe) return text(label || url);
  return `<a href="${safe}">${text(label || url)}</a>`;
}

function joinParts(parts: Array<string | null | undefined>): string {
  return parts
    .map((part) => (part ?? "").trim())
    .filter(Boolean)
    .join(" · ");
}

function statusClass(status: string): string {
  if (status === "disqualified") return "tone-danger";
  if (status === "sent") return "tone-info";
  if (status === "draft" || status === "followup" || status === "draft_failed") {
    return "tone-warn";
  }
  if (status === "closed" || status === "passed") return "tone-muted";
  return "";
}

function sameText(left: string, right: string): boolean {
  return left.replace(/\s+/g, " ").trim() === right.replace(/\s+/g, " ").trim();
}

function extraSignals(person: Person) {
  const primary = person.signal.text?.trim() ?? "";
  return (person.additionalSignals ?? [])
    .map((signal) => {
      const source = String(signal.source ?? signal.signal_source ?? "").trim();
      const at = String(signal.published_at ?? signal.date ?? "");
      const body = String(signal.signal_text ?? signal.text ?? "").trim();
      const url = String(signal.source_url ?? signal.url ?? "").trim();
      return { source: source || "Web", at: shortDate(at), text: body, url };
    })
    .filter((signal) => signal.text && !sameText(signal.text, primary));
}

function signalWhen(person: Person): string {
  const at = person.signal.date || person.createdAt || "";
  if (!at) return "Not reported";
  const parsed = new Date(at);
  if (Number.isNaN(parsed.getTime())) return at;
  return parsed.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function actorName(person: Person): string {
  const label = actorLabel(person);
  if (!label || label === "—" || label.toLowerCase() === "unknown") return "";
  return label;
}

function briefLine(value: string | null | undefined): string {
  const raw = (value ?? "").trim();
  if (!raw || raw.includes("\n") || raw.length > 180) return "";
  return raw.replace(/\s+/g, " ");
}

/** Local calendar day they were added to the campaign. A hunt lands as one day. */
export function addedDayKey(person: Pick<Person, "createdAt">): string {
  if (!person.createdAt) return "";
  const parsed = new Date(person.createdAt);
  if (Number.isNaN(parsed.getTime())) return "";
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  return `${parsed.getFullYear()}-${month}-${day}`;
}

/** "today", "yesterday", or "Oct 5". */
export function addedOnCaption(dayKey: string, now = new Date()): string {
  const [year, month, day] = dayKey.split("-").map(Number);
  if (!year || !month || !day) return dayKey;
  const added = new Date(year, month - 1, day);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((today.getTime() - added.getTime()) / 86_400_000);
  if (diff === 0) return "today";
  if (diff === 1) return "yesterday";
  return added.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function addedDayGroups(
  people: Pick<Person, "createdAt">[],
  now = new Date(),
): { key: string; count: number; label: string }[] {
  const counts = new Map<string, number>();
  for (const person of people) {
    const key = addedDayKey(person);
    if (!key) continue;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([key, count]) => {
      const caption = addedOnCaption(key, now);
      const label = caption === "today" ? "Today" : caption === "yesterday" ? "Yesterday" : caption;
      return { key, count, label: `${label} · ${count}` };
    });
}

function scopeLine(args: {
  peopleCount: number;
  campaignCount: number;
  listTitle: string;
  foundOn: string;
  addedOn: string;
  sortCaption: string;
  exportedAt: Date;
}): string {
  const when = args.exportedAt.toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  const count =
    args.peopleCount === args.campaignCount
      ? `${args.peopleCount} ${args.peopleCount === 1 ? "person" : "people"}`
      : `${args.peopleCount} of ${args.campaignCount} people`;
  const bits = [`Exported ${when}`, count];
  if (args.listTitle !== "In this campaign") bits.push(args.listTitle);
  if (args.foundOn !== "All") bits.push(`Found on ${args.foundOn}`);
  if (args.addedOn) bits.push(`Added ${args.addedOn}`);
  bits.push(`Sorted by ${args.sortCaption}`);
  return bits.join(" · ");
}

function stat(value: number, label: string): string {
  return `<div class="stat"><b>${value}</b><span>${esc(label)}</span></div>`;
}

const INDEX_COLUMNS = [
  { label: "Person", width: "28%" },
  { label: "Company", width: "22%" },
  { label: "Status", width: "22%" },
  { label: "Email", width: "28%" },
];

function tableColgroup(): string {
  return `<colgroup>${INDEX_COLUMNS.map((column) => `<col style="width:${column.width}">`).join("")}</colgroup>`;
}

function tableHead(): string {
  return INDEX_COLUMNS.map((column) => `<th>${column.label}</th>`).join("");
}

function tableRow(person: Person): string {
  const email = person.email?.trim();
  const emailCell = email
    ? `<span class="strong">${emailHtml(email)}</span>${
        person.emailSource ? `<span class="muted">${text(person.emailSource)}</span>` : ""
      }`
    : `<span class="muted">Not found</span>`;
  return `<tr>
    <td><span class="strong">${text(person.name, "Unnamed")}</span>${
      person.title?.trim() ? `<span class="muted">${text(person.title)}</span>` : ""
    }</td>
    <td>${text(person.company, "Not reported")}</td>
    <td class="${statusClass(person.status)}">${text(statusLabel(person.status))}</td>
    <td>${emailCell}</td>
  </tr>`;
}

function fact(label: string, value: string): string {
  return `<tr><th>${esc(label)}</th><td>${value}</td></tr>`;
}

function personCard(person: Person): string {
  const email = person.email?.trim();
  const phone = person.phone?.trim();
  const linkedin = href(person.linkedinUrl);
  const signalUrl = href(person.signal.url);
  const extras = extraSignals(person);
  const recommendation = person.recommendation ? sentenceCase(person.recommendation) : "";
  const notes = person.notes ?? [];

  const facts = [
    fact(
      "Email",
      email
        ? `${emailHtml(email)}${person.emailSource ? ` <span class="muted inline">${text(person.emailSource)}</span>` : ""}`
        : "Not found",
    ),
  ];
  if (phone) {
    facts.push(
      fact(
        "Phone",
        `${text(phone)}${person.phoneSource ? ` <span class="muted inline">${text(person.phoneSource)}</span>` : ""}`,
      ),
    );
  }
  if (linkedin) facts.push(fact("LinkedIn", anchor(person.linkedinUrl)));
  facts.push(fact("Found on", text(foundOnLabel(person))));
  facts.push(fact("Latest signal", text(signalWhen(person))));
  facts.push(fact("Last event", text(lastEvent(person))));

  const quote = person.signal.text?.trim()
    ? `<blockquote>${text(person.signal.text)}</blockquote>`
    : `<p class="muted block">No signal text recorded.</p>`;
  const why = person.signal.why?.trim()
    ? `<p class="why">Why this person surfaced: ${text(person.signal.why)}</p>`
    : "";
  const source = signalUrl ? `<p class="source">${anchor(person.signal.url, person.signal.url)}</p>` : "";
  const more =
    extras.length > 0
      ? `<h3>More signals</h3><ul class="signals">${extras
          .map((signal) => {
            const meta = joinParts([signal.source, signal.at]);
            const link = href(signal.url) ? ` ${anchor(signal.url, "Source")}` : "";
            return `<li><span class="muted inline">${text(meta)}</span> ${text(signal.text)}${link}</li>`;
          })
          .join("")}</ul>`
      : "";
  const reason = person.recommendationReason?.trim() ?? "";
  const readLabel = recommendation
    ? recommendation.endsWith(".")
      ? recommendation
      : `${recommendation}.`
    : "Trace's read.";
  const sections = hasStructuredResearch(person) ? researchSectionsHtml(person) : "";
  const read =
    sections
      ? ""
      : recommendation || reason
        ? `<p class="read"><span class="label">${text(readLabel)}</span>${
            reason ? ` ${text(reason)}` : ""
          }</p>`
        : "";
  const audit = joinParts([
    person.draftDecision ? `Draft decision ${person.draftDecision}` : "",
    person.outreachMotion ? `Motion ${person.outreachMotion}` : "",
    person.triggerOfferAlignment ? `Alignment ${person.triggerOfferAlignment}` : "",
    person.gapStatus ? `Gap ${person.gapStatus}` : "",
    person.contactStatus ? `Contact ${person.contactStatus}` : "",
  ]);
  const noteBlock =
    notes.length > 0
      ? `<h3>Notes</h3><ul class="notes">${notes
          .map(
            (note) =>
              `<li><span class="muted inline">${text(timestamp(note.at) || "Undated")}</span> ${text(note.text)}</li>`,
          )
          .join("")}</ul>`
      : "";

  const company = person.company?.trim() ?? "";
  const sub = joinParts([
    person.title,
    company && company.toLowerCase() !== person.name.trim().toLowerCase() ? company : "",
    actorName(person),
    statusLabel(person.status),
  ]);

  return `<article class="person">
    <div class="person-head">
      <h2>${text(person.name, "Unnamed")}</h2>
      <p class="sub">${text(sub)}</p>
      <table class="facts"><tbody>${facts.join("")}</tbody></table>
    </div>
    ${quote}
    ${why}
    ${source}
    ${more}
    ${audit ? `<p class="meta">${text(audit)}</p>` : ""}
    ${sections}
    ${read}
    ${noteBlock}
  </article>`;
}

const CSS = `
@page {
  size: letter;
  margin: 0.7in 0.75in;
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  color: #171717;
  background: #fff;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 12px;
  line-height: 1.45;
}
body { max-width: 720px; margin: 0 auto; padding: 0 28px 72px; }
p, li, td, th, a, blockquote, h1, h2 {
  overflow-wrap: anywhere;
}
a { color: #24506f; }
.toolbar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 12px;
  margin: 0 -28px 28px;
  padding: 12px 28px;
  background: #fff;
  border-bottom: 1px solid #e5e5e5;
}
.toolbar button {
  appearance: none;
  border: 0;
  border-radius: 6px;
  background: #171717;
  color: #fff;
  font: inherit;
  font-weight: 600;
  font-size: 13px;
  padding: 7px 12px;
  cursor: pointer;
}
.toolbar button.secondary {
  background: #fff;
  color: #171717;
  border: 1px solid #d4d4d4;
}
.toolbar p { margin: 0; color: #525252; font-size: 13px; }
.kicker {
  margin: 28px 0 6px;
  color: #737373;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
h1 { margin: 0; font-size: 28px; line-height: 1.15; font-weight: 600; letter-spacing: -0.02em; }
.lede { margin: 8px 0 0; color: #525252; font-size: 14px; }
.meta { margin: 8px 0 0; color: #737373; font-size: 12px; }
.stats { display: flex; flex-wrap: wrap; gap: 28px; margin: 22px 0 8px; }
.stat b { display: block; font-size: 22px; line-height: 1.1; font-weight: 600; }
.stat span { color: #737373; font-size: 12px; }
h2.section {
  margin: 28px 0 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid #171717;
  font-size: 13px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
table.index { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 11px; }
table.index th {
  padding: 0 12px 6px 0;
  border-bottom: 1px solid #171717;
  color: #737373;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.3;
  text-align: left;
}
table.index td {
  padding: 8px 12px 8px 0;
  border-bottom: 1px solid #e5e5e5;
  vertical-align: top;
}
.strong { font-weight: 600; }
.muted { display: block; margin-top: 1px; color: #737373; font-weight: 400; }
.muted.inline, .muted.block { display: inline; }
.muted.block { display: block; }
.tone-info { color: #24506f; }
.tone-warn { color: #6b5320; }
.tone-danger { color: #7a3630; }
.tone-muted { color: #737373; }
article.person {
  margin-top: 28px;
  padding-top: 18px;
  border-top: 1px solid #e5e5e5;
}
article.person h2 { margin: 0; font-size: 22px; line-height: 1.2; font-weight: 600; }
.sub { margin: 4px 0 14px; color: #525252; }
table.facts { width: 100%; border-collapse: collapse; margin: 0 0 14px; }
table.facts th {
  width: 9em;
  padding: 4px 12px 4px 0;
  color: #737373;
  font-size: 11px;
  font-weight: 600;
  text-align: left;
  vertical-align: top;
}
table.facts td { padding: 4px 0; vertical-align: top; }
blockquote {
  margin: 0;
  padding: 0 0 0 10px;
  border-left: 2px solid #d4d4d4;
  white-space: pre-wrap;
}
.why, .source, .read { margin: 8px 0 0; }
.why, .source { color: #525252; }
.label { font-weight: 600; }
article.person h3 { margin: 14px 0 6px; font-size: 12px; }
ul { margin: 0; padding-left: 18px; }
li { margin: 0 0 6px; }
li .muted { display: inline; }
@media print {
  .toolbar { display: none !important; }
  body { max-width: none; padding: 0; }
  a { color: inherit; text-decoration: none; }
  table.index thead { display: table-header-group; }
  table.index tr { break-inside: avoid; page-break-inside: avoid; }
  article.person {
    break-before: page;
    page-break-before: always;
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
  }
  .person-head { break-after: avoid; page-break-after: avoid; }
}
`;

export function buildProspectHtml(args: {
  profile: Pick<Profile, "name" | "productName" | "huntDescription">;
  people: Person[];
  campaignCount: number;
  listTitle: string;
  foundOn: string;
  addedOn?: string;
  sortCaption: string;
  exportedAt?: Date;
  hunt?: { target: number; reviewed: number; outreachReady: number } | null;
}): string {
  const exportedAt = args.exportedAt ?? new Date();
  const showPhone = args.people.some((person) => Boolean(person.phone?.trim()));
  const withEmail = args.people.filter((person) => person.email?.trim()).length;
  const withPhone = args.people.filter((person) => person.phone?.trim()).length;
  const sent = args.people.filter((person) => person.sentAt).length;
  const product =
    args.profile.productName?.trim() &&
    args.profile.productName.trim().toLowerCase() !== args.profile.name.trim().toLowerCase()
      ? args.profile.productName.trim()
      : "";
  const brief = briefLine(args.profile.huntDescription);
  const lede = product || brief ? `<p class="lede">${text(joinParts([product, brief]))}</p>` : "";
  const title = `${args.profile.name} prospects`;
  const downloadName = `${
    args.profile.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "prospects"
  }.html`;

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${text(title)}</title>
<style>${CSS}</style>
</head>
<body>
<div class="toolbar">
  <button type="button" onclick="window.print()">Save as PDF</button>
  <button type="button" class="secondary" id="download-html">Download HTML</button>
  <p>Save as PDF keeps one person to a page. This bar is left out.</p>
</div>
<p class="kicker">Trace · Prospect list</p>
<h1>${text(args.profile.name)}</h1>
${lede}
<p class="meta">${text(
    scopeLine({
      peopleCount: args.people.length,
      campaignCount: args.campaignCount,
      listTitle: args.listTitle,
      foundOn: args.foundOn,
      addedOn: args.addedOn ?? "",
      sortCaption: args.sortCaption,
      exportedAt,
    }),
  )}</p>
<div class="stats">
  ${stat(args.people.length, args.people.length === 1 ? "Person" : "People")}
  ${stat(withEmail, "With email")}
  ${stat(sent, "Sent")}
  ${showPhone ? stat(withPhone, "With phone") : ""}
  ${args.hunt ? stat(args.hunt.target, "Hunt target") : ""}
  ${args.hunt ? stat(args.hunt.reviewed, "Reviewed") : ""}
  ${args.hunt ? stat(args.hunt.outreachReady, "Meet the outreach bar") : ""}
</div>
<h2 class="section">List</h2>
<table class="index">
  ${tableColgroup()}
  <thead><tr>${tableHead()}</tr></thead>
  <tbody>${args.people.map((person) => tableRow(person)).join("")}</tbody>
</table>
${args.people.map((person) => personCard(person)).join("\n")}
<script>
document.getElementById("download-html").onclick = function () {
  var blob = new Blob(["<!DOCTYPE html>\\n" + document.documentElement.outerHTML], { type: "text/html;charset=utf-8" });
  var link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = ${JSON.stringify(downloadName)};
  link.click();
  URL.revokeObjectURL(link.href);
};
</script>
</body>
</html>`;
}

export function openProspectExport(html: string): boolean {
  const blob = new Blob([html], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const page = window.open(url, "_blank");
  if (!page) {
    URL.revokeObjectURL(url);
    return false;
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 120_000);
  return true;
}
