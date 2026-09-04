"use client";

import { Callout, H3, Stack, Table, Text } from "./ui";
import type { AdditionalSignal, Person } from "../lib/api";
import { axisRows, foundOnLabel, sentenceCase, shortDate } from "../lib/format";

function readSignal(signal: AdditionalSignal) {
  const source = String(signal.source ?? signal.signal_source ?? "");
  const at = String(signal.published_at ?? signal.date ?? "");
  const text = String(signal.signal_text ?? signal.text ?? "");
  const url = String(signal.source_url ?? signal.url ?? "");
  return { source: source || "Web", at: shortDate(at), text, url };
}

export function EvidencePanel({ person }: { person: Person }) {
  const extras = (person.additionalSignals ?? []).map(readSignal).filter((s) => s.text);
  const axes = axisRows(person.axes ?? {});
  const recommendation = person.recommendation ? sentenceCase(person.recommendation) : "";
  const actor = person.actorType ? sentenceCase(person.actorType) : "";

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

      <Stack gap={6}>
        <H3>Trace recommendation</H3>
        {recommendation ? (
          <Callout tone="info" title={`${recommendation} · you still decide`}>
            {person.recommendationReason || "No reason was recorded for this read."}
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
          ]}
        />
        {axes.length > 0 && (
          <Table headers={["Axis", "Score"]} rows={axes.map((row) => [row[0], row[1]])} />
        )}
      </Stack>
    </Stack>
  );
}
