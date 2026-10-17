from datetime import UTC, datetime

import pytest

from lead_integrity.normalize import (
    normalize_email,
    normalize_id,
    normalize_phone,
    parse_timestamp,
)


class TestEmail:
    def test_trims_and_lowercases(self):
        assert normalize_email("  Ana.Perez@Example.COM ") == "ana.perez@example.com"

    def test_keeps_gmail_dots_and_plus_tags(self):
        # The CRM treats these as different contacts, so we must too.
        assert normalize_email("a.b+promo@example.com") == "a.b+promo@example.com"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_is_none(self, value):
        assert normalize_email(value) is None


class TestPhone:
    # All numbers use the 555-01xx range reserved for fictional use.
    def test_local_format_uses_default_region(self):
        assert normalize_phone("(202) 555-0123", "US") == "+12025550123"

    def test_already_international(self):
        assert normalize_phone("+1 202 555 0123", "US") == "+12025550123"

    @pytest.mark.parametrize("value", ["202-555-0123", "202.555.0123", "+1-202-555-0123", "2025550123"])
    def test_written_variants_normalize_to_the_same_number(self, value):
        assert normalize_phone(value, "US") == "+12025550123"

    @pytest.mark.parametrize("value", [None, "", "abc", "555-0123"])
    def test_unusable_is_none(self, value):
        assert normalize_phone(value, "US") is None

    def test_without_default_region_only_international_numbers_parse(self):
        assert normalize_phone("+1 202 555 0123", None) == "+12025550123"
        assert normalize_phone("(202) 555-0123", None) is None


class TestId:
    def test_strips_meta_export_prefix(self):
        assert normalize_id("l:1234567890123456") == "1234567890123456"

    def test_strips_whitespace(self):
        assert normalize_id(" 1234567890123456 ") == "1234567890123456"

    @pytest.mark.parametrize("value", ["1.23457E+16", "1,23457E+16", "1.2E16"])
    def test_scientific_notation_is_rejected(self, value):
        # Excel destroys 17-digit IDs. Guessing them back would create false matches.
        with pytest.raises(ValueError, match="scientific notation"):
            normalize_id(value)

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_is_none(self, value):
        assert normalize_id(value) is None


class TestTimestamp:
    def test_meta_api_offset_format(self):
        assert parse_timestamp("2026-10-01T09:00:00+0000", "UTC") == datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

    def test_explicit_offset_wins_over_declared_timezone(self):
        got = parse_timestamp("2026-10-01T09:00:00-03:00", "Europe/Madrid")
        assert got == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    def test_naive_timestamp_without_declared_timezone_fails(self):
        with pytest.raises(ValueError, match="declares no timezone"):
            parse_timestamp("2026-10-01 09:00:00", None)

    def test_naive_timestamp_uses_declared_timezone(self):
        got = parse_timestamp("2026-10-01 09:00:00", "America/Chicago")
        assert got == datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
