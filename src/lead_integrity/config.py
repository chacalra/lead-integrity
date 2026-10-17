"""Run configuration from TOML. Every threshold and scope flag is explicit; nothing defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from lead_integrity.load import ColumnMap, load_destination, load_source
from lead_integrity.models import Result, Settings, TimezoneCheck
from lead_integrity.normalize import parse_timestamp
from lead_integrity.reconcile import reconcile


@dataclass(frozen=True)
class Run:
    source_path: Path
    destination_path: Path
    source_map: ColumnMap
    destination_map: ColumnMap
    settings: Settings
    title: str
    description: str


def load_run(path: Path) -> Run:
    cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    s, tz, ex = cfg["settings"], cfg["timezone_check"], cfg.get("exclusions", {})
    settings = Settings(
        as_of=parse_timestamp(s["as_of"], None),
        late_after=timedelta(minutes=s["late_after_minutes"]),
        grace_period=timedelta(minutes=s["grace_period_minutes"]),
        systemic_threshold=s["systemic_threshold"],
        destination_scope_is_pipeline_specific=s["destination_scope_is_pipeline_specific"],
        timezone_check=TimezoneCheck(
            min_pairs=tz["min_pairs"], min_share=tz["min_share"], tolerance=timedelta(minutes=tz["tolerance_minutes"])
        ),
        excluded_form_ids=frozenset(ex.get("form_ids", [])),
        excluded_lead_ids=frozenset(ex.get("lead_ids", [])),
    )
    return Run(
        source_path=path.parent / cfg["files"]["source"],
        destination_path=path.parent / cfg["files"]["destination"],
        source_map=_column_map(cfg["source"]),
        destination_map=_column_map(cfg["destination"]),
        settings=settings,
        title=cfg.get("report", {}).get("title", path.parent.name),
        description=cfg.get("report", {}).get("description", ""),
    )


def _column_map(section: dict) -> ColumnMap:
    return ColumnMap(
        columns=dict(section["columns"]),
        timezone=section.get("timezone"),
        default_region=section.get("default_region"),
    )


def execute(run: Run) -> Result:
    leads = load_source(run.source_path, run.source_map)
    records = load_destination(run.destination_path, run.destination_map)
    return reconcile(leads, records, run.settings)
