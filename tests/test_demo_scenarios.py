"""End-to-end: generated CSVs + run.toml -> load -> reconcile. Pins what each example scenario shows."""

import filecmp
from pathlib import Path

import pytest
from generate_examples import ROOT, generate

from lead_integrity.config import execute, load_run
from lead_integrity.models import (
    AmbiguityReason,
    Evidence,
    FindingKind,
    IssueKind,
    LatencyStatus,
    Status,
)
from lead_integrity.reconcile import NOTE_EXCLUDED_VERIFIED

S = Status
EXPECTED_COUNTS = {
    "a-lead-id-preserved": {S.VERIFIED: 27, S.MISSING: 3, S.PENDING: 1},
    "b-lead-id-not-preserved": {S.IDENTITY_MATCH: 25, S.AMBIGUOUS: 2, S.MISSING: 3, S.PENDING: 1},
    "c-same-person-two-leads": {S.VERIFIED: 7, S.MERGED: 2, S.AMBIGUOUS: 1},
    "d-timezone-uncertain": {S.VERIFIED: 19, S.TEMPORAL_UNCERTAIN: 2},
    "e-verified-duplicate": {S.VERIFIED: 13, S.DUPLICATED: 2},
    "f-preexisting-contact": {S.IDENTITY_MATCH: 12, S.AMBIGUOUS: 3},
    "g-excluded-lead-arrived": {S.VERIFIED: 12, S.EXCLUDED: 2},
    "h-campaign-id-never-sent": {S.VERIFIED: 25},
}


@pytest.fixture(scope="module")
def demo(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("demo")
    generate(out)
    return out


def run(demo: Path, name: str):
    return execute(load_run(demo / name / "run.toml"))


@pytest.mark.parametrize("name", EXPECTED_COUNTS)
def test_scenario_counts(demo, name):
    assert run(demo, name).counts() == EXPECTED_COUNTS[name]


def test_committed_examples_match_generator(demo):
    committed = ROOT / "examples" / "scenarios"
    for name in EXPECTED_COUNTS:
        for file in ("source.csv", "destination.csv", "run.toml"):
            assert filecmp.cmp(demo / name / file, committed / name / file, shallow=False), f"{name}/{file} is stale: rerun scripts/generate_examples.py"


def test_a_verified_pipeline_measures_latency(demo):
    result = run(demo, "a-lead-id-preserved")
    kinds = [i.kind for o in result.outcomes for i in o.issues]
    assert sorted(kinds) == sorted([IssueKind.LATE, IssueKind.ATTRIBUTION_MISSING, IssueKind.PHONE_UNPARSEABLE])
    received = [o for o in result.outcomes if o.status is S.VERIFIED]
    assert {o.latency_status for o in received} == {LatencyStatus.MEASURED}
    assert result.orphans == []


def test_b_identity_pipeline_proves_no_event(demo):
    result = run(demo, "b-lead-id-not-preserved")
    assert not any(o.evidence is Evidence.VERIFIED for o in result.outcomes)
    found = [o for o in result.outcomes if o.evidence is Evidence.IDENTITY]
    assert {o.latency_status for o in found} == {LatencyStatus.NOT_VERIFIABLE}
    assert {i.evidence for o in found for i in o.issues} == {Evidence.IDENTITY}
    assert result.finding_kinds == [FindingKind.LEAD_ID_NOT_PRESERVED, FindingKind.FIELD_NOT_TRANSMITTED]
    assert result.orphans is None


def test_c_claimed_record_does_not_prove_second_lead(demo):
    [ambiguous] = [o for o in run(demo, "c-same-person-two-leads").outcomes if o.status is S.AMBIGUOUS]
    assert ambiguous.reasons == [AmbiguityReason.CLAIMED_BY_OTHER_LEAD]


def test_d_timezone_doubt_blocks_every_time_claim(demo):
    result = run(demo, "d-timezone-uncertain")
    assert result.finding_kinds == [FindingKind.TIMEZONE_UNCERTAIN]
    assert {o.latency_status for o in result.outcomes if o.status is S.VERIFIED} == {LatencyStatus.TEMPORAL_UNCERTAIN}


def test_lead_id_finding_only_where_no_record_keeps_ids(demo):
    flagged = {n for n in EXPECTED_COUNTS if FindingKind.LEAD_ID_NOT_PRESERVED in run(demo, n).finding_kinds}
    assert flagged == {"b-lead-id-not-preserved", "f-preexisting-contact"}


def test_f_preexisting_contacts_are_ambiguous(demo):
    reasons = [o.reasons for o in run(demo, "f-preexisting-contact").outcomes if o.status is S.AMBIGUOUS]
    assert reasons == [[AmbiguityReason.PREEXISTING_RECORD]] * 3


def test_g_excluded_arrival_is_noted_and_manual_record_is_the_only_orphan(demo):
    result = run(demo, "g-excluded-lead-arrived")
    assert [n for o in result.outcomes for n in o.notes] == [NOTE_EXCLUDED_VERIFIED]
    assert [r.record_id for r in result.orphans] == ["11999"]


def test_h_systemic_gap_is_one_finding(demo):
    result = run(demo, "h-campaign-id-never-sent")
    assert [(f.kind, f.field) for f in result.findings] == [(FindingKind.FIELD_NOT_TRANSMITTED, "campaign_id")]
    [issue] = [i for o in result.outcomes for i in o.issues]
    assert (issue.kind, issue.field) == (IssueKind.ATTRIBUTION_MISMATCH, "campaign_name")
