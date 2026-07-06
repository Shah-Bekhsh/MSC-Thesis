"""
Typed output schemas for the Jupiter MCP tools.

These TypedDicts declare the *shape* of each tool's return value so that the
FastMCP server can expose a formal JSON output schema for every tool (not just
the four that happened to return `list[dict]`). They are annotation-only: a
TypedDict has NO runtime effect — the tools return the exact same dictionaries
they always did — so adding these does not change behavior, does not touch the
system prompt, and does not invalidate any evaluation run.

Type conventions follow database.py::_serialize_value, which normalizes rows at
the DB boundary:
  - datetime.date / datetime.datetime  -> ISO-8601 str
  - decimal.Decimal                    -> float
Everything below therefore uses JSON-native types (str, float, int, bool, None).

Where a tool can return more than one shape (a success payload OR an error /
not-found payload), that is expressed honestly as a Union. Where a summary
contains keys that are only present under some conditions, those keys are marked
NotRequired rather than pretended to be always present.
"""

from __future__ import annotations
from typing import TypedDict, NotRequired


# ---------------------------------------------------------------------------
# geocode_address
# ---------------------------------------------------------------------------

class GeocodeSuccess(TypedDict):
    address: str
    x_utm32: float
    y_utm32: float
    wgs84_lon: float
    wgs84_lat: float


class GeocodeError(TypedDict):
    error: str
    raw_keys: NotRequired[list[str]]  # present only on the DAWA-parse failure path


GeocodeResult = GeocodeSuccess | GeocodeError


# ---------------------------------------------------------------------------
# find_supply_plant  (returns a list of these rows)
# ---------------------------------------------------------------------------

class SupplyPlantRow(TypedDict):
    plantid: float           # DB numeric -> float (e.g. 28315.0)
    plantname: str | None
    plantaddress: str | None
    plantpostalcode: float | None   # DB numeric -> float
    watertype: str | None
    active: float | None            # DB numeric -> float (1.0 / 2.0)


# ---------------------------------------------------------------------------
# search_compound  (returns a list of these rows)
# ---------------------------------------------------------------------------

class CompoundRow(TypedDict):
    compoundno: float            # DB numeric -> float (e.g. 246.0)
    long_text: str | None
    short_text: str | None
    casno: float | None          # DB numeric -> float
    grwunit: float | None        # DB numeric -> float (unit code)
    drwunit: float | None        # DB numeric -> float (unit code)


# ---------------------------------------------------------------------------
# get_compound_group  (returns a list of these rows)
# ---------------------------------------------------------------------------

class CompoundGroupRow(TypedDict):
    compoundno: float            # DB numeric -> float
    long_text: str | None
    short_text: str | None


# ---------------------------------------------------------------------------
# get_water_quality  (returns a list of these rows)
# ---------------------------------------------------------------------------

class WaterQualityRow(TypedDict):
    plantname: str | None
    plantaddress: str | None
    sampledate: str | None        # ISO date string
    amount: float | None
    unit: str | None
    compound: str | None
    attribute: str | None
    detectionlimit: float | None
    legal_limit: float | None
    limit_unit: str | None
    status: str                   # OK | EXCEEDS_LIMIT | NOT_DETECTED | UNIT_MISMATCH


# ---------------------------------------------------------------------------
# get_legal_limit  (single dict, or {} when no limit exists)
# ---------------------------------------------------------------------------

class LegalLimit(TypedDict):
    compound: str | None
    consumer_min: float | None
    consumer_max: float | None
    waterworks_max: float | None
    unit: str | None


# The tool returns `... or {}`, i.e. the row above OR an empty dict. Expressed
# honestly, every key may be absent:
class LegalLimitResult(TypedDict, total=False):
    compound: str | None
    consumer_min: float | None
    consumer_max: float | None
    waterworks_max: float | None
    unit: str | None


# ---------------------------------------------------------------------------
# get_source_water_quality
# ---------------------------------------------------------------------------

class SourceMeasurement(TypedDict):
    sampledate: str | None
    amount: float | None
    unit: str | None
    attribute: str | None
    detectionlimit: float | None


class SourceLatest(TypedDict):
    sampledate: str | None
    amount: float | None
    unit: str | None
    attribute: str | None


class SourceBorehole(TypedDict):
    boreholeno: str
    n_measurements: int
    latest: SourceLatest
    series: list[SourceMeasurement]


class SourceSummary(TypedDict):
    n_boreholes: int
    n_measurements: int
    n_flagged: int
    unit: str | None
    earliest: str | None
    latest: str | None
    data_age_warning: str | None
    # present only when there is at least one numeric (non-flagged) amount:
    min_amount: NotRequired[float]
    max_amount: NotRequired[float]
    avg_amount: NotRequired[float]


class SourceWaterFound(TypedDict):
    plantid: int
    compoundno: int
    found: bool                   # True on this branch
    summary: SourceSummary
    per_borehole: list[SourceBorehole]


class SourceWaterNotFound(TypedDict):
    plantid: int
    compoundno: int
    found: bool                   # False on this branch
    note: str


SourceWaterResult = SourceWaterFound | SourceWaterNotFound


# ---------------------------------------------------------------------------
# get_water_level
# ---------------------------------------------------------------------------

class WaterLevelMeasurement(TypedDict):
    date: str | None
    elevation_m_msl: float | None
    depth_below_ground_m: float | None


class WaterLevelBorehole(TypedDict):
    boreholeno: str
    intakeno: float | None        # DB numeric -> float (e.g. 1.0)
    n_measurements: int           # computed via len() -> genuine int
    latest: WaterLevelMeasurement
    series: list[WaterLevelMeasurement]


class WaterLevelSummary(TypedDict):
    n_boreholes: int
    n_intakes: int
    n_measurements: int
    earliest: str | None
    latest: str | None
    data_age_warning: str | None
    # present only when at least one elevation value exists:
    min_elevation_m_msl: NotRequired[float]
    max_elevation_m_msl: NotRequired[float]
    latest_elevation_m_msl: NotRequired[float | None]
    elevation_range_m: NotRequired[float]


class WaterLevelFound(TypedDict):
    plantid: int
    found: bool                   # True
    convention_note: str
    summary: WaterLevelSummary
    per_borehole: list[WaterLevelBorehole]


class WaterLevelNotFound(TypedDict):
    plantid: int
    found: bool                   # False
    note: str


WaterLevelResult = WaterLevelFound | WaterLevelNotFound


# ---------------------------------------------------------------------------
# compare_source_to_tap  (nested: embeds source result + tap rows)
# ---------------------------------------------------------------------------

class CompareResult(TypedDict):
    plantid: int
    compoundno: int
    source: SourceWaterResult
    tap: list[WaterQualityRow]
    tap_latest: str | None
    source_latest: str | None
    caveats: list[str]