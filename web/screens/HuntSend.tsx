"use client";

import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  H2,
  Row,
  Stack,
  Text,
} from "../components/ui";
import type { Health, Hunt, Person, Profile } from "../lib/api";
import { senderLine } from "../lib/format";

export type SendResult = { tone: "success" | "danger"; message: string };

function sendState(person: Person, mailboxReady: boolean) {
  if (person.sentAt) return { can: false, why: "Already recorded as sent." };
  if (!person.email) return { can: false, why: "No email or phone yet." };
  if (!person.draft) return { can: false, why: "No draft yet." };
  if (!person.draft.sendable) {
    return { can: false, why: "This draft is not ready to send." };
  }
  if (!mailboxReady) {
    return { can: false, why: "No mailbox is connected, so Trace cannot send." };
  }
  return { can: true, why: "" };
}

export function HuntSend({
  hunt,
  profile,
  health,
  results,
  busyId,
  onSend,
  onSentMyself,
}: {
  hunt: Hunt | null;
  profile: Profile;
  health: Health | null;
  results: Record<string, SendResult>;
  busyId: string | null;
  onSend: (person: Person) => void;
  onSentMyself: (person: Person) => void;
}) {
  const approved = (hunt?.candidates ?? []).filter((p) => p.decision === "yes");
  const from = senderLine(profile);
  const mailboxReady = Boolean(health?.mailboxReady);

  if (approved.length === 0) {
    return (
      <Stack gap={12}>
        <H2>Nothing to send</H2>
        <Text tone="secondary">
          Approve at least one person on the review screen first.
        </Text>
      </Stack>
    );
  }

  return (
    <Stack gap={16}>
      <H2>Send is a separate decision</H2>
      <Callout tone="danger" title="This is the scary button on purpose">
        Finding people does not send mail. Approving does not send mail. Only this screen does.
      </Callout>

      {!mailboxReady && (
        <Callout tone="warning" title="Trace sending is off">
          No mailbox is connected
          {health?.mailbox ? ` (${health.mailbox})` : ""}, so Trace cannot put mail in the
          outbox. You can still record that you sent it yourself.
        </Callout>
      )}

      <Card>
        <CardHeader>About to leave the outbox as {from}</CardHeader>
        <CardBody>
          <Stack gap={14}>
            {approved.map((person) => {
              const state = sendState(person, mailboxReady);
              const result = results[person.id];
              return (
                <Stack key={person.id} gap={6}>
                  <Text>
                    {person.name} at {person.company} —{" "}
                    {person.draft?.subject || "No subject yet"}
                  </Text>
                  <Text size="small" tone="tertiary">
                    {person.email
                      ? `To ${person.email} · ${person.emailSource || "Unknown source"}`
                      : "No email or phone yet"}
                  </Text>
                  {!state.can && (
                    <Text size="small" tone="secondary">
                      {state.why}
                    </Text>
                  )}
                  {result && (
                    <Callout
                      tone={result.tone}
                      title={result.tone === "success" ? "Recorded" : "Not sent"}
                    >
                      {result.message}
                    </Callout>
                  )}
                  <Row gap={8} wrap>
                    <Button
                      variant="primary"
                      disabled={!state.can || busyId === person.id}
                      onClick={() => onSend(person)}
                    >
                      Send this
                    </Button>
                    <Button
                      variant="secondary"
                      disabled={Boolean(person.sentAt) || busyId === person.id}
                      onClick={() => onSentMyself(person)}
                    >
                      I&apos;ll send it myself
                    </Button>
                  </Row>
                </Stack>
              );
            })}
          </Stack>
        </CardBody>
      </Card>

      <Text size="small" tone="tertiary">
        Send this puts the draft in the connected mailbox as {from}. I&apos;ll send it myself
        only records that it went out, so Trace never writes the mail.
      </Text>
    </Stack>
  );
}
