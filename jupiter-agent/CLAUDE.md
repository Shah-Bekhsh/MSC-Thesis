# Jupiter Agent — Project Briefing for Claude Code

## What this project is

MSc thesis project at DTU (Technical University of Denmark) by Shah Bekhsh.
An LLM-powered agent that answers citizen questions about Danish drinking water
quality using the Jupiter database (GEUS — Geological Survey of Denmark and Greenland).

The system uses the Model Context Protocol (MCP). The agent calls MCP tools
that query a real PostgreSQL database instead of hallucinating answers.

## What has already been built

### Toy project (proof of concept) — COMPLETE
Located in this repo. A working MCP server + client against a demo e-commerce DB.
- `src/pg_mcp/database.py` — async PostgreSQL layer (asyncpg)
- `src/pg_mcp/server.py` — FastMCP server with list_tables, describe_table, run_query tools
- `src/pg_mcp/client.py` — MCP client with Ollama tool-calling loop

This establishes the architecture pattern. The real project adapts these files
to point at JupiterXL and adds domain-specific tools.

### Database — COMPLETE
JupiterXL PostgreSQL database is fully restored and running locally on WSL2.

```
Host:     localhost
Port:     5432
Database: jupiterxl
User:     pgmcp
Password: pgmcp_dev
Schema:   pcjupiterxlplusviews
```

Start PostgreSQL: `sudo service postgresql start`

PostGIS 3.4 is installed and enabled in jupiterxl.

Two external tables have been loaded:
- `pcjupiterxlplusviews.wsa` — 3,694 Water Supply Area polygons (EPSG:25832)
- `pcjupiterxlplusviews.wsa_plant` — 7,422 rows linking WSAID to Jupiter PLANTID

These come from Schullehner (2022), doi:10.34194/geusb.v49.8319.
They are the missing link between a user's location and their waterworks.

## Core architecture (what to build next)

```
User message
    → LLM Agent (Claude claude-sonnet-4-20250514 or similar)
        → MCP Client
            → MCP Server (src/pg_mcp/server.py — needs rewriting)
                → PostgreSQL JupiterXL
    → Response to user
```

## The six MCP tools to implement

These are the tools the agent needs. Implement them in server.py:

### 1. geocode_address(address: str) -> dict
Convert a Danish address string to UTM32 EUREF89 (EPSG:25832) coordinates.
Use the DAWA API: https://api.dataforsyningen.dk/adresser?q={address}&format=json
Extract etrs89koordinat.øst (x) and etrs89koordinat.nord (y).

### 2. find_supply_plant(x_utm32: float, y_utm32: float) -> list[dict]
Point-in-polygon lookup using WSA polygons.
Returns the drinking water plant(s) supplying that location.

```sql
SELECT wp.plantid, p.plantname, p.plantaddress, p.plantpostalcode,
       p.watertype, p.active
FROM pcjupiterxlplusviews.wsa w
JOIN pcjupiterxlplusviews.wsa_plant wp ON wp.wsaid = w.wsaid
JOIN pcjupiterxlplusviews.drwplant p ON p.plantid = wp.plantid
WHERE ST_Contains(w.geom, ST_SetSRID(ST_MakePoint($1, $2), 25832))
AND w.end = 2019
AND (p.active != 2 OR p.active IS NULL)
```

### 3. search_compound(name: str) -> list[dict]
Resolve a chemical name (English or Danish) to Jupiter compound number(s).
Always call this before any chemistry query — never hardcode compound IDs.

```sql
SELECT compoundno, long_text, short_text, casno, grwunit, drwunit
FROM pcjupiterxlplusviews.compoundlist
WHERE long_text ILIKE $1 OR short_text ILIKE $1
ORDER BY long_text
LIMIT 20
```

Call with '%nitrat%' style patterns. The agent should pass the name with
wildcards or the tool should add them.

### 4. get_compound_group(group_name: str) -> list[dict]
Get all compound IDs in a named group. Critical for PFAS queries.

```sql
SELECT c.compoundno, c.long_text, c.short_text
FROM pcjupiterxlplusviews.compoundlist c
JOIN pcjupiterxlplusviews.compoundgroup cg ON cg.compoundno = c.compoundno
JOIN pcjupiterxlplusviews.compoundgrouplist cgl ON cgl.compoundgroupno = cg.compoundgroupno
WHERE cgl.longtext ILIKE $1
ORDER BY c.long_text
```

Key group: 'Perfluorerede stoffer' = PFAS (compoundgroupno = 110)

### 5. get_water_quality(plantid: int, compoundno: int, limit: int = 20) -> list[dict]
Get recent chemistry measurements at a drinking water plant.
This is the core query — must apply all quality filters.

```sql
SELECT
    p.plantname, p.plantaddress,
    s.sampledate,
    a.amount,
    u.longtext AS unit,
    c.long_text AS compound,
    a.attribute,
    a.detectionlimit,
    l.consumer_max AS legal_limit,
    lu.longtext AS limit_unit,
    CASE
        WHEN a.attribute IS NOT NULL THEN 'NOT_DETECTED'
        WHEN l.consumer_max IS NOT NULL AND a.unit = l.unit
             AND a.amount > l.consumer_max THEN 'EXCEEDS_LIMIT'
        WHEN l.consumer_max IS NOT NULL AND a.unit != l.unit THEN 'UNIT_MISMATCH'
        ELSE 'OK'
    END AS status
FROM pcjupiterxlplusviews.drwplant p
JOIN pcjupiterxlplusviews.pltchemsample s ON s.plantid = p.plantid
JOIN pcjupiterxlplusviews.pltchemanalysis a ON a.sampleid = s.sampleid
JOIN pcjupiterxlplusviews.compoundlist c ON c.compoundno = a.compoundno
JOIN pcjupiterxlplusviews.code_752 u ON u.code = a.unit
LEFT JOIN pcjupiterxlplusviews.limitlist l ON l.compoundno = a.compoundno
LEFT JOIN pcjupiterxlplusviews.code_752 lu ON lu.code = l.unit
WHERE p.plantid = $1
AND a.compoundno = $2
AND (a.qualitycontrol IS NULL OR a.qualitycontrol NOT IN (4,5,6,8,12,13,14,15))
AND (s.samplestatus IS NULL OR s.samplestatus IN (2,4,6,8,10,14))
AND (s.project IS NULL OR s.project = 'DRV')
AND s.sampledate <= NOW()
ORDER BY s.sampledate DESC
LIMIT $3
```

### 6. get_legal_limit(compoundno: int) -> dict
Get the official Danish drinking water limit for a compound.

```sql
SELECT
    c.long_text AS compound,
    l.consumer_min,
    l.consumer_max,
    l.waterworks_max,
    u.longtext AS unit
FROM pcjupiterxlplusviews.limitlist l
JOIN pcjupiterxlplusviews.compoundlist c ON c.compoundno = l.compoundno
JOIN pcjupiterxlplusviews.code_752 u ON u.code = l.unit
WHERE l.compoundno = $1
```

## Key database facts (do not re-discover these)

### Schema
All tables are in schema `pcjupiterxlplusviews`. Every query needs this prefix.

### Critical quality filters (MUST apply to all chemistry queries)
```python
# Exclude rejected analyses
qualitycontrol NOT IN (4,5,6,8,12,13,14,15)  # or IS NULL

# Only approved samples (NULL = pre-2007 reform, also valid)
samplestatus IN (2,4,6,8,10,14)  # or IS NULL

# Drinking water project code
project = 'DRV'  # or IS NULL

# Exclude future-dated records (4 exist, data entry errors)
sampledate <= NOW()
```

### The attribute field (CRITICAL)
In grwchemanalysis and pltchemanalysis, a non-null `attribute` means
the measurement is special: '<' = below detection, '>' = above range,
'!' = rejected, 'A' = estimated. NEVER compare these to legal limits.
Always check attribute IS NULL before flagging exceedances.

### Unit system
- `grwchemanalysis.unit` and `pltchemanalysis.unit` are numeric FKs → `code_752.code`
- `code_752` contains longtext like 'mg/l', 'µg/l', 'pH'
- Join: `JOIN pcjupiterxlplusviews.code_752 u ON u.code = a.unit`
- Both columns are numeric — no casting needed

### Coordinate system
- All spatial data (WSA polygons, drwplant, borehole) uses EPSG:25832 (UTM32 ETRS89)
- User addresses geocoded via DAWA API come back in ETRS89 coordinates
- ST_SetSRID(ST_MakePoint(x, y), 25832) for point-in-polygon queries

### Key compound IDs (verified)
- Nitrat: compoundno=246, consumer limit=50 mg/l
- Nitrat-N: compoundno=247, consumer limit=11.289 mg/l
- PFAS group: compoundgroupno=110, longtext='Perfluorerede stoffer'
  - Most individual PFAS: consumer limit=0.1 µg/l
  - PFOA+PFOS+PFNA+PFHxS sum: consumer limit=0.002 µg/l

### Municipality lookup
DO NOT use borehole.municipal (free text, pre-2007 names, unreliable).
USE borhtownno2007 joined to code_808 for current municipality names.

### WSA coverage limitation
Supply zone data covers 1978-2019. Use w.end = 2019 for current queries.
Not officially authoritative — disclose this to users.

## Spatial index (add if not present)
```sql
CREATE INDEX IF NOT EXISTS wsa_geom_idx
ON pcjupiterxlplusviews.wsa USING GIST(geom);
```

## Agent system prompt (summary)

The agent is a water quality assistant for Danish citizens. Full system prompt
is in `docs/system_prompt.md` (copy from the Word doc if needed).

Key rules encoded in the system prompt:
1. Only answer questions about Danish water/groundwater
2. Never state specific measurements without querying the database
3. Always call search_compound before chemistry queries
4. Always check attribute field before flagging limit exceedances
5. Disclose data provenance and WSA limitations in every response
6. No medical or legal advice — report facts and refer to professionals
7. Respond in the user's language (Danish or English)
8. Decline out-of-scope questions politely with suggested alternatives

## Scope of the system

The agent answers any question it can using its tools + general knowledge.
It is NOT a hardcoded Q&A system. Tools are capabilities the agent invokes
when relevant — not scripts for specific questions.

Hard decline: questions about other countries, unrelated topics.
Soft decline: questions where data doesn't exist, explain why + suggest contact.
General knowledge: chemistry/science/regulation questions answered from LLM knowledge,
clearly labelled as such when it matters.

## What to build next (in order)

1. Add spatial index on wsa.geom (one SQL command)
2. Rewrite src/pg_mcp/server.py with the 6 tools above
3. Update src/pg_mcp/database.py — add PostGIS support, schema prefix
4. Update .env to point at jupiterxl database
5. Test each tool individually with mcp dev
6. Update src/pg_mcp/client.py — swap Ollama for Claude API, inject system prompt
7. End-to-end test with the 12 guardrail test cases in docs/

## Tech stack (same as toy project, additions noted)

```toml
dependencies = [
    "asyncpg>=0.31.0",
    "mcp[cli]>=1.26.0",
    "anthropic>=0.25.0",   # NEW — replace ollama
    "python-dotenv>=1.2.2",
    "httpx>=0.27.0",        # NEW — for DAWA geocoding API
]
```

## File structure to build toward

```
jupiter-agent/
├── CLAUDE.md               # this file
├── .env                    # DB + API config (never commit)
├── pyproject.toml
├── docs/
│   ├── system_prompt.md    # full system prompt text
│   └── technical_reference.md  # Jupiter schema notes
└── src/
    └── jupiter_agent/
        ├── __init__.py
        ├── database.py     # PostgreSQL + PostGIS async layer
        ├── server.py       # MCP server — 6 tools
        └── client.py       # MCP client — Claude tool-calling loop
```

## References

- Jupiter database: https://www.geus.dk/produkter-ydelser-og-faciliteter/data-og-kort/national-boringsdatabase-jupiter
- GEUS data portal: https://data.geus.dk
- WSA dataset: Schullehner (2022), https://doi.org/10.34194/geusb.v49.8319
- DAWA geocoding API: https://dawadocs.dataforsyningen.dk
- MCP documentation: https://modelcontextprotocol.io
- Code lookup tables: https://data.geus.dk/tabellerkoder
