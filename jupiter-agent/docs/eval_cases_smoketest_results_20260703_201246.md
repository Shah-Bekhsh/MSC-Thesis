# Eval results — ollama-smoketest

- Backend: **ollama** · model: `llama3.1:8b`
- Timestamp: 20260703_201246
- Runs per case: 1

Mark each case PASS / PARTIAL / FAIL by hand after reading the runs.

## SMOKE-01 — functional
**Prompt:** What is the nitrate level in the drinking water at Vesterbrogade 1, København?

**Expected:** Should call geocode_address, then find_supply_plant, then search_compound, then get_water_quality (order may vary), and return nitrate values. The point of the smoke test is whether tools_called is non-empty and sensible, NOT whether the final numbers are perfect.

### Run 1  ·  tools: ['geocode_address', 'find_supply_plant']
> usage: 3 API calls · in 9451 · out 152 · cache_read 0 · cache_create 0

The location is in the supply zone of multiple waterworks, but I will continue with the first plant that supplies this area.

{"name": "search_compound", "parameters":{"name":"Nitrat"}}

**Verdict:** _____ (PASS / PARTIAL / FAIL)

---
