"""Builders for in-memory test data. Reconciliation tests skip CSV entirely."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from lead_integrity.models import DestRecord, Settings, SourceLead, TimezoneCheck

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
AREA_CODES = ["202", "212", "312", "415"]


def make_lead(i: int, **overrides) -> SourceLead:
    # Fictional 555-01xx numbers; the area code changes every 100 leads so phones never collide.
    phone = overrides.get("phone", f"+1{AREA_CODES[i // 100]}55501{i % 100:02d}")
    overrides.setdefault("phone_raw", phone)
    lead = SourceLead(
        lead_id=f"1200000000000{i:04d}",
        created_at=T0 + timedelta(minutes=10 * i),
        email=f"lead{i}@example.com",
        phone=phone,
        full_name=f"Lead {i}",
        form_id="form-1",
        campaign_id="c-100",
        campaign_name="October Promo",
        adset_id="as-1",
        ad_id="ad-1",
    )
    return replace(lead, **overrides)


def record_for(lead: SourceLead, *, delay: timedelta = timedelta(minutes=2), **overrides) -> DestRecord:
    """A destination record that correctly mirrors the lead, keeping its lead ID (verified)."""
    phone = overrides.get("phone", lead.phone)
    overrides.setdefault("phone_raw", phone)
    record = DestRecord(
        record_id=f"crm-{lead.lead_id}",
        created_at=lead.created_at + delay,
        email=lead.email,
        phone=phone,
        full_name=lead.full_name,
        source_lead_ids=(lead.lead_id,),
        campaign_id=lead.campaign_id,
        campaign_name=lead.campaign_name,
    )
    return replace(record, **overrides)


def identity_record_for(lead: SourceLead, **overrides) -> DestRecord:
    """Same as record_for, but the CRM did not keep the lead ID (e.g. HubSpot native sync)."""
    return record_for(lead, source_lead_ids=(), **overrides)


def healthy(n: int) -> tuple[list[SourceLead], list[DestRecord]]:
    leads = [make_lead(i) for i in range(n)]
    return leads, [record_for(lead) for lead in leads]


def make_settings(**overrides) -> Settings:
    # Explicit test values, not the demo config.
    settings = Settings(
        as_of=T0 + timedelta(days=3),
        late_after=timedelta(minutes=30),
        grace_period=timedelta(hours=2),
        systemic_threshold=0.9,
        destination_scope_is_pipeline_specific=True,
        timezone_check=TimezoneCheck(min_pairs=10, min_share=0.8, tolerance=timedelta(minutes=15)),
    )
    return replace(settings, **overrides)
