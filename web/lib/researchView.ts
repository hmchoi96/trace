import type { Person } from "./api";

export function hasStructuredResearch(person: Person): boolean {
  const research = person.research;
  if (!research) return false;
  return (
    research.verifiedFacts.length > 0 ||
    research.currentWorkarounds.length > 0 ||
    research.inferences.length > 0 ||
    research.unknowns.length > 0 ||
    Boolean(research.gapAssessment?.status)
  );
}

function lines(title: string, items: string[]): string[] {
  if (items.length === 0) return [title, "None recorded"];
  return [title, ...items.map((item) => `- ${item}`)];
}

function confidence(value: string): string {
  const text = value.trim().toLowerCase();
  if (!text) return "";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Plain section text shared by the detail view and the printable export. */
export function researchPlainText(person: Person): string {
  const summary = person.decisionSummary;
  const research = person.research;
  const out: string[] = [];
  if (summary) {
    out.push(
      "Decision",
      summary.decision || "Not reported",
      "Why now",
      summary.whyNow || "Not reported",
      "Why this person",
      summary.whyThisPerson || "Not reported",
      "Reply reason",
      summary.replyReason || "Not reported",
      "Do not claim",
      summary.doNotClaim || "Not reported",
    );
  }
  out.push(
    `Draft decision: ${person.draftDecision || "Not reported"}`,
    `Outreach motion: ${person.outreachMotion || "Not reported"}`,
    `Trigger offer alignment: ${person.triggerOfferAlignment || "Not reported"}`,
    `Gap assessment: ${person.gapStatus || research?.gapAssessment?.status || "Not reported"}`,
    `Contact status: ${person.contactStatus || "Not reported"}`,
  );
  if (!research) return out.join("\n");
  out.push(
    ...lines(
      "Verified facts",
      research.verifiedFacts.map((fact) => fact.claim).filter(Boolean),
    ),
    ...lines(
      "Current workarounds",
      research.currentWorkarounds.map((fact) => fact.claim).filter(Boolean),
    ),
    ...lines(
      "Trace inferences",
      research.inferences.map((item) => {
        const rank = confidence(item.confidence);
        return rank ? `${item.claim} [${rank}]` : item.claim;
      }),
    ),
    ...lines("Unknowns", research.unknowns),
    ...lines("Sources", sourceUrls(person)),
  );
  return out.join("\n");
}

export function sourceUrls(person: Person): string[] {
  const research = person.research;
  if (!research) return [];
  const urls: string[] = [];
  for (const fact of [...research.verifiedFacts, ...research.currentWorkarounds]) {
    const url = fact.sourceUrl?.trim();
    if (url && !urls.includes(url)) urls.push(url);
  }
  return urls;
}

function esc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function researchSectionsHtml(person: Person): string {
  if (!person.decisionSummary && !hasStructuredResearch(person)) return "";
  const text = researchPlainText(person);
  const blocks = text.split("\n");
  const html: string[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length === 0) return;
    html.push(`<ul class="signals">${list.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`);
    list = [];
  };
  for (const line of blocks) {
    if (line.startsWith("- ")) {
      list.push(line.slice(2));
      continue;
    }
    flush();
    if (line.endsWith(":") || line.includes(": ")) {
      html.push(`<p>${esc(line)}</p>`);
    } else if (
      [
        "Decision",
        "Why now",
        "Why this person",
        "Reply reason",
        "Do not claim",
        "Verified facts",
        "Current workarounds",
        "Trace inferences",
        "Unknowns",
        "Sources",
      ].includes(line)
    ) {
      html.push(`<h3>${esc(line)}</h3>`);
    } else {
      html.push(`<p>${esc(line)}</p>`);
    }
  }
  flush();
  return html.join("");
}
