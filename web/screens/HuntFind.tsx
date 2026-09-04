"use client";

import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  H2,
  Row,
  Stack,
  Stat,
  Text,
} from "../components/ui";
import type { Hunt } from "../lib/api";
import { formatDuration, formatEta } from "../lib/format";

const STEPS = [
  { id: "starting", label: "Prepare" },
  { id: "search", label: "Search" },
  { id: "qualify", label: "Qualify" },
  { id: "deepening", label: "Deepen" },
  { id: "saving", label: "Save" },
] as const;

function stepIndex(stage: string): number {
  if (stage === "queued" || stage === "starting") return 0;
  if (stage === "search" || stage === "web" || stage === "x" || stage === "linkedin") return 1;
  if (stage === "qualify" || stage === "qualification") return 2;
  if (stage === "deepening") return 3;
  if (stage === "saving") return 4;
  if (stage === "done") return 5;
  return 0;
}

export function HuntFind({
  hunt,
  skipCount,
  onContinue,
  onRestart,
  onLeave,
}: {
  hunt: Hunt | null;
  skipCount: number;
  onContinue: () => void;
  onRestart: () => void;
  onLeave?: () => void;
}) {
  if (!hunt) {
    return (
      <Stack gap={16}>
        <H2>Trace is looking</H2>
        <Callout tone="neutral" title="No hunt is running">
          Go back to step 1 and start a hunt for this profile.
        </Callout>
        <Button variant="primary" onClick={onRestart}>
          Back to start
        </Button>
      </Stack>
    );
  }

  const running = hunt.status === "queued" || hunt.status === "running";
  const candidates = hunt.candidates ?? [];
  const found = candidates.length;
  const pending = candidates.filter((p) => p.decision === "pending").length;
  const activeStep = stepIndex(hunt.currentStage || hunt.status);
  const recentEvents = (hunt.events ?? []).slice(-8).reverse();
  const eta = formatEta(hunt.remainingSec ?? 0, running);
  const elapsed = formatDuration(hunt.elapsedSec ?? 0);
  const progressPct = hunt.progressPct ?? 0;
  const estimateSec = hunt.estimateSec ?? 0;

  return (
    <Stack gap={16}>
      <H2>Trace is looking</H2>
      <Text tone="secondary">
        It searches for the behavior around the problem, then deepens, then matches a LinkedIn
        or X account. People already in this file are left out.
      </Text>

      {hunt.status === "failed" && (
        <Callout tone="danger" title="This hunt failed">
          {hunt.error || "The hunt stopped without an error message."}
        </Callout>
      )}

      <Grid columns={3} gap={12}>
        <Stat value="Web, LinkedIn, X" label="Where it searched" />
        <Stat
          value={String(found)}
          label={`New people worth a look (cap ${hunt.limit})`}
          tone="info"
        />
        <Stat value={String(skipCount)} label="Skipped, already in file" />
      </Grid>

      {running && (
        <Card>
          <CardHeader trailing={eta || elapsed}>
            {hunt.progress || "Starting this hunt"}
          </CardHeader>
          <CardBody>
            <Stack gap={14}>
              <div className="hunt-progress-track" aria-hidden>
                <div
                  className="hunt-progress-fill"
                  style={{ width: `${Math.max(4, progressPct)}%` }}
                />
              </div>

              <div className="hunt-steps">
                <Row gap={6} wrap>
                  {STEPS.map((step, index) => {
                    const done = activeStep > index;
                    const active = activeStep === index;
                    return (
                      <span
                        key={step.id}
                        className={`hunt-step${done ? " hunt-step-done" : ""}${
                          active ? " hunt-step-active" : ""
                        }`}
                      >
                        {step.label}
                      </span>
                    );
                  })}
                </Row>
              </div>

              <Text size="small" tone="tertiary">
                Running for {elapsed}
                {estimateSec > 0 ? ` · usually takes ${formatDuration(estimateSec)}` : ""}
                {eta ? ` · ${eta}` : ""}
              </Text>

              {recentEvents.length > 0 && (
                <Stack gap={6}>
                  <Text size="small" weight="semibold">
                    Live log
                  </Text>
                  <ul className="hunt-event-log">
                    {recentEvents.map((event, index) => (
                      <li key={`${event.at}-${index}`}>
                        <Text size="small" tone="secondary">
                          {event.message}
                        </Text>
                      </li>
                    ))}
                  </ul>
                </Stack>
              )}

              {onLeave && (
                <Button variant="secondary" onClick={onLeave}>
                  Browse people while this runs
                </Button>
              )}
            </Stack>
          </CardBody>
        </Card>
      )}

      {!running && hunt.status === "done" && (
        <Callout tone="success" title="This hunt finished">
          Found {found} new {found === 1 ? "person" : "people"} in {elapsed}.
        </Callout>
      )}

      <Card>
        <CardHeader>What just happened, in plain language</CardHeader>
        <CardBody>
          <Stack gap={10}>
            <Text>1. Searched public posts for the pain.</Text>
            <Text>
              2. Skipped everyone already researched, closed, or disqualified in this profile.
            </Text>
            <Text>3. Deepened the new ones and matched LinkedIn or X.</Text>
            <Text>4. Stopped. Contact lookup waits until you say yes.</Text>
          </Stack>
        </CardBody>
      </Card>

      {hunt.status === "done" && found === 0 && (
        <Callout tone="neutral" title="This hunt found no new people">
          Everyone it surfaced was already in this file, or the search came back empty.
        </Callout>
      )}

      {hunt.status === "failed" ? (
        <Button variant="primary" onClick={onRestart}>
          Back to start
        </Button>
      ) : (
        <Button variant="primary" onClick={onContinue} disabled={found === 0}>
          {running
            ? "Still looking…"
            : `Review ${pending} ${pending === 1 ? "person" : "people"}`}
        </Button>
      )}
    </Stack>
  );
}
