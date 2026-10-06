import type { CostForecast } from "./api.ts";

function dollars(value: number): string {
  return `$${value.toFixed(2)}`;
}

export const TRACKED_SCOPE =
  "Tracked variable spend only. Contact data, email verification, drafting, mailbox, and fixed software are not in this total.";

export function formatUnitCost(value: number | null | undefined): string {
  if (value == null) return "—";
  return dollars(value);
}

export function formatStageCost(tracked: boolean, value: number | null | undefined): string {
  if (!tracked || value == null) return "Not tracked";
  return dollars(value);
}

export function formatShare(value: number | null | undefined, total: number | null | undefined): string {
  if (value == null || total == null || total <= 0) return "—";
  const part = Math.round(value * 100) / 100;
  const whole = Math.round(total * 100) / 100;
  if (whole <= 0) return "—";
  return `${((part / whole) * 100).toFixed(1)}%`;
}

export function formatRatio(
  num: number,
  den: number | null | undefined,
  rate: number | null | undefined,
): string {
  if (den == null || den <= 0 || rate == null) return "—";
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
