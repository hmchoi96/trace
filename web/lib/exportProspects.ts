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

function scopeLine(args: {
  peopleCount: number;
  campaignCount: number;
  listTitle: string;
  foundOn: string;
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
  bits.push(`Sorted by ${args.sortCaption}`);
  return bits.join(" · ");
}

function stat(value: number, label: string): string {
  return `<div class="stat"><b>${value}</b><span>${esc(label)}</span></div>`;
}

function tableColumns(showPhone: boolean): { label: string; width: string }[] {
  if (showPhone) {
    return [
      { label: "Person", width: "13%" },
      { label: "Company", width: "11%" },
      { label: "Actor", width: "10%" },
      { label: "Status", width: "13%" },
      { label: "Email", width: "18%" },
      { label: "Phone", width: "11%" },
      { label: "Found on", width: "7%" },
      { label: "Latest signal", width: "9%" },
      { label: "Last event", width: "8%" },
    ];
  }
  return [
    { label: "Person", width: "15%" },
    { label: "Company", width: "12%" },
    { label: "Actor", width: "11%" },
    { label: "Status", width: "15%" },
    { label: "Email", width: "19%" },
    { label: "Found on", width: "8%" },
    { label: "Latest signal", width: "10%" },
    { label: "Last event", width: "10%" },
  ];
}

function tableColgroup(showPhone: boolean): string {
  return `<colgroup>${tableColumns(showPhone)
    .map((column) => `<col style="width:${column.width}">`)
    .join("")}</colgroup>`;
}

function tableHead(showPhone: boolean): string {
  return tableColumns(showPhone)
    .map((column) => `<th>${column.label}</th>`)
    .join("");
}

function tableRow(person: Person, showPhone: boolean): string {
  const email = person.email?.trim();
  const phone = person.phone?.trim();
  const emailCell = email
    ? `<span class="strong">${emailHtml(email)}</span>${
        person.emailSource ? `<span class="muted">${text(person.emailSource)}</span>` : ""
      }`
    : `<span class="muted">Not found</span>`;
  const cells = [
    `<td><span class="strong">${text(person.name, "Unnamed")}</span>${
      person.title?.trim() ? `<span class="muted">${text(person.title)}</span>` : ""
    }</td>`,
    `<td>${text(person.company, "Not reported")}</td>`,
    `<td>${text(actorName(person) || "Not reported")}</td>`,
    `<td class="${statusClass(person.status)}">${text(statusLabel(person.status))}</td>`,
    `<td>${emailCell}</td>`,
    ...(showPhone
      ? [
          `<td>${
            phone
              ? `<span class="strong">${text(phone)}</span>${
                  person.phoneSource ? `<span class="muted">${text(person.phoneSource)}</span>` : ""
                }`
              : `<span class="muted">Not found</span>`
          }</td>`,
        ]
      : []),
    `<td>${text(foundOnLabel(person))}</td>`,
    `<td>${text(signalWhen(person))}</td>`,
    `<td>${text(lastEvent(person))}</td>`,
  ];
  return `<tr>${cells.join("")}</tr>`;
}

function fact(label: string, value: string): string {
  return `<div><dt>${esc(label)}</dt><dd>${value}</dd></div>`;
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
  const read =
    recommendation || reason
      ? `<p class="read"><span class="label">${text(readLabel)}</span>${
          reason ? ` ${text(reason)}` : ""
        }</p>`
      : "";
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
    <h2>${text(person.name, "Unnamed")}</h2>
    <p class="sub">${text(sub)}</p>
    <dl>${facts.join("")}</dl>
    ${quote}
    ${why}
    ${source}
    ${more}
    ${read}
    ${noteBlock}
  </article>`;
}

const CSS = `
@page {
  size: letter landscape;
  margin: 0.55in 0.6in 0.65in;
  @bottom-left {
    content: "Trace";
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 9px;
    color: #737373;
  }
  @bottom-right {
    content: counter(page);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 9px;
    color: #737373;
  }
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
body { max-width: 1100px; margin: 0 auto; padding: 0 32px 72px; }
a { color: #24506f; overflow-wrap: anywhere; }
.toolbar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 16px;
  margin: 0 -32px 28px;
  padding: 12px 32px;
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
.lede { margin: 8px 0 0; max-width: 70ch; color: #525252; font-size: 14px; }
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
table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 11px; }
th {
  padding: 0 10px 6px 0;
  border-bottom: 1px solid #171717;
  color: #737373;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.3;
  text-align: left;
}
td {
  padding: 7px 10px 7px 0;
  border-bottom: 1px solid #e5e5e5;
  vertical-align: top;
  overflow-wrap: break-word;
}
td:nth-last-child(-n + 2) { white-space: nowrap; }
.strong { font-weight: 600; }
.muted { display: block; margin-top: 1px; color: #737373; font-weight: 400; }
.muted.inline, .muted.block { display: inline; }
.muted.block { display: block; }
.tone-info { color: #24506f; }
.tone-warn { color: #6b5320; }
.tone-danger { color: #7a3630; }
.tone-muted { color: #737373; }
article.person { padding: 16px 0 8px; border-top: 1px solid #e5e5e5; }
article.person h2 { margin: 0; font-size: 16px; font-weight: 600; }
.sub { margin: 2px 0 10px; color: #525252; }
dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 18px; margin: 0 0 12px; }
dt { color: #737373; font-size: 10px; font-weight: 600; letter-spacing: 0.03em; text-transform: uppercase; }
dd { margin: 1px 0 0; overflow-wrap: anywhere; }
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
  thead { display: table-header-group; }
  article.person h2, article.person .sub, article.person dl { break-after: avoid; }
  tr, blockquote { break-inside: avoid; }
}
`;

export function buildProspectHtml(args: {
  profile: Pick<Profile, "name" | "productName" | "huntDescription">;
  people: Person[];
  campaignCount: number;
  listTitle: string;
  foundOn: string;
  sortCaption: string;
  exportedAt?: Date;
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
  <p>Print this page and choose Save as PDF. This bar is left out of the file.</p>
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
      sortCaption: args.sortCaption,
      exportedAt,
    }),
  )}</p>
<div class="stats">
  ${stat(args.people.length, args.people.length === 1 ? "Person" : "People")}
  ${stat(withEmail, "With email")}
  ${stat(sent, "Sent")}
  ${showPhone ? stat(withPhone, "With phone") : ""}
</div>
<h2 class="section">List</h2>
<table>
  ${tableColgroup(showPhone)}
  <thead><tr>${tableHead(showPhone)}</tr></thead>
  <tbody>${args.people.map((person) => tableRow(person, showPhone)).join("")}</tbody>
</table>
<h2 class="section">People</h2>
${args.people.map((person) => personCard(person)).join("\n")}
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
