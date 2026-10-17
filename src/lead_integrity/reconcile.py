"""Match source leads to destination records and apply reconciliation rules.

Core distinction (see docs/reconciliation-model.md):
- VERIFIED evidence: the destination kept this lead's ID.
- IDENTITY evidence: the person exists (email/phone); this lead/event is not proven.
State comparisons (attribution, fields) are allowed on IDENTITY_MATCH because they describe
the person's record; they do not prove the event was processed (Issue.evidence says which).
Event claims (arrival time, LATE) require VERIFIED. Identity matches against a CRM export that is not
scoped to the audited pipeline are never IDENTITY_MATCH: the contact may come from another source.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import timedelta

from lead_integrity.models import (
    AmbiguityReason,
    Confidence,
    DestRecord,
    Evidence,
    FindingKind,
    Issue,
    IssueKind,
    LatencyStatus,
    LeadOutcome,
    Match,
    MatchMethod,
    PipelineFinding,
    Result,
    Settings,
    SourceLead,
    Status,
)

# Fields whose loss we detect. Owner, lifecycle and tags are out of scope (see docs/reconciliation-model.md).
_ATTRIBUTION_FIELDS = ("campaign_id", "campaign_name")
_IDENTITY_FIELDS = ("email", "phone")

_COMPARABLE = (Status.VERIFIED, Status.IDENTITY_MATCH)

NOTE_EXCLUDED_VERIFIED = "present in destination (lead ID verified)"
NOTE_EXCLUDED_IDENTITY = "person found in destination by email/phone (event not proven)"
NOTE_PHONE_UNPARSEABLE = "phone unparseable: not used for matching"


class _Index:
    def __init__(self, records: Sequence[DestRecord]):
        self.by_lead_id: dict[str, list[DestRecord]] = defaultdict(list)
        self.by_email: dict[str, list[DestRecord]] = defaultdict(list)
        self.by_phone: dict[str, list[DestRecord]] = defaultdict(list)
        for r in records:
            for lead_id in r.source_lead_ids:
                self.by_lead_id[lead_id].append(r)
            for email in {r.email, *r.additional_emails} - {None}:
                self.by_email[email].append(r)
            if r.phone:
                self.by_phone[r.phone].append(r)

    def verified(self, lead: SourceLead) -> list[DestRecord]:
        return self.by_lead_id.get(lead.lead_id, [])

    def identity(self, lead: SourceLead) -> tuple[list[DestRecord], MatchMethod | None]:
        """Email first, phone only if email finds nothing. Invalid phones never get here."""
        if lead.email and lead.email in self.by_email:
            return self.by_email[lead.email], MatchMethod.EMAIL
        if lead.phone and lead.phone in self.by_phone:
            return self.by_phone[lead.phone], MatchMethod.PHONE
        return [], None


def reconcile(leads: Sequence[SourceLead], records: Sequence[DestRecord], settings: Settings) -> Result:
    index = _Index(records)
    by_id = {r.record_id: r for r in records}
    accounted: set[str] = set()  # record IDs explained by some source lead

    outcomes: list[LeadOutcome] = []
    candidates: dict[str, list[DestRecord]] = {}
    for lead in leads:
        outcome, cands = _first_pass(lead, index, settings)
        outcomes.append(outcome)
        candidates[lead.lead_id] = cands
        accounted.update(r.record_id for r in cands)

    # Records reached by identity from more than one active lead.
    claimants = Counter(
        r.record_id for o in outcomes if o.status is Status.IDENTITY_MATCH for r in candidates[o.lead.lead_id]
    )
    for o in outcomes:
        if o.status is Status.IDENTITY_MATCH:
            o.reasons = _structural_reasons(candidates[o.lead.lead_id], claimants)

    findings: list[PipelineFinding] = []
    tz_finding = _timezone_finding(outcomes, candidates, settings)
    time_is_reliable = tz_finding is None
    if tz_finding:
        findings.append(tz_finding)
    if records and not any(r.source_lead_ids for r in records):
        findings.append(
            PipelineFinding(
                FindingKind.LEAD_ID_NOT_PRESERVED,
                "source_lead_ids",
                "No destination record keeps a source lead ID: no event can be verified.",
            )
        )

    for o in outcomes:
        if o.status is Status.IDENTITY_MATCH:
            record = candidates[o.lead.lead_id][0]
            if time_is_reliable and not o.reasons and record.created_at < o.lead.created_at:
                o.reasons.append(AmbiguityReason.PREEXISTING_RECORD)
            if not settings.destination_scope_is_pipeline_specific:
                o.reasons.append(AmbiguityReason.UNSCOPED_DESTINATION)
            if o.reasons:
                o.status = Status.AMBIGUOUS
        elif o.status in (Status.MISSING, Status.PENDING):
            o.status = _missing_or_pending(o.lead, settings, time_is_reliable)
        _measure_latency(o, candidates[o.lead.lead_id], settings, time_is_reliable)

    comparable = [(o, by_id[o.matches[0].record_id]) for o in outcomes if o.status in _COMPARABLE]
    findings += _systemic_findings(comparable, settings)
    silenced = {f.field for f in findings}
    for o, record in comparable:
        _compare_record(o, record, silenced)

    orphans = None
    if settings.destination_scope_is_pipeline_specific:
        orphans = [r for r in records if r.record_id not in accounted]
    return Result(outcomes, orphans, findings, by_id)


def _first_pass(lead: SourceLead, index: _Index, settings: Settings) -> tuple[LeadOutcome, list[DestRecord]]:
    excluded = lead.lead_id in settings.excluded_lead_ids or lead.form_id in settings.excluded_form_ids

    verified = index.verified(lead)
    if verified:
        matches = [Match(r.record_id, MatchMethod.LEAD_ID, Confidence.HIGH) for r in verified]
        if excluded:
            return LeadOutcome(lead, Status.EXCLUDED, matches, notes=[NOTE_EXCLUDED_VERIFIED]), verified
        if len(verified) > 1:
            return LeadOutcome(lead, Status.DUPLICATED, matches), verified
        shared = len(verified[0].source_lead_ids) > 1
        return LeadOutcome(lead, Status.MERGED if shared else Status.VERIFIED, matches), verified

    cands, method = index.identity(lead)
    if cands:
        confidence = Confidence.HIGH if method is MatchMethod.EMAIL else Confidence.LOW
        matches = [Match(r.record_id, method, confidence) for r in cands]
        if excluded:
            return LeadOutcome(lead, Status.EXCLUDED, matches, notes=[NOTE_EXCLUDED_IDENTITY]), cands
        # Provisional: may become AMBIGUOUS once all leads are seen.
        return LeadOutcome(lead, Status.IDENTITY_MATCH, matches), cands

    if excluded:
        return LeadOutcome(lead, Status.EXCLUDED), []
    notes = [NOTE_PHONE_UNPARSEABLE] if lead.phone_raw and not lead.phone else []
    return LeadOutcome(lead, Status.MISSING, notes=notes), []  # MISSING vs PENDING decided later


def _structural_reasons(cands: list[DestRecord], claimants: Counter[str]) -> list[AmbiguityReason]:
    reasons = []
    if any(r.source_lead_ids for r in cands):
        reasons.append(AmbiguityReason.CLAIMED_BY_OTHER_LEAD)
    if len(cands) > 1:
        reasons.append(AmbiguityReason.MULTIPLE_CANDIDATES)
    if any(claimants[r.record_id] > 1 for r in cands):
        reasons.append(AmbiguityReason.SHARED_DESTINATION)
    return reasons


def _timezone_finding(
    outcomes: list[LeadOutcome], candidates: dict[str, list[DestRecord]], settings: Settings
) -> PipelineFinding | None:
    """Deltas clustered near the same non-zero whole hour suggest a timezone mistake. Never corrected."""
    check = settings.timezone_check
    deltas = [
        candidates[o.lead.lead_id][0].created_at - o.lead.created_at
        for o in outcomes
        if o.status is Status.VERIFIED or (o.status is Status.IDENTITY_MATCH and not o.reasons)
    ]
    if len(deltas) < check.min_pairs:
        return None
    hours = Counter()
    for d in deltas:
        h = round(d / timedelta(hours=1))
        if h != 0 and abs(d - timedelta(hours=h)) <= check.tolerance:
            hours[h] += 1
    if not hours:
        return None
    h, n = hours.most_common(1)[0]
    if n / len(deltas) < check.min_share:
        return None
    return PipelineFinding(
        FindingKind.TIMEZONE_UNCERTAIN,
        None,
        f"{n} of {len(deltas)} pairs differ by ~{h:+d}h: probably a misdeclared timezone. "
        "Time-based conclusions disabled; timestamps left unchanged.",
        affected=n,
        total=len(deltas),
        offset_hours=h,
    )


def _missing_or_pending(lead: SourceLead, settings: Settings, time_is_reliable: bool) -> Status:
    """MISSING is a time claim (outside the grace period); with doubtful clocks we cannot make it."""
    if not time_is_reliable:
        return Status.TEMPORAL_UNCERTAIN
    return Status.PENDING if lead.created_at > settings.as_of - settings.grace_period else Status.MISSING


def _measure_latency(o: LeadOutcome, cands: list[DestRecord], settings: Settings, time_is_reliable: bool) -> None:
    """Arrival is an event claim: only VERIFIED, single, non-shared records with trustworthy clocks."""
    if o.evidence is Evidence.NONE:
        return  # NOT_APPLICABLE
    if o.status is not Status.VERIFIED:
        o.latency_status = LatencyStatus.NOT_VERIFIABLE
        return
    if not time_is_reliable:
        o.latency_status = LatencyStatus.TEMPORAL_UNCERTAIN
        return
    record = cands[0]
    if record.created_at < o.lead.created_at:
        o.latency_status = LatencyStatus.NOT_VERIFIABLE
        o.notes.append("preexisting record updated: no verifiable arrival time")
        return
    o.latency = record.created_at - o.lead.created_at
    o.latency_status = LatencyStatus.MEASURED
    if o.latency > settings.late_after:
        o.issues.append(Issue(IssueKind.LATE, Evidence.VERIFIED))


def _lost(lead: SourceLead, record: DestRecord, field: str) -> bool:
    if field == "email":
        return record.email is None and not record.additional_emails
    if field == "phone":
        return record.phone_raw is None
    return getattr(record, field) is None


def _systemic_findings(comparable: list[tuple[LeadOutcome, DestRecord]], settings: Settings) -> list[PipelineFinding]:
    """A field missing in most compared records is one pipeline finding, not N record issues."""
    findings = []
    for field in _ATTRIBUTION_FIELDS + _IDENTITY_FIELDS:
        relevant = [(o.lead, rec) for o, rec in comparable if getattr(o.lead, field) is not None]
        lost = sum(_lost(lead, rec, field) for lead, rec in relevant)
        if relevant and lost / len(relevant) >= settings.systemic_threshold:
            findings.append(
                PipelineFinding(
                    FindingKind.FIELD_NOT_TRANSMITTED,
                    field,
                    f"{lost} of {len(relevant)} records without {field} in destination",
                    affected=lost,
                    total=len(relevant),
                )
            )
    return findings


def _compare_record(outcome: LeadOutcome, record: DestRecord, silenced: set[str]) -> None:
    """State comparisons. With IDENTITY evidence they describe the person's record, not this event."""
    lead = outcome.lead
    basis = outcome.evidence
    outcome.field_check_basis = basis

    for field in _ATTRIBUTION_FIELDS:
        src, dst = getattr(lead, field), getattr(record, field)
        if src is None or field in silenced:
            continue
        if dst is None:
            outcome.issues.append(Issue(IssueKind.ATTRIBUTION_MISSING, basis, field, src))
        elif src != dst:
            renamed = field == "campaign_name" and lead.campaign_id is not None and lead.campaign_id == record.campaign_id
            if not renamed:
                outcome.issues.append(Issue(IssueKind.ATTRIBUTION_MISMATCH, basis, field, src, dst))

    if lead.email and "email" not in silenced and lead.email not in {record.email, *record.additional_emails}:
        outcome.issues.append(Issue(IssueKind.FIELD_MISMATCH, basis, "email", lead.email, record.email))

    if lead.phone_raw and not lead.phone:
        outcome.issues.append(Issue(IssueKind.PHONE_UNPARSEABLE, basis, "phone", source_value=lead.phone_raw))
    if record.phone_raw and not record.phone:
        outcome.issues.append(Issue(IssueKind.PHONE_UNPARSEABLE, basis, "phone", dest_value=record.phone_raw))
    elif lead.phone and "phone" not in silenced and lead.phone != record.phone:
        outcome.issues.append(Issue(IssueKind.FIELD_MISMATCH, basis, "phone", lead.phone, record.phone))
