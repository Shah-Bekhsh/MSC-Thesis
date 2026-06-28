# Report-Worthy Material — Development Log

**For:** Jupiter Agent thesis (Shah Bekhsh, DTU 2026)
**Purpose:** Record of the important findings, decisions, and incidents from development that deserve a place in the report. Each entry is a couple of lines for recall; expand when writing.

---

## A. Data-quality findings (the diligence that caught real traps)

These show you validated the data rather than trusting it — strong material for the Data and Implementation chapters.

1. **The `attribute` / below-detection field is central, not peripheral.**
   A non-null `attribute` ("<", ">", "!", "A") means the value is not a true measurement (below detection, etc.). At Frederiksberg, **206 of 316** source-nitrate measurements (~two-thirds) were below detection. The tools keep these in the per-borehole series but exclude them from min/max/avg, so statistics reflect only genuine detections. Treating them as numbers would have materially misrepresented the water.

2. **Decommissioned intakes would have reported water from abandoned boreholes.**
   `drwplantintake.intakeusage` codes distinguish active abstraction (1 = Indvinding, 3 = Indvinding og monitering) from decommissioned (7 = Sløjfet/opgivet) etc. The Søndersø plant's linkage contained *only* code-7 boreholes (abandoned 1950s–1990s). A naive source query ignoring this would have told a citizen their water comes from boreholes abandoned decades ago. Fixed by filtering to `intakeusage IN (1,3)`.

3. **Coded units must be resolved, not read directly.**
   The `unit` column is a numeric FK into `code_752`, not a unit string. Comparisons against legal limits are only valid when units match; the tool resolves both and declines to compare on mismatch.

4. **Mandatory quality filters on all chemistry.**
   Exclude rejected QC (`qualitycontrol NOT IN (4,5,6,8,12,13,14,15)`), restrict to approved sample statuses (`IN (2,4,6,8,10,14)`), restrict to drinking-water project (`project = 'DRV'`), and exclude future-dated data-entry errors (`sampledate <= NOW()`), all tolerating nulls.

5. **Municipality naming uses the post-2007 code, not free text.**
   The raw `borehole.municipal` field holds inconsistent pre-2007 names; the post-2007 code via its code table is used instead.

6. **Raw Jupiter contains exact-duplicate analysis rows.**
   Some samples have multiple identical analysis records (e.g. the triple 2011-11-23 rows), which slightly inflate counts. Documented; optional `SELECT DISTINCT` dedupe noted as future refinement.

7. **Water level: the raw `waterlevel` column mixes conventions row-to-row.**
   In `watlevel`, the raw `waterlevel` value is sometimes a sea-level elevation, sometimes a raw measuring-point reading — inconsistent across rows. `watlevmsl` (elevation, m above mean sea level / DVR90) and `watlevgrsu` (depth below ground) were verified internally consistent across every row. Decision: never surface the raw column; report the two consistent ones. A data-quality decision grounded in direct inspection.

---

## B. Deliberate design decisions (defensible choices, not oversights)

These are the "I considered X and chose Y because Z" points examiners reward.

8. **No treatment-efficiency number in `compare_source_to_tap`.**
   The tool deliberately refuses to compute a "percent removed" figure. Source is per-borehole raw groundwater sampled in different years; tap is the blended, treated output. A borehole spike in 2017 has no arithmetic relationship to a treated 2025 sample. Returns both datasets + caveats and lets the agent explain qualitatively. *The system refuses to give a precise answer whose precise form would be misleading.*

9. **Hardcoded `w.end = 2019` kept as a documented scoping decision.**
   The WSA supply-zone dataset ends in 2019; 2019 is treated as the canonical "current" snapshot rather than dynamically deriving the latest year. A reasoned scope choice, documented, with dynamic coverage noted as future work.

10. **Tools, not raw SQL.**
    The agent composes a fixed set of read-only, parameterised tools rather than authoring arbitrary SQL. Domain expertise (which QC codes to drop, how to handle units and the attribute field) lives in tested code, not in the fallible LLM. The agent is structurally incapable of issuing bad queries or mutating data.

11. **Water level filtered to resting measurements (`situation = 0`).**
    Readings taken during pumping reflect drawdown, not the natural water table. Filtered to resting. The `situation` field has no code table; the interpretation (0 = resting, 1 = operational) is taken from the data distribution and standard Danish pejling practice, and documented as an explicit assumption **to confirm with GEUS**.

12. **Water level reported in two conventions, with no invented legal limit.**
    Elevation (DVR90) for the hydrogeological standard, depth-below-ground for citizen intuition. Explicitly no "limit" since water level is not a regulated parameter — a case where the agent must *not* fabricate a comparison.

13. **Borehole-level granularity for source water (a defensible simplification).**
    Source chemistry joins on `boreholeid` rather than matching the specific intake the plant draws from. Trades some precision for robustness; intake-level matching noted as future refinement.

---

## C. Development incidents (the narratives — strongest for Discussion/Evaluation)

These are concrete "something went wrong and the design responded" stories. The geocoder one is the headline.

14. **★ The geocoder failure → agent fabrication episode (headline grounding-failure case).**
    The DAWA address API had changed its response shape — coordinates moved to `adgangspunkt.koordinater` as WGS84, and the old `etrs89koordinat` field the tool read was gone. The tool silently failed and returned an error dict. **The agent then fabricated approximate coordinates from its own training knowledge and proceeded** — and they were close enough to return plausible-but-incomplete results, masking the failure. Caught by inspecting the trace; root-caused to the API drift; fixed by reading WGS84 and projecting to UTM32 ourselves with `pyproj` (same PROJ library PostGIS uses). Then added an anti-fabrication guardrail (identifiers must come from tools, never memory) + test case G-17. *The canonical illustration of why grounding guardrails matter in practice.*

15. **The fabricated coordinates produced materially worse results.**
    The guessed coordinates returned only 2 plants and **missed Frederiksberg Vandværk itself**; the correct projected coordinates returned the full set of 7 overlapping HOFOR-network plants including the local works. Concrete evidence that a grounding error causes real downstream correctness loss, not just theoretical risk.

16. **Multiple overlapping supply zones exposed a gap.**
    The corrected Frederiksberg lookup returned 7 plants. Prompted a "handling multiple supply plants" guardrail (list them, don't silently pick or merge) + test case G-18.

17. **Database migration surfaced three instructive issues.**
    (a) PostGIS on the Mac only packaged for PG17/18, forcing a 16→17 move — fine because a logical `pg_dump` restores across major versions. (b) A benign `spatial_ref_sys` permission error on restore (PostGIS pre-populates it). (c) A dependency hang caused by `uv` selecting Python 3.14, for which a compiled transitive dep (`rpds`) had no working wheel — fixed by pinning Python 3.12. Good reproducibility-section material.

18. **Søndersø was a bad demo plant — feasibility checked before committing.**
    Søndersø's intake linkage had no active boreholes with source chemistry, so it returned empty. Rather than abandon the feature, a database-wide check found **6,696 plants** with active-intake nitrate source chemistry, confirming viability at scale; Frederiksberg Vandværk (plantid 44357) was selected as the demo plant. Shows feasibility analysis before feature commitment.

---

## D. Architectural points (for Architecture/Implementation chapter)

19. **MCP as the integration layer.**
    Separation of reasoning (agent decides *what*) from execution (server decides *how*). Enforces safety as a capability boundary, decouples agent from data layer, and generalises to other public-sector databases (the N6 generality claim).

20. **Backend-agnostic agent.**
    The same tool-calling loop drives either the Anthropic cloud model (`claude-sonnet-4-6`) or a local Ollama model (Llama/DeepSeek), selected by env var — a dividend of MCP. Has a privacy dimension (fully local keeps data + queries on-premises) and supports the local-vs-cloud comparison.

21. **Token economics of the agent loop + prompt caching.**
    The API is stateless, so the system prompt + growing message history is re-sent on *every* step of the tool-calling loop — a single multi-tool query makes ~5 API calls, each larger than the last. This tripped the input-tokens-per-minute rate limit. Mitigated with prompt caching (cache the stable tools+system prefix; ~10% cost on cached reads). Good "production realities of agentic systems" material.

22. **Transparency-first GUI.**
    Streamlit interface that exposes the agent's reasoning/tool-call trace behind a toggle, plus a standing provenance note — turning the system's transparency into something the user can see. Faithful to the MCP architecture (launches the same server, calls the same tools; does not bypass MCP).

23. **The five-layer guardrail framework.**
    Scope boundary → grounding → data-quality/coverage disclosure → medical/legal boundary → always-on uncertainty disclosure. Operationalises the non-functional requirements; the evaluation suite is organised around it.

---

## E. Verification results (empirical confidence, for Evaluation chapter)

24. **WSA temporal consistency verified.**
    All **4,719** plant-linkage rows for current (end=2019) supply polygons also have `end_year = 2019`, confirming that a single filter on polygon validity is sufficient and no extra join condition on the linkage is needed. An assumption validated against the data rather than trusted.

25. **No duplicate-plant rows / no public-private filter needed.**
    Point-in-polygon lookup returns no duplicated plants, and no company-type filter is needed because the WSA dataset is already restricted to public (almene) waterworks by construction.

26. **Water-level active-intake filter verified on a mixed plant.**
    At Slangerup, the reported borehole 192.57S is `intakeusage = 1` (active, 300 measurements 1950–1992), while the plant's decommissioned boreholes (codes 7/8/11) were correctly excluded — verifying the filter empirically on a plant that actually has the mix.

---

## Quick map: which chapter each cluster feeds

- **Data chapter:** A (all), 24, 25
- **Architecture/Implementation:** 10, 19, 20, 22, 23; design decisions 8, 9, 11, 12, 13; migration 17
- **Evaluation:** 24, 25, 26; the guardrail suite; incident 14 as a worked failure case
- **Discussion:** the headline narrative 14–16; the "validate, don't trust" theme (2, 24); limitations (6, 9, 11, 13); token economics 21
- **For your GEUS supervisor specifically:** 7, 11 (water-level data-quality rigor + the situation-field assumption to confirm), 26

---

*Note to self: numbers and dates here are from development verification runs — re-confirm against a fresh query before quoting any specific figure in the final text.*
