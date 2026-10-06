"use client";

import { Callout, H3, Stack, Table, Text } from "./ui";
import type { AdditionalSignal, Person, ResearchFact, ResearchInference } from "../lib/api";
import { formatStageCost } from "../lib/economicsView";
import { axisRows, draftHeld, foundOnLabel, reasonLines, sentenceCase, shortDate, usd } from "../lib/format";
import { hasStructuredResearch } from "../lib/researchView";

function ReasonText({
  reason,
  label,
  empty,
}: {
  reason: string;
  label: string;
  empty: string;
}) {
  const lines = reasonLines(reason, label);
  if (lines.length === 0) return empty;
  return (
    <div className="reason">
      {lines.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </div>
  );
}

function readSignal(signal: AdditionalSignal) {
  const source = String(signal.source ?? signal.signal_source ?? "");
  const at = String(signal.published_at ?? signal.date ?? "");
  const text = String(signal.signal_text ?? signal.text ?? "");
  const url = String(signal.source_url ?? signal.url ?? "");
  return { source: source || "Web", at: shortDate(at), text, url };
}

function hostLabel(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "source";
  }
}

function SourceLink({ url }: { url: string }) {
  const href = url.trim();
  if (!href) return "—";
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {hostLabel(href)}
    </a>
  );
}

function BulletList({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="reason">
      {items.map((item) => (
        <p key={item}>{item}</p>
      ))}
    </div>
  );
}

const GAP_LABEL: Record<string, string> = {
  covered: "Covered",
  possible_gap: "Possible gap",
  confirmed_gap: "Confirmed gap",
  unknown: "Unknown",
};

function FactTable({ facts }: { facts: ResearchFact[] }) {
  if (facts.length === 0) return null;
  return (
    <Table
      headers={["Claim", "When", "Source"]}
      rows={facts.map((fact) => [
        fact.claim,
        fact.sourceDate || "—",
        <SourceLink key={fact.sourceUrl || fact.claim} url={fact.sourceUrl} />,
      ])}
    />
  );
}

function ResearchSections({ person }: { person: Person }) {
  const research = person.research;
  if (!research) return null;
  const gap = research.gapAssessment;
  const gapLabel = GAP_LABEL[gap?.status || ""] || "";
  const decision = person.decisionSummary?.decision || "";
  const title = [decision, gapLabel].filter(Boolean).join(" · ");
  return (
    <Stack gap={10}>
      {title && (gap?.reason || person.decisionSummary?.replyReason) ? (
        <Callout tone={gap?.status === "covered" ? "warning" : "info"} title={title}>
          {gap?.reason || person.decisionSummary?.replyReason}
        </Callout>
      ) : null}
      {person.decisionSummary?.whyThisPerson ? (
        <Text size="small">{person.decisionSummary.whyThisPerson}</Text>
      ) : null}
      {research.verifiedFacts.length > 0 && (
        <Stack gap={4}>
          <H3>Verified facts</H3>
          <FactTable facts={research.verifiedFacts} />
        </Stack>
      )}
      {research.currentWorkarounds.length > 0 && (
        <Stack gap={4}>
          <H3>Current workarounds</H3>
          <FactTable facts={research.currentWorkarounds} />
        </Stack>
      )}
      {research.inferences.length > 0 && (
        <Stack gap={4}>
          <H3>Trace inferences</H3>
          <Table
            headers={["Read", "Confidence"]}
            rows={research.inferences.map((item) => [
              item.claim,
              item.confidence ? sentenceCase(item.confidence) : "—",
            ])}
          />
        </Stack>
      )}
      {research.unknowns.length > 0 && (
        <Stack gap={4}>
          <H3>Unknowns</H3>
          <BulletList items={research.unknowns} />
        </Stack>
      )}
      {research.doNotClaim.length > 0 && (
        <Stack gap={4}>
          <H3>Do not claim</H3>
          <BulletList items={research.doNotClaim} />
        </Stack>
      )}
    </Stack>
  );
}

export function EvidencePanel({ person }: { person: Person }) {
  const extras = (person.additionalSignals ?? []).map(readSignal).filter((s) => s.text);
  const axes = axisRows(person.axes ?? {});
  const recommendation = person.recommendation ? sentenceCase(person.recommendation) : "";
  const actor = person.actorType ? sentenceCase(person.actorType) : "";
  const structured = hasStructuredResearch(person);

  return (
    <Stack gap={14}>
      <Stack gap={6}>
        <H3>Signal</H3>
        <Text size="small" tone="tertiary">
          {foundOnLabel(person)}
          {person.signal.date ? ` · ${shortDate(person.signal.date)}` : ""}
          {person.signal.url ? (
            <>
              {" · "}
              <SourceLink url={person.signal.url} />
            </>
          ) : null}
        </Text>
        {person.signal.text ? (
          <div className="quote">
            <Text>{person.signal.text}</Text>
          </div>
        ) : (
          <Text tone="tertiary">No signal text was recorded for this person.</Text>
        )}
        {person.signal.why && (
          <Text size="small" tone="secondary">
            Why surfaced: {person.signal.why}
          </Text>
        )}
      </Stack>

      {extras.length > 0 && (
        <Stack gap={6}>
          <H3>Deepened</H3>
          <Table
            headers={["Found on", "When", "What Trace found next"]}
            rows={extras.map((signal) => [
              signal.source,
              signal.at || "Not reported",
              signal.url ? `${signal.text} · ${signal.url}` : signal.text,
            ])}
          />
        </Stack>
      )}

      {person.linkedinUrl && (
        <Stack gap={6}>
          <H3>Identity</H3>
          <Text size="small">Matched {person.linkedinUrl}</Text>
        </Stack>
      )}

      {person.status === "unfit" && (
        <Callout tone="warning" title="Not a fit for outreach">
          {person.unfitReason ||
            "Trace researched this person and held them back. They stay in the file so the cost is not anonymous."}
        </Callout>
      )}

      {person.status === "contact_not_found" && (
        <Callout tone="warning" title="Contact not found">
          Lookup did not return an email. This person is not sendable. The research stays
          in the file.
        </Callout>
      )}

      <Stack gap={6}>
        {structured ? (
          <ResearchSections person={person} />
        ) : recommendation ? (
          <Callout tone="info" title={`${recommendation} · you still decide`}>
            <ReasonText
              reason={person.recommendationReason}
              label={recommendation}
              empty="No reason was recorded for this read."
            />
          </Callout>
        ) : (
          <Callout tone="neutral" title="No recommendation recorded · you still decide">
            Trace did not store a recommendation for this person.
          </Callout>
        )}
        <Table
          headers={["Field", "Trace's read"]}
          rows={[
            ["Actor", actor || "Not reported"],
            ["Outreach role", person.outreachRole || "Not reported"],
            ...(person.secondaryRoles?.length
              ? [["Secondary roles", person.secondaryRoles.join(", ")]]
              : []),
            ["Recommended ask", person.recommendedAsk || "Not reported"],
            ["Recommendation", recommendation || "Not reported"],
            ...(person.replyReason
              ? [
                  ["Outreach motion", sentenceCase(person.replyReason.motion || "")],
                  ["Reply reason", sentenceCase(person.replyReason.trigger_type || "")],
                  ["Draft decision", sentenceCase(person.replyReason.draft_decision || "")],
                ]
              : []),
          ]}
        />
        {draftHeld(person) && !structured && (
          <Callout tone="warning" title="Trace will not draft this email">
            {person.replyReason.reason || "No reply reason yet."}
            {person.replyReason.missing_evidence?.length
              ? ` Missing: ${person.replyReason.missing_evidence.join(", ")}.`
              : ""}
          </Callout>
        )}
        {axes.length > 0 && (
          <Table headers={["Axis", "Score"]} rows={axes.map((row) => [row[0], row[1]])} />
        )}
        {person.costTrace && (
          <Stack gap={6}>
            <H3>Tracked cost</H3>
            <Text size="small" tone="secondary">
              Source: {person.costTrace.sourceChannel}. Signal family: {person.costTrace.signalFamily}.{" "}
              {person.costTrace.huntLabel}
            </Text>
            <Table
              headers={["Stage", "Tracked cost"]}
              columnAlign={["left", "right"]}
              rows={[
                ...person.costTrace.lines.map((line) => [
                  line.allocated ? `${line.label} (allocated)` : line.label,
                  formatStageCost(line.tracked, line.usd),
                ]),
                [
                  "Total tracked cost",
                  person.costTrace.totalUsd == null ? "Not tracked" : usd(person.costTrace.totalUsd),
                ],
              ]}
            />
          </Stack>
        )}
      </Stack>
    </Stack>
  );
}
