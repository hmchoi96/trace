import assert from "node:assert/strict";

import {
  ALLOCATED_DISCOVERY,
  MEANINGFUL_REPLY,
  READY_IS_NOT_CONTACT,
  TRACKED_SCOPE,
  forecastLines,
  formatRatio,
  formatStageCost,
  formatUnitCost,
} from "./economicsView.ts";

assert.equal(formatUnitCost(null), "N/A");
assert.equal(formatUnitCost(1.5), "$1.50");
assert.equal(formatStageCost(false, null), "Not tracked");
assert.equal(formatStageCost(true, 0), "$0.00");
assert.equal(formatRatio(3, 0, null), "N/A");
assert.equal(formatRatio(3, 12, 0.25), "3 / 12 · 25%");
assert.match(TRACKED_SCOPE, /Grok research only/);
assert.match(READY_IS_NOT_CONTACT, /does not mean a contact was found/);
assert.match(MEANINGFUL_REPLY, /Positive or Engaged/);
assert.match(ALLOCATED_DISCOVERY, /split evenly/);

const cold = forecastLines({
  enoughHistory: false,
  target: 5,
  expectedReviewed: null,
  maximumReviewed: 25,
  expectedUsd: null,
  low: null,
  high: null,
  readyRate: null,
  rangeMethod: "recent min/max cost per reviewed candidate",
  message: "Not enough completed hunts for a reliable estimate.",
});
assert.match(cold[0], /Not enough completed hunts/);
assert.match(cold[1], /up to 25 candidates reviewed/);

const priced = forecastLines({
  enoughHistory: true,
  target: 5,
  expectedReviewed: 12,
  maximumReviewed: 25,
  expectedUsd: 8,
  low: 6,
  high: 11,
  readyRate: 0.4,
  rangeMethod: "recent min/max cost per reviewed candidate",
  message: "",
});
assert.deepEqual(priced.slice(0, 4), [
  "Target: 5 outreach-ready people",
  "Expected reviewed: 12",
  "Maximum reviewed: 25",
  "Expected tracked research cost: $8.00",
]);

console.log("economics view ok");
