"use client";

import { useState } from "react";
import { H2, H3, Pill, Row, Select, Stack, Stat, Table, Text } from "../components/ui";
import type { Cost, CostAttribution, Profile } from "../lib/api";
import {
  TRACKED_SCOPE,
  formatRatio,
  formatShare,
  formatStageCost,
  formatUnitCost,
} from "../lib/economicsView";

const WINDOWS = [
  { id: "all", label: "All time" },
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
];

const BREAKDOWNS = [
  { value: "source", label: "Source" },
  { value: "signal", label: "Signal" },
  { value: "campaign", label: "Campaign" },
  { value: "hunt", label: "Hunt" },
];

function attributionRows(rows: CostAttribution[]) {
  return rows.map((row) => [
    row.name,
    row.spend == null ? "—" : formatUnitCost(row.spend),
    String(row.reviewed),
    String(row.ready),
    formatUnitCost(row.costPerReady),
    String(row.sent),
    String(row.meaningfulReplies),
    formatUnitCost(row.costPerMeaningful),
    String(row.meetings),
    formatUnitCost(row.costPerCustomer),
  ]);
}

export function CostScreen({
  profile,
  cost,
  loading,
  windowName,
  start,
  end,
  huntId,
  onWindow,
}: {
  profile: Profile;
  cost: Cost | null;
  loading: boolean;
  windowName: string;
  start: string;
  end: string;
  huntId: string;
  onWindow: (windowName: string, start?: string, end?: string, huntId?: string) => void;
}) {
  const [breakdown, setBreakdown] = useState("source");

  if (!cost) {
    return (
      <Stack gap={16}>
        <H2>{profile.name}</H2>
        <Text tone="secondary">{TRACKED_SCOPE}</Text>
        <Text tone="secondary">{loading ? "Reading spend for this profile." : "No cost events for this profile yet."}</Text>
      </Stack>
    );
  }

  const cacLabel = cost.acquisitionComplete ? "Full CAC" : "Tracked CAC";
  const rows =
    breakdown === "signal"
      ? cost.bySignal
      : breakdown === "campaign"
        ? cost.byCampaign
        : breakdown === "hunt"
          ? cost.byHuntDetail
          : cost.bySource;

  return (
    <Stack gap={16}>
      <Stack gap={4}>
        <H2>{profile.name}</H2>
        <Text size="small" tone="tertiary">
          {cost.scopeNote || TRACKED_SCOPE}
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
              {hunt.label || hunt.huntId}
            </option>
          ))}
        </select>
      </Row>

      <Row gap={16} wrap>
        <Stat value={formatUnitCost(cost.totalUsd)} label="Total tracked spend" tone="info" />
        <Stat value={formatUnitCost(cost.unitCosts.outreachReady)} label="Cost / outreach-ready" />
        <Stat value={formatUnitCost(cost.unitCosts.meaningfulReply)} label="Cost / meaningful reply" />
        <Stat value={formatUnitCost(cost.unitCosts.meeting)} label="Cost / meeting" />
        <Stat value={formatUnitCost(cost.unitCosts.customer)} label={cacLabel} />
      </Row>

      <Stack gap={8}>
        <H3>Spend allocation</H3>
        <Table
          headers={["Stage", "Spend", "% of total"]}
          columnAlign={["left", "right", "right"]}
          rows={[
            ...cost.stages.map((stage) => [
              stage.stage,
              formatStageCost(stage.tracked, stage.usd),
              formatShare(stage.usd, cost.totalUsd),
            ]),
            ["Total", formatUnitCost(cost.totalUsd), cost.totalUsd == null ? "—" : "100%"],
          ]}
        />
        {cost.untrackedEvents > 0 ? (
          <Text size="small" tone="tertiary">
            {cost.untrackedEvents} cost events have no dollar amount.
          </Text>
        ) : null}
      </Stack>

      <Stack gap={8}>
        <H3>Unit economics</H3>
        <Table
          headers={["Stage", "People", "Conversion", "Cost per outcome"]}
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
        <Row gap={8} align="center">
          <Text weight="semibold" style={{ whiteSpace: "nowrap" }}>Break down by</Text>
          <Select value={breakdown} onChange={setBreakdown} options={BREAKDOWNS} />
        </Row>
        <Table
          headers={[
            BREAKDOWNS.find((item) => item.value === breakdown)?.label || "Name",
            "Spend",
            "Reviewed",
            "Ready",
            "Cost / Ready",
            "Sent",
            "Meaningful replies",
            "Cost / Meaningful reply",
            "Meetings",
            cacLabel,
          ]}
          columnAlign={["left", "right", "right", "right", "right", "right", "right", "right", "right", "right"]}
          rows={attributionRows(rows)}
          wide
        />
      </Stack>
    </Stack>
  );
}
