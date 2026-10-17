"""CSV loaders. Column names differ per export, so each file gets a ColumnMap."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from lead_integrity.models import DestRecord, SourceLead
from lead_integrity.normalize import (
    normalize_email,
    normalize_id,
    normalize_phone,
    parse_timestamp,
)

_ID_FIELDS = {"lead_id", "record_id", "form_id", "campaign_id", "adset_id", "ad_id"}


@dataclass(frozen=True)
class ColumnMap:
    columns: dict[str, str]  # canonical field -> CSV header
    timezone: str | None  # IANA zone for timestamps without offset; None means the file must carry offsets
    default_region: str | None  # ISO country for phones without country code


def _convert(name: str, raw: str, cmap: ColumnMap):
    if name in _ID_FIELDS:
        return normalize_id(raw)
    if name == "created_at":
        return parse_timestamp(raw, cmap.timezone)
    if name == "email":
        return normalize_email(raw)
    if name == "additional_emails":
        return tuple(e for e in (normalize_email(p) for p in raw.split(";")) if e)
    if name == "source_lead_ids":
        return tuple(i for i in (normalize_id(p) for p in raw.split(";")) if i)
    if name == "is_organic":
        return raw.strip().lower() in {"1", "true", "yes"}
    return raw.strip() or None


def _rows(path: Path, cmap: ColumnMap) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        absent = [f"{name} ({header!r})" for name, header in cmap.columns.items() if header not in (reader.fieldnames or [])]
        if absent:
            raise ValueError(f"{path.name}: mapped columns missing from the file: {', '.join(absent)}")
        out = []
        for line, row in enumerate(reader, start=2):
            try:
                values = {name: _convert(name, row[header] or "", cmap) for name, header in cmap.columns.items() if name != "phone"}
                if "phone" in cmap.columns:
                    raw = (row[cmap.columns["phone"]] or "").strip() or None
                    values["phone_raw"] = raw
                    values["phone"] = normalize_phone(raw, cmap.default_region)
                out.append(values)
            except ValueError as e:
                raise ValueError(f"{path.name}, row {line}: {e}") from e
        return out


def load_source(path: Path, cmap: ColumnMap) -> list[SourceLead]:
    return [SourceLead(**row) for row in _rows(path, cmap)]


def load_destination(path: Path, cmap: ColumnMap) -> list[DestRecord]:
    return [DestRecord(**row) for row in _rows(path, cmap)]
