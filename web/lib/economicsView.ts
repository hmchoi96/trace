import type { CostForecast } from "./api.ts";

function dollars(value: number): string {
  return `$${value.toFixed(2)}`;
}

export const TRACKED_SCOPE =
  "Tracked spend: Grok research only. Apollo, drafting, mailbox, and fixed software costs are not included unless separately recorded.";

export const READY_IS_NOT_CONTACT =
  "Outreach-ready means the person meets the outreach bar. It does not mean a contact was found.";

export const MEANINGFUL_REPLY = "A meaningful reply is Positive or Engaged. It is not a meeting.";

export const ALLOCATED_DISCOVERY =
  "Discovery cost is split evenly across the fresh candidates that wave actually reviewed.";

export function formatUnitCost(value: number | null | undefined): string {
  if (value == null) return "N/A";
  return dollars(value);
}

export function formatStageCost(tracked: boolean, value: number | null | undefined): string {
  if (!tracked || value == null) return "Not tracked";
  return dollars(value);
}

export function formatRatio(
  num: number,
  den: number | null | undefined,
  rate: number | null | undefined,
): string {
  if (den == null || rate == null) return "N/A";
  return `${num} / ${den} · ${Math.round(rate * 100)}%`;
}

export function forecastLines(next: CostForecast): string[] {
  if (!next.enoughHistory) {
    return [
      next.message || "Not enough completed hunts for a reliable estimate.",
      `Structural maximum: up to ${next.maximumReviewed} candidates reviewed.`,
    ];
  }
  return [
    `Target: ${next.target} outreach-ready people`,
    `Expected reviewed: ${next.expectedReviewed}`,
    `Maximum reviewed: ${next.maximumReviewed}`,
    `Expected tracked research cost: ${dollars(next.expectedUsd ?? 0)}`,
    `Likely range: ${dollars(next.low ?? 0)}–${dollars(next.high ?? 0)}`,
    next.rangeMethod,
  ];
}
