from datetime import UTC, datetime

import pytest

from lead_integrity.load import ColumnMap, load_destination, load_source

META_MAP = ColumnMap(
    columns={
        "lead_id": "id",
        "created_at": "created_time",
        "email": "email",
        "phone": "phone_number",
        "form_id": "form_id",
        "campaign_id": "campaign_id",
        "campaign_name": "campaign_name",
        "is_organic": "is_organic",
    },
    timezone="UTC",
    default_region="US",
)

HUBSPOT_MAP = ColumnMap(
    columns={
        "record_id": "Record ID",
        "created_at": "Create Date",
        "email": "Email",
        "additional_emails": "Additional email addresses",
        "phone": "Phone Number",
        "source_lead_ids": "Meta Lead ID",
    },
    timezone="America/Chicago",
    default_region="US",
)


def write(tmp_path, text):
    path = tmp_path / "data.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_meta_export(tmp_path):
    path = write(
        tmp_path,
        "id,created_time,email,phone_number,form_id,campaign_id,campaign_name,is_organic\n"
        "l:1200000000000001,2026-10-01T09:00:00+0000, Ana@Example.com ,(202) 555-0123,f:77,c:100,Promo,false\n",
    )
    [lead] = load_source(path, META_MAP)
    assert lead.lead_id == "1200000000000001"
    assert lead.created_at == datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    assert lead.email == "ana@example.com"
    assert lead.phone == "+12025550123"
    assert lead.phone_raw == "(202) 555-0123"
    assert lead.form_id == "77"
    assert lead.campaign_id == "100"
    assert lead.is_organic is False


def test_unmapped_optional_field_is_none(tmp_path):
    cmap = ColumnMap(
        columns={"lead_id": "id", "created_at": "created_time", "email": "email"},
        timezone="UTC",
        default_region="US",
    )
    path = write(tmp_path, "id,created_time,email\n1,2026-10-01T09:00:00+0000,a@example.com\n")
    [lead] = load_source(path, cmap)
    assert lead.campaign_id is None
    assert lead.phone is None


def test_mapped_column_absent_from_file_fails_loudly(tmp_path):
    path = write(tmp_path, "id,created_time\n1,2026-10-01T09:00:00+0000\n")
    with pytest.raises(ValueError, match="email"):
        load_source(path, META_MAP)


def test_lead_id_in_scientific_notation_fails_with_row_number(tmp_path):
    path = write(
        tmp_path,
        "id,created_time,email,phone_number,form_id,campaign_id,campaign_name,is_organic\n"
        "1.2E+16,2026-10-01T09:00:00+0000,a@example.com,,,,,false\n",
    )
    with pytest.raises(ValueError, match="row 2"):
        load_source(path, META_MAP)


def test_load_hubspot_export(tmp_path):
    path = write(
        tmp_path,
        "Record ID,Create Date,Email,Additional email addresses,Phone Number,Meta Lead ID\n"
        "501,2026-10-01 09:05:00,ana@example.com,ana.old@example.org;ANA@example.net,+12025550123,l:111;l:222\n"
        "502,2026-10-01 09:06:00,bo@example.com,,555-0123,\n",
    )
    first, second = load_destination(path, HUBSPOT_MAP)
    assert first.record_id == "501"
    assert first.created_at == datetime(2026, 10, 1, 14, 5, tzinfo=UTC)
    assert first.additional_emails == ("ana.old@example.org", "ana@example.net")
    assert first.source_lead_ids == ("111", "222")
    assert second.source_lead_ids == ()
    assert (second.phone, second.phone_raw) == (None, "555-0123")


def test_naive_timestamps_require_declared_timezone(tmp_path):
    cmap = ColumnMap(columns={"lead_id": "id", "created_at": "created_time"}, timezone=None, default_region=None)
    path = write(tmp_path, "id,created_time\n1,2026-10-01 09:00:00\n")
    with pytest.raises(ValueError, match="row 2.*declares no timezone"):
        load_source(path, cmap)
