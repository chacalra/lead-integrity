# Integration traceability: Meta Lead Ads to CRM

How far can each common integration let you **prove** that a specific Meta lead reached the CRM? This page records what public documentation supports, integration by integration, and connects it to the evidence model in [`reconciliation-model.md`](reconciliation-model.md).

**Verified on 2026-10-17, from public documentation only.** No integration was tested with a live account. Vendors change their products and documentation; if something here is outdated, please open an issue with a source.

## How to read this page

**Evidence tags**

| Tag | Meaning |
|---|---|
| `DOC` | Stated in official documentation of the vendor or of Meta. A year is added when the page is old, e.g. `DOC (2018)`. |
| `COM` | Community forum, ideas board, search snippet or third-party content. Never used alone to support a conclusion. |
| `NOT VERIFIED` | No documentation found either way. |

**Absence of documentation is not evidence of absence.** `NOT VERIFIED` means we did not find it documented, not that the integration cannot do it.

**Traceability levels**

| Level | Meaning |
|---|---|
| `DETERMINISTIC_CONFIGURABLE` | Documentation shows the source lead ID can be written to the CRM record, if the pipeline is configured to do so. |
| `IDENTITY_LEVEL` | Documentation shows, at minimum, a person-level link (email or phone). This does **not** prove that a specific lead event was processed, and does **not** show that deterministic traceability is impossible. |
| `UNKNOWN` | Documentation does not establish the level. |

Each integration gets two levels:

- **Documented default traceability:** what the documented default setup supports.
- **Documented maximum traceability:** the strongest level any documented configuration supports.

No integration in this review documents writing the Meta lead ID to the CRM by default.

## Summary

| Integration | Documented default | Documented maximum | Expected result in Lead Integrity |
|---|---|---|---|
| HubSpot (native) | `IDENTITY_LEVEL` | `UNKNOWN` | Like scenario B unless the lead ID is found on real records |
| Zapier | `UNKNOWN` | `DETERMINISTIC_CONFIGURABLE` | Like scenario A if the lead ID is mapped to a CRM field |
| Make | `UNKNOWN` | `DETERMINISTIC_CONFIGURABLE` | Like scenario A if the lead ID is mapped to a CRM field |
| GoHighLevel (native) | `IDENTITY_LEVEL` | `UNKNOWN` | Like scenario B unless the lead ID is confirmed on real records |
| Kommo (native) | `IDENTITY_LEVEL` | `UNKNOWN` | Like scenario B unless the lead ID is found on real records |
| Pipedrive via Outfunnel | `IDENTITY_LEVEL` | `UNKNOWN` | Like scenario B unless the lead ID is found on real records |
| Pipedrive via Zapier | `UNKNOWN` | `DETERMINISTIC_CONFIGURABLE` | Same as Zapier |

Pipedrive has **no native Meta Lead Ads integration** of its own: its Marketplace app is provided by a third party (Outfunnel) [P2], and its own help center documents a Zapier route [P1].

When the level is `UNKNOWN`, running Lead Integrity on a real pair of exports settles it: if no destination record carries a lead ID, the report raises `LEAD_ID_NOT_PRESERVED`.

## Attribute matrix

| Integration | Meta lead ID on record | Meta `created_time` | Campaign, ad set, ad, form IDs | Custom form fields | Record may already exist |
|---|---|---|---|---|---|
| **HubSpot (native)** | `NOT VERIFIED`. The documented default mapping covers profile and identity fields (email, name, phone, etc.) and does not list a lead ID [H1]. | `NOT VERIFIED`. Leads are logged as form submissions in HubSpot [H2]. | Campaign, ad set and ad **names** on the contact timeline; campaign name in the source drill-down (`COM`) [HC1]. IDs: `NOT VERIFIED`. | Yes, with limits (`DOC`): custom questions map to text properties; mapping changes apply only to newly synced leads; labels without English characters are not synced [H1][H3]. | Likely. HubSpot deduplicates form submissions by email (`DOC`, general) [H4]; not stated specifically for lead ads. |
| **Zapier** | Configurable (`DOC`, Meta): the lead ID is in the `id` field of Zapier's Facebook Lead Ads trigger and "must be mapped to a field in your CRM" [M1]. Not written unless mapped. | Available to map (`COM`) [ZC1]. | Ad, ad set and campaign, by name and ID (`DOC (2018)`) [Z1]. Form ID: `NOT VERIFIED`. | Form answers appear in the Zap editor once the form has a submission (`DOC`) [Z2]. Editing a form gives it a new ID, so Zaps must re-select it (`DOC`) [Z3]. | Zapier does not deduplicate instant triggers (`DOC`) [Z4]; depends on the CRM action used (e.g. find or create). |
| **Make** | Configurable (`DOC`, Meta): the lead ID is in the "Lead ID" field of Make's Facebook Lead Ads scenario and can be mapped to a CRM field [M1]. Make does not document its output schema [MK1]. | `NOT VERIFIED` | `NOT VERIFIED` | Partial (`DOC`): the module returns the information the user filled in; the form must have at least one submission first [MK1]. | `NOT VERIFIED` for Make; duplicates reported by users (`COM`) [MKC1]. |
| **GoHighLevel (native)** | `NOT VERIFIED` in official docs. One user reports reading it via `{{contact.facebookLeadId}}`; another reports it empty (`COM`) [GC1]. | `NOT VERIFIED` | Lead form tracking parameters sync to the CRM when mapped, for forms used through HighLevel's Ad Manager (`DOC`) [G3]; which parameters is not stated. IDs: `NOT VERIFIED`. | Yes (`DOC`): form fields map to contact and custom fields; custom fields must be created first [G1][G3]. | Contact deduplication by email or phone is documented for forms and Messenger (`DOC`) [G2]; lead ads are not listed. |
| **Kommo (native)** | `NOT VERIFIED` [K1] | `NOT VERIFIED` | Manual tags per connected form (`DOC`) [K1]; Meta IDs: `NOT VERIFIED`. | Yes (`DOC`): form fields are matched to Kommo fields [K1]. Unmapped fields are lost (`DOC (2017)`) [K3]. | Each submission creates a new lead (deal) [K1]. General duplicate control exists but does not name lead ads (`DOC`) [K2]. |
| **Pipedrive via Outfunnel** | `NOT VERIFIED` [P3] | `NOT VERIFIED` | "Lead source and UTM data" (vendor marketing page) [P4]; IDs: `NOT VERIFIED`. | Yes, with limits (`DOC`): email, first and last name required; conditional questions not supported [P3]. | "Updates are linked to existing records when a match is found" (`DOC`, match key not stated) [P2]. |
| **Pipedrive via Zapier** | Configurable: see Zapier [M1]. | See Zapier. | See Zapier. | Custom fields supported (`DOC`) [P1]. | See Zapier. Historical leads cannot be passed through Zapier; export and import instead (`DOC`) [P1]. |

Across all seven routes, **no official documentation states that Meta's `created_time` is preserved** on the CRM record. Arrival delay is therefore measured against the CRM's own creation date.

## What each level means in Lead Integrity

| Level | Best possible status | What the report can and cannot say |
|---|---|---|
| `DETERMINISTIC_CONFIGURABLE` (configured) | `VERIFIED` | Each lead event can be proven; duplicates of the same lead are detectable; arrival delay is measurable. |
| `IDENTITY_LEVEL` | `IDENTITY_MATCH` | The person is found, but the event is not proven. Repeat submissions and preexisting contacts become `AMBIGUOUS`; arrival delay is not verifiable. |
| `UNKNOWN` | Depends on the real configuration | Run the tool on real exports; `LEAD_ID_NOT_PRESERVED` shows whether lead IDs are present. |

### Destination export scope

How much the scope of the CRM export matters depends on the evidence level:

- **Deterministic matching (lead ID): a general CRM export is harmless.** A record that stores the lead ID belongs to that lead, whatever else the export contains.
- **Identity matching (email or phone): a general export is not harmless.** A contact created by another source, another pipeline or an earlier campaign can match by email or phone. That proves the person exists in the CRM, not that the audited lead arrived through this pipeline, and it can hide a real `MISSING`. Identity matches are meaningful only when the export carries enough provenance to restrict it to the audited pipeline, source and time window. Without that provenance, an identity match is ambiguous, never evidence of arrival.

Lead Integrity enforces this: when `destination_scope_is_pipeline_specific = false`, identity-only matches are reported as `AMBIGUOUS` with reason `UNSCOPED_DESTINATION`. See [destination scope and orphans](reconciliation-model.md#8-destination-scope-and-orphans).

### When is `MISSING` high-confidence?

Deterministic traceability is necessary but **not sufficient**. `MISSING` findings are complete and trustworthy only when all of these hold:

1. **Complete coverage of the audited population.** The destination export contains every record the pipeline could have written for the audited source and time window: nothing filtered out, paginated away or deleted.
2. **Unrelated records cannot produce false identity matches.** With lead-ID matching this holds by construction. With identity matching, records from other sources or pipelines must be excludable by provenance (see above); otherwise a coincidental email or phone match can hide a lead that never arrived.
3. **Time conditions are valid.** Timezones are declared, no timezone inconsistency is detected, and the lead is older than the grace period. Otherwise the tool reports `TEMPORAL_UNCERTAIN` or `PENDING` instead of `MISSING`. See [dataset-level findings](reconciliation-model.md#7-dataset-level-findings) and [destination scope and orphans](reconciliation-model.md#8-destination-scope-and-orphans).

With identity-level traceability, `MISSING` also assumes that the email or phone stored in the CRM still matches the source: a changed email can make a delivered lead look missing.

## Sources

All accessed on 2026-10-17.

**Meta**
- [M1] How to find the lead ID (mentions Zapier and Make): https://developers.facebook.com/documentation/ads-commerce/conversions-api/conversion-leads-integration/how-to-find-the-lead-id

**HubSpot**
- [H1] Lead ads field mappings: https://knowledge.hubspot.com/ads/lead-ads-field-mappings-to-hubspot
- [H2] Sync leads from your Facebook page: https://knowledge.hubspot.com/ads/sync-leads-from-your-facebook-page-or-linkedin-ads-account-to-hubspot
- [H3] Configure sync timeframe and property mappings: https://knowledge.hubspot.com/ads/configure-sync-timeframe-and-property-mappings-for-your-lead-ads
- [H4] Deduplication of records: https://knowledge.hubspot.com/records/deduplication-of-records
- [HC1] `COM`, search snippet only (page returned 403): https://community.hubspot.com/t/capturing-meta-ads-interaction-data-campaign-ad-set-ad-name-into-contact-properties/144003

**Zapier**
- [Z1] `DOC (2018)` Facebook Lead Ads: get more info on leads: https://zapier.com/blog/updates/1044/facebook-lead-ads-get-more-info-on-leads
- [Z2] Get started with Facebook Lead Ads on Zapier: https://help.zapier.com/hc/en-us/articles/8496061306253-How-to-get-started-with-Facebook-Lead-Ads-on-Zapier
- [Z3] Facebook Lead Ads Zap not triggering: https://help.zapier.com/hc/en-us/articles/25377944537997-Facebook-Lead-Ads-Zap-not-triggering
- [Z4] Data deduplication in Zaps: https://help.zapier.com/hc/en-us/articles/8496260269965-Data-deduplication-in-Zaps
- [ZC1] `COM`: https://community.zapier.com/how-do-i-3/i-want-to-add-the-date-of-adding-each-row-lead-automatically-26278

**Make**
- [MK1] Facebook Lead Ads app: https://apps.make.com/facebook-lead-ads
- [MKC1] `COM`: https://community.make.com/t/facebook-lead-ad-watch-leads-is-returning-same-lead-at-least-twice-and-changes-format-of-phone-number/94313

**GoHighLevel**
- [G1] Facebook lead ad integration troubleshooting guide: https://help.gohighlevel.com/support/solutions/articles/48000987779-facebook-lead-ad-integration-troubleshooting-guide
- [G2] Contact deduplication preferences: https://help.gohighlevel.com/support/solutions/articles/48001181714-allow-duplicate-contacts-contact-deduplication-preferences-
- [G3] How to create a Facebook form in Ad Manager: https://help.gohighlevel.com/support/solutions/articles/155000002439-how-to-create-a-facebook-form-in-ad-manager
- [GC1] `COM`, ideas board: https://ideas.gohighlevel.com/integrations/p/enable-the-id-field-on-facebook-lead-ads-to-unlock-conversion-lead-ads-in-facebo

**Kommo**
- [K1] Facebook form ads: https://support.kommo.com/docs/facebook-form-ads
- [K2] Set up duplicate control: https://support.kommo.com/docs/set-up-duplicate-control.md
- [K3] `DOC (2017)` Facebook Lead Ads integration: https://www.kommo.com/blog/facebook-lead-ads-integration/

**Pipedrive and Outfunnel**
- [P1] Pipedrive: add new Facebook ad leads as people (Zapier): https://support.pipedrive.com/en/article/zapier-add-new-facebook-ad-leads-as-people-in-pipedrive
- [P2] Pipedrive Marketplace: Facebook Lead Ads Sync (provided by Outfunnel): https://www.pipedrive.com/en/marketplace/app/facebook-lead-ads-sync/f7d03043685585ef
- [P3] Outfunnel: Facebook Lead Ads CRM connection: https://support.outfunnel.com/en/articles/5537716-facebook-lead-ads-crm-connection
- [P4] Outfunnel: Pipedrive Facebook Ads integration (marketing page): https://outfunnel.com/pipedrive-facebook-ads-integration/
