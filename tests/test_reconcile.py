"""Reconciliation semantics. See docs/reconciliation-model.md.

Central distinction:
- VERIFIED evidence: the destination kept this lead's ID.
- IDENTITY evidence: the person exists (email/phone); this lead/event is not proven.
- NONE: neither, outside the grace period -> MISSING (or TEMPORAL_UNCERTAIN if clocks are in doubt).
"""

from dataclasses import replace
from datetime import timedelta

from helpers import healthy, identity_record_for, make_lead, make_settings, record_for

from lead_integrity.models import (
    AmbiguityReason,
    Confidence,
    Evidence,
    FindingKind,
    IssueKind,
    LatencyStatus,
    MatchMethod,
    Status,
    TimezoneCheck,
)
from lead_integrity.reconcile import (
    NOTE_EXCLUDED_IDENTITY,
    NOTE_EXCLUDED_VERIFIED,
    NOTE_PHONE_UNPARSEABLE,
    reconcile,
)

# --- Verified evidence -------------------------------------------------------


def test_healthy_dataset():  # caso 1
    leads, records = healthy(100)
    result = reconcile(leads, records, make_settings())
    assert result.counts() == {Status.VERIFIED: 100}
    assert {o.evidence for o in result.outcomes} == {Evidence.VERIFIED}
    assert {o.latency_status for o in result.outcomes} == {LatencyStatus.MEASURED}
    assert {o.field_check_basis for o in result.outcomes} == {Evidence.VERIFIED}
    assert all(not o.issues for o in result.outcomes)
    assert result.orphans == []
    assert result.findings == []


def test_missing_records():  # caso 2
    leads, records = healthy(100)
    result = reconcile(leads, records[:96], make_settings())
    missing = [o.lead.lead_id for o in result.outcomes if o.status is Status.MISSING]
    assert missing == [lead.lead_id for lead in leads[96:]]
    assert result.outcome(leads[96].lead_id).evidence is Evidence.NONE
    assert result.outcome(leads[96].lead_id).latency_status is LatencyStatus.NOT_APPLICABLE
    assert result.outcome(leads[96].lead_id).field_check_basis is None


def test_duplicate_destination_record():  # caso 3
    leads, records = healthy(100)
    records.append(replace(records[10], record_id="crm-dup"))
    result = reconcile(leads, records, make_settings())
    outcome = result.outcome(leads[10].lead_id)
    assert outcome.status is Status.DUPLICATED
    assert {m.record_id for m in outcome.matches} == {records[10].record_id, "crm-dup"}
    assert result.orphans == []


def test_attribution_missing_in_destination():  # caso 4
    leads, records = healthy(100)
    records[5] = replace(records[5], campaign_id=None)
    result = reconcile(leads, records, make_settings())
    outcome = result.outcome(leads[5].lead_id)
    assert outcome.status is Status.VERIFIED
    assert outcome.issue_kinds == {IssueKind.ATTRIBUTION_MISSING}
    assert outcome.issues[0].field == "campaign_id"
    assert outcome.issues[0].evidence is Evidence.VERIFIED
    assert result.findings == []


def test_late_arrival_depends_on_configured_threshold():  # caso 5
    leads, records = healthy(10)
    records[7] = record_for(leads[7], delay=timedelta(minutes=20))

    strict = reconcile(leads, records, make_settings(late_after=timedelta(minutes=15)))
    assert strict.outcome(leads[7].lead_id).issue_kinds == {IssueKind.LATE}
    assert strict.outcome(leads[7].lead_id).latency == timedelta(minutes=20)

    lenient = reconcile(leads, records, make_settings(late_after=timedelta(minutes=30)))
    assert lenient.outcome(leads[7].lead_id).issues == []


def test_intentionally_filtered_lead_is_excluded():  # caso 8
    leads = [make_lead(0), make_lead(1, form_id="form-test"), make_lead(2)]
    settings = make_settings(excluded_form_ids=frozenset({"form-test"}), excluded_lead_ids=frozenset({leads[2].lead_id}))
    result = reconcile(leads, [record_for(leads[0])], settings)
    assert result.outcome(leads[1].lead_id).status is Status.EXCLUDED
    assert result.outcome(leads[2].lead_id).status is Status.EXCLUDED
    assert result.outcome(leads[1].lead_id).notes == []


def test_lead_id_match_wins_and_reports_changed_email():
    leads, records = healthy(10)
    records[2] = replace(records[2], email="other@example.com")
    outcome = reconcile(leads, records, make_settings()).outcome(leads[2].lead_id)
    assert [m.method for m in outcome.matches] == [MatchMethod.LEAD_ID]
    assert outcome.issue_kinds == {IssueKind.FIELD_MISMATCH}
    assert outcome.issues[0].field == "email"


# --- Identity evidence --------------------------------------------------------


def test_without_lead_ids_in_destination_nothing_is_verified():
    # Typical HubSpot native sync: the person exists, the event is not proven.
    leads = [make_lead(i) for i in range(10)]
    records = [identity_record_for(lead) for lead in leads]
    result = reconcile(leads, records, make_settings())
    assert result.counts() == {Status.IDENTITY_MATCH: 10}
    assert {o.evidence for o in result.outcomes} == {Evidence.IDENTITY}
    assert result.finding_kinds == [FindingKind.LEAD_ID_NOT_PRESERVED]


def test_identity_match_makes_no_arrival_claims():
    leads = [make_lead(i) for i in range(10)]
    records = [identity_record_for(lead, delay=timedelta(hours=5, minutes=17)) for lead in leads]
    result = reconcile(leads, records, make_settings())
    assert all(o.latency is None for o in result.outcomes)
    assert {o.latency_status for o in result.outcomes} == {LatencyStatus.NOT_VERIFIABLE}
    assert all(IssueKind.LATE not in o.issue_kinds for o in result.outcomes)


def test_identity_match_compares_the_persons_record_not_the_event():
    leads = [make_lead(i) for i in range(10)]
    records = [identity_record_for(lead) for lead in leads]
    records[3] = replace(records[3], campaign_id="c-999")
    outcome = reconcile(leads, records, make_settings()).outcome(leads[3].lead_id)
    assert outcome.status is Status.IDENTITY_MATCH
    assert outcome.issue_kinds == {IssueKind.ATTRIBUTION_MISMATCH}
    assert outcome.issues[0].evidence is Evidence.IDENTITY
    assert outcome.field_check_basis is Evidence.IDENTITY


def test_identity_match_via_additional_emails():
    lead = make_lead(4)
    record = identity_record_for(lead, email="new@example.com", additional_emails=(lead.email,))
    outcome = reconcile([lead], [record], make_settings()).outcome(lead.lead_id)
    assert outcome.status is Status.IDENTITY_MATCH
    assert [m.method for m in outcome.matches] == [MatchMethod.EMAIL]
    assert outcome.issues == []


def test_phone_fallback_is_low_confidence_identity():  # caso 7
    lead = make_lead(3)
    record = identity_record_for(lead, email=None)
    outcome = reconcile([lead], [record], make_settings()).outcome(lead.lead_id)
    assert outcome.status is Status.IDENTITY_MATCH
    assert [(m.method, m.confidence) for m in outcome.matches] == [(MatchMethod.PHONE, Confidence.LOW)]


# --- Two leads, one record ----------------------------------------------------


def test_same_email_different_lead_ids_is_ambiguous():  # caso 10
    a = make_lead(0, email="ana@example.com")
    b = make_lead(1, email="ana@example.com")
    result = reconcile([a, b], [identity_record_for(a)], make_settings())
    for lead in (a, b):
        outcome = result.outcome(lead.lead_id)
        assert outcome.status is Status.AMBIGUOUS
        assert outcome.reasons == [AmbiguityReason.SHARED_DESTINATION]
        assert outcome.issues == []  # record cannot be attributed, so no state comparison
        assert outcome.field_check_basis is None
        assert outcome.latency_status is LatencyStatus.NOT_VERIFIABLE


def test_crm_merge_via_additional_email_is_ambiguous():  # caso 6
    a = make_lead(0, email="ana@example.com")
    b = make_lead(1, email="ana.perez@example.org")
    merged = identity_record_for(a, additional_emails=("ana.perez@example.org",))
    result = reconcile([a, b], [merged], make_settings())
    assert {result.outcome(a.lead_id).status, result.outcome(b.lead_id).status} == {Status.AMBIGUOUS}


def test_merge_with_both_lead_ids_kept_is_verified_merged():
    a = make_lead(0, email="ana@example.com")
    b = make_lead(1, email="ana@example.com")
    merged = record_for(a, source_lead_ids=(a.lead_id, b.lead_id))
    result = reconcile([a, b], [merged], make_settings())
    for lead in (a, b):
        assert result.outcome(lead.lead_id).status is Status.MERGED
        assert result.outcome(lead.lead_id).evidence is Evidence.VERIFIED


# --- Record older than the lead -----------------------------------------------


def test_preexisting_record_with_identity_only_is_ambiguous():
    lead = make_lead(0)
    record = identity_record_for(lead, delay=-timedelta(days=30))
    outcome = reconcile([lead], [record], make_settings()).outcome(lead.lead_id)
    assert outcome.status is Status.AMBIGUOUS
    assert outcome.reasons == [AmbiguityReason.PREEXISTING_RECORD]
    assert outcome.latency is None


def test_preexisting_record_holding_lead_id_is_verified_without_latency():
    lead = make_lead(0)
    record = record_for(lead, delay=-timedelta(days=30))
    outcome = reconcile([lead], [record], make_settings()).outcome(lead.lead_id)
    assert outcome.status is Status.VERIFIED
    assert outcome.latency is None
    assert outcome.latency_status is LatencyStatus.NOT_VERIFIABLE
    assert IssueKind.LATE not in outcome.issue_kinds


# --- Email points to a record that holds another lead's ID --------------------


def test_record_claimed_by_other_lead_cannot_prove_this_one():
    # The case that would hide a lost lead if treated as MERGED.
    a = make_lead(0, email="ana@example.com")
    b = make_lead(1, email="ana@example.com")
    result = reconcile([a, b], [record_for(a)], make_settings())
    assert result.outcome(a.lead_id).status is Status.VERIFIED
    assert result.outcome(b.lead_id).status is Status.AMBIGUOUS
    assert result.outcome(b.lead_id).reasons == [AmbiguityReason.CLAIMED_BY_OTHER_LEAD]


def test_several_records_for_same_identity_is_ambiguous():
    lead = make_lead(0)
    records = [identity_record_for(lead), identity_record_for(lead, record_id="crm-other")]
    outcome = reconcile([lead], records, make_settings()).outcome(lead.lead_id)
    assert outcome.status is Status.AMBIGUOUS
    assert outcome.reasons == [AmbiguityReason.MULTIPLE_CANDIDATES]


# --- Timezone uncertainty -----------------------------------------------------


def test_systematic_offset_is_a_finding_and_disables_late():  # caso 9
    leads = [make_lead(i) for i in range(30)]
    records = [record_for(lead, delay=timedelta(hours=3, minutes=2)) for lead in leads]
    result = reconcile(leads, records, make_settings())
    assert result.finding_kinds == [FindingKind.TIMEZONE_UNCERTAIN]
    assert all(o.status is Status.VERIFIED for o in result.outcomes)
    assert all(o.latency is None and not o.issues for o in result.outcomes)
    assert {o.latency_status for o in result.outcomes} == {LatencyStatus.TEMPORAL_UNCERTAIN}


def test_systematic_negative_offset_disables_preexisting_inference():
    leads = [make_lead(i) for i in range(30)]
    records = [identity_record_for(lead, delay=-timedelta(hours=3) + timedelta(minutes=2)) for lead in leads]
    result = reconcile(leads, records, make_settings())
    assert result.finding_kinds == [FindingKind.TIMEZONE_UNCERTAIN, FindingKind.LEAD_ID_NOT_PRESERVED]
    assert result.counts() == {Status.IDENTITY_MATCH: 30}


def test_timestamps_are_never_corrected():
    leads = [make_lead(i) for i in range(30)]
    records = [record_for(lead, delay=timedelta(hours=3)) for lead in leads]
    result = reconcile(leads, records, make_settings())
    assert [o.lead for o in result.outcomes] == leads


def test_uncertain_time_means_undecidable_not_missing_and_no_window():
    # MISSING is a time claim (outside grace). With doubtful clocks we do not make it at any age.
    leads = [make_lead(i) for i in range(30)]
    records = [record_for(lead, delay=timedelta(hours=3)) for lead in leads]
    settings = make_settings()
    recent = make_lead(100, created_at=settings.as_of - timedelta(minutes=30))
    near = make_lead(101, created_at=settings.as_of - timedelta(hours=5))
    far = make_lead(102, created_at=settings.as_of - timedelta(days=60))
    result = reconcile(leads + [recent, near, far], records, settings)
    for lead in (recent, near, far):
        outcome = result.outcome(lead.lead_id)
        assert outcome.status is Status.TEMPORAL_UNCERTAIN
        assert outcome.evidence is Evidence.NONE


def test_timezone_heuristic_is_configurable():
    leads = [make_lead(i) for i in range(6)]
    records = [record_for(lead, delay=timedelta(hours=3)) for lead in leads]
    default = reconcile(leads, records, make_settings())
    assert default.findings == []  # 6 pairs < min_pairs=10: no diagnosis
    loose = make_settings(timezone_check=TimezoneCheck(min_pairs=5, min_share=0.8, tolerance=timedelta(minutes=15)))
    assert reconcile(leads, records, loose).finding_kinds == [FindingKind.TIMEZONE_UNCERTAIN]


def test_scattered_delays_are_not_a_timezone_problem():
    leads = [make_lead(i) for i in range(30)]
    records = [record_for(lead, delay=timedelta(minutes=7 * i)) for i, lead in enumerate(leads)]
    assert reconcile(leads, records, make_settings()).findings == []


# --- Excluded lead that still arrived -----------------------------------------


def test_excluded_lead_present_in_destination_is_noted_not_orphan():
    lead = make_lead(0, form_id="form-test")
    result = reconcile([lead], [record_for(lead)], make_settings(excluded_form_ids=frozenset({"form-test"})))
    outcome = result.outcome(lead.lead_id)
    assert outcome.status is Status.EXCLUDED
    assert outcome.notes == [NOTE_EXCLUDED_VERIFIED]
    assert outcome.issues == []
    assert result.orphans == []


def test_excluded_lead_found_only_by_identity_does_not_claim_the_event():
    lead = make_lead(0, form_id="form-test")
    result = reconcile([lead], [identity_record_for(lead)], make_settings(excluded_form_ids=frozenset({"form-test"})))
    assert result.outcome(lead.lead_id).notes == [NOTE_EXCLUDED_IDENTITY]


# --- Orphans only with a pipeline-specific destination scope ------------------


def test_unmatched_destination_record_is_orphan_when_scope_is_pipeline_specific():
    leads, records = healthy(5)
    stray = identity_record_for(make_lead(99, email="manual@example.com", phone=None))
    result = reconcile(leads, records + [stray], make_settings())
    assert result.orphans == [stray]


def test_orphans_not_evaluated_for_general_crm_export():
    leads, records = healthy(5)
    stray = identity_record_for(make_lead(99, email="manual@example.com", phone=None))
    result = reconcile(leads, records + [stray], make_settings(destination_scope_is_pipeline_specific=False))
    assert result.orphans is None


# --- Phones -------------------------------------------------------------------


def test_unparseable_phone_is_never_used_for_matching():
    lead = make_lead(0, email=None, phone=None, phone_raw="123")
    record = identity_record_for(lead, email=None, phone=None, phone_raw="123")
    outcome = reconcile([lead], [record], make_settings()).outcome(lead.lead_id)
    assert outcome.status is Status.MISSING
    assert outcome.notes == [NOTE_PHONE_UNPARSEABLE]


def test_unparseable_destination_phone_is_reported_as_such():
    leads, records = healthy(10)
    records[2] = replace(records[2], phone=None, phone_raw="555-0123")
    outcome = reconcile(leads, records, make_settings()).outcome(leads[2].lead_id)
    assert outcome.issue_kinds == {IssueKind.PHONE_UNPARSEABLE}
    assert (outcome.issues[0].source_value, outcome.issues[0].dest_value) == (None, "555-0123")


def test_unparseable_source_phone_is_reported_as_such():
    leads, records = healthy(10)
    leads[2] = replace(leads[2], phone=None, phone_raw="09")
    outcome = reconcile(leads, records, make_settings()).outcome(leads[2].lead_id)
    assert outcome.issue_kinds == {IssueKind.PHONE_UNPARSEABLE}


def test_different_valid_numbers_are_never_treated_as_equivalent():
    # No fuzzy or country-specific phone repairs: only exact E.164 equality matches.
    lead = make_lead(0, email=None, phone="+12025550123")
    record = identity_record_for(lead, phone="+12125550123")
    assert reconcile([lead], [record], make_settings()).outcome(lead.lead_id).status is Status.MISSING


# --- Field and attribution rules ----------------------------------------------


def test_organic_lead_without_attribution_has_no_issues():
    lead = make_lead(0, is_organic=True, campaign_id=None, campaign_name=None, adset_id=None, ad_id=None)
    outcome = reconcile([lead], [record_for(lead)], make_settings()).outcome(lead.lead_id)
    assert outcome.status is Status.VERIFIED
    assert outcome.issues == []


def test_renamed_campaign_is_not_flagged_when_ids_match():
    leads, records = healthy(10)
    records[1] = replace(records[1], campaign_name="October Promo v2")
    assert reconcile(leads, records, make_settings()).outcome(leads[1].lead_id).issues == []


def test_campaign_id_mismatch():
    leads, records = healthy(10)
    records[1] = replace(records[1], campaign_id="c-999")
    outcome = reconcile(leads, records, make_settings()).outcome(leads[1].lead_id)
    assert outcome.issue_kinds == {IssueKind.ATTRIBUTION_MISMATCH}


def test_phone_lost_in_destination():
    leads, records = healthy(10)
    records[6] = replace(records[6], phone=None, phone_raw=None)
    outcome = reconcile(leads, records, make_settings()).outcome(leads[6].lead_id)
    assert outcome.issue_kinds == {IssueKind.FIELD_MISMATCH}
    assert outcome.issues[0].field == "phone"


def test_extra_destination_data_is_not_an_issue():
    leads, records = healthy(10)
    leads[0] = replace(leads[0], phone=None, phone_raw=None)
    assert reconcile(leads, records, make_settings()).outcome(leads[0].lead_id).issues == []


# --- Pipeline-level findings --------------------------------------------------


def test_field_never_transmitted_is_one_finding_not_n_issues():
    leads, records = healthy(50)
    records = [replace(r, campaign_id=None) for r in records]
    result = reconcile(leads, records, make_settings())
    assert [(f.kind, f.field) for f in result.findings] == [(FindingKind.FIELD_NOT_TRANSMITTED, "campaign_id")]
    assert all(not o.issues for o in result.outcomes)


def test_systemic_campaign_id_falls_back_to_name_comparison():
    leads, records = healthy(50)
    records = [replace(r, campaign_id=None) for r in records]
    records[3] = replace(records[3], campaign_name="Another campaign")
    outcome = reconcile(leads, records, make_settings()).outcome(leads[3].lead_id)
    assert outcome.issue_kinds == {IssueKind.ATTRIBUTION_MISMATCH}
    assert outcome.issues[0].field == "campaign_name"
