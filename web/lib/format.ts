import type { Person, PersonStatus } from "./api";

export function usd(value: number): string {
  return `$${value.toFixed(2)}`;
}

/** VERY_HIGH -> "Very high". PERSONA_FIT -> "Persona fit". */
export function sentenceCase(value: string): string {
  const words = value.replace(/_/g, " ").trim().toLowerCase();
  if (!words) return "";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function axisRows(axes: Record<string, string>): [string, string][] {
  return Object.entries(axes)
    .filter(([, score]) => typeof score === "string" && score.length > 0)
    .map(([axis, score]) => [sentenceCase(axis), sentenceCase(score)]);
}

export function actorLabel(person: Person): string {
  const raw = person.actorType?.trim();
  if (raw) return sentenceCase(raw);
  if (person.outreachRole?.trim()) return person.outreachRole;
  return "—";
}

export function statusLabel(status: PersonStatus): string {
  if (status === "sent") return "Sent";
  if (status === "draft") return "Draft, not sent";
  if (status === "draft_failed") return "Draft failed";
  if (status === "approved") return "Approved, no draft yet";
  if (status === "passed") return "Passed";
  if (status === "closed") return "Closed";
  if (status === "disqualified") return "Disqualified";
  return "Researched, not sent";
}

/** ISO or free-form date to a short readable day. Falls back to the raw string. */
export function shortDate(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function daysAgo(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const days = Math.floor((Date.now() - parsed.getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

export function latestSignalAt(person: Person): string {
  return person.signal.date || person.createdAt || "";
}

export function latestSignalLabel(person: Person): string {
  const at = latestSignalAt(person);
  if (!at) return "Not reported";
  const ago = daysAgo(at);
  return ago ? `${shortDate(at)} · ${ago}` : shortDate(at);
}

export function foundOnLabel(person: Person): string {
  return person.foundOn || "Web";
}

export function lastEvent(person: Person): string {
  if (person.status === "disqualified") return event("Disqualified", person.decidedAt);
  if (person.status === "closed") return "Closed";
  if (person.sentAt && person.sendMethod === "self") return event("You sent", person.sentAt);
  if (person.sentAt) return event("Sent", person.sentAt);
  if (person.status === "draft") return event("Draft since", person.draft?.createdAt);
  if (person.status === "draft_failed") return event("Draft failed", person.draft?.createdAt);
  if (person.status === "passed") return event("Passed", person.decidedAt);
  if (person.status === "approved") return event("Approved", person.decidedAt);
  return event("Researched", person.createdAt);
}

function event(label: string, at: string | null | undefined): string {
  const when = shortDate(at);
  return when ? `${label} ${when}` : label;
}

export function emailSourceLabel(person: Person): string {
  return person.emailSource || "Not found";
}

export function senderLine(profile: { senderName: string; senderCompany: string }): string {
  return (
    [profile.senderName, profile.senderCompany].filter(Boolean).join(", ") || "Name, company"
  );
}

export function timestamp(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Seconds to "2 min" or "about 4 min left". */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 45) return "under a minute";
  const minutes = Math.max(1, Math.round(total / 60));
  if (minutes === 1) return "about 1 min";
  return `about ${minutes} min`;
}

export function formatEta(remainingSec: number, running: boolean): string {
  if (!running) return "";
  if (remainingSec <= 0) return "Finishing up";
  return `${formatDuration(remainingSec)} left`;
}

export function huntStatusLabel(status: string): string {
  if (status === "running") return "Running";
  if (status === "queued") return "Queued";
  if (status === "done") return "Done";
  if (status === "failed") return "Failed";
  if (status === "cancelled") return "Cancelled";
  return status;
}
