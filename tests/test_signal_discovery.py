"""Signal discovery / qualification / human gate tests. No live Grok or Apollo."""

from __future__ import annotations

import json

from main import LIST_TO_PROFILE, PRODUCT_PROFILES, load_leads_from_csv
from segmentation import normalize_csv_row
from signal_discovery import (
    apply_human_decision,
    build_candidate,
    candidate_to_lead,
    canonical_url,
    dedupe_signals,
    derive_recommendation,
    discovery_context_from_profile,
    discovery_prompt,
    discovery_channel_plan,
    enrich_approved_candidates,
    entity_key_for,
    entity_search_fallbacks,
    export_approved_csv,
    format_signal_evidence_block,
    identity_resolved_enough,
    import_enriched_leads,
    is_identifiable,
    load_candidates,
    load_custom_profile,
    load_research_costs,
    parse_signal_list,
    rank_signals,
    run_discovery,
    save_candidates,
    should_deepen,
    should_draft,
    should_enrich,
    should_research_person,
    signal_group_key,
    signal_jsonl_fields,
)


def _sig(**kwargs):
    base = {
        "source": "x",
        "source_url": "https://x.com/a/status/1",
        "author_name": "Alex",
        "author_handle": "@alex",
        "published_at": "2026-08-14",
        "signal_text": "we still end up going through old IC decks",
        "why_relevant": "prior-deal reasoning",
        "relevance": "relevant",
    }
    base.update(kwargs)
    return base


def test_akashic_and_helix_discovery_context_are_product_relative():
    ak = discovery_context_from_profile(PRODUCT_PROFILES["akashic"])
    hx = discovery_context_from_profile(PRODUCT_PROFILES["problem_validation"])
    assert ak["product_name"] == "Akashic Record"
    assert "CJR" not in ak["what_it_does"]
    assert "on-prem" not in ak["what_it_does"].lower()
    assert "underwriting" in ak["what_it_does"].lower()
    assert ak["signal_ontology"] == "decision_reasoning"
    assert ak["prefer_web"] is True
    assert "why we passed" in ak["search_guidance"].lower()
    assert hx["product_name"] == "Helix"
    assert ak["product_name"] != hx["product_name"]
    assert LIST_TO_PROFILE["helix"] == "problem_validation"
    assert LIST_TO_PROFILE["akashic"] == "akashic"
    prompt = discovery_prompt(ak, 3, "x")
    assert "Do not search primarily for people asking for the product" in prompt
    assert "behavior surrounding the problem" in prompt
    assert "RETRIEVAL" in prompt
    assert "STORAGE-only" in prompt
    assert "why we passed" in prompt
    assert "Older evidence is not automatically weak" in prompt
    assert "x_search" in prompt
    assert "Search ONLY X" in prompt
    web_prompt = discovery_prompt(ak, 3, "web")
    assert "Do NOT treat CRM usage" in web_prompt


def test_akashic_refine_demotes_crm_storage_only():
    from signal_discovery import refine_akashic_signal

    weak = refine_akashic_signal(
        _sig(
            signal_text="Our CRM helps us keep track of opportunities in Salesforce.",
            relevance="highly_relevant",
            evidence_kind="WORKAROUND",
        )
    )
    assert weak["relevance"] == "generic"
    assert weak["evidence_kind"] == "STORAGE"

    strong = refine_akashic_signal(
        _sig(
            signal_text=(
                "We record why we passed and revisit that when a similar add-on shows up."
            ),
            relevance="relevant",
            evidence_kind="WORKAROUND",
        )
    )
    assert strong["relevance"] == "relevant"
    assert strong["evidence_kind"] in ("REASONING_CAPTURE", "RETRIEVAL", "REUSE")


def test_akashic_rank_prefers_reuse_over_storage():
    storage = _sig(
        signal_text="All deal notes live in our CRM.",
        relevance="relevant",
        evidence_kind="STORAGE",
    )
    reuse = _sig(
        signal_text="We look back at prior IC memos when a similar deal shows up.",
        relevance="relevant",
        evidence_kind="RETRIEVAL",
    )
    ranked = rank_signals([storage, reuse])
    assert ranked[0]["evidence_kind"] == "RETRIEVAL"


def test_myzel_profile_uses_existing_pipeline():
    mz = discovery_context_from_profile(PRODUCT_PROFILES["myzel"])
    assert mz["product_name"] == "Myzel Organics"
    assert LIST_TO_PROFILE["myzel"] == "myzel"
    assert PRODUCT_PROFILES["myzel"]["email_mode"] == "trace_strategy_email"
    brief = discovery_prompt(mz, 3, "web")
    assert "Do not hunt for people asking for mushrooms" in brief
    assert "Myzel Organics" in brief
    assert "Helix" not in brief


def test_myzel_pet_profile_is_pet_only():
    pet = discovery_context_from_profile(PRODUCT_PROFILES["myzel_pet"])
    food = discovery_context_from_profile(PRODUCT_PROFILES["myzel"])
    assert LIST_TO_PROFILE["myzel_pet"] == "myzel_pet"
    assert pet["product_name"] == "Myzel Organics"
    brief = discovery_prompt(pet, 3, "web")
    assert "This run is pet only" in brief
    assert "group-buy" in brief
    assert "calming chew" in brief
    food_brief = discovery_prompt(food, 3, "web")
    assert "Pet nutrition is a separate profile" in food_brief


def test_oneaway_profile_is_separate_from_akashic_and_helix():
    oa = discovery_context_from_profile(PRODUCT_PROFILES["oneaway"])
    assert LIST_TO_PROFILE["oneaway"] == "oneaway"
    assert oa["product_name"] == "OneAway"
    assert oa["prefer_web"] is True
    assert oa["search_channels"] == ["web", "x"]
    assert oa["channel_limit_ratios"]["web"] == 1.0
    assert oa["channel_limit_ratios"]["x"] == 0.4
    plan = discovery_channel_plan(oa, 5)
    assert plan[0] == ("web", 5)
    assert plan[1] == ("x", 2)
    brief = discovery_prompt(oa, 5, "web")
    assert "OneAway" in brief
    assert "outsourced outbound" in brief
    assert "site:linkedin.com" in brief
    assert "Helix" not in brief
    assert "Akashic" not in brief
    assert "Myzel" not in brief
    assert PRODUCT_PROFILES["oneaway"]["email_mode"] == "trace_strategy_email"


def test_rank_prefer_web_puts_company_page_ahead_of_x():
    web = _sig(
        source="web",
        source_url="https://example.com/jobs",
        author_name="Pat Lee",
        published_at="2026-08-01",
        signal_text="hiring first SDR",
        evidence_kind="COMPANY_TRIGGER",
        relevance="relevant",
    )
    tweet = _sig(
        source="x",
        source_url="https://x.com/a/99",
        author_name="Pat Lee",
        published_at="2026-08-01",
        signal_text="hiring first SDR",
        evidence_kind="COMPANY_TRIGGER",
        relevance="relevant",
    )
    ranked = rank_signals([tweet, web], prefer_web=True)
    assert ranked[0]["source"] == "web"


def test_custom_product_config(tmp_path):
    path = tmp_path / "widget.json"
    path.write_text(
        json.dumps({
            "product_name": "Widget",
            "what_it_does": "Tracks warehouse cycle counts",
            "target_users_or_buyers": "Ops managers",
            "problems_it_solves": ["lost cycle-count history"],
            "examples_of_problem_signals": ["we recount from scratch"],
            "obvious_non_targets_or_adjacent_vendors": ["WMS vendors"],
        }),
        encoding="utf-8",
    )
    profile = load_custom_profile(str(path))
    ctx = discovery_context_from_profile(profile)
    assert ctx["product_name"] == "Widget"
    assert "cycle counts" in ctx["what_it_does"]


def test_parse_and_dedupe_signals():
    raw = json.dumps({
        "signals": [
            {
                "source": "x",
                "source_url": "https://x.com/a/1",
                "signal_text": "Hello",
                "why_relevant": "x",
                "relevance": "relevant",
            },
            {
                "source": "twitter",
                "source_url": "https://x.com/a/1",
                "signal_text": "Hello",
                "why_relevant": "dup",
            },
        ]
    })
    items = dedupe_signals(parse_signal_list(raw))
    assert len(items) == 1
    assert items[0]["source"] == "x"


def test_generic_commentary_skips_person_research():
    sig = _sig(relevance="generic", signal_text="AI will change private equity.")
    assert should_research_person(sig) is False


def test_case_a_pe_practitioner_stays_pending():
    rec = build_candidate(
        signal=_sig(),
        person={"name": "John Smith", "title": "Principal", "company": "XYZ Capital"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="PE principal describing IC-deck reconstruction.",
        list_name="akashic",
        profile_key="akashic",
        product_name="Akashic",
    )
    assert rec["human_status"] == "PENDING"
    assert rec["recommendation"] == "LIKELY_PROSPECT"
    assert should_enrich(rec) is False
    assert should_draft(rec) is False


def test_case_b_kip_style_builder_not_auto_enriched():
    rec = build_candidate(
        signal=_sig(
            signal_text="Funds should have a system that orchestrates their historical underwriting so AI can use it.",
            relevance="highly_relevant",
        ),
        person={"name": "Kip", "title": "Founder", "company": "Adjacent Co"},
        actor_type="BUILDER_OR_VENDOR",
        recommendation="LIKELY_NOT_PROSPECT",
        recommendation_reason="Founder building around the same problem, not a user.",
        list_name="akashic",
        profile_key="akashic",
        product_name="Akashic",
    )
    assert rec["actor_type"] == "BUILDER_OR_VENDOR"
    assert rec["recommendation"] == "LIKELY_NOT_PROSPECT"
    assert rec["human_status"] == "PENDING"
    assert should_enrich(rec) is False


def test_case_c_helix_founder_can_be_practitioner():
    rec = build_candidate(
        signal=_sig(
            signal_text="I still do most of our cold calling myself and have no good way to practice objections before dialing.",
            source="x",
        ),
        person={"name": "Sam Founder", "title": "Founder", "company": "SaaS Co"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="Founder personally doing cold calls for Helix's workflow.",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    assert rec["actor_type"] == "PRACTITIONER"
    assert rec["recommendation"] == "LIKELY_PROSPECT"


def test_case_g_human_rejects_without_erasing_ai():
    rec = build_candidate(
        signal=_sig(),
        person={"name": "Pat", "title": "VP Sales", "company": "Acme"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="Looks like a practitioner.",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    apply_human_decision([rec], rec["candidate_id"], "REJECTED", "vendor")
    assert rec["recommendation"] == "LIKELY_PROSPECT"
    assert rec["human_status"] == "REJECTED"
    assert rec["human_reject_reason"] == "vendor"
    assert should_enrich(rec) is False
    assert should_draft(rec) is False


def test_import_enrichment_only_hits_approved():
    prospect = build_candidate(
        signal=_sig(source_url="https://x.com/a/1", signal_text="one"),
        person={"name": "Jane Doe", "title": "VP Sales", "company": "Acme"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    vendor = build_candidate(
        signal=_sig(source_url="https://x.com/b/2", signal_text="two"),
        person={"name": "Vendor Guy", "title": "Founder", "company": "RoleplayAI"},
        actor_type="BUILDER_OR_VENDOR",
        recommendation="LIKELY_NOT_PROSPECT",
        recommendation_reason="sells roleplay",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    apply_human_decision([prospect], prospect["candidate_id"], "APPROVED")
    leads = [
        {
            **normalize_csv_row({
                "First Name": "Jane",
                "Last Name": "Doe",
                "Title": "VP Sales",
                "Company Name": "Acme",
                "Email": "jane@acme.com",
                "Person Linkedin Url": "",
            }),
        },
        {
            **normalize_csv_row({
                "First Name": "Vendor",
                "Last Name": "Guy",
                "Title": "Founder",
                "Company Name": "RoleplayAI",
                "Email": "v@roleplay.ai",
                "Person Linkedin Url": "",
            }),
        },
    ]
    n = import_enriched_leads([prospect, vendor], leads)
    assert n == 1
    assert prospect["email"] == "jane@acme.com"
    assert prospect["email_found"] is True
    assert vendor["email"] == ""
    assert should_draft(prospect) is True
    assert should_draft(vendor) is False


def test_approved_without_email_does_not_draft():
    rec = build_candidate(
        signal=_sig(),
        person={"name": "No Mail", "title": "Principal", "company": "Fund"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="akashic",
        profile_key="akashic",
        product_name="Akashic",
    )
    apply_human_decision([rec], rec["candidate_id"], "APPROVED")
    assert should_enrich(rec) is True
    assert should_draft(rec) is False


def test_anonymous_reddit_stays_unclear():
    rec = build_candidate(
        signal=_sig(
            source="reddit",
            source_url="https://reddit.com/r/pe/comments/abc",
            author_name="",
            author_handle="u/throwaway_PE_92",
            signal_text="Every new associate ends up digging through old IC decks.",
        ),
        person={},
        actor_type="UNKNOWN",
        recommendation="UNCLEAR",
        recommendation_reason="Identity could not be verified.",
        list_name="akashic",
        profile_key="akashic",
        product_name="Akashic",
    )
    lead = candidate_to_lead(rec)
    assert lead["email"] == ""
    assert rec["human_status"] == "PENDING"
    assert should_draft(rec) is False


def test_signal_context_on_lead_does_not_use_research_basis():
    rec = build_candidate(
        signal=_sig(),
        person={"name": "Jane Doe", "title": "VP", "company": "Acme"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    rec["email"] = "jane@acme.com"
    lead = candidate_to_lead(rec)
    fields = signal_jsonl_fields(lead)
    assert "research_basis" not in fields
    assert fields["signal_url"] == rec["signal_url"]
    block = format_signal_evidence_block(lead)
    assert "PUBLIC SIGNAL" in block
    assert "old IC decks" in block


def test_export_approved_csv_skips_pending(tmp_path):
    rec = build_candidate(
        signal=_sig(),
        person={"name": "Jane Doe", "title": "VP", "company": "Acme"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    out = tmp_path / "approved.csv"
    assert export_approved_csv([rec], str(out)) == 0
    apply_human_decision([rec], rec["candidate_id"], "APPROVED")
    assert export_approved_csv([rec], str(out)) == 1
    text = out.read_text(encoding="utf-8")
    assert "Jane" in text
    assert rec["candidate_id"] in text


def test_rank_prefers_recent_identifiable_pain():
    old_anon = _sig(
        source="web",
        source_url="https://example.com/old",
        author_name="Senior Associate 1",
        published_at="2022-07-31",
        signal_text="open old LBO templates",
        evidence_kind="adjacent_reuse",
        relevance="relevant",
    )
    named_recent = _sig(
        source="x",
        source_url="https://x.com/a/99",
        author_name="Vladimir Andonov",
        author_handle="@vlad",
        published_at="2026-04-23",
        signal_text="we track why we passed",
        evidence_kind="pain_workaround",
        relevance="relevant",
    )
    ranked = rank_signals([old_anon, named_recent])
    assert ranked[0]["author_name"] == "Vladimir Andonov"
    assert is_identifiable(named_recent) is True
    assert is_identifiable(old_anon) is False
    assert should_research_person(old_anon) is False


def test_run_discovery_mocked_keeps_human_pending():
    signals = {
        "signals": [
            {
                "source": "x",
                "source_url": "https://x.com/p/1",
                "author_name": "Priya",
                "author_handle": "@priya",
                "published_at": "2026-08-14",
                "signal_text": "When we look at a new add-on we still end up going through old IC decks.",
                "why_relevant": "IC retrieval",
                "relevance": "relevant",
            },
            {
                "source": "web",
                "source_url": "https://example.com/post",
                "author_name": "Kip",
                "signal_text": "Funds should have a system that orchestrates their historical underwriting so AI can use it.",
                "why_relevant": "underwriting memory",
                "relevance": "highly_relevant",
            },
            {
                "source": "web",
                "source_url": "https://example.com/generic",
                "author_name": "Pundit",
                "signal_text": "AI will change private equity.",
                "why_relevant": "generic",
                "relevance": "generic",
            },
        ]
    }

    deepened = []

    def researcher(prompt, tools=None, **kwargs):
        if "person-deepening researcher" in prompt:
            deepened.append(prompt)
            return {"text": json.dumps({"evidence": []}), "citations": []}
        if "identity researcher" in prompt:
            if "orchestrates their historical underwriting" in prompt:
                return {"text": json.dumps({
                    "person": {
                        "name": "Kip",
                        "title": "Founder",
                        "company": "Adjacent",
                        "linkedin_url": "https://linkedin.com/in/kip",
                    },
                    "identity_resolved": True,
                    "actor_type": "BUILDER_OR_VENDOR",
                    "recommendation": "LIKELY_NOT_PROSPECT",
                    "recommendation_reason": "Building an adjacent solution.",
                }), "citations": []}
            return {"text": json.dumps({
                "person": {
                    "name": "Priya Shah",
                    "title": "Principal",
                    "company": "XYZ Capital",
                    "linkedin_url": "https://linkedin.com/in/priya",
                },
                "identity_resolved": True,
                "actor_type": "PRACTITIONER",
                "recommendation": "LIKELY_PROSPECT",
                "recommendation_reason": "PE principal describing the workflow.",
            }), "citations": []}
        if "Search ONLY X" in prompt:
            return {"text": json.dumps({"signals": [signals["signals"][0]]}), "citations": []}
        return {"text": json.dumps({"signals": signals["signals"][1:]}), "citations": []}

    rows = run_discovery(
        PRODUCT_PROFILES["akashic"],
        list_name="akashic",
        profile_key="akashic",
        limit=8,
        researcher=researcher,
    )
    by_name = {r["name"]: r for r in rows}
    assert by_name["Priya Shah"]["recommendation"] == "LIKELY_PROSPECT"
    assert by_name["Kip"]["actor_type"] == "BUILDER_OR_VENDOR"
    assert by_name["Kip"]["recommendation"] == "LIKELY_NOT_PROSPECT"
    generic = [r for r in rows if "AI will change" in r["signal_text"]][0]
    assert generic["recommendation"] == "LIKELY_NOT_PROSPECT"
    assert generic["person_researched"] is False
    assert all(r["human_status"] == "PENDING" for r in rows)
    assert len(deepened) == 1
    assert "Priya Shah" in deepened[0]
    assert "Kip" not in deepened[0]


def test_run_discovery_writes_research_cost_log(tmp_path):
    def researcher(prompt, tools=None, **kwargs):
        if "person-deepening researcher" in prompt:
            return {"text": json.dumps({"evidence": [{"source_url": "https://x.com/p/1", "quote_or_paraphrase": "old IC"}]}), "citations": ["https://x.com/p/1"], "usage": {"cost_usd": 0.5, "prompt_tokens": 1000, "web_calls_billable": 3, "web_calls_attempted": 4, "x_calls_billable": 0, "x_calls_attempted": 0}}
        if "identity researcher" in prompt:
            if "orchestrates" in prompt:
                return {"text": json.dumps({
                    "person": {"name": "Kip", "title": "Founder", "company": "Adjacent", "linkedin_url": "https://linkedin.com/in/kip"},
                    "identity_resolved": True,
                    "actor_type": "BUILDER_OR_VENDOR",
                    "recommendation": "LIKELY_NOT_PROSPECT",
                }), "citations": [], "usage": {"cost_usd": 0.1}}
            return {"text": json.dumps({
                "person": {"name": "Priya Shah", "title": "Principal", "company": "XYZ Capital", "linkedin_url": "https://linkedin.com/in/priya"},
                "identity_resolved": True,
                "actor_type": "PRACTITIONER",
                "recommendation": "LIKELY_PROSPECT",
            }), "citations": [], "usage": {"cost_usd": 0.2, "prompt_tokens": 900}}
        if "Search ONLY X" in prompt:
            return {"text": json.dumps({"signals": [{
                "source": "x", "source_url": "https://x.com/p/1", "author_name": "Priya",
                "author_handle": "@priya", "published_at": "2026-08-14",
                "signal_text": "When we look at a new add-on we still end up going through old IC decks.",
                "why_relevant": "IC retrieval", "relevance": "relevant",
            }]}), "citations": [], "usage": {"cost_usd": 0.3, "x_calls_billable": 8, "x_calls_attempted": 8}}
        return {"text": json.dumps({"signals": [{
            "source": "web", "source_url": "https://example.com/post", "author_name": "Kip",
            "signal_text": "Funds should have a system that orchestrates their historical underwriting so AI can use it.",
            "why_relevant": "underwriting memory", "relevance": "highly_relevant",
        }]}), "citations": [], "usage": {"cost_usd": 0.4, "web_calls_billable": 10}}

    cost_path = tmp_path / "research_cost.jsonl"
    run_discovery(
        PRODUCT_PROFILES["akashic"],
        list_name="akashic",
        profile_key="akashic",
        limit=8,
        researcher=researcher,
        cost_log_path=str(cost_path),
        run_id="test-run",
    )
    events = load_research_costs(str(cost_path))
    stages = [e["stage"] for e in events]
    assert "discovery_x" in stages
    assert "discovery_web" in stages
    assert "qualification" in stages
    assert "deepening" in stages
    assert all("prompt_ge_200k" not in e for e in events)
    assert all(e["run_id"] == "test-run" for e in events)
    deepen = [e for e in events if e["stage"] == "deepening"][0]
    assert deepen["evidence_count"] == 1
    assert deepen["cost_usd"] == 0.5
    disc_x = [e for e in events if e["stage"] == "discovery_x"][0]
    assert disc_x["evidence_count"] == 1
    assert entity_key_for("Priya Shah", "XYZ Capital") == "priya shah|xyz capital"


def test_existing_csv_loader_still_requires_email(tmp_path):
    csv_path = tmp_path / "helix_like.csv"
    csv_path.write_text(
        "First Name,Last Name,Title,Company Name,Email,Qualify Contact\n"
        "A,One,CEO,Co,,\n"
        "B,Two,CEO,Co,b@co.com,\n",
        encoding="utf-8",
    )
    leads = load_leads_from_csv(str(csv_path))
    assert len(leads) == 1
    assert leads[0]["email"] == "b@co.com"


def test_entity_search_fallbacks_do_not_require_exact_full_name():
    queries = entity_search_fallbacks(
        "Jane Q Public",
        "Acme Partners",
        "Managing Director",
    )
    blob = "\n".join(queries)
    assert '"Jane Q Public" interview' in blob
    assert '"Acme Partners" "Jane"' in blob
    assert '"Acme Partners" "Q Public"' in blob
    assert '"Acme Partners" "Managing Director"' in blob


def test_should_deepen_named_relevant_not_builder():
    qual = {
        "person": {"name": "Pat Lee", "company": "Acme"},
        "actor_type": "PRACTITIONER",
        "researched": True,
    }
    assert should_deepen(_sig(), qual) is True
    assert should_deepen(_sig(relevance="generic"), qual) is False
    assert should_deepen(_sig(), {**qual, "actor_type": "BUILDER_OR_VENDOR"}) is False
    assert should_deepen(_sig(author_name="Senior Associate 1", author_handle=""), qual) is False
    anon = {
        "person": {"name": "carried_no_interest", "title": "", "company": "", "linkedin_url": ""},
        "actor_type": "UNKNOWN",
        "researched": True,
        "identity_resolved": False,
    }
    assert identity_resolved_enough(_sig(author_name="carried_no_interest", author_handle="@carry"), anon) is False
    assert should_deepen(_sig(author_name="carried_no_interest", author_handle="@carry"), anon) is False


def test_canonical_url_strips_tracking():
    a = "https://www.intapp.com/case-study/ten-eleven-intapp-dealcloud/?gt=0"
    b = "https://intapp.com/case-study/ten-eleven-intapp-dealcloud/"
    assert canonical_url(a) == canonical_url(b)
    assert canonical_url("https://twitter.com/larryvc/status/1") == canonical_url("https://x.com/larryvc/status/1/")


def test_same_handle_signals_qualify_once(tmp_path):
    calls = {"qual": 0, "deep": 0}

    def researcher(prompt, tools=None, **kwargs):
        if "person-deepening researcher" in prompt:
            calls["deep"] += 1
            return {"text": json.dumps({"evidence": []}), "citations": []}
        if "identity researcher" in prompt:
            calls["qual"] += 1
            return {"text": json.dumps({
                "person": {
                    "name": "Larry Cheng",
                    "title": "Managing Partner",
                    "company": "Volition Capital",
                    "linkedin_url": "https://www.linkedin.com/in/larrycheng",
                },
                "identity_resolved": True,
                "actor_type": "PRACTITIONER",
                "recommendation": "PRIMARY_PROSPECT",
                "recommendation_reason": "Owns underwriting.",
            }), "citations": []}
        if "Search ONLY X" in prompt:
            return {"text": json.dumps({"signals": [
                {
                    "source": "x",
                    "source_url": "https://x.com/larryvc/status/1",
                    "author_name": "Larry Cheng",
                    "author_handle": "@larryvc",
                    "published_at": "2024-05-15",
                    "signal_text": "review original diligence years later",
                    "why_relevant": "hindsight",
                    "relevance": "highly_relevant",
                },
                {
                    "source": "x",
                    "source_url": "https://x.com/larryvc/status/2",
                    "author_name": "Larry Cheng",
                    "author_handle": "@larryvc",
                    "published_at": "2024-12-01",
                    "signal_text": "reconstruct why a great investment could have been passed",
                    "why_relevant": "pass reasons",
                    "relevance": "highly_relevant",
                },
            ]}), "citations": []}
        return {"text": json.dumps({"signals": []}), "citations": []}

    rows = run_discovery(
        PRODUCT_PROFILES["akashic"],
        list_name="akashic",
        profile_key="akashic",
        limit=8,
        researcher=researcher,
        cache_path=str(tmp_path / "cache.jsonl"),
    )
    larry = [r for r in rows if r.get("name") == "Larry Cheng"]
    assert len(larry) == 1
    assert len(larry[0].get("additional_signals") or []) == 1
    assert calls["qual"] == 1
    assert signal_group_key({
        "author_handle": "@larryvc",
        "source_url": "https://x.com/larryvc/status/9",
        "author_name": "Larry Cheng",
    }) == "h:larryvc"


def test_cache_hit_skips_qualification_and_deepening(tmp_path):
    cache_path = tmp_path / "cache.jsonl"
    calls = {"qual": 0, "deep": 0}

    def researcher(prompt, tools=None, **kwargs):
        if "person-deepening researcher" in prompt:
            calls["deep"] += 1
            return {"text": json.dumps({"evidence": [{"source_url": "https://x.com/b/1", "quote_or_paraphrase": "pass twice"}]}), "citations": []}
        if "identity researcher" in prompt:
            calls["qual"] += 1
            return {"text": json.dumps({
                "person": {
                    "name": "Brent Beshore",
                    "title": "CEO",
                    "company": "Permanent Equity",
                    "linkedin_url": "https://www.linkedin.com/in/brentbeshore",
                },
                "identity_resolved": True,
                "actor_type": "PRACTITIONER",
                "recommendation": "CHAMPION_CANDIDATE",
                "pain_evidence": "HIGH",
                "behavioral_evidence": "HIGH",
            }), "citations": []}
        if "Search ONLY X" in prompt:
            return {"text": json.dumps({"signals": [{
                "source": "x",
                "source_url": "https://x.com/BrentBeshore/status/1858140336993738877",
                "author_name": "Brent Beshore",
                "author_handle": "@BrentBeshore",
                "published_at": "2024-11-17",
                "signal_text": "I passed on it twice",
                "why_relevant": "pass history",
                "relevance": "highly_relevant",
            }]}), "citations": []}
        return {"text": json.dumps({"signals": []}), "citations": []}

    run_discovery(
        PRODUCT_PROFILES["akashic"],
        list_name="akashic",
        profile_key="akashic",
        researcher=researcher,
        cache_path=str(cache_path),
    )
    assert calls["qual"] == 1
    assert calls["deep"] == 1
    calls["qual"] = 0
    calls["deep"] = 0
    rows = run_discovery(
        PRODUCT_PROFILES["akashic"],
        list_name="akashic",
        profile_key="akashic",
        researcher=researcher,
        cache_path=str(cache_path),
    )
    assert calls["qual"] == 0
    assert calls["deep"] == 0
    brent = [r for r in rows if "Beshore" in (r.get("name") or "")][0]
    assert brent.get("cache_hit") is True


def test_title_cannot_veto_strong_behavioral_evidence():
    rec = derive_recommendation(
        actor_type="PRACTITIONER",
        raw_recommendation="LIKELY_NOT_PROSPECT",
        axes={
            "persona_fit": "MEDIUM",
            "pain_evidence": "HIGH",
            "behavioral_evidence": "VERY_HIGH",
            "workaround_evidence": "HIGH",
            "outcome_feedback_evidence": "HIGH",
            "influence_or_champion_potential": "HIGH",
            "economic_buyer_likelihood": "UNKNOWN",
            "end_user_likelihood": "LOW",
        },
    )
    assert rec == "CHAMPION_CANDIDATE"
    rec_no_champion = derive_recommendation(
        actor_type="PRACTITIONER",
        raw_recommendation="LIKELY_NOT_PROSPECT",
        axes={
            "pain_evidence": "HIGH",
            "behavioral_evidence": "HIGH",
            "influence_or_champion_potential": "LOW",
        },
    )
    assert rec_no_champion == "HIGH_VALUE_DISCOVERY"


def test_builder_stays_not_prospect_even_with_high_pain():
    rec = derive_recommendation(
        actor_type="BUILDER_OR_VENDOR",
        raw_recommendation="LIKELY_NOT_PROSPECT",
        axes={"pain_evidence": "HIGH", "behavioral_evidence": "HIGH"},
    )
    assert rec == "LIKELY_NOT_PROSPECT"


def test_rank_does_not_bury_old_named_workaround():
    old_named = _sig(
        source="web",
        source_url="https://example.com/old-named",
        author_name="Pat Lee",
        author_handle="",
        published_at="2022-04-23",
        signal_text="we track why we passed",
        evidence_kind="WORKAROUND",
        relevance="relevant",
    )
    recent_adjacent = _sig(
        source="web",
        source_url="https://example.com/new-adj",
        author_name="Riley Chen",
        published_at="2026-08-01",
        signal_text="we reuse old templates",
        evidence_kind="adjacent_reuse",
        relevance="relevant",
    )
    ranked = rank_signals([recent_adjacent, old_named])
    assert ranked[0]["author_name"] == "Pat Lee"


def test_evidence_kind_families_normalize():
    items = parse_signal_list(json.dumps({
        "signals": [
            {
                "source": "web",
                "source_url": "https://example.com/a",
                "signal_text": "that became our playbook",
                "evidence_kind": "BEHAVIORAL_REUSE",
            },
            {
                "source": "web",
                "source_url": "https://example.com/b",
                "signal_text": "crm pass reasons",
                "evidence_kind": "pain_workaround",
            },
        ]
    }))
    assert items[0]["evidence_kind"] == "BEHAVIORAL_REUSE"
    assert items[1]["evidence_kind"] == "WORKAROUND"


def test_enrich_approved_attaches_apollo_email_only_to_approved():
    prospect = build_candidate(
        signal=_sig(source_url="https://x.com/a/1", signal_text="one"),
        person={"name": "Jane Doe", "title": "VP", "company": "Acme",
                "linkedin_url": "https://linkedin.com/in/jane"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    pending = build_candidate(
        signal=_sig(source_url="https://x.com/b/2", signal_text="two"),
        person={"name": "Pat Lee", "title": "Principal", "company": "Fund"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    apply_human_decision([prospect], prospect["candidate_id"], "APPROVED")

    def matcher(details, **kwargs):
        assert kwargs.get("reveal_personal_emails") is True
        assert kwargs.get("reveal_phone_number") is False
        assert details[0]["linkedin_url"]
        return {"matches": [{"email": "jane@acme.com", "title": "VP Sales"}]}

    n = enrich_approved_candidates([prospect, pending], matcher=matcher, progress=None)
    assert n == 1
    assert prospect["email"] == "jane@acme.com"
    assert prospect["email_found"] is True
    assert pending["email"] == ""
    assert pending.get("enrichment_attempted") is not True


def test_enrich_approved_falls_back_to_hunter(monkeypatch):
    prospect = build_candidate(
        signal=_sig(source_url="https://x.com/a/1", signal_text="one"),
        person={"name": "Jane Doe", "title": "VP", "company": "Acme",
                "linkedin_url": "https://linkedin.com/in/jane"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    apply_human_decision([prospect], prospect["candidate_id"], "APPROVED")
    monkeypatch.setenv("HUNTER_API_KEY", "test-hunter")

    def matcher(details, **kwargs):
        return {"matches": [None]}

    def hunter_finder(rec):
        assert rec["name"] == "Jane Doe"
        return {"email": "jane@acme.com"}

    n = enrich_approved_candidates(
        [prospect],
        matcher=matcher,
        hunter_finder=hunter_finder,
        progress=None,
    )
    assert n == 1
    assert prospect["email"] == "jane@acme.com"
    assert prospect["email_source"] == "Hunter.io"


def test_interactive_review_approve_reject_skip(tmp_path, monkeypatch):
    from main import _run_interactive_review

    approved_src = build_candidate(
        signal=_sig(source_url="https://x.com/a/1", signal_text="one"),
        person={"name": "Jane Doe", "title": "VP", "company": "Acme"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    rejected_src = build_candidate(
        signal=_sig(source_url="https://x.com/b/2", signal_text="two"),
        person={"name": "Pat Lee", "title": "Principal", "company": "Fund"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    skipped_src = build_candidate(
        signal=_sig(source_url="https://x.com/c/3", signal_text="three"),
        person={"name": "Sam Kim", "title": "AE", "company": "Co"},
        actor_type="PRACTITIONER",
        recommendation="LIKELY_PROSPECT",
        recommendation_reason="ok",
        list_name="helix",
        profile_key="problem_validation",
        product_name="Helix",
    )
    path = tmp_path / "cands.jsonl"
    save_candidates(str(path), [approved_src, rejected_src, skipped_src])
    answers = iter(["a", "r", "vendor", "s"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    n = _run_interactive_review(str(path))
    assert n == 1
    by_name = {r["name"]: r for r in load_candidates(str(path))}
    assert by_name["Jane Doe"]["human_status"] == "APPROVED"
    assert by_name["Pat Lee"]["human_status"] == "REJECTED"
    assert by_name["Pat Lee"]["human_reject_reason"] == "vendor"
    assert by_name["Sam Kim"]["human_status"] == "PENDING"


def test_prompt_yes_no_defaults(monkeypatch):
    from main import _prompt_yes_no

    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    assert _prompt_yes_no("go?", default=True) is True
    assert _prompt_yes_no("go?", default=False) is False
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    assert _prompt_yes_no("go?", default=False) is True



