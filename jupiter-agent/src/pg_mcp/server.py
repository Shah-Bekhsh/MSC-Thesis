from mcp.server.fastmcp import FastMCP
import sys
import os
import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
import src.pg_mcp.database as db
from contextlib import asynccontextmanager
from mcp.types import ToolAnnotations
from dotenv import load_dotenv
import httpx

from src.pg_mcp.schemas import (
    GeocodeResult, SupplyPlantRow, CompoundRow, CompoundGroupRow,
    WaterQualityRow, LegalLimitResult, SourceWaterResult,
    WaterLevelResult, CompareResult,
)

from pyproj import Transformer

# WGS84 (lon/lat) -> ETRS89 / UTM zone 32N (EPSG:25832), the CRS Jupiter uses.
# always_xy=True means we pass (lon, lat) and get (easting, northing).
_WGS84_TO_UTM32 = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)


load_dotenv()

pool = None

READONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False,
    idempotentHint=True, openWorldHint=False,
)
READONLY_EXTERNAL = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False,
    idempotentHint=True, openWorldHint=True,
)

@asynccontextmanager
async def lifespan(app):
    global pool
    pool = await db.get_pool()
    yield
    await pool.close()

mcp = FastMCP("jupiter-mcp", lifespan=lifespan)

@mcp.tool(
        annotations=READONLY_EXTERNAL
)
async def geocode_address(address: str) -> GeocodeResult:
    """Convert a Danish address string to UTM32 EUREF89 (EPSG:25832) coordinates.
    Call this whenever the user provides an address instead of coordinates.
    Returns address, x_utm32 (easting), and y_utm32 (northing).
    If the address cannot be found, returns an 'error' field — in that case do
    NOT proceed with guessed coordinates; report that the address could not be located."""
    
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.dataforsyningen.dk/adresser",
            params={"q": address, "format": "json"},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()

    if not data:
        return {"error": f"No address found for: {address}"}

    hit = data[0]
    # DAWA returns WGS84 lon/lat under adgangsadresse.adgangspunkt.koordinater
    try:
        koord = hit["adgangsadresse"]["adgangspunkt"]["koordinater"]
        lon, lat = float(koord[0]), float(koord[1])
    except (KeyError, TypeError, IndexError, ValueError):
        return {
            "error": "Could not extract coordinates from the DAWA response.",
            "raw_keys": list(hit.keys()),
        }

    # Project WGS84 -> UTM32 (EPSG:25832) ourselves, rather than relying on DAWA
    # to return UTM coordinates (which it no longer does in this response shape).
    x, y = _WGS84_TO_UTM32.transform(lon, lat)

    return {
        "address": hit.get("adressebetegnelse", address),
        "x_utm32": round(x, 2),
        "y_utm32": round(y, 2),
        "wgs84_lon": lon,
        "wgs84_lat": lat,
    }


@mcp.tool(
        annotations=READONLY
)
async def find_supply_plant(x_utm32: float, y_utm32: float) -> list[SupplyPlantRow]:
    """Find the drinking water plant(s) supplying a location.
    Input: UTM32 EUREF89 (EPSG:25832) coordinates.
    Always call geocode_address first when the user provides an address.
    Returns list of plants with plantid, name, address, watertype, and active status."""
    query = """
        SELECT wp.plantid, p.plantname, p.plantaddress, p.plantpostalcode,
               p.watertype, p.active
        FROM pcjupiterxlplusviews.wsa w
        JOIN pcjupiterxlplusviews.wsa_plant wp ON wp.wsaid = w.wsaid
        JOIN pcjupiterxlplusviews.drwplant p ON p.plantid = wp.plantid
        WHERE ST_Contains(w.geom, ST_SetSRID(ST_MakePoint($1, $2), 25832))
        AND w.end = 2019
        AND (p.active != 2 OR p.active IS NULL)
    """
    return await db.fetch_all(pool, query, x_utm32, y_utm32)


@mcp.tool(
        annotations=READONLY
)
async def search_compound(name: str) -> list[CompoundRow]:
    """Resolve a chemical name (English or Danish) to Jupiter compound number(s).
    Always call this before any chemistry query — never hardcode compound IDs.
    Compound names in Jupiter are Danish (e.g. 'Nitrat', 'Perfluorerede stoffer').
    Wildcards (%) are added automatically if omitted."""

    if "%" not in name:
        name = f"%{name}%"
    query = """
        SELECT compoundno, long_text, short_text, casno, grwunit, drwunit
        FROM pcjupiterxlplusviews.compoundlist
        WHERE long_text ILIKE $1 OR short_text ILIKE $1
        ORDER BY long_text
        LIMIT 20
    """
    return await db.fetch_all(pool, query, name)

# Common citizen-facing names / acronyms → the Danish group term stored in
# compoundgrouplist.longtext. The database's group names are Danish, but users
# (and the international literature) use acronyms like "PFAS". This small alias
# layer bridges that gap so the tool works regardless of which name is used.
_GROUP_ALIASES = {
    "pfas": "Perfluorerede stoffer",
    "perfluorinated": "Perfluorerede stoffer",
    "pesticides": "Pesticider",
    "pesticide": "Pesticider",
}

@mcp.tool(
        annotations=READONLY
)
async def get_compound_group(group_name: str) -> list[CompoundGroupRow]:
    """Get all compound IDs belonging to a named compound group.
    Use this for any CLASS of substances (PFAS, pesticides, etc.) rather than
    searching for individual compounds. Common English names and acronyms are
    understood — e.g. get_compound_group("PFAS") or get_compound_group("pesticides")
    — and are mapped internally to the Danish group terms in Jupiter, so you do
    not need to supply the Danish name yourself. Wildcards (%) are added
    automatically if omitted."""

    # Resolve known aliases (case-insensitive) before searching.
    key = group_name.strip().lower().strip("%")
    if key in _GROUP_ALIASES:
        group_name = _GROUP_ALIASES[key]

    if "%" not in group_name:
        group_name = f"%{group_name}%"
    query = """
        SELECT c.compoundno, c.long_text, c.short_text
        FROM pcjupiterxlplusviews.compoundlist c
        JOIN pcjupiterxlplusviews.compoundgroup cg ON cg.compoundno = c.compoundno
        JOIN pcjupiterxlplusviews.compoundgrouplist cgl ON cgl.compoundgroupno = cg.compoundgroupno
        WHERE cgl.longtext ILIKE $1
        ORDER BY c.long_text
    """
    return await db.fetch_all(pool, query, group_name)


async def _tap_water_quality(plantid: int, compoundno: int, limit: int = 20) -> list[dict]:
    query = """
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
    """
    return await db.fetch_all(pool, query, plantid, compoundno, limit)


@mcp.tool(
    annotations=READONLY
)
async def get_water_quality(plantid: int, compoundno: int, limit: int = 20) -> list[WaterQualityRow]:
    """Get recent chemistry measurements at a drinking water plant (treated/tap water).
    Applies all Jupiter quality filters (qualitycontrol, samplestatus, project, date).
    Returns measurements with dates, amounts, units, legal limits, and a status flag:
      OK — measurement within legal limit
      EXCEEDS_LIMIT — amount exceeds consumer_max
      NOT_DETECTED — attribute field is non-null (below detection, flagged, or estimated)
      UNIT_MISMATCH — cannot compare due to differing units"""
    
    return await _tap_water_quality(plantid, compoundno, limit)


@mcp.tool(
    annotations=READONLY
)
async def get_legal_limit(compoundno: int) -> LegalLimitResult:
    """Get the official Danish drinking water limit for a compound.
    Returns consumer_min, consumer_max, waterworks_max, and unit.
    Returns an empty dict if no legal limit exists for the compound."""
    query = """
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
    """
    return await db.fetch_one(pool, query, compoundno) or {}

async def _source_water_quality(plantid: int, compoundno: int) -> dict:
    """Source groundwater chemistry for all active-abstraction boreholes feeding a plant.
    Returns per-borehole detail plus a plant-level summary. Active intakes only
    (intakeusage 1=Indvinding, 3=Indvinding og monitering)."""
    query = """
        WITH source_boreholes AS (
            SELECT DISTINCT dpi.boreholeid
            FROM pcjupiterxlplusviews.drwplantintake dpi
            WHERE dpi.plantid = $1
            AND dpi.intakeusage IN (1, 3)
        )
        SELECT
            b.boreholeno,
            s.sampledate,
            a.amount,
            u.longtext AS unit,
            a.attribute,
            a.detectionlimit
        FROM source_boreholes sb
        JOIN pcjupiterxlplusviews.borehole b ON b.boreholeid = sb.boreholeid
        JOIN pcjupiterxlplusviews.grwchemsample s ON s.boreholeid = sb.boreholeid
        JOIN pcjupiterxlplusviews.grwchemanalysis a ON a.sampleid = s.sampleid
        JOIN pcjupiterxlplusviews.code_752 u ON u.code = a.unit
        WHERE a.compoundno = $2
        AND (a.qualitycontrol IS NULL OR a.qualitycontrol NOT IN (4,5,6,8,12,13,14,15))
        AND (s.samplestatus IS NULL OR s.samplestatus IN (2,4,6,8,10,14))
        AND s.sampledate <= NOW()
        ORDER BY b.boreholeno, s.sampledate DESC
    """
    rows = await db.fetch_all(pool, query, plantid, compoundno)

    if not rows:
        return {
            "plantid": plantid,
            "compoundno": compoundno,
            "found": False,
            "note": ("No source-water measurements found for active abstraction "
                     "intakes (intakeusage 1 or 3) feeding this plant. The plant's "
                     "source boreholes may be unmonitored for this compound, or its "
                     "intake linkage may only record decommissioned boreholes."),
        }

    by_borehole: dict[str, list[dict]] = {}
    for r in rows:
        by_borehole.setdefault(r["boreholeno"], []).append(r)

    boreholes_out = []
    numeric_amounts: list[float] = []
    all_dates: list[str] = []
    flagged_count = 0
    unit = None

    for bn, measurements in by_borehole.items():
        latest = measurements[0]  # already sorted desc by date
        if unit is None and latest.get("unit"):
            unit = latest["unit"]
        series = []
        for m in measurements:
            if m["sampledate"]:
                all_dates.append(m["sampledate"])
            if m["attribute"] is None and m["amount"] is not None:
                numeric_amounts.append(m["amount"])
            else:
                flagged_count += 1
            series.append({
                "sampledate": m["sampledate"],
                "amount": m["amount"],
                "unit": m["unit"],
                "attribute": m["attribute"],
                "detectionlimit": m["detectionlimit"],
            })
        boreholes_out.append({
            "boreholeno": bn,
            "n_measurements": len(measurements),
            "latest": {
                "sampledate": latest["sampledate"],
                "amount": latest["amount"],
                "unit": latest["unit"],
                "attribute": latest["attribute"],
            },
            "series": series,
        })

    latest_date = max(all_dates) if all_dates else None
    earliest_date = min(all_dates) if all_dates else None

    age_warning = None
    if latest_date:
        try:
            latest_dt = datetime.datetime.fromisoformat(latest_date)
            years = (datetime.datetime.now() - latest_dt).days / 365.25
            if years > 2:
                age_warning = (
                    f"Most recent source-water measurement is from {latest_date[:10]} "
                    f"(~{years:.0f} years ago). Source groundwater is sampled less often "
                    f"than treated water and may not reflect current source conditions."
                )
        except (ValueError, TypeError):
            pass

    summary = {
        "n_boreholes": len(boreholes_out),
        "n_measurements": len(rows),
        "n_flagged": flagged_count,
        "unit": unit,
        "earliest": earliest_date,
        "latest": latest_date,
        "data_age_warning": age_warning,
    }
    if numeric_amounts:
        summary["min_amount"] = min(numeric_amounts)
        summary["max_amount"] = max(numeric_amounts)
        summary["avg_amount"] = round(sum(numeric_amounts) / len(numeric_amounts), 3)

    return {
        "plantid": plantid,
        "compoundno": compoundno,
        "found": True,
        "summary": summary,
        "per_borehole": boreholes_out,
    }


@mcp.tool(
        annotations=READONLY
)
async def get_source_water_quality(plantid: int, compoundno: int) -> SourceWaterResult:
    """Get the SOURCE groundwater chemistry for the boreholes feeding a drinking water plant.
    This is the raw, untreated water at the abstraction boreholes — distinct from the
    treated tap water returned by get_water_quality.
    Only includes active abstraction intakes (intakeusage 1 or 3); decommissioned
    boreholes are excluded.
    Returns a plant-level summary (min/max/avg across boreholes, date range, and a
    data-age warning if the latest source sample is old) plus per-borehole detail with
    the latest value and full historical series for each borehole."""

    return await _source_water_quality(plantid, compoundno)


async def _water_level(plantid: int) -> WaterLevelResult:
    """Resting groundwater levels at the active abstraction boreholes feeding a plant.
    Reports both elevation (m above mean sea level) and depth below ground surface.
    Uses resting measurements only (situation = 0) and excludes rejected QC rows."""
    query = """
        WITH source_boreholes AS (
            SELECT DISTINCT dpi.boreholeid
            FROM pcjupiterxlplusviews.drwplantintake dpi
            WHERE dpi.plantid = $1 AND dpi.intakeusage IN (1, 3)
        )
        SELECT
            b.boreholeno,
            w.intakeno,
            w.timeofmeas,
            w.watlevmsl,      -- elevation, m above mean sea level (DVR90)
            w.watlevgrsu      -- depth below ground surface, m
        FROM source_boreholes sb
        JOIN pcjupiterxlplusviews.borehole b ON b.boreholeid = sb.boreholeid
        JOIN pcjupiterxlplusviews.watlevel w ON w.boreholeid = sb.boreholeid
        WHERE w.situation = 0
          AND (w.qualitycontrol IS NULL OR w.qualitycontrol NOT IN (4, 5))
          AND w.watlevmsl IS NOT NULL
          AND w.timeofmeas <= NOW()
        ORDER BY b.boreholeno, w.intakeno, w.timeofmeas DESC
    """
    rows = await db.fetch_all(pool, query, plantid)

    if not rows:
        return {
            "plantid": plantid,
            "found": False,
            "note": ("No resting water-level measurements were found for the active "
                     "abstraction boreholes feeding this plant."),
        }

    # Group by (borehole, intake)
    by_key: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["boreholeno"], r["intakeno"])
        by_key.setdefault(key, []).append(r)

    boreholes_out = []
    all_msl: list[float] = []
    all_dates: list[str] = []

    for (bn, intk), measurements in by_key.items():
        latest = measurements[0]  # sorted desc by date
        series = []
        for m in measurements:
            if m["timeofmeas"]:
                all_dates.append(m["timeofmeas"])
            if m["watlevmsl"] is not None:
                all_msl.append(m["watlevmsl"])
            series.append({
                "date": m["timeofmeas"],
                "elevation_m_msl": m["watlevmsl"],
                "depth_below_ground_m": m["watlevgrsu"],
            })
        boreholes_out.append({
            "boreholeno": bn,
            "intakeno": intk,
            "n_measurements": len(measurements),
            "latest": {
                "date": latest["timeofmeas"],
                "elevation_m_msl": latest["watlevmsl"],
                "depth_below_ground_m": latest["watlevgrsu"],
            },
            "series": series,
        })

    latest_date = max(all_dates) if all_dates else None
    earliest_date = min(all_dates) if all_dates else None

    age_warning = None
    if latest_date:
        try:
            latest_dt = datetime.datetime.fromisoformat(latest_date)
            years = (datetime.datetime.now() - latest_dt).days / 365.25
            if years > 2:
                age_warning = (
                    f"Most recent resting water-level measurement is from "
                    f"{latest_date[:10]} (~{years:.0f} years ago)."
                )
        except (ValueError, TypeError):
            pass

    summary = {
        "n_boreholes": len({k[0] for k in by_key}),
        "n_intakes": len(by_key),
        "n_measurements": len(rows),
        "earliest": earliest_date,
        "latest": latest_date,
        "data_age_warning": age_warning,
    }
    if all_msl:
        summary["min_elevation_m_msl"] = min(all_msl)
        summary["max_elevation_m_msl"] = max(all_msl)
        summary["latest_elevation_m_msl"] = boreholes_out[0]["latest"]["elevation_m_msl"]
        summary["elevation_range_m"] = round(max(all_msl) - min(all_msl), 2)

    return {
        "plantid": plantid,
        "found": True,
        "convention_note": (
            "Elevation is metres relative to mean sea level (DVR90); higher = "
            "higher water table. Depth below ground is metres from the terrain "
            "surface down to the water; larger = deeper water table. Resting "
            "measurements only (situation=0); operational/pumped readings excluded."
        ),
        "summary": summary,
        "per_borehole": boreholes_out,
    }


@mcp.tool(
    annotations=READONLY
)
async def get_water_level(plantid: int) -> WaterLevelResult:
    """Get the groundwater LEVEL (water table) at the boreholes feeding a drinking
    water plant. This is the height of the water table, NOT water chemistry.
    Reports each measurement two ways: elevation in metres above mean sea level
    (the standard hydrogeological datum, DVR90), and depth below the ground
    surface in metres. Uses resting/natural measurements only (excludes readings
    taken during pumping) and excludes rejected quality-control records. Returns a
    plant-level summary (range of water-table elevation across boreholes, date
    span, data-age warning) plus per-borehole/per-intake detail with the latest
    value and full historical series. Use this for questions about water level,
    the water table, groundwater head, or how the water table has changed over time."""

    return await _water_level(plantid)


@mcp.tool(
        annotations=READONLY
)
async def compare_source_to_tap(plantid: int, compoundno: int) -> CompareResult:
    """Compare SOURCE groundwater chemistry against TREATED tap water for a plant.
    Returns both datasets side by side along with structured caveats.
    IMPORTANT: this tool deliberately does NOT compute a single treatment-efficiency
    percentage. Source values are per-borehole raw groundwater (often sampled in
    different years), while tap values are the blended, treated plant output. A naive
    subtraction would be scientifically misleading. Present the comparison qualitatively,
    surfacing the caveats to the user."""

    source = await _source_water_quality(plantid, compoundno)
    tap = await _tap_water_quality(plantid, compoundno, limit=20)

    caveats = [
        ("Source values are per-borehole raw groundwater; tap values are the blended, "
         "treated output of the plant. They are not directly subtractable."),
        ("Source groundwater is typically sampled far less frequently than treated water, "
         "so the two datasets may cover different time periods."),
        ("No single treatment-efficiency percentage is provided because date ranges and "
         "blending differ. Interpret the comparison qualitatively."),
    ]

    return {
        "plantid": plantid,
        "compoundno": compoundno,
        "source": source,
        "tap": tap,
        "tap_latest": tap[0]["sampledate"] if tap else None,
        "source_latest": source.get("summary", {}).get("latest") if source.get("found") else None,
        "caveats": caveats,
    }


if __name__ == "__main__":
    mcp.run()
