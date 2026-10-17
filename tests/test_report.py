"""Report outputs. Pins the wording rules, not the layout."""

import csv
import json

import pytest
from generate_examples import generate
from helpers import identity_record_for, make_lead, make_settings

from lead_integrity.config import execute, load_run
from lead_integrity.reconcile import reconcile
from lead_integrity.report import SURE_VERIFIED, build_items, write_outputs


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    demo = tmp_path_factory.mktemp("demo")
    generate(demo)
    out = {}
    for name in ("a-lead-id-preserved", "b-lead-id-not-preserved", "d-timezone-uncertain", "h-campaign-id-never-sent"):
        run = load_run(demo / name / "run.toml")
        result = execute(run)
        target = tmp_path_factory.mktemp(name)
        write_outputs(result, run.settings, target, run.title, run.description)
        out[name] = (run, result, target)
    return out


def html(reports, name):
    return (reports[name][2] / "report.html").read_text(encoding="utf-8")


def summary(reports, name):
    return json.loads((reports[name][2] / "summary.json").read_text(encoding="utf-8"))


def test_identity_evidence_is_never_worded_as_verified(reports):
    run, result, _ = reports["b-lead-id-not-preserved"]
    assert not any(i.sure.startswith(SURE_VERIFIED) for i in build_items(result, run.settings))


def test_b_states_zero_verified_and_why(reports):
    page = html(reports, "b-lead-id-not-preserved")
    assert "0 of 31 lead events could be deterministically verified" in page
    assert "not verifiable" in page
    assert summary(reports, "b-lead-id-not-preserved")["events_verified"] == {"count": 0, "of": 31}
    assert summary(reports, "b-lead-id-not-preserved")["latency"]["late_evaluated"] is False


def test_a_reports_measured_latency(reports):
    s = summary(reports, "a-lead-id-preserved")
    assert s["events_verified"] == {"count": 27, "of": 31}
    assert s["latency"]["late"] == 1 and s["latency"]["late_evaluated"] is True


def test_d_timezone_banner_says_not_evaluated(reports):
    page = html(reports, "d-timezone-uncertain")
    assert "Time-based checks disabled" in page
    assert "not evaluated, because time-based checks are disabled" in page
    assert "; 0 late (" not in page  # the count phrase; the banner itself quotes “0 late” to deny it


def test_h_systemic_gap_is_one_banner_and_one_row(reports):
    *_, target = reports["h-campaign-id-never-sent"]
    assert "The pipeline does not transmit the campaign ID" in html(reports, "h-campaign-id-never-sent")
    with (target / "exceptions.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["group"] for r in rows] == ["data"]


def test_pipeline_findings_come_before_summary_and_exceptions(reports):
    page = html(reports, "b-lead-id-not-preserved")
    assert page.index("1. Pipeline findings") < page.index("2. Reconciliation summary") < page.index("3. Exceptions")
    assert page.index("The CRM does not store the original lead ID") < page.index("2. Reconciliation summary")


def test_main_view_has_no_developer_codes(reports):
    for name in reports:
        page = html(reports, name)
        main_view = page[: page.index("<details>")]
        for code in ("IDENTITY_MATCH", "CLAIMED_BY_OTHER_LEAD", "confidence", "source_lead_id", "LOW"):
            assert code not in main_view, (name, code)


def test_unscoped_destination_reason_has_business_wording():
    lead = make_lead(0)
    settings = make_settings(destination_scope_is_pipeline_specific=False)
    result = reconcile([lead], [identity_record_for(lead)], settings)
    [item] = [i for i in build_items(result, settings) if i.group == "ambiguous"]
    assert "not limited to this pipeline" in item.what
    assert not item.sure.startswith(SURE_VERIFIED)
