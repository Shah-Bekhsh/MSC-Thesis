These are the notes that are relevant when I'm writing the report:


1. This is a really important design lesson for the tool, and a great paragraph for your report: a naive source-to-tap tool that didn't filter intakeusage would tell a citizen their water comes from boreholes that were abandoned in 1994, reporting 30-year-old nitrate as if it were their current source. That's exactly the kind of domain-specific data-quality trap that distinguishes a thoughtful system from a naive one.

2. It took me approximately 6 complex SQL queries to figure out which plants were currently using active intakes, and to then find out the list of active plants with their ids.

3. A few things the data surfaces that are genuinely good material for your report:
The n_flagged: 206 out of 316 is striking and important — about two-thirds of source measurements are below the detection limit (attribute <). Your tool correctly excluded these from the min/max/avg so they don't pollute the statistics, while still keeping them in the series. That's exactly the detection-limit handling we built, and it's working. Worth noting in the writeup that source groundwater nitrate at this site is mostly below detection, which tells a real scientific story.
The per-borehole variation is the showcase: borehole 201. 5312 has that single 9.9 mg/l spike in 2017 against an otherwise sub-1 background, while 201.15830 only has 4 measurements total. This heterogeneity across boreholes feeding one plant is precisely the argument for why the tool returns per-borehole detail rather than a single number — and it's a strong figure for your report.
One small thing I notice that's worth being aware of (not a bug): there are occasional exact-duplicate rows — same borehole, same timestamp, same value (e.g. the 2011-11-23 entries in 201. 3702). These are genuine duplicates in Jupiter itself, not something your query introduced — the source database sometimes has multiple analysis records for one sample. They slightly inflate n_measurements and the flagged count. It's harmless for the demo, but if you want precision you could add SELECT DISTINCT to the source query, and it's worth a sentence acknowledging the duplication exists in the raw data. Your call whether to dedupe or just document it.

4. compare_source_to_tap works exactly as designed. Both tools are now verified end-to-end against real data. Here's what this output confirms:
The structure is clean — a full source block (the six-borehole summary plus per-borehole detail), a tap block (treated water, 2024–2025, all flagged correctly), the two _latest dates surfaced for the agent to compare (tap_latest 2025-12, source_latest 2024-12), and the three caveats. Critically, it does not emit a treatment-efficiency number — the deliberate design decision holds.
And the data tells a genuinely interesting scientific story that your tool now captures faithfully:
Source groundwater nitrate at Frederiksberg is mostly at or below detection limit, with occasional real detections — the notable one being that 9.9 mg/l spike at borehole 201. 5312 in June 2017, and elevated readings at 201. 5311 (3.5 mg/l in 2023). The treated tap water sits steadily at 1.5–3.0 mg/l. So the "story" here isn't dramatic treatment removal — it's that the blended output across six boreholes smooths out the per-borehole variability into a stable, low, well-within-limit tap value. That's exactly why a naive source-minus-tap efficiency number would be nonsense, and why the per-borehole view matters. Your tool surfaces this correctly.
Both tools are done and validated. Two small things worth noting for later, neither blocking:
The exact-duplicate rows from the raw Jupiter data are still present (e.g. the triple 2011-11-23 entries at 201. 5311). Harmless for the demo; dedupe with SELECT DISTINCT later if you want precision, or just document it.
The tap data shows your status flagging working perfectly — note the two NOT_DETECTED rows where attribute is <, correctly distinguished from the OK numeric readings.


5. THe system response time is fucked. so lets put a pin in this and make a note in the report that since this is a proof of concept system, we are not working on to make sure that the response times are perfect, rather the responses in general are CORRECT and ACCURATE.

6. With the proper coordinates (the real address point, not the agent's guess), the supply lookup now returns seven plants, including Frederiksberg Vandværk (44357) itself — which is the most important one and was missing from the earlier fabricated-coordinate result. That earlier run only found Slangerup and Søndersø because the guessed coordinates landed in a different overlapping zone. The accurate coordinates place the address inside the Frederiksberg Vandværk supply area plus the broader HOFOR network zones (Slangerup, Søndersø, Islevbro, Lejre, Marbjerg, Thorsbro — all real HOFOR greater-Copenhagen plants). This is hydrogeologically sensible: central Frederiksberg genuinely sits within Frederiksberg's own waterworks zone and the interconnected HOFOR supply network. So the fix didn't just stop the fabrication — it produced a materially better, more complete answer. That's a clean demonstration for your write-up that grounding errors cause real downstream correctness loss, not just theoretical risk.
One thing this surfaces, worth noting (not fixing now): seven plants is a lot to handle gracefully, and it confirms the "plant has multiple supply zones" case in your guardrails is a real scenario the agent must handle well — listing them and querying the relevant one(s), not silently picking one. Good evaluation material. BASICALLY THERE WAS A GIANT FUCK UP WITH THE GEOCODE ADDRESS TOOL AND IT WAS GUESSING ADDRESSES RATHER THAN STOPPING IMMEDIATELY. VERY IMPORTANT TO WRITE ABOUT THIS IN THE REPORT!

7. In the report, we need to write about the schema of all the tools. Input schema + output schema (Very important)

8. Design notes for streamlit app.
- Streamlit reruns this whole script on every interaction. A long-lived async
  MCP session does not survive that naturally, so we keep one persistent event
  loop + MCP session alive across reruns using st.cache_resource, and run each
  turn on that loop with loop.run_until_complete(...).
- Conversation state (the Anthropic `messages` list and the rendered history)
  lives in st.session_state so it persists across reruns.

