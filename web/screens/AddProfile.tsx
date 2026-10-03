"use client";

import { useState } from "react";
import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Checkbox,
  Grid,
  H2,
  Row,
  Select,
  Spacer,
  Stack,
  Text,
  TextArea,
  TextInput,
  Toggle,
} from "../components/ui";
import type { Profile, ProfilePayload, Template } from "../lib/api";

type ProfileDraft = {
  name: string;
  whatItDoes: string;
  senderName: string;
  senderCompany: string;
  senderWork: string;
  signOff: string;
  desiredOutcome: string;
  buyers: string;
  problems: string;
  goodSignals: string;
  skip: string;
  qualify: string;
  searchGuidance: string;
  template: string;
  web: boolean;
  linkedin: boolean;
  x: boolean;
  preferWeb: boolean;
};

function senderField(block: string, label: string): string {
  const prefix = `- ${label}:`;
  const hit = block.split("\n").find((line) => line.startsWith(prefix));
  return hit ? hit.slice(prefix.length).trim() : "";
}

function asLines(value: unknown): string {
  if (Array.isArray(value)) return value.map((line) => String(line).trim()).filter(Boolean).join("\n");
  return typeof value === "string" ? value : "";
}

export function draftFromProfile(profile: Profile): ProfileDraft {
  const raw = profile.profile as {
    discovery?: Record<string, unknown>;
    sender_block?: string;
  };
  const discovery = raw.discovery ?? {};
  const channels = Array.isArray(discovery.search_channels)
    ? discovery.search_channels.map(String)
    : ["web"];
  const block = String(raw.sender_block ?? "");
  const signOff = profile.signOff.includes("\n")
    ? profile.signOff.replace(/\n/g, ", ")
    : profile.signOff;
  return {
    name: profile.productName || profile.name,
    whatItDoes: String(discovery.what_it_does ?? ""),
    senderName: profile.senderName,
    senderCompany: profile.senderCompany,
    senderWork: senderField(block, "Current work") || senderField(block, "Status"),
    signOff,
    desiredOutcome: senderField(block, "Desired outcome"),
    buyers: String(discovery.target_users_or_buyers ?? profile.huntDescription),
    problems: asLines(discovery.problems_it_solves),
    goodSignals: asLines(discovery.examples_of_problem_signals),
    skip: asLines(discovery.obvious_non_targets_or_adjacent_vendors),
    qualify: String(discovery.qualification_question ?? ""),
    searchGuidance: String(discovery.search_guidance ?? ""),
    template: profile.defaultTemplate,
    web: channels.includes("web"),
    linkedin: channels.includes("linkedin"),
    x: channels.includes("x"),
    preferWeb: Boolean(discovery.prefer_web),
  };
}

function emptyDraft(template: string): ProfileDraft {
  return {
    name: "",
    whatItDoes: "",
    senderName: "",
    senderCompany: "",
    senderWork: "",
    signOff: "",
    desiredOutcome: "",
    buyers: "",
    problems: "",
    goodSignals: "",
    skip: "",
    qualify: "",
    searchGuidance: "",
    template,
    web: true,
    linkedin: true,
    x: true,
    preferWeb: true,
  };
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Stack gap={4}>
      <Text size="small" weight="semibold">
        {label}
      </Text>
      {children}
    </Stack>
  );
}

export function AddProfile({
  templates,
  onSave,
  onCancel,
  initial,
  heading = "Add a profile",
  lede = "This is the brief Trace hunts from. Not a name and a sentence. Helix, Akashic, and OneAway each need their own. They do not share people.",
  saveLabel = "Save profile",
}: {
  templates: Template[];
  onSave: (payload: ProfilePayload) => Promise<void>;
  onCancel: () => void;
  initial?: ProfileDraft;
  heading?: string;
  lede?: string;
  saveLabel?: string;
}) {
  const defaultTemplate = templates[0]?.id ?? "strategy";
  const [draft, setDraft] = useState<ProfileDraft>(
    () => initial ?? emptyDraft(defaultTemplate),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  const ready = Boolean(draft.name.trim() && draft.whatItDoes.trim());

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave({
        name: draft.name.trim(),
        whatItDoes: draft.whatItDoes.trim(),
        senderName: draft.senderName.trim(),
        senderCompany: draft.senderCompany.trim(),
        senderWork: draft.senderWork.trim(),
        signOff: draft.signOff.trim(),
        desiredOutcome: draft.desiredOutcome.trim(),
        buyers: draft.buyers.trim(),
        problems: draft.problems.trim(),
        goodSignals: draft.goodSignals.trim(),
        skip: draft.skip.trim(),
        qualify: draft.qualify.trim(),
        searchGuidance: draft.searchGuidance.trim(),
        productContext: "",
        fromEmail: "",
        template: draft.template,
        searchWeb: draft.web,
        searchX: draft.x,
        searchLinkedin: draft.linkedin,
        preferWeb: draft.preferWeb,
      });
      setDraft(emptyDraft(defaultTemplate));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap={16}>
      <Stack gap={4}>
        <H2>{heading}</H2>
        <Text tone="secondary">{lede}</Text>
      </Stack>

      {error && (
        <Callout tone="danger" title="Trace could not save this profile">
          {error}
        </Callout>
      )}

      <Card>
        <CardHeader>1. What you are selling</CardHeader>
        <CardBody>
          <Stack gap={12}>
            <Field label="Company or offer name">
              <TextInput
                value={draft.name}
                onChange={(v) => set("name", v)}
                placeholder="OneAway"
              />
            </Field>
            <Field label="What it actually is">
              <TextArea
                value={draft.whatItDoes}
                onChange={(v) => set("whatItDoes", v)}
                rows={3}
                placeholder="B2B outbound agency: cold email, LinkedIn DMs, appointment setting, and GTM workflow for companies without a mature in-house outbound engine."
              />
            </Field>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>2. Who the email is from</CardHeader>
        <CardBody>
          <Stack gap={12}>
            <Grid columns={2} gap={12}>
              <Field label="Sender name">
                <TextInput
                  value={draft.senderName}
                  onChange={(v) => set("senderName", v)}
                  placeholder="Xavier"
                />
              </Field>
              <Field label="Sender company">
                <TextInput
                  value={draft.senderCompany}
                  onChange={(v) => set("senderCompany", v)}
                  placeholder="OneAway"
                />
              </Field>
            </Grid>
            <Field label="What the sender actually does">
              <TextArea
                value={draft.senderWork}
                onChange={(v) => set("senderWork", v)}
                rows={2}
                placeholder="Runs outbound and GTM workflow for B2B SaaS that is still founder-led or hiring the first SDR."
              />
            </Field>
            <Field label="Sign-off">
              <TextInput
                value={draft.signOff}
                onChange={(v) => set("signOff", v)}
                placeholder="Xavier, OneAway"
              />
            </Field>
            <Field label="What a good reply looks like">
              <TextArea
                value={draft.desiredOutcome}
                onChange={(v) => set("desiredOutcome", v)}
                rows={2}
                placeholder="A 15-minute call this quarter about whether outsourced outbound would help pipeline."
              />
            </Field>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>3. Who to find, and who to skip</CardHeader>
        <CardBody>
          <Stack gap={12}>
            <Field label="Titles and companies that buy">
              <TextArea
                value={draft.buyers}
                onChange={(v) => set("buyers", v)}
                rows={3}
                placeholder="Founder, CEO, Head of Sales, Head of Growth, CRO, RevOps at B2B SaaS. Small or new outbound. Buyers of the work, not people selling it."
              />
            </Field>
            <Field label="Problems this offer solves">
              <TextArea
                value={draft.problems}
                onChange={(v) => set("problems", v)}
                rows={3}
                placeholder="Need pipeline but no SDR team yet. Founder-led sales is the bottleneck. New market with no local sales hire. Clay / Apollo / HubSpot still unfinished."
              />
            </Field>
            <Field label="Good public signals — hunt the behavior, not the pitch">
              <TextArea
                value={draft.goodSignals}
                onChange={(v) => set("goodSignals", v)}
                rows={4}
                placeholder="Hiring first SDR. Just raised. Opening a new market. Founder still doing outbound. Pipeline inconsistent. Trying to make Clay or Apollo actually run."
              />
            </Field>
            <Field label="Skip these people">
              <TextArea
                value={draft.skip}
                onChange={(v) => set("skip", v)}
                rows={3}
                placeholder="Other agencies. GTM vendors selling the same thing. Sales coaches. Large SDR orgs with a mature engine."
              />
            </Field>
            <Field label="Qualification — would they buy this now?">
              <TextArea
                value={draft.qualify}
                onChange={(v) => set("qualify", v)}
                rows={2}
                placeholder="Does this company have a reason to buy outsourced outbound or GTM engineering right now?"
              />
            </Field>
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>4. How Trace searches and writes</CardHeader>
        <CardBody>
          <Stack gap={12}>
            <Field label="Search notes">
              <TextArea
                value={draft.searchGuidance}
                onChange={(v) => set("searchGuidance", v)}
                rows={3}
                placeholder="Do not hunt people asking for an agency. Hunt hiring posts, funding, new-market launches, founder-led outbound. Prefer company pages and LinkedIn over Twitter chatter."
              />
            </Field>
            <Text size="small" weight="semibold">
              Channels
            </Text>
            <Row gap={16} wrap>
              <Checkbox checked={draft.web} onChange={(v) => set("web", v)} label="Web" />
              <Checkbox
                checked={draft.linkedin}
                onChange={(v) => set("linkedin", v)}
                label="LinkedIn"
              />
              <Checkbox checked={draft.x} onChange={(v) => set("x", v)} label="X" />
            </Row>
            <Row gap={8} align="center">
              <Text size="small">
                Prefer company pages and hiring posts over social chatter
              </Text>
              <Spacer />
              <Toggle checked={draft.preferWeb} onChange={(v) => set("preferWeb", v)} />
            </Row>
            <Field label="Email shape">
              <Select
                value={draft.template}
                onChange={(v) => set("template", v)}
                options={templates.map((t) => ({
                  value: t.id,
                  label: `${t.label} — ${t.description}`,
                }))}
              />
            </Field>
            <Row gap={8}>
              <Button variant="primary" onClick={save} disabled={!ready || busy}>
                {busy ? "Saving…" : saveLabel}
              </Button>
              <Button variant="ghost" onClick={onCancel}>
                Cancel
              </Button>
            </Row>
            {!ready && (
              <Text size="small" tone="tertiary">
                A name and what it actually is are both required before Trace can hunt from
                this brief.
              </Text>
            )}
          </Stack>
        </CardBody>
      </Card>
    </Stack>
  );
}
