"use client";

import { useEffect, useRef, useState } from "react";
import { EvidencePanel } from "../components/EvidencePanel";
import {
  BarChart,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  ClickStat,
  H2,
  H3,
  Pill,
  Row,
  Select,
  Spacer,
  Stack,
  Table,
  Text,
  TextArea,
  type Tone,
} from "../components/ui";
import type { Health, HuntSummary, Person, Profile, Template } from "../lib/api";
import {
  actorLabel,
  emailSourceLabel,
  formatDuration,
  formatEta,
  foundOnLabel,
  huntStatusLabel,
  lastEvent,
  latestSignalAt,
  latestSignalLabel,
  senderLine,
  statusLabel,
  timestamp,
} from "../lib/format";

type RecordFilter = "all" | "researched" | "sent" | "replied";
type FoundFilter = "all" | "LinkedIn" | "X" | "Web";
type SortKey =
  | "added"
  | "name"
  | "company"
  | "actor"
  | "status"
  | "foundOn"
  | "signal"
  | "email"
  | "event";
type SortDir = "asc" | "desc";

const LIST_MIN = 4;
const LIST_MAX = 12;
const LIST_HEADER_PX = 40;
const LIST_ROW_PX = 48;
const LIST_ROW_WITH_SEND_PX = 72;

function clampListSize(count: number, total: number) {
  if (total <= LIST_MIN) return total;
  return Math.min(total, LIST_MAX, Math.max(LIST_MIN, count));
}

function tableViewportHeight(visibleRows: number, withSend: boolean) {
  const row = withSend ? LIST_ROW_WITH_SEND_PX : LIST_ROW_PX;
  return LIST_HEADER_PX + visibleRows * row;
}

function matchesFilter(person: Person, filter: RecordFilter) {
  if (filter === "sent") return Boolean(person.sentAt) && person.status !== "disqualified";
  if (filter === "researched") return !person.sentAt;
  if (filter === "replied") return false;
  return true;
}

function filterTitle(filter: RecordFilter) {
  if (filter === "sent") return "Sent";
  if (filter === "researched") return "Researched, not sent";
  if (filter === "replied") return "Replied";
  return "In this campaign";
}

function emptyMessage(filter: RecordFilter) {
  if (filter === "sent") return "No one has been sent from this profile yet.";
  if (filter === "researched") return "No one is waiting on a send decision yet.";
  if (filter === "replied") return "Replies are not tracked yet.";
  return "A hunt has not put anyone in this campaign yet.";
}

function canMarkSent(person: Person) {
  return (
    Boolean(person.email) &&
    !person.sentAt &&
    person.status !== "passed" &&
    person.status !== "closed" &&
    person.status !== "disqualified"
  );
}

function sortValue(person: Person, key: SortKey): string | number {
  if (key === "added") return new Date(person.createdAt ?? 0).getTime() || 0;
  if (key === "signal") return new Date(latestSignalAt(person)).getTime() || 0;
  if (key === "name") return person.name;
  if (key === "company") return person.company;
  if (key === "actor") return actorLabel(person);
  if (key === "status") return statusLabel(person.status);
  if (key === "foundOn") return foundOnLabel(person);
  if (key === "email") return emailSourceLabel(person);
  return lastEvent(person);
}

function compareRows(a: Person, b: Person, key: SortKey, dir: SortDir) {
  const mul = dir === "asc" ? 1 : -1;
  const left = sortValue(a, key);
  const right = sortValue(b, key);
  if (typeof left === "number" && typeof right === "number") return (left - right) * mul;
  return String(left).localeCompare(String(right)) * mul;
}

function rowTone(person: Person): Tone {
  if (person.status === "disqualified") return "danger";
  if (person.status === "sent") return "info";
  if (person.status === "closed") return "neutral";
  if (person.status === "draft") return "warning";
  return "neutral";
}

function TableListHandle({
  shown,
  total,
  onChange,
}: {
  shown: number;
  total: number;
  onChange: (next: number) => void;
}) {
  const drag = useRef<{ y: number; count: number } | null>(null);
  if (total <= LIST_MIN) return null;
  const cap = Math.min(total, LIST_MAX);
  return (
    <Stack gap={4}>
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-valuemin={LIST_MIN}
        aria-valuemax={cap}
        aria-valuenow={shown}
        title="Drag to make the table taller or shorter"
        className="list-handle"
        onPointerDown={(event) => {
          event.preventDefault();
          drag.current = { y: event.clientY, count: shown };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const state = drag.current;
          if (!state) return;
          const next = Math.round(state.count + (event.clientY - state.y) / 48);
          onChange(clampListSize(next, total));
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <div className="list-handle-bar" />
      </div>
      <Text size="small" tone="tertiary">
        Window is {shown} rows. Hover the table and scroll to see all {total}. Drag the line to
        make the window taller, up to {cap} rows.
      </Text>
    </Stack>
  );
}

function DraftTemplatePicker({
  person,
  profile,
  templates,
  busy,
  onRewrite,
  onRewritten,
}: {
  person: Person;
  profile: Profile;
  templates: Template[];
  busy: boolean;
  onRewrite: (personId: string, templateId: string) => Promise<void>;
  onRewritten?: () => void;
}) {
  const fallback = templates[0]?.id ?? profile.defaultTemplate ?? "strategy";
  const [templateId, setTemplateId] = useState(
    person.draft?.templateId ?? profile.defaultTemplate ?? fallback,
  );
  const [rewriting, setRewriting] = useState(false);

  useEffect(() => {
    setTemplateId(person.draft?.templateId ?? profile.defaultTemplate ?? fallback);
  }, [person.id, person.draft?.templateId, profile.defaultTemplate, fallback]);

  const current = templates.find((t) => t.id === templateId);

  async function regenerate() {
    setRewriting(true);
    try {
      await onRewrite(person.id, templateId);
      onRewritten?.();
    } finally {
      setRewriting(false);
    }
  }

  if (templates.length === 0) return null;

  return (
    <Stack gap={6}>
      <Stack gap={4}>
        <Text size="small" tone="tertiary">
          Email template
        </Text>
        <Row gap={8} align="center" wrap>
          <Select
            value={templateId}
            onChange={setTemplateId}
            options={templates.map((t) => ({
              value: t.id,
              label: t.label,
            }))}
          />
          <Button
            variant="secondary"
            disabled={busy || rewriting}
            onClick={() => void regenerate()}
          >
            {rewriting
              ? "Writing…"
              : person.draft?.body || person.status === "draft_failed"
                ? "Rewrite draft"
                : "Write draft"}
          </Button>
        </Row>
        {current && (
          <Text size="small" tone="secondary">
            {current.label}: {current.description}
          </Text>
        )}
      </Stack>
    </Stack>
  );
}

function ContactFields({
  person,
  health,
  busy,
  pullingContact,
  onPullContact,
  onSaveContact,
}: {
  person: Person;
  health: Health | null;
  busy: boolean;
  pullingContact: boolean;
  onPullContact: (person: Person) => void;
  onSaveContact: (personId: string, contact: { email: string; phone: string }) => Promise<void>;
}) {
  const lookupReady = Boolean(health?.apollo || health?.hunter);
  const [emailDraft, setEmailDraft] = useState(person.email ?? "");
  const [phoneDraft, setPhoneDraft] = useState(person.phone ?? "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setEmailDraft(person.email ?? "");
    setPhoneDraft(person.phone ?? "");
  }, [person.id, person.email, person.phone]);

  const dirty =
    emailDraft.trim() !== (person.email ?? "").trim() ||
    phoneDraft.trim() !== (person.phone ?? "").trim();

  async function save() {
    setSaving(true);
    try {
      await onSaveContact(person.id, {
        email: emailDraft.trim(),
        phone: phoneDraft.trim(),
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Stack gap={8}>
      <H3>Contact</H3>
      <Text size="small" tone="secondary">
        Add or fix email and phone here. Saved values show their source (Apollo, Hunter.io, Manual, etc.).
      </Text>
      <Stack gap={6}>
        <Stack gap={4}>
          <Text size="small" tone="tertiary">
            Email
          </Text>
          <input
            className="input"
            type="email"
            value={emailDraft}
            placeholder="you@company.com"
            onChange={(e) => setEmailDraft(e.target.value)}
          />
          {person.emailSource && person.email && !dirty && (
            <Text size="small" tone="tertiary">
              Source: {person.emailSource}
            </Text>
          )}
        </Stack>
        <Stack gap={4}>
          <Text size="small" tone="tertiary">
            Phone
          </Text>
          <input
            className="input"
            type="tel"
            value={phoneDraft}
            placeholder="+1 555 000 0000"
            onChange={(e) => setPhoneDraft(e.target.value)}
          />
          {person.phoneSource && person.phone && !dirty && (
            <Text size="small" tone="tertiary">
              Source: {person.phoneSource}
            </Text>
          )}
        </Stack>
      </Stack>
      <Row gap={8} wrap>
        <Button
          variant="primary"
          disabled={busy || saving || !dirty || (!emailDraft.trim() && !phoneDraft.trim())}
          onClick={() => void save()}
        >
          {saving ? "Saving…" : "Save contact"}
        </Button>
        <Button
          variant="secondary"
          disabled={!lookupReady || busy || pullingContact || saving}
          onClick={() => onPullContact(person)}
        >
          {pullingContact ? "Looking up contact…" : "Pull contact"}
        </Button>
      </Row>
      {!lookupReady && (
        <Text size="small" tone="tertiary">
          Add APOLLO_API_KEY and/or HUNTER_API_KEY to .env. Apollo runs first; Hunter.io only
          if Apollo does not find an email.
        </Text>
      )}
    </Stack>
  );
}

function RecordDetail({
  person,
  profile,
  templates,
  from,
  health,
  onSendDraft,
  onSentMyself,
  onPullContact,
  onSaveContact,
  onRewriteDraft,
  onNote,
  onOutcome,
  actionError,
  busy,
  pullingContact,
}: {
  person: Person;
  profile: Profile;
  templates: Template[];
  from: string;
  health: Health | null;
  onSendDraft: (person: Person) => void;
  onSentMyself: (person: Person) => void;
  onPullContact: (person: Person) => void;
  onSaveContact: (personId: string, contact: { email: string; phone: string }) => Promise<void>;
  onRewriteDraft: (personId: string, templateId: string) => Promise<void>;
  onNote: (person: Person, text: string) => Promise<void>;
  onOutcome: (person: Person, outcome: "closed" | "disqualified") => void;
  actionError: string | null;
  busy: boolean;
  pullingContact: boolean;
}) {
  const [noteDraft, setNoteDraft] = useState("");
  const mailed = Boolean(person.sentAt);
  const mailboxReady = Boolean(health?.mailboxReady);

  useEffect(() => {
    setNoteDraft("");
  }, [person.id]);

  const accounts: string[][] = [];
  if (person.linkedinUrl) accounts.push(["LinkedIn", person.linkedinUrl]);
  if (person.signal.url) accounts.push(["Signal", person.signal.url]);

  return (
    <Stack gap={16}>
      <Card size="lg">
        <CardHeader trailing={statusLabel(person.status)}>{person.name}</CardHeader>
        <CardBody>
          <Stack gap={12}>
            <Stack gap={4}>
              <Text tone="secondary">
                {person.title} · {person.company}
              </Text>
              <Text size="small">
                Signal found on {foundOnLabel(person)}
                {person.signal.url ? ` · ${person.signal.url}` : ""}
              </Text>
            </Stack>

            {accounts.length > 0 && <Table headers={["Account", "URL"]} rows={accounts} />}

            <EvidencePanel person={person} />

            {actionError && (
              <Callout tone="danger" title="That did not go through">
                {actionError}
              </Callout>
            )}

            {mailed && person.draft?.subject && (
              <Stack gap={4}>
                <Text size="small" tone="tertiary">
                  {person.sendMethod === "self"
                    ? "You sent this from your inbox"
                    : `Sent by Trace as ${from}`}
                </Text>
                <Text weight="semibold">{person.draft.subject}</Text>
                {person.draft.body && <Text>{person.draft.body}</Text>}
              </Stack>
            )}

            {canMarkSent(person) && !mailed && (
              <>
                <DraftTemplatePicker
                  person={person}
                  profile={profile}
                  templates={templates}
                  busy={busy}
                  onRewrite={onRewriteDraft}
                />

                <Stack gap={8}>
                  <H3>Draft Trace wrote</H3>
                  {person.status === "draft_failed" && (
                    <Callout tone="warning" title="Draft failed">
                      {person.draft?.error || "Trace could not produce a draft."}
                    </Callout>
                  )}
                  {!person.draft?.body && person.status === "approved" && (
                    <Callout tone="info" title="Trace is writing the draft">
                      Claude is drafting from your profile template and this person&apos;s
                      research. Usually takes a minute or two. This screen refreshes until the
                      draft is ready.
                    </Callout>
                  )}
                  {person.draft?.body && (
                    <Callout tone="warning" title="Not sent yet">
                      Read the draft below before sending from {from}.
                    </Callout>
                  )}
                  {person.email && (
                    <Text size="small" tone="tertiary">
                      To {person.email} · {person.emailSource || "Unknown source"}
                    </Text>
                  )}
                  {person.draft?.templateId && (
                    <Text size="small" tone="tertiary">
                      Template:{" "}
                      {templates.find((t) => t.id === person.draft?.templateId)?.label ??
                        person.draft.templateId}
                    </Text>
                  )}
                  <Text size="small" tone="tertiary">
                    Subject
                  </Text>
                  <Text weight="semibold">{person.draft?.subject || "No subject yet"}</Text>
                  <Text size="small" tone="tertiary">
                    Body
                  </Text>
                  <div className="quote">
                    <Text>
                      {person.draft?.body ||
                        (person.status === "draft_failed"
                          ? "No draft body was saved."
                          : person.status === "approved"
                            ? "Draft in progress…"
                            : "Pick a template and click Write draft, or wait for Trace to finish.")}
                    </Text>
                  </div>
                  {person.draft && !person.draft.sendable && person.draft.body && (
                    <Callout tone="warning" title="This draft is not ready to send">
                      {person.draft.verdict
                        ? `Trace's verdict: ${person.draft.verdict}`
                        : "Trace held it back."}
                    </Callout>
                  )}
                  {!mailboxReady && (
                    <Callout tone="warning" title="Trace sending is off">
                      No mailbox is connected
                      {health?.mailbox ? ` (${health.mailbox})` : ""}, so Trace cannot send this.
                    </Callout>
                  )}
                  {person.draft?.body && (
                    <Row gap={8} wrap>
                      <Button
                        variant="primary"
                        onClick={() => onSendDraft(person)}
                        disabled={
                          busy || !person.draft?.sendable || !mailboxReady
                        }
                      >
                        Send this
                      </Button>
                      <Button variant="ghost" onClick={() => onSentMyself(person)} disabled={busy}>
                        I&apos;ll write it myself
                      </Button>
                    </Row>
                  )}
                </Stack>
              </>
            )}
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>File, contact, and what you do next</CardHeader>
        <CardBody>
          <Stack gap={12}>
            <Callout tone="info" title="Already in this file">
              Next hunt for this profile will skip {person.name}. Research is not redone from
              scratch.
            </Callout>

            <ContactFields
              person={person}
              health={health}
              busy={busy}
              pullingContact={pullingContact}
              onPullContact={onPullContact}
              onSaveContact={onSaveContact}
            />

            {mailed && (
              <Stack gap={6}>
                <H3>Tracking</H3>
                <Callout tone="warning" title="Opens and clicks are not tracked">
                  Trace records that mail went out. It does not read delivery, opens, or
                  clicks, and replies are not wired up yet.
                </Callout>
                <Table
                  headers={["Event", "When"]}
                  rows={[
                    [
                      person.sendMethod === "self" ? "You sent" : "Sent by Trace",
                      timestamp(person.sentAt) || "Not reported",
                    ],
                  ]}
                />
              </Stack>
            )}

            <Stack gap={8}>
              <H3>Notes and outcome</H3>
              {person.notes.length > 0 && (
                <Table
                  headers={["When", "Note"]}
                  rows={person.notes.map((note) => [timestamp(note.at), note.text])}
                />
              )}
              <TextArea
                value={noteDraft}
                onChange={setNoteDraft}
                rows={3}
                placeholder="What happened on the call, why they are closed, why they are not a fit"
              />
              <Row gap={8} wrap>
                <Button
                  variant="primary"
                  disabled={!noteDraft.trim() || busy}
                  onClick={async () => {
                    const text = noteDraft.trim();
                    if (!text) return;
                    await onNote(person, text);
                    setNoteDraft("");
                  }}
                >
                  Save note
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => onOutcome(person, "closed")}
                  disabled={busy || person.status === "closed"}
                >
                  Close
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => onOutcome(person, "disqualified")}
                  disabled={busy || person.status === "disqualified"}
                >
                  Disqualify
                </Button>
              </Row>
              <Text size="small" tone="tertiary">
                Close means done with them. Disqualify means never a buyer. Both stay in the
                file so the next hunt skips them.
              </Text>
            </Stack>
          </Stack>
        </CardBody>
      </Card>
    </Stack>
  );
}

export function Records({
  profile,
  people,
  templates,
  loading,
  health,
  selectedId,
  onSelect,
  onHunt,
  recentHunts,
  activeHuntId,
  onOpenHunt,
  onReviewHunt,
  onNote,
  onOutcome,
  onSendDraft,
  onSentMyself,
  onPullContact,
  onPullContactsBulk,
  onSaveContact,
  onRewriteDraft,
  onRefresh,
  actionError,
  busy,
  pullingContactId,
  bulkPullProgress,
}: {
  profile: Profile;
  people: Person[];
  templates: Template[];
  loading: boolean;
  health: Health | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onHunt: () => void;
  recentHunts: HuntSummary[];
  activeHuntId: string | null;
  onOpenHunt: (huntId: string) => void;
  onReviewHunt: (huntId: string) => void;
  onNote: (person: Person, text: string) => Promise<void>;
  onOutcome: (person: Person, outcome: "closed" | "disqualified") => void;
  onSendDraft: (person: Person) => void;
  onSentMyself: (person: Person) => void;
  onPullContact: (person: Person) => void;
  onPullContactsBulk: (people: Person[]) => void | Promise<void>;
  onSaveContact: (personId: string, contact: { email: string; phone: string }) => Promise<void>;
  onRewriteDraft: (personId: string, templateId: string) => Promise<void>;
  onRefresh: () => void;
  actionError: string | null;
  busy: boolean;
  pullingContactId: string | null;
  bulkPullProgress: { done: number; total: number; found: number } | null;
}) {
  const [filter, setFilter] = useState<RecordFilter>("all");
  const [foundFilter, setFoundFilter] = useState<FoundFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("added");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [listSize, setListSize] = useState(LIST_MIN);

  useEffect(() => {
    setFilter("all");
    setFoundFilter("all");
  }, [profile.id]);

  useEffect(() => {
    if (!selectedId) return;
    const person = people.find((p) => p.id === selectedId);
    if (!person || person.sentAt || person.draft?.body) return;
    if (person.status !== "approved" && person.status !== "draft_failed") return;
    const timer = window.setInterval(() => onRefresh(), 3000);
    return () => window.clearInterval(timer);
  }, [selectedId, people, onRefresh]);

  const from = senderLine(profile);
  const rows = people
    .filter((p) => matchesFilter(p, filter))
    .filter((p) => foundFilter === "all" || foundOnLabel(p) === foundFilter)
    .slice()
    .sort((a, b) => compareRows(a, b, sortKey, sortDir));

  const selected = rows.find((p) => p.id === selectedId) ?? rows[0];
  const sentCount = people.filter((p) => Boolean(p.sentAt)).length;
  const researchedCount = people.filter((p) => !p.sentAt).length;
  const traceSent = people.filter((p) => p.sentAt && p.sendMethod !== "self").length;
  const selfSent = people.filter((p) => p.sentAt && p.sendMethod === "self").length;
  const showTracking = (filter === "sent" || filter === "replied") && sentCount > 0;

  function pick(next: RecordFilter) {
    setFilter(next);
    const match = people.find((p) => matchesFilter(p, next));
    if (match) onSelect(match.id);
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
      return;
    }
    setSortKey(key);
    setSortDir(key === "name" || key === "company" ? "asc" : "desc");
  }

  function sortHead(label: string, key: SortKey) {
    const mark = sortKey === key ? sortDir : undefined;
    return (
      <Pill size="sm" active={sortKey === key} onClick={() => toggleSort(key)}>
        {mark ? `${label} ${mark}` : label}
      </Pill>
    );
  }

  const visibleRows = clampListSize(listSize, rows.length);
  const withSend = filter !== "sent" && filter !== "replied";
  const missingContact = rows.filter((p) => !String(p.email || "").trim());
  const lookupReady = Boolean(health?.apollo || health?.hunter);
  const pullingBulk = bulkPullProgress !== null;
  const showBulkPull = missingContact.length > 0 || pullingBulk;

  return (
    <Stack gap={16}>
      <Stack gap={4}>
        <H2>{profile.name}</H2>
        <Text tone="secondary">
          {profile.huntDescription}. From {from}. Click a number to open that list.
        </Text>
      </Stack>

      {people.length > 0 && (
        <Callout tone="info" title="Next hunt skips this file">
          {people.length} people already researched here will not show up again, including
          closed and disqualified.
        </Callout>
      )}

      {recentHunts.length > 0 && (
        <Card>
          <CardHeader>Recent hunts</CardHeader>
          <CardBody>
            <Stack gap={10}>
              <Text size="small" tone="secondary">
                Start a hunt and keep browsing. Come back here to reopen one while it runs or
                after it finishes.
              </Text>
              <Table
                headers={["When", "Status", "Progress", "Found", ""]}
                rows={recentHunts.slice(0, 8).map((item) => {
                  const running = item.status === "queued" || item.status === "running";
                  const action =
                    item.status === "done" ? (
                      <Button variant="primary" onClick={() => onReviewHunt(item.id)}>
                        Review
                      </Button>
                    ) : running ? (
                      <Button variant="primary" onClick={() => onOpenHunt(item.id)}>
                        {item.id === activeHuntId ? "Watch" : "Open"}
                      </Button>
                    ) : item.status === "failed" ? (
                      <Button variant="ghost" onClick={() => onOpenHunt(item.id)}>
                        Details
                      </Button>
                    ) : (
                      ""
                    );
                  return [
                    timestamp(item.startedAt || item.createdAt),
                    item.id === activeHuntId && running
                      ? `${huntStatusLabel(item.status)} now`
                      : huntStatusLabel(item.status),
                    running
                      ? `${item.progress || "Running"} · ${formatEta(item.remainingSec, true)}`
                      : item.progress || "—",
                    String(item.candidateCount),
                    action,
                  ];
                })}
              />
            </Stack>
          </CardBody>
        </Card>
      )}

      <Row gap={8} wrap>
        <ClickStat
          active={filter === "all"}
          value={String(people.length)}
          label="In this campaign"
          onClick={() => pick("all")}
        />
        <ClickStat
          active={filter === "researched"}
          value={String(researchedCount)}
          label="Researched, not sent"
          onClick={() => pick("researched")}
        />
        <ClickStat
          active={filter === "sent"}
          value={String(sentCount)}
          label="Sent"
          tone="info"
          onClick={() => pick("sent")}
        />
        <ClickStat
          active={filter === "replied"}
          value="0"
          label="Replied — not tracked yet"
          onClick={() => pick("replied")}
        />
      </Row>

      {showTracking && (
        <Stack gap={8}>
          <H3>{profile.name} email tracking</H3>
          <BarChart
            categories={["Sent by Trace", "Sent myself"]}
            series={[{ name: "People", data: [traceSent, selfSent], tone: "info" }]}
            height={140}
            valueSuffix=" people"
          />
          <Callout tone="warning" title="Opens and clicks are not a given">
            Trace records that mail went out and nothing else. There is no open or click pixel
            behind these numbers, and replies are not wired up yet, so this is a count of sends,
            not a funnel.
          </Callout>
        </Stack>
      )}

      {loading && people.length === 0 && (
        <Callout tone="neutral" title={`Loading ${profile.name}`}>
          Reading this profile&apos;s people from the Trace API.
        </Callout>
      )}

      {rows.length > 0 && (
        <Stack gap={12}>
          <Row gap={12} wrap align="center">
            <H3>
              {filterTitle(filter)} · {rows.length}
            </H3>
            <Spacer />
            {showBulkPull && (
              <Button
                variant="secondary"
                disabled={!lookupReady || busy || pullingBulk}
                onClick={() => void onPullContactsBulk(missingContact)}
              >
                {pullingBulk && bulkPullProgress
                  ? `Looking up ${bulkPullProgress.done}/${bulkPullProgress.total}… (${bulkPullProgress.found} found)`
                  : `Find missing contacts (${missingContact.length})`}
              </Button>
            )}
          </Row>
          {!lookupReady && missingContact.length > 0 && (
            <Text size="small" tone="tertiary">
              Add APOLLO_API_KEY and/or HUNTER_API_KEY to .env to look up contacts in bulk.
            </Text>
          )}
          <Row gap={8} wrap align="center">
            <Text size="small" tone="secondary">
              Sort
            </Text>
            <Pill
              size="sm"
              active={sortKey === "added"}
              onClick={() => {
                setSortKey("added");
                setSortDir("desc");
              }}
            >
              Date added
            </Pill>
            <Text size="small" tone="secondary">
              Found on
            </Text>
            {(["all", "LinkedIn", "X", "Web"] as FoundFilter[]).map((option) => (
              <Pill
                key={option}
                size="sm"
                active={foundFilter === option}
                onClick={() => setFoundFilter(option)}
              >
                {option === "all" ? "All" : option}
              </Pill>
            ))}
          </Row>

          <Table
            headers={
              withSend
                ? [
                    sortHead("Person", "name"),
                    sortHead("Company", "company"),
                    sortHead("Actor", "actor"),
                    sortHead("Status", "status"),
                    sortHead("Found on", "foundOn"),
                    sortHead("Latest signal", "signal"),
                    sortHead("Email source", "email"),
                    sortHead("Last event", "event"),
                    "Send",
                  ]
                : [
                    sortHead("Person", "name"),
                    sortHead("Company", "company"),
                    sortHead("Actor", "actor"),
                    sortHead("Status", "status"),
                    sortHead("Found on", "foundOn"),
                    sortHead("Latest signal", "signal"),
                    sortHead("Email source", "email"),
                    sortHead("Last event", "event"),
                  ]
            }
            rowKeys={rows.map((p) => p.id)}
            rowTone={rows.map(rowTone)}
            wide
            striped
            stickyHeader
            style={
              rows.length > LIST_MIN
                ? {
                    maxHeight: tableViewportHeight(visibleRows, withSend),
                    overflowY: "auto",
                    overscrollBehavior: "contain",
                  }
                : undefined
            }
            rows={rows.map((person) => {
              const base: React.ReactNode[] = [
                <Button
                  key="name"
                  variant={selected?.id === person.id ? "primary" : "ghost"}
                  onClick={() => onSelect(person.id)}
                >
                  {person.name}
                </Button>,
                person.company,
                actorLabel(person),
                statusLabel(person.status),
                foundOnLabel(person),
                latestSignalLabel(person),
                emailSourceLabel(person),
                lastEvent(person),
              ];
              if (!withSend) return base;
              return [
                ...base,
                canMarkSent(person) ? (
                  <Stack key="send" gap={4}>
                    <Button
                      variant="primary"
                      onClick={() => onSelect(person.id)}
                    >
                      Review
                    </Button>
                    <Button variant="secondary" onClick={() => onSentMyself(person)}>
                      I&apos;ll send it myself
                    </Button>
                  </Stack>
                ) : (
                  ""
                ),
              ];
            })}
          />

          <TableListHandle shown={visibleRows} total={rows.length} onChange={setListSize} />

          <Text size="small" tone="tertiary">
            Default order is date added, newest first. Click a column to sort. Latest signal is
            when the public post or page was found, not when they were emailed. Review opens
            the person below with their draft. Send this sends the draft Trace wrote.
          </Text>

          {selected && (
            <RecordDetail
              person={selected}
              profile={profile}
              templates={templates}
              from={from}
              health={health}
              onSendDraft={onSendDraft}
              onSentMyself={onSentMyself}
              onPullContact={onPullContact}
              onSaveContact={onSaveContact}
              onRewriteDraft={onRewriteDraft}
              onNote={onNote}
              onOutcome={onOutcome}
              actionError={actionError}
              busy={busy}
              pullingContact={pullingContactId === selected.id}
            />
          )}
        </Stack>
      )}

      {rows.length === 0 && people.length > 0 && (
        <Callout tone="neutral" title="Nothing in this cut">
          This campaign has people, but not with that source. Switch Found on back to All.
        </Callout>
      )}

      {rows.length === 0 && people.length === 0 && !loading && (
        <Card>
          <CardHeader>{profile.name}</CardHeader>
          <CardBody>
            <Stack gap={10}>
              <Text>{emptyMessage(filter)}</Text>
              <Button variant="primary" onClick={onHunt}>
                Find people for {profile.name}
              </Button>
            </Stack>
          </CardBody>
        </Card>
      )}
    </Stack>
  );
}
