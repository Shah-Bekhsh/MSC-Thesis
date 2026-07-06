"""
Verify that the TypedDict output-schema changes did NOT alter runtime behavior.

This calls each tool function DIRECTLY against the database — no LLM, no Claude
API, zero credits spent. TypedDicts are annotation-only, so every tool should
return exactly what it returned before the change. This script just lets you see
that the tools still execute and produce well-formed output.

Run from the repo root:

    uv run python verify_tools_direct.py

It exercises all nine tools (the smoke test only ever hits ~4), using the known
Frederiksberg / Vesterbrogade anchors from the evaluation. Adjust the anchor
constants if your DB uses different demo values.
"""

import asyncio
import json
import src.pg_mcp.database as db
import src.pg_mcp.server as server


# Known-good anchors from the evaluation work.
ADDRESS = "Vesterbrogade 1, København"
PLANTID = 44357          # Frederiksberg Vandværk
NITRATE = 246            # compound number for nitrate


def show(label, result):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    # Truncate long outputs so the console stays readable.
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if len(text) > 2000:
        text = text[:2000] + f"\n... [truncated, {len(text)} chars total]"
    print(text)


async def main():
    # Bring up the pool exactly as the server's lifespan would.
    server.pool = await db.get_pool()
    try:
        # 1. geocode (external API + projection)
        geo = await server.geocode_address(ADDRESS)
        show("geocode_address", geo)

        # 2. find_supply_plant (uses geocoded coords if available)
        if "x_utm32" in geo:
            plants = await server.find_supply_plant(geo["x_utm32"], geo["y_utm32"])
            show("find_supply_plant", plants)

        # 3. search_compound
        comp = await server.search_compound("Nitrat")
        show("search_compound", comp)

        # 4. get_compound_group
        grp = await server.get_compound_group("PFAS")
        show("get_compound_group", grp)

        # 5. get_water_quality (tap)
        wq = await server.get_water_quality(PLANTID, NITRATE)
        show("get_water_quality", wq)

        # 6. get_legal_limit  (also tests the `or {}` empty path if you pass a
        #    compound with no limit)
        ll = await server.get_legal_limit(NITRATE)
        show("get_legal_limit", ll)

        # 7. get_source_water_quality (found/not-found union)
        swq = await server.get_source_water_quality(PLANTID, NITRATE)
        show("get_source_water_quality", swq)

        # 8. get_water_level (found/not-found union)
        wl = await server.get_water_level(PLANTID)
        show("get_water_level", wl)

        # 9. compare_source_to_tap (nested)
        cmp = await server.compare_source_to_tap(PLANTID, NITRATE)
        show("compare_source_to_tap", cmp)

        print(f"\n{'='*70}\nAll nine tools executed. "
              f"Compare these outputs to pre-change samples to confirm they match.\n{'='*70}")
    finally:
        await server.pool.close()


if __name__ == "__main__":
    asyncio.run(main())
