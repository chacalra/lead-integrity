"""Canonical records, settings and reconciliation outcomes.

All values here are already normalized: timestamps are UTC-aware, emails are
lowercased, phones are E.164 or None (raw kept apart), IDs have no export prefixes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


@dataclass(frozen=True)
class SourceLead:
    """One row of a Meta form export: the source of truth."""

    lead_id: str
    created_at: datetime
    email: str | None = None
    phone: str | None = None  # E.164, only if phonenumbers says it is valid
    phone_raw: str | None = None
    full_name: str | None = None
    form_id: str | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None
    adset_id: str | None = None
    ad_id: str | None = None
    is_organic: bool = False


@dataclass(frozen=True)
class DestRecord:
    """One record the pipeline created or updated in the CRM."""

    record_id: str
    created_at: datetime
    email: str | None = None
    additional_emails: tuple[str, ...] = ()
    phone: str | None = None
    phone_raw: str | None = None
    full_name: str | None = None
    source_lead_ids: tuple[str, ...] = ()  # Meta lead IDs the CRM kept: the only deterministic evidence
    campaign_id: str | None = None
    campaign_name: str | None = None


@dataclass(frozen=True)
class TimezoneCheck:
    """Experimental heuristic, not a domain rule. It only switches time-based rules off."""

    min_pairs: int  # fewer trustworthy pairs than this: no diagnosis
    min_share: float  # share of pairs that must sit near the same non-zero whole hour
    tolerance: timedelta  # how near to the whole hour


@dataclass(frozen=True)
class Settings:
    """Run parameters. No defaults on purpose: thresholds and scope are per pipeline.

    See examples/base-config.toml for the demo values and why they are not universal.
    """

    as_of: datetime  # when the destination export was taken (UTC)
    late_after: timedelta
    grace_period: timedelta
    systemic_threshold: float  # share of compared leads missing a field to call it systemic
    # Only then are unmatched records meaningful orphans and identity matches attributable to the pipeline.
    destination_scope_is_pipeline_specific: bool
    timezone_check: TimezoneCheck
    excluded_form_ids: frozenset[str] = frozenset()
    excluded_lead_ids: frozenset[str] = frozenset()


class Evidence(StrEnum):
    VERIFIED = "VERIFIED"  # the destination kept this lead's ID
    IDENTITY = "IDENTITY"  # the person exists (email/phone); this event is not proven
    NONE = "NONE"


class Status(StrEnum):
    VERIFIED = "VERIFIED"  # deterministic match to exactly one record
    DUPLICATED = "DUPLICATED"  # deterministic match to two or more records
    MERGED = "MERGED"  # informational: verified, and the record also holds other lead IDs
    IDENTITY_MATCH = "IDENTITY_MATCH"  # one unshared candidate, no contradicting evidence
    AMBIGUOUS = "AMBIGUOUS"  # person exists, but something prevents attributing the record to this lead
    MISSING = "MISSING"
    PENDING = "PENDING"
    TEMPORAL_UNCERTAIN = "TEMPORAL_UNCERTAIN"  # not found, and timezone doubt prevents deciding MISSING vs PENDING
    EXCLUDED = "EXCLUDED"


EVIDENCE_BY_STATUS = {
    Status.VERIFIED: Evidence.VERIFIED,
    Status.DUPLICATED: Evidence.VERIFIED,
    Status.MERGED: Evidence.VERIFIED,
    Status.IDENTITY_MATCH: Evidence.IDENTITY,
    Status.AMBIGUOUS: Evidence.IDENTITY,
    Status.MISSING: Evidence.NONE,
    Status.PENDING: Evidence.NONE,
    Status.TEMPORAL_UNCERTAIN: Evidence.NONE,
    Status.EXCLUDED: Evidence.NONE,
}


class AmbiguityReason(StrEnum):
    CLAIMED_BY_OTHER_LEAD = "CLAIMED_BY_OTHER_LEAD"  # candidate record holds a different lead ID
    MULTIPLE_CANDIDATES = "MULTIPLE_CANDIDATES"  # several records share the email/phone
    SHARED_DESTINATION = "SHARED_DESTINATION"  # other source leads point to the same record
    PREEXISTING_RECORD = "PREEXISTING_RECORD"  # record created before the lead
    UNSCOPED_DESTINATION = "UNSCOPED_DESTINATION"  # CRM export not limited to the pipeline: identity is not attributable


class LatencyStatus(StrEnum):
    MEASURED = "MEASURED"  # VERIFIED, record created after the lead, clocks trustworthy
    NOT_VERIFIABLE = "NOT_VERIFIABLE"  # no deterministic evidence of this event's record creation
    TEMPORAL_UNCERTAIN = "TEMPORAL_UNCERTAIN"  # would be measurable, but timezone is in doubt
    NOT_APPLICABLE = "NOT_APPLICABLE"  # nothing arrived (or excluded)


class IssueKind(StrEnum):
    LATE = "LATE"
    ATTRIBUTION_MISSING = "ATTRIBUTION_MISSING"
    ATTRIBUTION_MISMATCH = "ATTRIBUTION_MISMATCH"
    FIELD_MISMATCH = "FIELD_MISMATCH"
    PHONE_UNPARSEABLE = "PHONE_UNPARSEABLE"


class MatchMethod(StrEnum):
    LEAD_ID = "LEAD_ID"
    EMAIL = "EMAIL"
    PHONE = "PHONE"


class Confidence(StrEnum):
    HIGH = "HIGH"  # deterministic
    LOW = "LOW"  # heuristic


class FindingKind(StrEnum):
    FIELD_NOT_TRANSMITTED = "FIELD_NOT_TRANSMITTED"
    TIMEZONE_UNCERTAIN = "TIMEZONE_UNCERTAIN"
    LEAD_ID_NOT_PRESERVED = "LEAD_ID_NOT_PRESERVED"  # no destination record keeps a Meta lead ID


@dataclass(frozen=True)
class Match:
    record_id: str
    method: MatchMethod
    confidence: Confidence


@dataclass(frozen=True)
class Issue:
    kind: IssueKind
    evidence: Evidence  # IDENTITY: describes the person's record, does not prove this event was processed
    field: str | None = None
    source_value: str | None = None
    dest_value: str | None = None


@dataclass
class LeadOutcome:
    lead: SourceLead
    status: Status
    matches: list[Match] = field(default_factory=list)
    reasons: list[AmbiguityReason] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    latency: timedelta | None = None
    latency_status: LatencyStatus = LatencyStatus.NOT_APPLICABLE
    field_check_basis: Evidence | None = None  # which record field/attribution checks ran against, if any
    notes: list[str] = field(default_factory=list)

    @property
    def evidence(self) -> Evidence:
        return EVIDENCE_BY_STATUS[self.status]

    @property
    def issue_kinds(self) -> set[IssueKind]:
        return {i.kind for i in self.issues}


@dataclass(frozen=True)
class PipelineFinding:
    kind: FindingKind
    field: str | None
    detail: str
    affected: int | None = None
    total: int | None = None
    offset_hours: int | None = None  # TIMEZONE_UNCERTAIN only: the suspected offset, never applied


@dataclass
class Result:
    outcomes: list[LeadOutcome]
    orphans: list[DestRecord] | None  # None: not evaluated (destination scope not pipeline-specific)
    findings: list[PipelineFinding]
    records: dict[str, DestRecord] = field(default_factory=dict)

    def counts(self) -> Counter[Status]:
        return Counter(o.status for o in self.outcomes)

    def outcome(self, lead_id: str) -> LeadOutcome:
        return next(o for o in self.outcomes if o.lead.lead_id == lead_id)

    @property
    def finding_kinds(self) -> list[FindingKind]:
        return [f.kind for f in self.findings]
