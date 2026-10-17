"""Business-readable outputs: report.html, exceptions.csv, summary.json.

Wording rule (docs/reconciliation-model.md): never phrase IDENTITY evidence as if the event were verified.
Each exception answers: what happened, why we say so, how sure we are.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from lead_integrity.models import (
    AmbiguityReason,
    Evidence,
    FindingKind,
    Issue,
    IssueKind,
    LatencyStatus,
    LeadOutcome,
    MatchMethod,
    Result,
    Settings,
    Status,
)
from lead_integrity.reconcile import NOTE_EXCLUDED_VERIFIED

FIELD_LABEL = {"campaign_id": "campaign ID", "campaign_name": "campaign name", "email": "email", "phone": "phone"}
METHOD_WORD = {MatchMethod.EMAIL: "email", MatchMethod.PHONE: "phone number"}
EVIDENCE_TEXT = {
    MatchMethod.LEAD_ID: "The CRM record stores this lead's original ID",
    MatchMethod.EMAIL: "Same email (person's identity)",
    MatchMethod.PHONE: "Same phone number (person's identity; weaker than email)",
    None: "No match by lead ID, email or phone",
}
SURE_VERIFIED = "Event verified"
SURE_IDENTITY = "Event not verified"

# (key, title, tone, actionable)
GROUPS = [
    ("missing", "Missing", "critical", True),
    ("ambiguous", "Ambiguous: person found, lead cannot be attributed", "warning", True),
    ("duplicated", "Duplicated", "serious", True),
    ("data", "Data problems in records that were found", "serious", True),
    ("undecidable", "Undecidable: timezone uncertainty", "undecided", False),
    ("pending", "Pending: still inside the grace period", "pending", False),
    ("phone", "Found by phone number only", "identity", False),
    ("info", "Informational", "neutral", False),
]


@dataclass(frozen=True)
class Item:
    group: str
    status: str
    lead_id: str
    who: str
    contact: str
    when: str
    what: str
    why: str
    sure: str
    records: str
    codes: str


# --- Formatting --------------------------------------------------------------


def _when(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _dur(d: timedelta) -> str:
    minutes = int(abs(d).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 48 * 60:
        h, m = divmod(minutes, 60)
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{minutes // (24 * 60)} days"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# --- Exception items -----------------------------------------------------------


def _issue_text(issue: Issue, outcome: LeadOutcome, settings: Settings) -> str:
    subject = "The CRM record" if issue.evidence is Evidence.VERIFIED else "The person's CRM record"
    label = FIELD_LABEL.get(issue.field or "", issue.field or "")
    src, dst = issue.source_value, issue.dest_value
    match issue.kind:
        case IssueKind.LATE:
            return (f"Arrived {_dur(outcome.latency)} after the lead was created "
                    f"(demo threshold: {_dur(settings.late_after)}).")
        case IssueKind.ATTRIBUTION_MISSING:
            return f"{subject} has no {label} (source: {src})."
        case IssueKind.ATTRIBUTION_MISMATCH | IssueKind.FIELD_MISMATCH if dst is not None:
            return f"{subject} has {label} “{dst}”, but the source says “{src}”."
        case IssueKind.FIELD_MISMATCH:
            return f"{subject} has no {label} (source: {src})."
        case IssueKind.PHONE_UNPARSEABLE if dst is not None:
            return f"{subject} has a phone value that is not a valid number (“{dst}”)."
        case IssueKind.PHONE_UNPARSEABLE:
            return f"The source phone is not a valid number (“{src}”), so it could not be used to search or compare."
    return issue.kind.value


def _ambiguity_text(o: LeadOutcome, result: Result, sharers: dict[str, int]) -> str:
    method = METHOD_WORD.get(o.matches[0].method, "identifier")
    record = result.records[o.matches[0].record_id]
    parts = []
    for reason in o.reasons:
        match reason:
            case AmbiguityReason.CLAIMED_BY_OTHER_LEAD:
                parts.append(
                    f"A CRM contact with the same {method} exists, but it is already linked to a different source "
                    "lead. There is no proof that this lead arrived."
                )
            case AmbiguityReason.SHARED_DESTINATION:
                others = sharers[record.record_id] - 1
                parts.append(
                    f"This lead and {_plural(others, 'other lead', 'other leads')} point to the same CRM contact. "
                    "The person exists; we cannot tell whether every lead was processed."
                )
            case AmbiguityReason.PREEXISTING_RECORD:
                parts.append(
                    f"The contact already existed since {record.created_at:%Y-%m-%d}, before this lead. The person "
                    "existing does not prove that this new lead was processed."
                )
            case AmbiguityReason.MULTIPLE_CANDIDATES:
                parts.append(
                    f"{len(o.matches)} contacts share the same {method}; we cannot tell which one belongs to this lead."
                )
    return " ".join(parts)


def build_items(result: Result, settings: Settings) -> list[Item]:
    sharers: dict[str, int] = {}
    for o in result.outcomes:
        if o.status in (Status.AMBIGUOUS, Status.IDENTITY_MATCH):
            for m in o.matches:
                sharers[m.record_id] = sharers.get(m.record_id, 0) + 1

    items: list[Item] = []
    for o in result.outcomes:
        lead = o.lead
        method = o.matches[0].method if o.matches else None
        why = EVIDENCE_TEXT[method]

        def add(group: str, what: str, sure: str, why: str = why, codes: str = "",
                o: LeadOutcome = o, lead=lead) -> None:
            items.append(
                Item(
                    group=group,
                    status=o.status.value,
                    lead_id=lead.lead_id,
                    who=lead.full_name or "(no name)",
                    contact=lead.email or lead.phone_raw or "",
                    when=_when(lead.created_at),
                    what=what,
                    why=why,
                    sure=sure,
                    records=";".join(m.record_id for m in o.matches),
                    codes=codes or ";".join([o.status.value, *(r.value for r in o.reasons)]),
                )
            )

        match o.status:
            case Status.MISSING:
                extra = " Its phone is not a valid number, so only the lead ID and email could be searched." if o.notes else ""
                add("missing", "This lead was not found in the CRM." + extra,
                    f"High: more than {_dur(settings.grace_period)} have passed since it was created (demo grace period).")
            case Status.PENDING:
                ago = _dur(settings.as_of - lead.created_at)
                add("pending", f"Not in the CRM yet. It was created {ago} before the export; the grace period is "
                    f"{_dur(settings.grace_period)}.", "No conclusion yet.")
            case Status.TEMPORAL_UNCERTAIN:
                add("undecidable", "Not in the CRM. We cannot tell whether it is missing or simply not due yet: "
                    "source and destination timestamps are not comparable.",
                    "No conclusion: timezone uncertain.")
            case Status.DUPLICATED:
                add("duplicated", f"This lead created {len(o.matches)} records in the CRM.",
                    f"{SURE_VERIFIED}: every record stores its ID.")
            case Status.AMBIGUOUS:
                add("ambiguous", _ambiguity_text(o, result, sharers), f"{SURE_IDENTITY}.")
            case Status.MERGED:
                add("info", "This lead shares a contact with another lead; the CRM stores both lead IDs.",
                    f"{SURE_VERIFIED}.")
            case Status.EXCLUDED if o.notes:
                verified = o.notes == [NOTE_EXCLUDED_VERIFIED]
                add("info", "Lead excluded by configuration (for example, a test lead) that still appears in the CRM. "
                    "Not counted as an error.", f"{SURE_VERIFIED}." if verified else f"{SURE_IDENTITY}.")
            case Status.IDENTITY_MATCH if method is MatchMethod.PHONE:
                add("phone", "Lead found by phone number, but the original event cannot be verified.",
                    f"{SURE_IDENTITY}.")

        for issue in o.issues:
            sure = f"{SURE_VERIFIED}." if issue.evidence is Evidence.VERIFIED else (
                f"{SURE_IDENTITY}: this describes the person's record, not proof that this lead arrived.")
            add("data", _issue_text(issue, o, settings), sure, codes=f"{issue.kind.value};{issue.field or ''}")

    for record in result.orphans or []:
        items.append(
            Item(
                group="info", status="ORPHAN", lead_id="", who=record.full_name or "(CRM contact)",
                contact=record.email or record.phone_raw or "", when=_when(record.created_at),
                what="CRM contact with no matching source lead.",
                why="Does not match any source lead by lead ID, email or phone.",
                sure="Meaningful only because the CRM export is scoped to this pipeline.",
                records=record.record_id, codes="ORPHAN",
            )
        )
    return items


# --- Summary -------------------------------------------------------------------


def build_summary(result: Result, settings: Settings, title: str) -> dict:
    counts = result.counts()
    evaluated = [o for o in result.outcomes if o.status is not Status.EXCLUDED]
    verified = sum(counts[s] for s in (Status.VERIFIED, Status.MERGED, Status.DUPLICATED))
    measured = [o.latency for o in result.outcomes if o.latency_status is LatencyStatus.MEASURED]
    return {
        "scenario": title,
        "as_of": settings.as_of.isoformat(),
        "settings_demo_values": {
            "late_after_minutes": settings.late_after.total_seconds() / 60,
            "grace_period_minutes": settings.grace_period.total_seconds() / 60,
            "systemic_threshold": settings.systemic_threshold,
            "destination_scope_is_pipeline_specific": settings.destination_scope_is_pipeline_specific,
        },
        "leads": {"total": len(result.outcomes), "evaluated": len(evaluated), "excluded": counts[Status.EXCLUDED]},
        "events_verified": {"count": verified, "of": len(evaluated)},
        "by_status": {s.value: counts[s] for s in Status if counts[s]},
        "by_evidence": {e.value: sum(o.evidence is e for o in evaluated) for e in Evidence},
        "latency": {
            "by_status": {s.value: sum(o.latency_status is s for o in evaluated) for s in LatencyStatus},
            "median_minutes": round(statistics.median(measured).total_seconds() / 60, 1) if measured else None,
            "max_minutes": round(max(measured).total_seconds() / 60, 1) if measured else None,
            "late": sum(IssueKind.LATE in o.issue_kinds for o in result.outcomes),
            "late_evaluated": bool(measured),
        },
        "findings": [
            {"kind": f.kind.value, "field": f.field, "detail": f.detail, "affected": f.affected, "total": f.total,
             "offset_hours": f.offset_hours}
            for f in result.findings
        ],
        "orphans": None if result.orphans is None else len(result.orphans),
    }


# --- HTML ------------------------------------------------------------------------

CSS = """
:root{--ink:#1d1d1b;--ink2:#4a4a46;--muted:#6f6f69;--line:#e3e2dc;--bg:#f7f6f2;--card:#fff;
--good:#0a7f0a;--good-bg:#e7f4e7;--identity:#3b5b8c;--identity-bg:#e8eef7;--warning:#a86a00;--warning-bg:#fdf1d8;
--warning-mark:#fab219;--critical:#b42f2f;--critical-bg:#fbe7e7;--serious:#b8562f;--serious-bg:#fcebe3;
--pending:#77776f;--pending-bg:#efeee9;--undecided:#5d4a8c;--undecided-bg:#eeeaf6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:34px 0 12px}h3{font-size:15px;margin:22px 0 8px}
.sub{color:var(--ink2);margin:0 0 6px;max-width:820px}.meta{color:var(--muted);font-size:13px}
.banner{border-radius:10px;padding:14px 16px;margin:10px 0;border-left:6px solid}
.banner b{display:block;font-size:15px;margin-bottom:2px}.banner p{margin:0;color:var(--ink2)}
.banner small{display:block;color:var(--muted);margin-top:4px}
.b-undecided{background:var(--undecided-bg);border-color:var(--undecided)}
.b-identity{background:var(--identity-bg);border-color:var(--identity)}
.b-serious{background:var(--serious-bg);border-color:var(--serious)}
.none{color:var(--muted);font-size:14px}
.hero{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px}
.hero .big{font-size:40px;font-weight:700;line-height:1.1}.hero .big span{font-size:22px;color:var(--muted);font-weight:600}
.hero .lead{font-size:16px;margin:4px 0 14px}
.bar{display:flex;gap:2px;height:22px;border-radius:6px;overflow:hidden;background:var(--card)}
.bar div{min-width:4px}
.blocks{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:16px}
.block{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.block h4{margin:0 0 8px;font-size:13px;color:var(--ink2);font-weight:600}
.tiles{display:flex;gap:8px}.tile{flex:1;border-radius:8px;padding:8px 10px}
.tile .n{font-size:26px;font-weight:700;line-height:1.1}.tile .l{font-size:12.5px;color:var(--ink2);white-space:nowrap}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:baseline}
.t-good{background:var(--good-bg)}.t-identity{background:var(--identity-bg)}.t-warning{background:var(--warning-bg);outline:2px solid var(--warning-mark)}
.t-critical{background:var(--critical-bg)}.t-pending{background:var(--pending-bg);border:1px dashed var(--pending)}
.t-undecided{background:var(--undecided-bg)}
.s-good{background:var(--good)}.s-identity{background:var(--identity)}.s-warning{background:var(--warning-mark)}
.s-critical{background:var(--critical)}.s-pending{background:#c9c8c0}
.s-undecided{background:repeating-linear-gradient(45deg,var(--undecided) 0 3px,#b9aedb 3px 6px)}
.facts{margin:14px 0 0;padding:0;list-style:none;color:var(--ink2);font-size:14px}.facts li{margin:3px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}
th{text-align:left;font-size:12px;color:var(--muted);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--line);background:#fbfaf7}
td{padding:9px 10px;border-top:1px solid var(--line);vertical-align:top}
td.who b,td.who span{display:block}td.who span{color:var(--muted);font-size:12.5px}
td.sure{font-size:13px;color:var(--ink2);width:22%}td.why{font-size:13px;color:var(--ink2);width:22%}
.tag{display:inline-block;font-size:12px;font-weight:600;padding:1px 8px;border-radius:999px;margin-left:6px;vertical-align:1px}
.g-critical{background:var(--critical-bg);color:var(--critical)}.g-warning{background:var(--warning-bg);color:var(--warning)}
.g-serious{background:var(--serious-bg);color:var(--serious)}.g-pending{background:var(--pending-bg);color:var(--pending)}
.g-undecided{background:var(--undecided-bg);color:var(--undecided)}.g-identity{background:var(--identity-bg);color:var(--identity)}
.g-neutral{background:var(--pending-bg);color:var(--ink2)}
details{margin-top:34px}summary{cursor:pointer;font-weight:600;color:var(--ink2)}
.tech td,.tech th{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
@media (max-width:820px){.blocks{grid-template-columns:1fr 1fr}}
@media (max-width:520px){.blocks{grid-template-columns:1fr}td.sure,td.why{width:auto}}
@media print{body{background:#fff}details{display:none}}
"""

ICON = {"good": "✓", "identity": "●", "warning": "⚠", "critical": "✕", "pending": "◷", "undecided": "?",
        "serious": "!", "neutral": "i"}


def _findings_html(result: Result, evaluated: int) -> str:
    banners = []
    for f in result.findings:
        if f.kind is FindingKind.TIMEZONE_UNCERTAIN:
            banners.append(
                '<div class="banner b-undecided"><b>? Time-based checks disabled</b>'
                "<p>Source and destination timestamps appear to use inconsistent timezones "
                f"(most pairs differ by about {abs(f.offset_hours)} hours). Conclusions that depend on time were not "
                "evaluated: missing, late, and preexisting-contact checks. This does not mean “0 late”; it means "
                "“not evaluated”. Timestamps were not corrected.</p>"
                f"<small>{f.affected} of {f.total} comparable pairs show the same offset.</small></div>"
            )
        elif f.kind is FindingKind.LEAD_ID_NOT_PRESERVED:
            banners.append(
                '<div class="banner b-identity"><b>● The CRM does not store the original lead ID</b>'
                f"<p>0 of {evaluated} lead events could be deterministically verified. People can be found by email "
                "or phone, but we cannot prove that each lead arrived, or measure how long it took.</p></div>"
            )
        elif f.kind is FindingKind.FIELD_NOT_TRANSMITTED:
            label = FIELD_LABEL.get(f.field, f.field)
            banners.append(
                f'<div class="banner b-serious"><b>! The pipeline does not transmit the {escape(label)}</b>'
                f"<p>{f.affected} of {f.total} matched records arrive without a {escape(label)}. Reported once "
                "instead of as one exception per record.</p></div>"
            )
    return "".join(banners) or '<p class="none">No pipeline-level findings.</p>'


def _summary_html(result: Result, settings: Settings) -> str:
    c = result.counts()
    evaluated = len(result.outcomes) - c[Status.EXCLUDED]
    verified = c[Status.VERIFIED] + c[Status.MERGED] + c[Status.DUPLICATED]
    identity, ambiguous = c[Status.IDENTITY_MATCH], c[Status.AMBIGUOUS]
    missing, pending, undecided = c[Status.MISSING], c[Status.PENDING], c[Status.TEMPORAL_UNCERTAIN]

    if any(f.kind is FindingKind.LEAD_ID_NOT_PRESERVED for f in result.findings):
        lead = "No lead event could be verified, because the CRM does not store the original lead ID."
    else:
        lead = "lead events verified in the CRM by their original lead ID."
    found = identity + ambiguous
    line2 = []
    if found:
        line2.append(f"{_plural(found, 'person', 'people')} found by email or phone, without proof of the event")
    if missing:
        line2.append(f"{missing} missing")
    if pending:
        line2.append(f"{pending} pending inside the grace period")
    if undecided:
        line2.append(_plural(undecided, "lead undecidable", "leads undecidable"))

    segments = [("good", verified, "Event verified"), ("identity", identity, "Person found, no conflict"),
                ("warning", ambiguous, "Ambiguous"), ("critical", missing, "Missing"),
                ("pending", pending, "Pending"), ("undecided", undecided, "Undecidable")]
    bar = "".join(
        f'<div class="s-{tone}" style="flex:{n}" title="{escape(label)}: {n}"></div>' for tone, n, label in segments if n
    )

    def tile(tone: str, n: int, label: str) -> str:
        return (f'<div class="tile t-{tone}"><div class="n">{n}</div>'
                f'<div class="l"><span class="sw s-{tone}"></span>{ICON[tone]} {escape(label)}</div></div>')

    extra_verified = []
    if c[Status.DUPLICATED]:
        extra_verified.append(f"{c[Status.DUPLICATED]} duplicated")
    if c[Status.MERGED]:
        extra_verified.append(f"{c[Status.MERGED]} sharing a contact")
    verified_label = "Verified" + (f" (incl. {', '.join(extra_verified)})" if extra_verified else "")

    blocks = (
        f'<div class="block"><h4>Event verified</h4><div class="tiles">{tile("good", verified, verified_label)}</div></div>'
        f'<div class="block"><h4>Person found, event not proven</h4><div class="tiles">'
        f'{tile("identity", identity, "No conflict")}{tile("warning", ambiguous, "Ambiguous")}</div></div>'
        f'<div class="block"><h4>Not found</h4><div class="tiles">'
        f'{tile("critical", missing, "Missing")}{tile("pending", pending, "Pending")}</div></div>'
        f'<div class="block"><h4>Undecidable</h4><div class="tiles">{tile("undecided", undecided, "Timezone uncertain")}</div></div>'
    )

    facts = []
    measured = [o.latency for o in result.outcomes if o.latency_status is LatencyStatus.MEASURED]
    late = sum(IssueKind.LATE in o.issue_kinds for o in result.outcomes)
    if measured:
        facts.append(f"<b>Arrival delay</b> (verified events only): median {_dur(statistics.median(measured))}, "
                     f"max {_dur(max(measured))}; {late} late (over {_dur(settings.late_after)}, demo threshold).")
    elif any(f.kind is FindingKind.TIMEZONE_UNCERTAIN for f in result.findings):
        facts.append("<b>Arrival delay:</b> not evaluated, because time-based checks are disabled.")
    else:
        facts.append("<b>Arrival delay:</b> not verifiable, because no event has its lead ID in the CRM.")
    excluded = [o for o in result.outcomes if o.status is Status.EXCLUDED]
    if excluded:
        present = sum(bool(o.notes) for o in excluded)
        facts.append(f"<b>Excluded by configuration:</b> {len(excluded)} (not counted in the total)"
                     + (f"; {present} still appear(s) in the CRM." if present else "."))
    if result.orphans is None:
        facts.append("<b>CRM contacts with no source lead:</b> not evaluated, because the CRM export is not scoped "
                     "to this pipeline.")
    else:
        facts.append(f"<b>CRM contacts with no source lead:</b> {len(result.orphans)}.")

    return (
        f'<div class="hero"><div class="big">{verified} <span>of {evaluated}</span></div>'
        f'<div class="lead">{escape(lead)}</div><div class="bar" role="img" aria-label="Lead distribution">{bar}</div>'
        + (f'<p class="sub" style="margin-top:10px">{escape("; ".join(line2))}.</p>' if line2 else "")
        + f'</div><div class="blocks">{blocks}</div><ul class="facts">{"".join(f"<li>{x}</li>" for x in facts)}</ul>'
    )


def _items_html(items: list[Item], actionable: bool) -> str:
    out = []
    for key, title, tone, is_actionable in GROUPS:
        group = [i for i in items if i.group == key]
        if is_actionable != actionable or not group:
            continue
        rows = "".join(
            f'<tr><td class="who"><b>{escape(i.who)}</b><span>{escape(i.contact)}</span><span>{escape(i.when)}</span></td>'
            f"<td>{escape(i.what)}</td><td class=\"why\">{escape(i.why)}</td><td class=\"sure\">{escape(i.sure)}</td></tr>"
            for i in group
        )
        out.append(
            f'<h3>{escape(title)}<span class="tag g-{tone}">{ICON[tone]} {len(group)}</span></h3>'
            "<table><thead><tr><th>Lead</th><th>What happened</th><th>Why we say so</th><th>How sure</th></tr>"
            f"</thead><tbody>{rows}</tbody></table>"
        )
    if not out:
        return '<p class="none">Nothing in this section.</p>'
    return "".join(out)


def _tech_html(result: Result) -> str:
    rows = []
    for o in result.outcomes:
        rows.append(
            "<tr>" + "".join(f"<td>{escape(str(x))}</td>" for x in (
                o.lead.lead_id, o.status.value, o.evidence.value,
                ",".join(f"{m.method.value}/{m.confidence.value}" for m in o.matches) or "-",
                ",".join(m.record_id for m in o.matches) or "-",
                ",".join(r.value for r in o.reasons) or "-",
                o.latency_status.value,
                ",".join(f"{i.kind.value}({i.evidence.value})" for i in o.issues) or "-",
            )) + "</tr>"
        )
    head = "".join(f"<th>{h}</th>" for h in ("lead_id", "status", "evidence", "match", "records", "reasons", "latency", "issues"))
    findings = "".join(f"<li>{escape(f.kind.value)}: {escape(f.detail)}</li>" for f in result.findings)
    return (f"<details><summary>Technical detail</summary><ul class=\"tech\">{findings}</ul>"
            f'<table class="tech"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></details>')


def render_html(result: Result, settings: Settings, title: str, description: str) -> str:
    items = build_items(result, settings)
    evaluated = sum(o.status is not Status.EXCLUDED for o in result.outcomes)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lead integrity: {escape(title)}</title>
<style>{CSS}</style></head><body><main>
<h1>Lead integrity: {escape(title)}</h1>
<p class="sub">{escape(description)}</p>
<p class="meta">CRM export taken at {_when(settings.as_of)} · {len(result.outcomes)} source leads · Synthetic demo data</p>
<h2>1. Pipeline findings</h2>{_findings_html(result, evaluated)}
<h2>2. Reconciliation summary</h2>{_summary_html(result, settings)}
<h2>3. Exceptions to review</h2>{_items_html(items, actionable=True)}
<h2>4. Inconclusive and informational</h2>{_items_html(items, actionable=False)}
{_tech_html(result)}
</main></body></html>"""


def write_outputs(result: Result, settings: Settings, out_dir: Path, title: str, description: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.html").write_text(render_html(result, settings, title, description), encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(build_summary(result, settings, title), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (out_dir / "exceptions.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["group", "status", "lead_id", "name", "contact", "created", "what_happened", "evidence",
                         "confidence", "record_ids", "codes"])
        for i in build_items(result, settings):
            writer.writerow([i.group, i.status, i.lead_id, i.who, i.contact, i.when, i.what, i.why, i.sure,
                             i.records, i.codes])
