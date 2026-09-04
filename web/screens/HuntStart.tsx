"use client";

import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  H2,
  Pill,
  Row,
  Stack,
  Text,
} from "../components/ui";
import type { Cost, Profile } from "../lib/api";
import { senderLine, usd } from "../lib/format";

export function HuntStart({
  profile,
  cost,
  limits,
  huntLimit,
  peopleFound,
  starting,
  error,
  onLimit,
  onStart,
}: {
  profile: Profile;
  cost: Cost | null;
  limits: number[];
  huntLimit: number;
  peopleFound: number;
  starting: boolean;
  error: string | null;
  onLimit: (limit: number) => void;
  onStart: () => void;
}) {
  const next = cost?.nextHunt ?? null;

  return (
    <Stack gap={16}>
      <H2>Find people for {profile.name}</H2>
      <Text tone="secondary">
        {profile.huntDescription}. Emails go out as {senderLine(profile)}.
      </Text>

      <Card>
        <CardHeader>After you approve someone</CardHeader>
        <CardBody>
          <Stack gap={8}>
            <Text>
              Trace looks up email and phone one at a time: Apollo first. Only if Apollo
              does not find an email does Trace try Hunter.io. It does not invent an address.
            </Text>
            <Text>
              People already in this file are skipped on the next hunt, including closed and
              disqualified.
            </Text>
          </Stack>
        </CardBody>
      </Card>

      <Stack gap={8}>
        <Text weight="semibold">How many new people</Text>
        <Text size="small" tone="secondary">
          Cap for this hunt. Already in the file do not count against it.
        </Text>
        <Row gap={8} wrap>
          {limits.map((n) => (
            <Pill key={n} active={huntLimit === n} onClick={() => onLimit(n)}>
              {n}
            </Pill>
          ))}
        </Row>
      </Stack>

      {next ? (
        <Callout tone="info" title={`This hunt, about ${usd(next.low)}–${usd(next.high)}`}>
          Looking for up to {huntLimit} new people. {peopleFound} already in this file are
          skipped. Search still runs. Full spend is under Cost.
        </Callout>
      ) : (
        <Callout tone="neutral" title="Loading the estimate for this hunt">
          Looking for up to {huntLimit} new people. {peopleFound} already in this file are
          skipped.
        </Callout>
      )}

      {error && (
        <Callout tone="danger" title="Trace could not start this hunt">
          {error}
        </Callout>
      )}

      <Row align="center" gap={12}>
        <Button variant="primary" onClick={onStart} disabled={starting}>
          {starting
            ? "Starting…"
            : `Find up to ${huntLimit} people for ${profile.name}`}
        </Button>
        <Text size="small" tone="tertiary">
          Takes a few minutes. You can walk away.
        </Text>
      </Row>
    </Stack>
  );
}
