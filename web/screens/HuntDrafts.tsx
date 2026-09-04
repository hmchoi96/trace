"use client";

import { useEffect, useState } from "react";
import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  H2,
  Row,
  Select,
  Stack,
  Stat,
  Text,
} from "../components/ui";
import type { Health, Hunt, Person, Profile, Template } from "../lib/api";
import { senderLine } from "../lib/format";

function ContactLines({
  person,
  lookupReady,
  pulling,
  busy,
  onPullContact,
}: {
  person: Person;
  lookupReady: boolean;
  pulling: boolean;
  busy: boolean;
  onPullContact: () => void;
}) {
  if (!person.email && !person.phone) {
    return (
      <Stack gap={8}>
        <Callout tone="warning" title="No email or phone yet">
          Trace checks saved Apollo exports first, then Apollo live. If there is still no
          email, Trace tries Hunter.io. It will not guess an address.
        </Callout>
        <Button
          variant="primary"
          disabled={!lookupReady || busy || pulling}
          onClick={onPullContact}
        >
          {pulling ? "Looking up contact…" : "Pull email and phone"}
        </Button>
      </Stack>
    );
  }
  return (
    <Stack gap={4}>
      {person.email && (
        <Text size="small">
          Email {person.email} · {person.emailSource || "Unknown source"}
        </Text>
      )}
      {person.phone && (
        <Text size="small">
          Phone {person.phone} · {person.phoneSource || "Unknown source"}
        </Text>
      )}
    </Stack>
  );
}

function DraftCard({
  person,
  profile,
  from,
  templates,
  lookupReady,
  pullingContact,
  contactBusy,
  onPullContact,
  onRewrite,
  onSave,
}: {
  person: Person;
  profile: Profile;
  from: string;
  templates: Template[];
  lookupReady: boolean;
  pullingContact: boolean;
  contactBusy: boolean;
  onPullContact: (personId: string) => void;
  onRewrite: (personId: string, templateId: string) => Promise<void>;
  onSave: (draftId: string, subject: string, body: string) => Promise<void>;
}) {
  const draft = person.draft;
  const fallback = templates[0]?.id ?? profile.defaultTemplate ?? "strategy";
  const [templateId, setTemplateId] = useState(draft?.templateId ?? fallback);
  const [subject, setSubject] = useState(draft?.subject ?? "");
  const [body, setBody] = useState(draft?.body ?? "");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const draftId = draft?.id ?? null;
  const draftSubject = draft?.subject ?? "";
  const draftBody = draft?.body ?? "";

  useEffect(() => {
    setSubject(draftSubject);
    setBody(draftBody);
    setSaved(false);
  }, [draftId, draftSubject, draftBody]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card size="lg">
      <CardHeader trailing={draft?.sendable ? "Ready to send later" : "Not ready to send"}>
        {person.name} · {person.company}
      </CardHeader>
      <CardBody>
        <Stack gap={10}>
          <ContactLines
            person={person}
            lookupReady={lookupReady}
            pulling={pullingContact}
            busy={contactBusy}
            onPullContact={() => onPullContact(person.id)}
          />
          <Divider />

          {!draft ? (
            <Callout tone="info" title="No draft yet">
              Trace looks up the contact and writes the draft in the background after a yes.
              This person is still in the queue.
            </Callout>
          ) : (
            <Stack gap={10}>
              {draft.error && (
                <Callout tone="danger" title="Drafting reported a problem">
                  {draft.error}
                </Callout>
              )}
              {!draft.sendable && (
                <Callout tone="warning" title="This draft is not ready to send">
                  {draft.verdict
                    ? `Trace's verdict: ${draft.verdict}`
                    : "Trace held it back. Rewrite it or edit it before sending."}
                </Callout>
              )}

              <Stack gap={4}>
                <Text size="small" tone="tertiary">
                  Template
                </Text>
                <Row gap={8} align="center">
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
                    disabled={busy}
                    onClick={() => run(() => onRewrite(person.id, templateId))}
                  >
                    Rewrite
                  </Button>
                </Row>
                {templates.find((t) => t.id === templateId) && (
                  <Text size="small" tone="secondary">
                    {templates.find((t) => t.id === templateId)!.description}
                  </Text>
                )}
              </Stack>

              <Stack gap={4}>
                <Text size="small" tone="tertiary">
                  Subject
                </Text>
                <input
                  className="input"
                  value={subject}
                  onChange={(e) => {
                    setSubject(e.target.value);
                    setSaved(false);
                  }}
                />
              </Stack>

              <Stack gap={4}>
                <Text size="small" tone="tertiary">
                  Body
                </Text>
                <textarea
                  className="textarea"
                  rows={9}
                  value={body}
                  onChange={(e) => {
                    setBody(e.target.value);
                    setSaved(false);
                  }}
                />
              </Stack>

              <Text size="small" tone="tertiary">
                — {from}
              </Text>

              {error && (
                <Callout tone="danger" title="That did not go through">
                  {error}
                </Callout>
              )}

              <Row gap={8} align="center">
                <Button
                  variant="secondary"
                  disabled={busy || !draft.id}
                  onClick={() =>
                    run(async () => {
                      await onSave(draft.id, subject, body);
                      setSaved(true);
                    })
                  }
                >
                  Save edits
                </Button>
                {saved && (
                  <Text size="small" tone="tertiary">
                    Saved.
                  </Text>
                )}
              </Row>
            </Stack>
          )}
        </Stack>
      </CardBody>
    </Card>
  );
}

export function HuntDrafts({
  hunt,
  profile,
  templates,
  health,
  pullingContactId,
  onPullContact,
  onRewrite,
  onSave,
  onBack,
  onNext,
}: {
  hunt: Hunt | null;
  profile: Profile;
  templates: Template[];
  health: Health | null;
  pullingContactId: string | null;
  onPullContact: (person: Person) => void;
  onRewrite: (personId: string, templateId: string) => Promise<void>;
  onSave: (draftId: string, subject: string, body: string) => Promise<void>;
  onBack: () => void;
  onNext: () => void;
}) {
  const candidates = hunt?.candidates ?? [];
  const approved = candidates.filter((p) => p.decision === "yes");
  const rejected = candidates.filter((p) => p.decision === "no");
  const pendingCount = candidates.filter((p) => p.decision === "pending").length;
  const from = senderLine(profile);

  return (
    <Stack gap={16}>
      <H2>Drafts, not sent</H2>
      <Text tone="secondary">
        After a hunt, this is the pile of drafts Trace wrote. Same text appears under the person
        in People and history. Contact lookup already ran. Still not sent.
      </Text>

      <Row gap={20}>
        <Stat value={String(approved.length)} label="Approved" tone="success" />
        <Stat value={String(rejected.length)} label="Passed" />
        <Stat
          value={String(pendingCount)}
          label="Still undecided"
          tone={pendingCount > 0 ? "warning" : undefined}
        />
      </Row>

      {pendingCount > 0 && (
        <Callout tone="warning" title="A few people are still unmarked">
          Unmarked people stay in Researched, not sent. They will not get a draft.
        </Callout>
      )}

      {approved.length > 0 ? (
        <Stack gap={12}>
          {approved.map((person) => (
            <DraftCard
              key={person.id}
              person={person}
              profile={profile}
              from={from}
              templates={templates}
              lookupReady={Boolean(health?.apollo || health?.hunter)}
              pullingContact={pullingContactId === person.id}
              contactBusy={pullingContactId !== null}
              onPullContact={(personId) => {
                const target = approved.find((p) => p.id === personId);
                if (target) onPullContact(target);
              }}
              onRewrite={onRewrite}
              onSave={onSave}
            />
          ))}
        </Stack>
      ) : (
        <Callout tone="neutral" title="No one was approved">
          They stay in this campaign as researched. No lookup, no draft.
        </Callout>
      )}

      <Row gap={8}>
        <Button variant="ghost" onClick={onBack}>
          Back to people
        </Button>
        <Button variant="primary" onClick={onNext} disabled={approved.length === 0}>
          Review send
        </Button>
      </Row>
    </Stack>
  );
}
