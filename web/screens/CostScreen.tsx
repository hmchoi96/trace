"use client";

import {
  BarChart,
  Button,
  Callout,
  H2,
  H3,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
} from "../components/ui";
import type { Cost, CostAttribution, Profile } from "../lib/api";
import {
  ALLOCATED_DISCOVERY,
  MEANINGFUL_REPLY,
  READY_IS_NOT_CONTACT,
  TRACKED_SCOPE,
  forecastLines,
  formatRatio,
  formatStageCost,
  formatUnitCost,
} from "../lib/economicsView";

const WINDOWS = [
  { id: "all", label: "All time" },
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
];

function Tip({ label, tip }: { label: string; tip: string }) {
  return <span title={tip}>{label}</span>;
}

function attributionRows(rows: CostAttribution[]) {
  return rows.map((row) => [
    row.name,
    row.spend == null ? "Not tracked" : formatUnitCost(row.spend),
    String(row.reviewed),
    String(row.ready),
    String(row.sent),
    String(row.humanReplies),
    String(row.meaningfulReplies),
    String(row.meetings),
    formatUnitCost(row.costPerReady),
    formatUnitCost(row.costPerMeaningful),
  ]);
}

const ATTRIBUTION_HEADERS = [
  "Name",
  "Spend",
  "Reviewed",
  "Ready",
  "Sent",
  "Human replies",
  "Positive/Engaged",
  "Meetings",
  "Cost/Ready",
  "Cost/Meaningful Reply",
];

export function CostScreen({
  profile,
  cost,
  loading,
  huntLimit,
  windowName,
  start,
  end,
  huntId,
  onWindow,
  mailboxReady,
  replyNote,
  replyBusy,
  onCheckReplies,
}: {
  profile: Profile;
  cost: Cost | null;
  loading: boolean;
  huntLimit: number;
  windowName: string;
  start: string;
  end: string;
  huntId: string;
  onWindow: (windowName: string, start?: string, end?: string, huntId?: string) => void;
  mailboxReady: boolean;
  replyNote: string;
  replyBusy: boolean;
  onCheckReplies: () => void;
}) {
  if (!cost) {
    return (
      <Stack gap={16}>
        <H2>{profile.name}</H2>
        <Text tone="secondary">{TRACKED_SCOPE}</Text>
        <Callout tone="neutral" title={loading ? "Loading cost" : "No cost data"}>
          {loading
            ? "Reading spend for this profile from the Trace API."
            : "The Trace API has no cost events for this profile yet."}
        </Callout>
      </Stack>
    );
  }

  const counts = cost.counts;
  const next = cost.nextHunt;
  const forecast = forecastLines(next);
  const trackedStages = cost.stages.filter((stage) => stage.tracked && stage.usd != null);

  return (
    <Stack gap={16}>
      <Stack gap={4}>
        <H2>{profile.name}</H2>
        <Text tone="secondary">{cost.scopeNote || TRACKED_SCOPE}</Text>
        <Text size="small" tone="tertiary">
          {cost.windowLabel} Outreach-ready is not contact-found. {READY_IS_NOT_CONTACT}
        </Text>
      </Stack>

      <Row gap={8} wrap>
        {WINDOWS.map((item) => (
          <Pill key={item.id} active={windowName === item.id && !huntId} onClick={() => onWindow(item.id, "", "", "")}>
            {item.label}
          </Pill>
        ))}
        <input
          type="date"
          aria-label="Custom range start"
          value={start}
          onChange={(event) => onWindow("custom", event.target.value, end, huntId)}
        />
        <input
          type="date"
          aria-label="Custom range end"
          value={end}
          onChange={(event) => onWindow("custom", start, event.target.value, huntId)}
        />
        <select
          aria-label="Hunt"
          value={huntId}
          onChange={(event) => onWindow(windowName === "custom" ? "custom" : "all", start, end, event.target.value)}
        >
          <option value="">All hunts</option>
          {cost.byHunt.map((hunt) => (
            <option key={hunt.huntId} value={hunt.huntId}>
              {hunt.huntId}
            </option>
          ))}
        </select>
      </Row>

      <Stack gap={8}>
        <H3>Summary</H3>
        <Row gap={16} wrap>
          <Stat value={formatUnitCost(cost.totalUsd)} label="Tracked spend" tone="info" />
          <Stat value={String(counts.reviewed)} label="Candidates reviewed" />
          <Stat
            value={String(counts.outreachReady)}
            label="Outreach-ready"
          />
          <Stat value={String(counts.contactFound)} label="Contacts found" />
          <Stat value={String(counts.sent)} label="Emails sent" />
          <Stat value={String(counts.humanReplies)} label="Human replies" />
          <Stat value={String(counts.meaningfulReplies)} label="Positive/engaged replies" />
          <Stat value={String(counts.meetings)} label="Meetings" />
        </Row>
        <Row gap={16} wrap>
          <Stat value={formatUnitCost(cost.unitCosts.reviewed)} label="Cost / reviewed" />
          <Stat value={formatUnitCost(cost.unitCosts.outreachReady)} label="Cost / outreach-ready" />
          <Stat value={formatUnitCost(cost.unitCosts.sent)} label="Cost / sent" />
          <Stat value={formatUnitCost(cost.unitCosts.meaningfulReply)} label="Cost / meaningful reply" />
          <Stat value={formatUnitCost(cost.unitCosts.meeting)} label="Cost / meeting" />
        </Row>
        <Text size="small" tone="tertiary">
          {MEANINGFUL_REPLY} {cost.untrackedEvents > 0
            ? `${cost.untrackedEvents} cost events have no dollar amount and are excluded from totals.`
            : ""}
          {cost.excludedUsd != null ? ` Excluded from total: ${formatUnitCost(cost.excludedUsd)}.` : ""}
        </Text>
        <Row gap={8} align="center">
          <Button
            variant="secondary"
            disabled={replyBusy || !mailboxReady}
            title={mailboxReady ? "Read the connected mailbox and match replies to sends" : "No mailbox is connected"}
            onClick={onCheckReplies}
          >
            {replyBusy ? "Checking mailbox…" : "Check mailbox for replies"}
          </Button>
          {replyNote ? <Text size="small" tone="secondary">{replyNote}</Text> : null}
        </Row>
        {cost.legacyExcluded && Object.values(cost.legacyExcluded).some((count) => count > 0) ? (
          <Text size="small" tone="tertiary">
            Legacy outcomes stay out of this funnel
            {cost.legacyExcluded.approved ? ` · approved ${cost.legacyExcluded.approved}` : ""}
            {cost.legacyExcluded.humanReplies ? ` · replies ${cost.legacyExcluded.humanReplies}` : ""}
            {cost.legacyExcluded.sent ? ` · sent ${cost.legacyExcluded.sent}` : ""}.
            Strict conversion does not exceed 100%.
          </Text>
        ) : null}
      </Stack>

      <Stack gap={8}>
        <H3>Funnel</H3>
        <Table
          headers={["Stage", "People", "Conversion", "Tracked cost per outcome"]}
          columnAlign={["left", "right", "right", "right"]}
          rows={cost.funnel.map((row) => [
            row.stage,
            String(row.people),
            row.conversion.den == null
              ? "—"
              : formatRatio(row.conversion.num, row.conversion.den, row.conversion.rate),
            formatUnitCost(row.unitCost),
          ])}
        />
      </Stack>

      <Stack gap={8}>
        <H3>Spend</H3>
        {trackedStages.length > 0 ? (
          <BarChart
            categories={trackedStages.map((stage) => stage.stage)}
            series={[{ name: "Tracked spend", data: trackedStages.map((stage) => stage.usd ?? 0), tone: "info" }]}
            height={140}
            valuePrefix="$"
          />
        ) : null}
        <Table
          headers={["Stage", "Spend"]}
          columnAlign={["left", "right"]}
          rows={[
            ...cost.stages.map((stage) => [
              stage.stage,
              formatStageCost(stage.tracked, stage.usd),
            ]),
            ["Tracked total", formatUnitCost(cost.totalUsd)],
          ]}
        />
        <Text size="small" tone="tertiary">
          {cost.hunts} hunts have recorded cost events. {ALLOCATED_DISCOVERY}
        </Text>
      </Stack>

      <Stack gap={8}>
        <H3>Attribution</H3>
        <Text size="small" tone="tertiary">
          <Tip label="Source channel" tip={READY_IS_NOT_CONTACT} />. Missing origin stays Unknown.
        </Text>
        <Table
          headers={["Source", ...ATTRIBUTION_HEADERS.slice(1)]}
          columnAlign={["left", "right", "right", "right", "right", "right", "right", "right", "right", "right"]}
          rows={attributionRows(cost.bySource)}
          wide
        />
        <H3>Signal family</H3>
        <Table
          headers={["Signal", ...ATTRIBUTION_HEADERS.slice(1)]}
          columnAlign={["left", "right", "right", "right", "right", "right", "right", "right", "right", "right"]}
          rows={attributionRows(cost.bySignal)}
          wide
        />
        <H3>Hunt</H3>
        <Table
          headers={["Hunt", ...ATTRIBUTION_HEADERS.slice(1)]}
          columnAlign={["left", "right", "right", "right", "right", "right", "right", "right", "right", "right"]}
          rows={attributionRows(cost.byHuntDetail)}
          wide
        />
      </Stack>

      <Callout tone="info" title={`Next hunt, ${huntLimit} outreach-ready people`}>
        {forecast.map((line) => (
          <div key={line}>{line}</div>
        ))}
      </Callout>
    </Stack>
  );
}
