"use client";

import { Callout, H3, Stack, Table, Text } from "./ui";
import type { AdditionalSignal, Person, ResearchFact, ResearchInference } from "../lib/api";
import { axisRows, draftHeld, foundOnLabel, reasonLines, sentenceCase, shortDate } from "../lib/format";
import { hasStructuredResearch, sourceUrls } from "../lib/researchView";

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

function factLine(fact: ResearchFact): string {
  const bits = [fact.claim];
  if (fact.sourceDate) bits.push(fact.sourceDate);
  if (fact.sourceUrl) bits.push(fact.sourceUrl);
  return bits.join(" · ");
}

function inferenceLine(item: ResearchInference): string {
  const rank = item.confidence ? sentenceCase(item.confidence) : "";
  return rank ? `${item.claim} [${rank}]` : item.claim;
}

function BulletList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <Text tone="tertiary">{empty}</Text>;
  return (
    <div className="reason">
      {items.map((item) => (
        <p key={item}>{item}</p>
      ))}
    </div>
  );
}

function DecisionSummary({ person }: { person: Person }) {
  const summary = person.decisionSummary;
  if (!summary) return null;
  return (
    <Table
      headers={["Decision", "Trace's read"]}
      rows={[
        ["Decision", summary.decision || "Not reported"],
        ["Why now", summary.whyNow || "Not reported"],
        ["Why this person", summary.whyThisPerson || "Not reported"],
        ["Reply reason", summary.replyReason || "Not reported"],
        ["Do not claim", summary.doNotClaim || "Not reported"],
      ]}
    />
  );
}

function ResearchSections({ person }: { person: Person }) {
  const research = person.research;
  if (!research) return null;
  const sources = sourceUrls(person);
  return (
    <Stack gap={10}>
      <Stack gap={4}>
        <H3>Verified facts</H3>
        <BulletList
          items={research.verifiedFacts.map(factLine)}
          empty="None recorded"
        />
      </Stack>
      <Stack gap={4}>
        <H3>Current workarounds</H3>
        <BulletList
          items={research.currentWorkarounds.map(factLine)}
          empty="None recorded"
        />
      </Stack>
      <Stack gap={4}>
        <H3>Trace inferences</H3>
        <BulletList
          items={research.inferences.map(inferenceLine)}
          empty="None recorded"
        />
      </Stack>
      <Stack gap={4}>
        <H3>Unknowns</H3>
        <BulletList items={research.unknowns} empty="None recorded" />
      </Stack>
      <Stack gap={4}>
        <H3>Sources</H3>
        <BulletList items={sources} empty="None recorded" />
      </Stack>
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
          {person.signal.url ? ` · ${person.signal.url}` : ""}
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

      {person.status === "contact_not_found" && (
        <Callout tone="warning" title="Contact not found">
          Lookup did not return an email. This person is not sendable. The research stays
          in the file.
        </Callout>
      )}

      <Stack gap={6}>
        {(person.decisionSummary || structured) && (
          <>
            <H3>Decision</H3>
            <DecisionSummary person={person} />
          </>
        )}
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
        {draftHeld(person) && (
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
      </Stack>
    </Stack>
  );
}
