"""Generate the synthetic example scenarios into examples/scenarios/<scenario>/.

Deterministic (fixed seeds). All data is fictional:
- emails use the reserved example.com / example.org / example.net domains (RFC 2606);
- phone numbers use the 555-0100..555-0199 range reserved for fictional use in North America.

The source export layout (l:/c:/f: ID prefixes, column order) follows public descriptions of
Meta Lead Ads CSV exports and is NOT verified against a real export.

Usage: uv run python scripts/generate_examples.py [out_dir]
"""

from __future__ import annotations

import copy
import csv
import json
import random
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from lead_integrity.normalize import normalize_phone

ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = ROOT / "examples" / "base-config.toml"
LOCAL = ZoneInfo("America/Chicago")  # the CRM's local time in these examples
AS_OF = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)  # 12:00 local: when the CRM export was taken
START = datetime(2026, 10, 1, 11, 0, tzinfo=UTC)

FIRST = ["alex", "maria", "james", "priya", "david", "sofia", "michael", "aisha", "daniel", "emma",
         "carlos", "olivia", "kevin", "grace", "lucas", "nina", "omar", "julia", "ryan", "mei"]
LAST = ["smith", "garcia", "johnson", "patel", "lee", "brown", "nguyen", "martin",
        "clark", "lopez", "walker", "kim", "hall", "young", "rivera", "scott"]
DOMAINS = ["example.com", "example.org", "example.net"]
AREA_CODES = ["202", "212", "312", "415", "617", "303", "206", "512"]
PHONE_FORMATS = ["({a}) 555-{n}", "{a}-555-{n}", "+1 {a} 555 {n}", "{a}.555.{n}"]
CAMPAIGNS = [("120210000000001", "Spring Promo"), ("120210000000002", "October Retargeting")]
FORM = ("5550001", "Quote Request Form")
TEST_FORM = ("5550999", "Test Form")

SOURCE_COLUMNS = ["id", "created_time", "ad_id", "ad_name", "adset_id", "adset_name", "campaign_id",
                  "campaign_name", "form_id", "form_name", "is_organic", "platform", "full_name", "email",
                  "phone_number"]
CRM_COLUMNS = ["Record ID", "Create Date", "First Name", "Last Name", "Email", "Additional email addresses",
               "Phone Number", "Meta Lead ID", "Campaign ID", "Campaign Name", "Lifecycle Stage", "Original Source"]


@dataclass(frozen=True)
class Lead:
    lead_id: str
    created: datetime
    first: str
    last: str
    email: str
    phone: str  # as typed in the lead form
    campaign: tuple[str, str] | None
    form: tuple[str, str] = FORM
    is_organic: bool = False


# --- Builders ------------------------------------------------------------------


def fictional_phone(i: int, rng: random.Random) -> str:
    """555-01xx with an area code chosen by index, so phones never collide within a scenario."""
    area, line = AREA_CODES[(i // 100) % len(AREA_CODES)], f"01{i % 100:02d}"
    return rng.choice(PHONE_FORMATS).format(a=area, n=line)


def make_leads(n: int, seed: str, offset: int = 0) -> list[Lead]:
    """offset keeps emails, phones and IDs unique across calls within one scenario."""
    rng = random.Random(seed)
    leads, t = [], START
    for i in range(offset, offset + n):
        t += timedelta(minutes=rng.randint(40, 170), seconds=rng.randint(0, 59))
        first, last = rng.choice(FIRST), rng.choice(LAST)
        email = f"{first}.{last}{10 + i}@{rng.choice(DOMAINS)}"
        if i % 5 == 2:
            email = email.title()  # typed with capitals: normalization must handle it
        lead_id = str(1201234560000000 + i * 7919 + rng.randint(0, 7000))
        leads.append(
            Lead(lead_id, t, first.title(), last.title(), email, fictional_phone(i, rng), rng.choice(CAMPAIGNS))
        )
    return leads


def recent_lead(seed: str, minutes_before_as_of: int) -> Lead:
    lead = make_leads(1, seed, offset=500)[0]
    return replace(lead, created=AS_OF - timedelta(minutes=minutes_before_as_of))


def source_row(lead: Lead) -> dict:
    c = lead.campaign
    return {
        "id": f"l:{lead.lead_id}",
        "created_time": lead.created.strftime("%Y-%m-%dT%H:%M:%S+0000"),
        "ad_id": f"ag:{c[0][:-1]}7" if c else "",
        "ad_name": f"{c[1]} - Video" if c else "",
        "adset_id": f"as:{c[0][:-1]}5" if c else "",
        "adset_name": f"{c[1]} - US 25-45" if c else "",
        "campaign_id": f"c:{c[0]}" if c else "",
        "campaign_name": c[1] if c else "",
        "form_id": f"f:{lead.form[0]}",
        "form_name": lead.form[1],
        "is_organic": str(lead.is_organic).lower(),
        "platform": "ig" if int(lead.lead_id) % 3 == 0 else "fb",
        "full_name": f"{lead.first} {lead.last}",
        "email": lead.email,
        "phone_number": lead.phone,
    }


def crm_row(record_id: int, lead: Lead, delay: timedelta, tz: ZoneInfo = LOCAL, **columns) -> dict:
    """A CRM record that mirrors the lead. Keyword overrides use CRM column names with _ for spaces."""
    c = lead.campaign
    row = {
        "Record ID": str(record_id),
        "Create Date": (lead.created + delay).astimezone(tz).strftime("%Y-%m-%d %H:%M"),
        "First Name": lead.first,
        "Last Name": lead.last,
        "Email": lead.email.lower(),
        "Additional email addresses": "",
        "Phone Number": normalize_phone(lead.phone, "US") or lead.phone,
        "Meta Lead ID": lead.lead_id,
        "Campaign ID": c[0] if c else "",
        "Campaign Name": c[1] if c else "",
        "Lifecycle Stage": "Lead",
        "Original Source": "Paid Social",
    }
    row.update({k.replace("_", " "): v for k, v in columns.items()})
    return row


def quick(rng: random.Random) -> timedelta:
    """Instant-trigger automation: one to four minutes."""
    return timedelta(minutes=rng.randint(1, 4), seconds=rng.randint(0, 59))


# --- Scenarios -------------------------------------------------------------------
# Each returns (description, leads, crm_rows, config_overrides, crm_columns_absent).


def base_leads() -> list[Lead]:
    """Shared by A and B: the same campaign, two different pipelines."""
    return make_leads(30, "ab") + [recent_lead("ab-recent", 40)]


def scenario_a():
    rng, leads = random.Random("a"), base_leads()
    rows = []
    for i, lead in enumerate(leads[:30]):
        if i in (5, 6, 7):
            continue  # lost during an outage window: consecutive leads
        row = crm_row(50100 + i, lead, timedelta(minutes=47) if i == 12 else quick(rng))
        if i == 15:
            row["Campaign ID"] = ""
        if i == 18:
            row["Phone Number"] = "555-0123"  # no area code: not a valid number
        rows.append(row)
    desc = (
        "An automation that stores each source lead's ID in the CRM. Every event can be proven to have arrived, "
        "and arrival delay can be measured. Three consecutive leads were lost; one recent lead is still inside "
        "the grace period."
    )
    return desc, leads, rows, {}, set()


def scenario_b():
    rng, leads = random.Random("b"), base_leads()
    leads[27] = replace(leads[27], email=leads[20].email)  # the same person filled the form twice
    rows = []
    for i, lead in enumerate(leads[:30]):
        if i in (5, 6, 7, 27):
            continue  # 27: the CRM deduplicates by email, so only one contact exists for 20 and 27
        name = CAMPAIGNS[1][1] if i == 15 and lead.campaign == CAMPAIGNS[0] else lead.campaign[1]
        if i == 15 and lead.campaign == CAMPAIGNS[1]:
            name = CAMPAIGNS[0][1]
        rows.append(crm_row(60100 + i, lead, timedelta(minutes=rng.randint(20, 95)), Campaign_Name=name))
    desc = (
        "The same leads as A, sent through an integration that does not store the original lead ID or the "
        "campaign ID. People can be found by email or phone, but individual lead events cannot be proven. "
        "The CRM export contains all contacts, so unmatched CRM contacts are not evaluated."
    )
    overrides = {"settings": {"destination_scope_is_pipeline_specific": False}}
    return desc, leads, rows, overrides, {"Meta Lead ID", "Campaign ID"}


def scenario_c():
    rng, leads = random.Random("c"), make_leads(10, "c")
    leads[8] = replace(leads[8], email=leads[3].email)
    leads[9] = replace(leads[9], email=leads[4].email)
    rows = []
    for i, lead in enumerate(leads[:8]):
        ids = f"{lead.lead_id};{leads[9].lead_id}" if i == 4 else lead.lead_id
        rows.append(crm_row(70100 + i, lead, quick(rng), Meta_Lead_ID=ids))
    desc = (
        "One person submitted two leads and the CRM holds a single contact. In one case the contact stores only "
        "one of the lead IDs, so the other lead cannot be counted as arrived (it could be a lost lead). In the "
        "other case the contact stores both IDs, so both events are proven."
    )
    return desc, leads, rows, {}, set()


def scenario_d():
    rng, leads = random.Random("d"), make_leads(20, "d") + [recent_lead("d-recent", 30)]
    rows = [crm_row(80100 + i, lead, quick(rng)) for i, lead in enumerate(leads[:20]) if i != 4]
    desc = (
        "The CRM exports local time (US Central), but the configuration declares it as UTC. Every pair differs "
        "by about 5 hours. Nothing is corrected: time-based conclusions are switched off, and leads that were "
        "not found stay undecided, whatever their age."
    )
    overrides = {"destination": {"timezone": "UTC"}}
    return desc, leads, rows, overrides, set()


def scenario_e():
    rng, leads = random.Random("e"), make_leads(15, "e")
    rows = [crm_row(90100 + i, lead, quick(rng)) for i, lead in enumerate(leads)]
    for i in (4, 9):  # replayed two days later
        rows.append(crm_row(90900 + i, leads[i], timedelta(days=2, hours=rng.randint(15, 17))))
    desc = (
        "A CRM that allows repeated contacts. Two leads were created again two days later by a replay; both "
        "records store the same lead ID."
    )
    return desc, leads, rows, {}, set()


def scenario_f():
    rng, leads = random.Random("f"), make_leads(15, "f")
    rows = []
    for i, lead in enumerate(leads):
        if i in (2, 7, 11):  # contacts that already existed months before the lead
            months_before = timedelta(days=rng.randint(60, 120))
            rows.append(crm_row(10100 + i, lead, -months_before, Campaign_ID="", Campaign_Name="",
                                Original_Source="Organic Search"))
        else:
            rows.append(crm_row(10100 + i, lead, quick(rng)))
    desc = (
        "An automation that stores the campaign but not the lead ID. Three people already existed in the CRM "
        "months before submitting the lead: the person existing does not prove that the new lead was processed."
    )
    return desc, leads, rows, {}, {"Meta Lead ID"}


def scenario_g():
    rng, leads = random.Random("g"), make_leads(12, "g")
    tests = [
        replace(lead, first="Test", last=f"Lead {n}", email=f"test{n}@example.com", campaign=None,
                form=TEST_FORM, is_organic=True)
        for n, lead in enumerate(make_leads(2, "g-test", offset=600), start=1)
    ]
    leads = leads + tests
    rows = [crm_row(11100 + i, lead, quick(rng)) for i, lead in enumerate(leads[:12])]
    rows.append(crm_row(11900, tests[0], quick(rng)))  # a test lead got through
    manual = replace(leads[0], first="Sam", last="Taylor", email="sam.taylor@example.net", phone="(312) 555-0188")
    rows.append(crm_row(11999, manual, timedelta(hours=5), Meta_Lead_ID="", Original_Source="Offline Sources"))
    desc = (
        "Leads from a test form are excluded by configuration. One of them still reached the CRM: it is "
        "reported, but not as an error. A contact entered manually by sales is the only unmatched CRM contact."
    )
    overrides = {"exclusions": {"form_ids": [TEST_FORM[0]]}}
    return desc, leads, rows, overrides, set()


def scenario_h():
    rng, leads = random.Random("h"), make_leads(25, "h")
    rows = []
    for i, lead in enumerate(leads):
        name = "US 25-45" if i == 10 else lead.campaign[1]  # the automation mapped the ad set name by mistake
        rows.append(crm_row(12100 + i, lead, quick(rng), Campaign_ID="", Campaign_Name=name))
    desc = (
        "An automation that stores the lead ID but never sent the campaign ID: reported once instead of as 25 "
        "exceptions. One record also carries an ad set name instead of the campaign name."
    )
    return desc, leads, rows, {}, set()


SCENARIOS: dict[str, tuple[str, Callable]] = {
    "a-lead-id-preserved": ("A. Lead ID preserved", scenario_a),
    "b-lead-id-not-preserved": ("B. Same leads, lead ID not preserved", scenario_b),
    "c-same-person-two-leads": ("C. Same person, two leads", scenario_c),
    "d-timezone-uncertain": ("D. Timezone uncertainty", scenario_d),
    "e-verified-duplicate": ("E. Verified duplicate", scenario_e),
    "f-preexisting-contact": ("F. Preexisting contacts", scenario_f),
    "g-excluded-lead-arrived": ("G. Excluded lead that still arrived", scenario_g),
    "h-campaign-id-never-sent": ("H. Campaign ID never sent", scenario_h),
}


# --- Output ----------------------------------------------------------------------


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _merge(base: dict, overrides: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        out[key] = _merge(out.get(key, {}), value) if isinstance(value, dict) else value
    return out


def _toml(cfg: dict, header: str) -> str:
    def value(v):
        if isinstance(v, bool):
            return str(v).lower()
        if isinstance(v, list):
            return "[" + ", ".join(value(x) for x in v) + "]"
        return json.dumps(v) if isinstance(v, str) else repr(v)

    lines = [f"# {line}".rstrip() for line in header.splitlines()] + [""]

    def table(name: str, data: dict) -> None:
        lines.append(f"[{name}]")
        lines.extend(f"{k} = {value(v)}" for k, v in data.items() if not isinstance(v, dict))
        lines.append("")
        for k, v in data.items():
            if isinstance(v, dict):
                table(f"{name}.{k}", v)

    for name, data in cfg.items():
        table(name, data)
    return "\n".join(lines)


def generate(out_dir: Path) -> None:
    base = tomllib.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    base["settings"]["as_of"] = AS_OF.isoformat()
    for name, (title, build) in SCENARIOS.items():
        desc, leads, rows, overrides, absent = build()
        overrides = _merge(overrides, {"report": {"title": title, "description": desc}})
        target = out_dir / name
        target.mkdir(parents=True, exist_ok=True)
        _write_csv(target / "source.csv", SOURCE_COLUMNS, [source_row(lead) for lead in leads])
        columns = [c for c in CRM_COLUMNS if c not in absent]
        _write_csv(target / "destination.csv", columns, rows)
        cfg = _merge(base, overrides)
        cfg["destination"]["columns"] = {k: v for k, v in cfg["destination"]["columns"].items() if v not in absent}
        header = f"Scenario {name}. Generated by scripts/generate_examples.py; do not edit by hand."
        (target / "run.toml").write_text(_toml(cfg, header), encoding="utf-8")


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "scenarios")
