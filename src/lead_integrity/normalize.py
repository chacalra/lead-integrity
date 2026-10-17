"""Value normalization shared by source and destination loaders."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import phonenumbers

_SCIENTIFIC = re.compile(r"^\d+([.,]\d+)?E\+?\d+$", re.IGNORECASE)
_EXPORT_PREFIX = re.compile(r"^[a-z]{1,3}:")  # Meta CSV: l:, f:, c:, ag:... (based on public descriptions, unverified)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def normalize_email(value: str | None) -> str | None:
    value = _clean(value)
    return value.lower() if value else None


def normalize_phone(value: str | None, default_region: str | None) -> str | None:
    """E.164 if phonenumbers says the number is valid, else None. No repairs: country-specific prefixes are not rewritten."""
    value = _clean(value)
    if not value:
        return None
    try:
        number = phonenumbers.parse(value, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def normalize_id(value: str | None) -> str | None:
    value = _clean(value)
    if not value:
        return None
    if _SCIENTIFIC.match(value):
        raise ValueError(f"ID in scientific notation ({value!r}): the file was probably opened in a spreadsheet")
    return _EXPORT_PREFIX.sub("", value)


def parse_timestamp(value: str, timezone: str | None) -> datetime:
    """Explicit offsets win; naive values need a declared timezone."""
    parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        if timezone is None:
            raise ValueError(f"timestamp without timezone ({value!r}) and the dataset declares no timezone")
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed.astimezone(UTC)
