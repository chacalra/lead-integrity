# Reconciliation model

This document defines what Lead Integrity means by "a lead arrived correctly", how source leads are matched to destination records, and which conclusions the tool is willing to draw. The implementation lives in [`src/lead_integrity/reconcile.py`](../src/lead_integrity/reconcile.py).

## 1. Person versus event

"This person exists in the CRM" and "this specific lead reached the CRM" are different claims. Every source lead gets an **evidence level**:

| Evidence | Meaning | Source of the evidence |
|---|---|---|
| `VERIFIED` | This lead event reached the destination | The destination record stores the lead's original ID |
| `IDENTITY` | The person exists in the destination; this event is not proven | Same email or phone number |
| `NONE` | Neither | No match |

Two rules follow:

- **Event claims require `VERIFIED` evidence.** "It arrived", "it took 47 minutes" and "it was late" are claims about an event.
- **State comparisons are allowed with `IDENTITY` evidence, but say so.** Comparing campaign or contact fields describes the person's record, not the event. Every issue carries the evidence it was computed on, and the report never words an identity-based finding as verified.

If the destination never stores the source lead ID, no lead can be `VERIFIED`. That is a result, not a bug: the dataset-level finding `LEAD_ID_NOT_PRESERVED` reports it.

## 2. Records

- **Source record:** one row of a lead-source export. The examples model a Meta Lead Ads per-form export. Required fields: `lead_id`, `created_at`.
- **Destination record:** the record the pipeline created or updated in the CRM (a contact or a deal, depending on the CRM). Required fields: `record_id`, `created_at`.
- `source_lead_ids` is a tuple, so a CRM that keeps several lead IDs on one record (a multi-value property, or a merge that preserves both) can be represented. In CSV it is `;`-separated.
- Column names differ per export, so every file is read through a column map in the run configuration. A mapped column that is missing from the file is an error. An unmapped optional field is `None`.

## 3. Normalization

| Field | Rule |
|---|---|
| email | Trim and lowercase. Dots and `+tags` are kept, because CRMs treat those as different addresses. |
| phone | The raw value is always kept. The normalized E.164 value is set only if [`phonenumbers`](https://github.com/daviddrysdale/python-phonenumbers) considers it valid, using an optional per-dataset default region. No repairs: alternative or legacy national prefixes are not rewritten. Invalid phones are never used for matching. |
| IDs | Trimmed; export prefixes such as `l:` are removed. An ID in scientific notation (a 17-digit ID that went through a spreadsheet) stops the load with an error instead of being guessed. |
| timestamps | Everything is converted to UTC. An explicit offset wins. A timestamp without an offset requires the dataset to declare its `timezone`; otherwise the load fails. |

## 4. Matching

1. **Deterministic:** destination records whose `source_lead_ids` contain the lead ID.
2. **Identity**, only if step 1 found nothing: email, against the record's email and additional emails. If no email matches, phone (E.164, valid numbers only). Phone is the weaker identity signal.

There is no name-based or fuzzy matching. A consequence: a duplicate created with a different email but the same phone is not detected, because email wins the cascade.

## 5. Statuses

Each source lead gets exactly one status.

| Status | Evidence | Condition |
|---|---|---|
| `VERIFIED` | VERIFIED | Exactly one record stores the lead ID, and it stores no other lead IDs |
| `MERGED` | VERIFIED | Informational: the record stores this lead ID and others, so both events are proven |
| `DUPLICATED` | VERIFIED | Two or more records store the lead ID |
| `IDENTITY_MATCH` | IDENTITY | One unshared candidate by email or phone, with no ambiguity reason |
| `AMBIGUOUS` | IDENTITY | The person exists, but the record cannot be attributed to this lead (see reasons) |
| `MISSING` | NONE | No deterministic or identity match, and the lead is older than the grace period |
| `PENDING` | NONE | Same, but still inside the grace period |
| `TEMPORAL_UNCERTAIN` | NONE | No match, and timezone doubt prevents deciding between `MISSING` and `PENDING` |
| `EXCLUDED` | NONE | The lead or its form is excluded by configuration. If it still appears in the destination, a note says so (with its evidence), and that record is not an orphan. |

`AMBIGUOUS` reasons can accumulate:

| Reason | Condition |
|---|---|
| `CLAIMED_BY_OTHER_LEAD` | The candidate record stores a different lead's ID. This is the case that could hide a lost lead. |
| `MULTIPLE_CANDIDATES` | Several records share the email or phone |
| `SHARED_DESTINATION` | Another source lead points to the same record by identity |
| `PREEXISTING_RECORD` | The record was created before the lead (evaluated only when clocks are trustworthy, with zero tolerance) |

A record that stores the lead ID counts as received even if it was created before the lead; its latency is simply not measurable.

## 6. Latency and issues

Every lead carries an explicit `latency_status`, so a report never shows "0 late" when nothing could be measured.

| `latency_status` | When |
|---|---|
| `MEASURED` | `VERIFIED`, the record was created after the lead, and clocks are trustworthy |
| `NOT_VERIFIABLE` | Identity evidence or a shared record, or a `VERIFIED` lead whose record predates it |
| `TEMPORAL_UNCERTAIN` | Would be measurable, but timezone is in doubt |
| `NOT_APPLICABLE` | Nothing arrived, or the lead is excluded |

Issues apply to `VERIFIED` and `IDENTITY_MATCH` leads, except `LATE`, which requires `MEASURED`:

| Issue | Rule |
|---|---|
| `LATE` | Latency above `late_after` |
| `ATTRIBUTION_MISSING` | The source has a campaign ID or name and the destination does not |
| `ATTRIBUTION_MISMATCH` | Values differ. A renamed campaign with the same campaign ID is not flagged. |
| `FIELD_MISMATCH` | The source email or (valid) phone is absent from or different in the destination |
| `PHONE_UNPARSEABLE` | A raw phone exists on either side but is not a valid number |

A value present in the destination but absent in the source is not an issue. Owner, lifecycle stage and tags are out of scope: CRM workflows change them legitimately after creation, and without property history that comparison is mostly noise.

## 7. Dataset-level findings

- **`LEAD_ID_NOT_PRESERVED`**: no destination record stores a source lead ID, so no event can be verified.
- **`FIELD_NOT_TRANSMITTED`**: a field is missing in at least `systemic_threshold` of the compared records that have it in the source. It is reported once, and the per-record issues for that field are silenced. If the campaign ID is systemic, attribution falls back to comparing the campaign name.
- **`TIMEZONE_UNCERTAIN`**: with at least `min_pairs` trustworthy pairs, if `min_share` of the source-to-destination time differences sit within `tolerance` of the same non-zero whole hour. This is a diagnostic only: timestamps are never corrected. While it is active, no time-based claim is made: no latency, no `LATE`, no `PREEXISTING_RECORD`, and unmatched leads become `TEMPORAL_UNCERTAIN` instead of `MISSING` or `PENDING`. The parameters are an experimental heuristic, not a domain rule.

## 8. Orphans

An orphan is a destination record that matches no source lead (verified, identity, or excluded). Orphans are evaluated only when `destination_scope_is_pipeline_specific = true`, meaning the CRM export is filtered to the same pipeline, source and time window. In a general CRM export, "unmatched" means nothing, so `orphans` is `None` (not evaluated).

## 9. Configuration

Thresholds and scope have no defaults in code; each run declares them (see [`examples/base-config.toml`](../examples/base-config.toml)). The example values are illustrative: normal delivery delay depends on the pipeline, and real values should be agreed with whoever owns it.

## 10. Legitimate differences

Several source/destination differences are normal and are deliberately not reported as errors: CRM deduplication by email, contact merges, intentionally filtered or test leads (when declared), timezone representation, delivery delay inside the grace period, optional fields, phone formatting, and fields that workflows change after creation.

## 11. Assumptions and references

The export layouts in the examples follow public documentation and descriptions. They are **not verified against real exports**. In particular, the Meta CSV ID prefixes and column order, and the CRM export's date format and timezone, are assumptions.

- Meta, retrieving Lead Ads leads (fields, filtering, export): https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/retrieving
- Meta, the lead ID as a match key: https://developers.facebook.com/documentation/ads-commerce/conversions-api/conversion-leads-integration/how-to-find-the-lead-id
- Meta, webhook delivery and retries: https://developers.facebook.com/docs/graph-api/webhooks/getting-started
- HubSpot, deduplication of records: https://knowledge.hubspot.com/records/deduplication-of-records
- HubSpot, merging records: https://knowledge.hubspot.com/records/merge-records
