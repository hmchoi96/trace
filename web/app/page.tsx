"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button,
  Callout,
  Divider,
  H1,
  Pill,
  Row,
  Spacer,
  Stack,
  Text,
} from "../components/ui";
import {
  api,
  errorMessage,
  type Cost,
  type Health,
  type Hunt,
  type HuntSummary,
  type Person,
  type Profile,
  type ProfilePayload,
  type Template,
} from "../lib/api";
import { HuntStart } from "../screens/HuntStart";
import { HuntFind } from "../screens/HuntFind";
import { HuntReview } from "../screens/HuntReview";
import { HuntDrafts } from "../screens/HuntDrafts";
import { HuntSend, type SendResult } from "../screens/HuntSend";
import { Records } from "../screens/Records";
import { CostScreen } from "../screens/CostScreen";
import { AddProfile } from "../screens/AddProfile";

type Mode = "hunt" | "records" | "cost" | "add";
type Screen = "home" | "find" | "review" | "drafts" | "send";

const HUNT_STEPS: { id: Screen; label: string }[] = [
  { id: "home", label: "1. Start" },
  { id: "find", label: "2. Find" },
  { id: "review", label: "3. You decide" },
  { id: "drafts", label: "4. Drafts" },
  { id: "send", label: "5. You send" },
];

const DEFAULT_LIMITS = [3, 5, 8, 12, 20];

function readStored(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* storage is optional */
  }
}

export default function TracePage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [bootError, setBootError] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);

  const [profileId, setProfileId] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("records");
  const [screen, setScreen] = useState<Screen>("home");
  const [huntLimit, setHuntLimit] = useState(5);

  const [people, setPeople] = useState<Person[]>([]);
  const [peopleLoading, setPeopleLoading] = useState(false);
  const [cost, setCost] = useState<Cost | null>(null);
  const [costLoading, setCostLoading] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);

  const [huntId, setHuntId] = useState<string | null>(null);
  const [hunt, setHunt] = useState<Hunt | null>(null);
  const [recentHunts, setRecentHunts] = useState<HuntSummary[]>([]);
  const [huntNotice, setHuntNotice] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [decideError, setDecideError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState(false);

  const [sendResults, setSendResults] = useState<Record<string, SendResult>>({});
  const [sendBusyId, setSendBusyId] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [recordError, setRecordError] = useState<string | null>(null);
  const [recordBusy, setRecordBusy] = useState(false);
  const [pullContactId, setPullContactId] = useState<string | null>(null);
  const [bulkPullProgress, setBulkPullProgress] = useState<{
    done: number;
    total: number;
    found: number;
  } | null>(null);

  const profile = profiles.find((p) => p.id === profileId) ?? null;
  const limits = cost?.limits ?? DEFAULT_LIMITS;

  /* ── Boot: view state, then health / templates / profiles ─────────────── */

  useEffect(() => {
    const storedMode = readStored("trace.mode");
    if (storedMode === "hunt" || storedMode === "records" || storedMode === "cost") {
      setMode(storedMode);
    }
    const storedScreen = readStored("trace.screen");
    if (HUNT_STEPS.some((step) => step.id === storedScreen)) {
      setScreen(storedScreen as Screen);
    }
    const storedLimit = Number(readStored("trace.huntLimit"));
    if (DEFAULT_LIMITS.includes(storedLimit)) setHuntLimit(storedLimit);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [healthResult, templateResult, profileResult] = await Promise.all([
          api.health(),
          api.templates(),
          api.profiles(),
        ]);
        if (cancelled) return;
        setHealth(healthResult);
        setTemplates(templateResult);
        setProfiles(profileResult);
        const stored = readStored("trace.profileId");
        const initial =
          profileResult.find((p) => p.id === stored)?.id ?? profileResult[0]?.id ?? null;
        setProfileId(initial);
      } catch (error) {
        if (!cancelled) setBootError(errorMessage(error));
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    writeStored("trace.mode", mode);
  }, [mode]);
  useEffect(() => {
    writeStored("trace.screen", screen);
  }, [screen]);
  useEffect(() => {
    writeStored("trace.huntLimit", String(huntLimit));
  }, [huntLimit]);
  useEffect(() => {
    if (profileId) writeStored("trace.profileId", profileId);
  }, [profileId]);

  /* ── Per-profile data ─────────────────────────────────────────────────── */

  const loadPeople = useCallback(async (id: string) => {
    setPeopleLoading(true);
    try {
      const rows = await api.people(id);
      setPeople(rows);
      setDataError(null);
      return rows;
    } catch (error) {
      setPeople([]);
      setDataError(errorMessage(error));
      return [];
    } finally {
      setPeopleLoading(false);
    }
  }, []);

  const loadCost = useCallback(async (id: string, limit: number) => {
    setCostLoading(true);
    try {
      setCost(await api.cost(id, limit));
    } catch (error) {
      setCost(null);
      setDataError(errorMessage(error));
    } finally {
      setCostLoading(false);
    }
  }, []);

  const loadRecentHunts = useCallback(async (id: string) => {
    try {
      setRecentHunts(await api.hunts(id));
    } catch {
      setRecentHunts([]);
    }
  }, []);

  useEffect(() => {
    if (!profileId) return;
    setPeople([]);
    setCost(null);
    setSelectedId(null);
    setSendResults({});
    setRecordError(null);
    setDecideError(null);
    setStartError(null);
    setHunt(null);
    setHuntNotice(null);
    setHuntId(readStored(`trace.hunt.${profileId}`));
    void loadPeople(profileId);
    void loadRecentHunts(profileId);
  }, [profileId, loadPeople, loadRecentHunts]);

  useEffect(() => {
    if (!profileId) return;
    void loadCost(profileId, huntLimit);
  }, [profileId, huntLimit, loadCost]);

  /* ── Hunt polling ─────────────────────────────────────────────────────── */

  const huntStatus = hunt?.status ?? null;
  const huntRunning = huntStatus === "queued" || huntStatus === "running";
  const shouldPoll =
    Boolean(huntId) &&
    (huntStatus === null ||
      huntRunning ||
      screen === "review" ||
      screen === "drafts" ||
      screen === "send");

  const pollRef = useRef<string | null>(null);
  pollRef.current = huntId;

  const previousStatus = useRef<string | null>(null);
  useEffect(() => {
    previousStatus.current = null;
  }, [huntId]);

  useEffect(() => {
    if (!huntId) return;
    let cancelled = false;
    void api
      .hunt(huntId)
      .then((result) => {
        if (!cancelled) setHunt(result);
      })
      .catch(() => {
        /* hunt may have been cleared */
      });
    return () => {
      cancelled = true;
    };
  }, [huntId]);

  useEffect(() => {
    if (!huntId || !shouldPoll) return;
    let cancelled = false;

    async function tick() {
      try {
        const result = await api.hunt(huntId!);
        if (!cancelled && pollRef.current === huntId) setHunt(result);
      } catch {
        /* transient poll failures are not worth a banner */
      }
    }

    void tick();
    const timer = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [huntId, shouldPoll]);

  useEffect(() => {
    if (!profileId || huntStatus === null) return;

    if (previousStatus.current === null) {
      previousStatus.current = huntStatus;
      if ((huntStatus === "failed" || huntStatus === "cancelled") && huntId) {
        writeStored(`trace.hunt.${profileId}`, null);
        setHuntId(null);
        setHunt(null);
        setHuntNotice(null);
      }
      return;
    }

    if (previousStatus.current !== "done" && huntStatus === "done") {
      void loadPeople(profileId);
      void loadCost(profileId, huntLimit);
      void loadRecentHunts(profileId);
      if (mode !== "hunt") {
        const count = hunt?.candidates.length ?? 0;
        setHuntNotice(
          count > 0
            ? `Hunt finished. Found ${count} new ${count === 1 ? "person" : "people"}.`
            : "Hunt finished. No new people this time.",
        );
      }
    }
    const wasActive =
      previousStatus.current === "queued" || previousStatus.current === "running";
    if (wasActive && huntStatus === "failed") {
      void loadRecentHunts(profileId);
      if (mode !== "hunt") {
        setHuntNotice(hunt?.error || "This hunt failed.");
      }
      if (huntId) writeStored(`trace.hunt.${profileId}`, null);
    }
    previousStatus.current = huntStatus;
  }, [
    huntStatus,
    profileId,
    huntLimit,
    loadPeople,
    loadCost,
    loadRecentHunts,
    mode,
    hunt?.candidates.length,
    hunt?.error,
  ]);

  /* ── Actions ──────────────────────────────────────────────────────────── */

  async function startHunt() {
    if (!profileId) return;
    setStarting(true);
    setStartError(null);
    setHuntNotice(null);
    try {
      const { huntId: newHuntId } = await api.createHunt(profileId, huntLimit);
      writeStored(`trace.hunt.${profileId}`, newHuntId);
      setHuntId(newHuntId);
      setHunt(null);
      setScreen("find");
      setMode("hunt");
      void loadRecentHunts(profileId);
    } catch (error) {
      setStartError(errorMessage(error));
    } finally {
      setStarting(false);
    }
  }

  function resumeHunt(nextHuntId: string, nextScreen: Screen = "find") {
    if (!profileId) return;
    writeStored(`trace.hunt.${profileId}`, nextHuntId);
    setHuntId(nextHuntId);
    setHunt(null);
    setHuntNotice(null);
    setMode("hunt");
    setScreen(nextScreen);
  }

  function openFinishedHunt(nextHuntId: string) {
    resumeHunt(nextHuntId, "review");
  }

  function huntExitLabel(): string {
    if (!hunt) return "Leave hunt";
    if (hunt.status === "queued") return "Cancel hunt";
    if (hunt.status === "running") return "Leave hunt";
    return "Start over";
  }

  async function exitHunt() {
    if (huntId && hunt?.status === "queued") {
      try {
        await api.cancelHunt(huntId);
        if (profileId) void loadRecentHunts(profileId);
      } catch (error) {
        setDataError(errorMessage(error));
        return;
      }
    }
    if (profileId) writeStored(`trace.hunt.${profileId}`, null);
    setHuntId(null);
    setHunt(null);
    setHuntNotice(null);
    setScreen("home");
    setStartError(null);
    setDecideError(null);
    setSendResults({});
  }

  async function decide(id: string, decision: "yes" | "no") {
    setDeciding(true);
    setDecideError(null);
    try {
      await api.decide(id, decision);
      if (huntId) setHunt(await api.hunt(huntId));
      if (profileId) void loadPeople(profileId);
    } catch (error) {
      setDecideError(errorMessage(error));
    } finally {
      setDeciding(false);
    }
  }

  async function rewriteDraft(personId: string, templateId: string) {
    await api.draft(personId, templateId);
    if (huntId) setHunt(await api.hunt(huntId));
    if (profileId) void loadPeople(profileId);
  }

  async function saveDraft(draftId: string, subject: string, body: string) {
    await api.editDraft(draftId, { subject, body });
    if (huntId) setHunt(await api.hunt(huntId));
    if (profileId) void loadPeople(profileId);
  }

  async function refreshAfterSend() {
    if (huntId) {
      try {
        setHunt(await api.hunt(huntId));
      } catch {
        /* the hunt may be gone; people is the source of truth */
      }
    }
    if (profileId) await loadPeople(profileId);
  }

  async function sendDraftFor(person: Person) {
    if (!person.draft) return;
    setSendBusyId(person.id);
    try {
      const result = await api.sendDraft(person.draft.id);
      setSendResults((prev) => ({
        ...prev,
        [person.id]: {
          tone: "success",
          message: result.alreadySent
            ? "This was already recorded as sent. Nothing went out twice."
            : `Sent to ${person.email}.`,
        },
      }));
      setRecordError(null);
      await refreshAfterSend();
    } catch (error) {
      const message = errorMessage(error);
      setSendResults((prev) => ({ ...prev, [person.id]: { tone: "danger", message } }));
      setRecordError(message);
    } finally {
      setSendBusyId(null);
    }
  }

  async function markSentMyself(person: Person) {
    setSendBusyId(person.id);
    try {
      const result = await api.sentMyself(person.id);
      setSendResults((prev) => ({
        ...prev,
        [person.id]: {
          tone: "success",
          message: result.alreadySent
            ? "This was already recorded as sent."
            : "Recorded as sent from your own inbox. Trace wrote nothing.",
        },
      }));
      setRecordError(null);
      await refreshAfterSend();
    } catch (error) {
      const message = errorMessage(error);
      setSendResults((prev) => ({ ...prev, [person.id]: { tone: "danger", message } }));
      setRecordError(message);
    } finally {
      setSendBusyId(null);
    }
  }

  async function saveContactFor(personId: string, contact: { email: string; phone: string }) {
    setRecordBusy(true);
    setRecordError(null);
    try {
      await api.saveContact(personId, contact);
      if (profileId) await loadPeople(profileId);
    } catch (error) {
      setRecordError(errorMessage(error));
    } finally {
      setRecordBusy(false);
    }
  }

  async function pullContactFor(person: Person) {
    setPullContactId(person.id);
    setRecordError(null);
    try {
      const result = await api.pullContact(person.id);
      if (profileId) await loadPeople(profileId);
      if (huntId) {
        try {
          setHunt(await api.hunt(huntId));
        } catch {
          /* hunt may be gone */
        }
      }
      if (!result.found) {
        setRecordError("Apollo and Hunter.io did not return an email or phone for this person.");
      }
    } catch (error) {
      setRecordError(errorMessage(error));
    } finally {
      setPullContactId(null);
    }
  }

  async function pullContactsBulk(targets: Person[]) {
    const missing = targets.filter((p) => !String(p.email || "").trim());
    if (missing.length === 0) return;
    const ok = window.confirm(
      `Look up email/phone for ${missing.length} people without contact info?\n\nApollo runs first; Hunter.io fills gaps. This uses API credits.`,
    );
    if (!ok) return;

    setBulkPullProgress({ done: 0, total: missing.length, found: 0 });
    setRecordError(null);
    let found = 0;
    try {
      for (let i = 0; i < missing.length; i += 1) {
        const person = missing[i];
        setPullContactId(person.id);
        try {
          const result = await api.pullContact(person.id);
          if (result.found) found += 1;
        } catch (error) {
          setRecordError(errorMessage(error));
        }
        setBulkPullProgress({ done: i + 1, total: missing.length, found });
        if (profileId) await loadPeople(profileId);
      }
      if (found === 0) {
        setRecordError(
          `Apollo and Hunter.io did not return contact info for any of the ${missing.length} people.`,
        );
      }
    } finally {
      setPullContactId(null);
      setBulkPullProgress(null);
    }
  }

  async function addNote(person: Person, text: string) {
    setRecordBusy(true);
    setRecordError(null);
    try {
      await api.note(person.id, text);
      if (profileId) await loadPeople(profileId);
    } catch (error) {
      setRecordError(errorMessage(error));
    } finally {
      setRecordBusy(false);
    }
  }

  async function setOutcome(person: Person, outcome: "closed" | "disqualified") {
    setRecordBusy(true);
    setRecordError(null);
    try {
      await api.outcome(person.id, outcome);
      if (profileId) await loadPeople(profileId);
    } catch (error) {
      setRecordError(errorMessage(error));
    } finally {
      setRecordBusy(false);
    }
  }

  async function createProfile(payload: ProfilePayload) {
    const created = await api.createProfile(payload);
    setProfiles(await api.profiles());
    setProfileId(created.id);
    setMode("records");
  }

  function switchProfile(id: string) {
    setProfileId(id);
    setMode("records");
  }

  /* ── Render ───────────────────────────────────────────────────────────── */

  if (booting) {
    return (
      <main className="page">
        <Stack gap={12}>
          <H1>Trace</H1>
          <Text tone="secondary">Loading profiles from the Trace API…</Text>
        </Stack>
      </main>
    );
  }

  if (bootError || !profile) {
    return (
      <main className="page">
        <Stack gap={16}>
          <H1>Trace</H1>
          <Callout tone="danger" title="Trace cannot reach the API">
            {bootError ?? "The API returned no profiles."}
          </Callout>
          <Button variant="primary" onClick={() => window.location.reload()}>
            Try again
          </Button>
        </Stack>
      </main>
    );
  }

  return (
    <main className="page">
      <Stack gap={20}>
        <Stack gap={6}>
          <H1>Trace</H1>
          <Text tone="secondary">
            Outbound tool. Each campaign keeps its own people, sends, and contact sources.
          </Text>
          {health && (
            <Text size="small" tone="tertiary">
              {health.mailboxReady
                ? `Mailbox connected: ${health.mailbox}.`
                : `No mailbox connected${health.mailbox ? ` (${health.mailbox})` : ""}. Trace sending is off; you can still record mail you send yourself.`}
            </Text>
          )}
        </Stack>

        <Row gap={8} wrap align="center">
          {profiles.map((option) => (
            <Pill
              key={option.id}
              active={mode !== "add" && profileId === option.id}
              onClick={() => switchProfile(option.id)}
            >
              {option.name}
            </Pill>
          ))}
          <Pill active={mode === "add"} onClick={() => setMode("add")}>
            Add profile
          </Pill>
          <Spacer />
          <Pill active={mode === "hunt"} onClick={() => setMode("hunt")}>
            New hunt
          </Pill>
          <Pill active={mode === "records"} onClick={() => setMode("records")}>
            People and history
          </Pill>
          <Pill active={mode === "cost"} onClick={() => setMode("cost")}>
            Cost
          </Pill>
        </Row>

        {dataError && (
          <Callout tone="danger" title="Trace could not load this profile">
            {dataError}
          </Callout>
        )}

        {huntRunning && mode !== "hunt" && hunt && (
          <Callout
            tone="info"
            title={`Hunt running for ${profile.name}`}
          >
            <Stack gap={8}>
              <Text>{hunt.progress || "Trace is still searching."}</Text>
              <Row gap={8} wrap>
                <Button variant="primary" onClick={() => resumeHunt(hunt.id, "find")}>
                  Watch progress
                </Button>
                <Button variant="ghost" onClick={() => setHuntNotice(null)}>
                  Keep browsing
                </Button>
              </Row>
            </Stack>
          </Callout>
        )}

        {huntNotice && !huntRunning && (
          <Callout
            tone={huntStatus === "failed" ? "danger" : "success"}
            title={huntStatus === "failed" ? "Hunt failed" : "Hunt finished"}
          >
            <Stack gap={8}>
              <Text>{huntNotice}</Text>
              {huntId && huntStatus === "done" && (
                <Button variant="primary" onClick={() => openFinishedHunt(huntId)}>
                  Review results
                </Button>
              )}
              <Button
                variant="ghost"
                onClick={() => {
                  setHuntNotice(null);
                  if (
                    profileId &&
                    (huntStatus === "failed" || huntStatus === "cancelled")
                  ) {
                    writeStored(`trace.hunt.${profileId}`, null);
                    setHuntId(null);
                    setHunt(null);
                  }
                }}
              >
                Dismiss
              </Button>
            </Stack>
          </Callout>
        )}

        {mode === "hunt" && (
          <Row gap={8} wrap align="center">
            {HUNT_STEPS.map((step) => (
              <Pill key={step.id} active={screen === step.id} onClick={() => setScreen(step.id)}>
                {step.label}
              </Pill>
            ))}
            <Spacer />
            <Button variant="ghost" onClick={() => void exitHunt()}>
              {huntExitLabel()}
            </Button>
          </Row>
        )}

        {mode === "hunt" && hunt?.status === "running" && (
          <Callout tone="info" title="This hunt keeps running if you leave">
            Trace cannot stop a search in progress. Leaving only hides it here — check People
            and history when it finishes.
          </Callout>
        )}

        {mode === "hunt" && screen === "home" && (
          <HuntStart
            profile={profile}
            cost={cost}
            limits={limits}
            huntLimit={huntLimit}
            peopleFound={people.length}
            starting={starting}
            error={startError}
            onLimit={setHuntLimit}
            onStart={startHunt}
          />
        )}

        {mode === "hunt" && screen === "find" && (
          <HuntFind
            hunt={hunt}
            skipCount={people.length}
            onContinue={() => setScreen("review")}
            onRestart={() => setScreen("home")}
            onLeave={() => setMode("records")}
          />
        )}

        {mode === "hunt" && screen === "review" && (
          <HuntReview
            hunt={hunt}
            busy={deciding}
            error={decideError}
            onDecide={decide}
            onNext={() => setScreen("drafts")}
          />
        )}

        {mode === "hunt" && screen === "drafts" && (
          <HuntDrafts
            hunt={hunt}
            profile={profile}
            templates={templates}
            health={health}
            pullingContactId={pullContactId}
            onPullContact={pullContactFor}
            onRewrite={rewriteDraft}
            onSave={saveDraft}
            onBack={() => setScreen("review")}
            onNext={() => setScreen("send")}
          />
        )}

        {mode === "hunt" && screen === "send" && (
          <HuntSend
            hunt={hunt}
            profile={profile}
            health={health}
            results={sendResults}
            busyId={sendBusyId}
            onSend={sendDraftFor}
            onSentMyself={markSentMyself}
          />
        )}

        {mode === "records" && (
          <Records
            profile={profile}
            people={people}
            templates={templates}
            loading={peopleLoading}
            health={health}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onHunt={() => setMode("hunt")}
            recentHunts={recentHunts}
            activeHuntId={huntRunning ? huntId : null}
            onOpenHunt={resumeHunt}
            onReviewHunt={openFinishedHunt}
            onNote={addNote}
            onOutcome={setOutcome}
            onSendDraft={sendDraftFor}
            onSentMyself={markSentMyself}
            onPullContact={pullContactFor}
            onPullContactsBulk={pullContactsBulk}
            onSaveContact={saveContactFor}
            onRewriteDraft={rewriteDraft}
            onRefresh={() => {
              if (profileId) void loadPeople(profileId);
            }}
            actionError={recordError}
            busy={recordBusy || sendBusyId !== null || bulkPullProgress !== null}
            pullingContactId={pullContactId}
            bulkPullProgress={bulkPullProgress}
          />
        )}

        {mode === "cost" && (
          <CostScreen
            profile={profile}
            cost={cost}
            loading={costLoading}
            peopleFound={people.length}
            huntLimit={huntLimit}
          />
        )}

        {mode === "add" && (
          <AddProfile
            templates={templates}
            onSave={createProfile}
            onCancel={() => setMode("records")}
          />
        )}

        <Divider />
        <Text size="small" tone="tertiary">
          Each campaign is a separate pile. Emails and numbers keep the source they came from:
          Apollo, Hunter.io, or Clay. Auto-send uses a connected mailbox. I&apos;ll send it
          myself is written in the inbox. Either one records that it went out.
        </Text>
      </Stack>
    </main>
  );
}
