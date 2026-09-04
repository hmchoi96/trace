"use client";

import {
  BarChart,
  Callout,
  H2,
  H3,
  Row,
  Stack,
  Stat,
  Table,
  Text,
} from "../components/ui";
import type { Cost, Profile } from "../lib/api";
import { sentenceCase, usd } from "../lib/format";

export function CostScreen({
  profile,
  cost,
  loading,
  peopleFound,
  huntLimit,
}: {
  profile: Profile;
  cost: Cost | null;
  loading: boolean;
  peopleFound: number;
  huntLimit: number;
}) {
  if (!cost) {
    return (
      <Stack gap={16}>
        <Stack gap={4}>
          <H2>{profile.name}</H2>
          <Text tone="secondary">
            Grok research only. Not Apollo, not sending mail. Campaign total, not per person.
          </Text>
        </Stack>
        <Callout tone="neutral" title={loading ? "Loading cost" : "No cost data"}>
          {loading
            ? "Reading spend for this profile from the Trace API."
            : "The Trace API has no cost events for this profile yet."}
        </Callout>
      </Stack>
    );
  }

  const stages = cost.byStage;
  const next = cost.nextHunt;

  return (
    <Stack gap={16}>
      <Stack gap={4}>
        <H2>{profile.name}</H2>
        <Text tone="secondary">
          Grok research only. Not Apollo, not sending mail. Campaign total, not per person.
        </Text>
      </Stack>

      <Stack gap={8}>
        <H3>Research cost</H3>
        <Row gap={16} wrap>
          <Stat value={usd(cost.totalUsd)} label="Spent so far" tone="info" />
          <Stat value={String(peopleFound)} label="People found" />
          <Stat value={String(cost.hunts)} label="Hunts" />
          <Stat
            value={`${usd(next.low)}–${usd(next.high)}`}
            label={`Next hunt, ${huntLimit} people`}
            tone="warning"
          />
        </Row>

        {stages.length > 0 ? (
          <>
            <BarChart
              categories={stages.map((s) => sentenceCase(s.stage))}
              series={[
                {
                  name: "Grok research",
                  data: stages.map((s) => s.usd),
                  tone: "info",
                },
              ]}
              height={140}
              valuePrefix="$"
            />
            <Table
              headers={["Stage", "Spend"]}
              columnAlign={["left", "right"]}
              rows={[
                ...stages.map((s) => [sentenceCase(s.stage), usd(s.usd)]),
                ["Total", usd(cost.totalUsd)],
              ]}
            />
          </>
        ) : (
          <Callout tone="neutral" title="No spend recorded yet">
            No hunt has logged research cost for {profile.name}. The next-hunt estimate uses a
            default per-person rate until there is real history.
          </Callout>
        )}

        <Callout tone="info" title="This hunt will not re-research the file">
          {peopleFound} people already in this campaign are skipped. Search still runs. Cap is{" "}
          {huntLimit} new people. Estimate {usd(next.low)}–{usd(next.high)}. Cache hits are
          cheaper.
        </Callout>
      </Stack>
    </Stack>
  );
}
