import assert from "node:assert/strict";

import { researchPlainText, researchSectionsHtml } from "./researchView.ts";
import type { Person } from "./api.ts";

const person = {
  name: "Zach Bruggeman",
  status: "researched",
  draftDecision: "send_now",
  outreachMotion: "cold_product",
  triggerOfferAlignment: "direct",
  gapStatus: "possible_gap",
  contactStatus: "not_looked_up",
  recommendationReason: "One long repeated paragraph that must not be the facts section.",
  decisionSummary: {
    decision: "Send now",
    whyNow: "Inspect is actively operating across production systems.",
    whyThisPerson: "Zach built and documented the current authentication model.",
    replyReason: "Ask how user authority will be preserved as agents expand across systems.",
    doNotClaim: "Do not claim Ramp has an unresolved security vulnerability.",
  },
  research: {
    verifiedFacts: [
      {
        claim: "Inspect is used in production.",
        sourceUrl: "https://example.com/inspect",
        sourceDate: "2026-04-01",
        quoteOrParaphrase: "Inspect runs in production.",
      },
    ],
    currentWorkarounds: [
      { claim: "Sandboxed VMs", sourceUrl: "https://example.com/inspect", sourceDate: "2026-04-01" },
    ],
    inferences: [
      {
        claim: "Cross-system authorization may still be fragmented.",
        confidence: "medium",
        basedOn: ["https://example.com/inspect"],
      },
    ],
    unknowns: ["Whether a centralized authorization layer already exists."],
    gapAssessment: {
      status: "possible_gap",
      reason: "The remaining gap is inferred.",
      basedOn: ["https://example.com/inspect"],
    },
    doNotClaim: ["Do not claim Ramp has an unresolved security vulnerability."],
  },
} as Person;

const text = researchPlainText(person);
const facts = text.split("Verified facts")[1]?.split("Current workarounds")[0] ?? "";
const inferences = text.split("Trace inferences")[1]?.split("Unknowns")[0] ?? "";
assert.match(facts, /Inspect is used in production/);
assert.doesNotMatch(facts, /fragmented/);
assert.match(inferences, /fragmented/);
assert.match(inferences, /\[Medium\]/);
assert.match(text, /Do not claim/);
assert.doesNotMatch(facts, /repeated paragraph/);

const html = researchSectionsHtml(person);
assert.match(html, /<h3>Verified facts<\/h3>/);
assert.match(html, /<h3>Trace inferences<\/h3>/);
const htmlFacts = html.split("<h3>Verified facts</h3>")[1]?.split("<h3>Current workarounds</h3>")[0] ?? "";
assert.doesNotMatch(htmlFacts, /fragmented/);
assert.match(html.split("<h3>Trace inferences</h3>")[1] ?? "", /fragmented/);

console.log("research view sections ok");
