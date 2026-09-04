"use client";

import { EvidencePanel } from "../components/EvidencePanel";
import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  H2,
  Row,
  Spacer,
  Stack,
  Stat,
  Text,
} from "../components/ui";
import type { Hunt } from "../lib/api";
import { foundOnLabel } from "../lib/format";

export function HuntReview({
  hunt,
  busy,
  error,
  onDecide,
  onNext,
}: {
  hunt: Hunt | null;
  busy: boolean;
  error: string | null;
  onDecide: (id: string, decision: "yes" | "no") => void;
  onNext: () => void;
}) {
  const candidates = hunt?.candidates ?? [];
  const pending = candidates.filter((p) => p.decision === "pending");
  const person = pending[0];
  const decidedCount = candidates.length - pending.length;

  if (!hunt) {
    return (
      <Stack gap={16}>
        <H2>Does this person look right?</H2>
        <Callout tone="neutral" title="No hunt is running">
          Start a hunt on step 1 first.
        </Callout>
      </Stack>
    );
  }

  if (!person) {
    return (
      <Stack gap={16}>
        <H2>Does this person look right?</H2>
        <Callout tone="neutral" title="Nobody is waiting on a decision">
          {candidates.length === 0
            ? "This hunt has not put anyone in front of you yet."
            : "You decided on everyone in this hunt. Drafts are next."}
        </Callout>
        <Button variant="primary" onClick={onNext} disabled={candidates.length === 0}>
          Go to drafts
        </Button>
      </Stack>
    );
  }

  return (
    <Stack gap={16}>
      <Row align="end">
        <Stack gap={4}>
          <H2>Does this person look right?</H2>
          <Text tone="secondary">
            Person {decidedCount + 1} of {candidates.length}. {pending.length} still waiting.
          </Text>
        </Stack>
        <Spacer />
        <Stat value={pending.length} label="Left to decide" />
      </Row>

      {error && (
        <Callout tone="danger" title="Trace could not record that decision">
          {error}
        </Callout>
      )}

      <Card size="lg">
        <CardHeader>{person.company}</CardHeader>
        <CardBody>
          <Stack gap={14}>
            <Stack gap={4}>
              <H2>{person.name}</H2>
              <Text tone="secondary">
                {person.title} · {person.company}
              </Text>
              <Text size="small" tone="tertiary">
                Found on {foundOnLabel(person)}
                {person.signal.url ? ` · ${person.signal.url}` : ""}
              </Text>
              {person.linkedinUrl && (
                <Text size="small" tone="tertiary">
                  {person.linkedinUrl}
                </Text>
              )}
            </Stack>

            <EvidencePanel person={person} />

            <Row gap={8}>
              <Button
                variant="primary"
                onClick={() => onDecide(person.id, "yes")}
                disabled={busy}
              >
                Yes, find contact and draft
              </Button>
              <Button
                variant="secondary"
                onClick={() => onDecide(person.id, "no")}
                disabled={busy}
              >
                No, not them
              </Button>
            </Row>
          </Stack>
        </CardBody>
      </Card>
    </Stack>
  );
}
