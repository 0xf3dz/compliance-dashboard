"""Data quality collector and the detectors that need cross-row context.

Row-level fixes (date parse, unit conversion, cost parse) happen inline in
ingest.py. The detectors here need a whole column or a join to decide, so they
live apart. Every finding becomes a row in `data_quality_issues`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ingestion.cleaning import canonical_name

POWER_KEYWORDS = ("substation", "grid supply", "power outage", "power supply",
                  "loss of grid", "generator")

# A reading below 5% of its own meter's maximum is a unit or decimal fault, not
# demand. MTR-07 falls from ~250,000 kWh to ~250 kWh, a factor of 1000.
SCALE_ERROR_RATIO = 0.05

# A site-wide month below 50% of its trailing mean is an outage or a shutdown,
# not seasonal variation. Queensland month-to-month swing stays well inside it.
SITE_DIP_RATIO = 0.5

# Three months is the shortest window that averages out a single odd month
# while still reacting inside one quarter.
SITE_DIP_TRAILING = 3

# Fuel type text maps to an emission factor key by substring, longest-specific
# first. api/src/emissions.ts fuelFactor applies the same rule in the same
# order, so the Python guard and the TypeScript math agree on every input.
FUEL_KEY_ORDER = ("petrol", "diesel")


@dataclass
class QualityLog:
    """Accumulates data_quality_issues rows across the ingest run."""

    rows: list[dict] = field(default_factory=list)

    def add(
        self,
        source_file: str,
        issue_type: str,
        action: str,
        *,
        source_row: int | None = None,
        record_ref: str | None = None,
        field: str | None = None,
        raw_value: str | None = None,
        resolution: str | None = None,
        detail: str | None = None,
    ) -> None:
        assert action in ("fixed", "flagged", "rejected"), action
        self.rows.append(
            {
                "source_file": source_file,
                "source_row": source_row,
                "record_ref": record_ref,
                "issue_type": issue_type,
                "field": field,
                "raw_value": raw_value,
                "action": action,
                "resolution": resolution,
                "detail": detail,
            }
        )

    def counts(self) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for r in self.rows:
            key = (r["source_file"], r["issue_type"])
            out[key] = out.get(key, 0) + 1
        return out


def detect_duplicate_fuel_rows(df: pd.DataFrame) -> list[int]:
    """Return positional indices of exact-duplicate fuel rows to reject.

    Match key: (invoice_no, delivery_date, quantity_l, cost_aud, site_area) on
    the cleaned values. The first occurrence of each key is kept; every later
    identical copy is returned for rejection.
    """
    key_cols = ["invoice_no", "delivery_date", "quantity_l", "cost_aud", "site_area"]
    seen: set[tuple] = set()
    reject: list[int] = []
    for pos, (_, row) in enumerate(df.iterrows()):
        key = tuple(row[c] for c in key_cols)
        if key in seen:
            reject.append(pos)
        else:
            seen.add(key)
    return reject


def detect_scale_errors(
    elec_df: pd.DataFrame, ratio: float = SCALE_ERROR_RATIO
) -> list[int]:
    """Return positional indices of readings that collapse ~1000x.

    Per meter, flag any reading below max(meter) * ratio. Catches MTR-07, which
    drops from ~250,000 kWh to ~250 kWh from 2025-10 onward.
    """
    flagged: list[int] = []
    elec_df = elec_df.reset_index(drop=True)
    for _meter_id, grp in elec_df.groupby("meter_id"):
        threshold = grp["consumption_kwh"].max() * ratio
        for pos in grp.index:
            if elec_df.at[pos, "consumption_kwh"] < threshold:
                flagged.append(int(pos))
    return sorted(flagged)


@dataclass
class SiteDip:
    period: date
    site_total: float
    trailing_mean: float
    resolved_incident: str | None
    detail: str


def detect_site_wide_dip(
    elec_df: pd.DataFrame,
    incidents_df: pd.DataFrame,
    drop_ratio: float = SITE_DIP_RATIO,
    trailing: int = SITE_DIP_TRAILING,
) -> list[SiteDip]:
    """Flag months where site-wide consumption drops below drop_ratio of the
    trailing mean, then try to resolve each with a power incident that month.
    """
    totals = (
        elec_df.groupby("period")["consumption_kwh"].sum().sort_index()
    )
    periods = list(totals.index)
    out: list[SiteDip] = []
    for i, period in enumerate(periods):
        if i < trailing:
            continue
        window = totals.iloc[i - trailing:i]
        mean = float(window.mean())
        total = float(totals.iloc[i])
        if mean > 0 and total < mean * drop_ratio:
            incident = _find_power_incident(incidents_df, period)
            if incident is not None:
                detail = (
                    f"Site consumption {total:,.0f} kWh vs trailing "
                    f"{trailing}-month mean {mean:,.0f} kWh "
                    f"({total / mean:.0%}). Explained by incident {incident}."
                )
            else:
                detail = (
                    f"Site consumption {total:,.0f} kWh vs trailing "
                    f"{trailing}-month mean {mean:,.0f} kWh "
                    f"({total / mean:.0%}). No power incident found this month."
                )
            out.append(SiteDip(period, total, mean, incident, detail))
    return out


def _find_power_incident(incidents_df: pd.DataFrame, period: date) -> str | None:
    """Return the incident_id of a power/electrical incident in `period`'s
    month, or None."""
    for _, row in incidents_df.iterrows():
        d = row["incident_date"]
        if d is None or pd.isna(d):
            continue
        if d.year != period.year or d.month != period.month:
            continue
        text = f"{row.get('type_code', '')} {row.get('description', '')}".lower()
        if row.get("type_code") == "ELE" or any(k in text for k in POWER_KEYWORDS):
            return row["incident_id"]
    return None


@dataclass
class DescriptionGroup:
    """Incidents that share one exact description string."""

    description: str
    incident_ids: list[str]
    source_rows: list[int]
    severity_raws: list[str]


def _group_by_description(incidents_df: pd.DataFrame) -> list[DescriptionGroup]:
    """Group cleaned incident rows on the exact description text.

    Grouping is exact, not fuzzy. The register recycles whole boilerplate
    sentences, so an exact match is enough and it cannot produce a false pair.
    """
    df = incidents_df.reset_index(drop=True)
    buckets: dict[str, DescriptionGroup] = {}
    for _, row in df.iterrows():
        text = str(row["description"]).strip()
        if text == "":
            continue
        group = buckets.get(text)
        if group is None:
            group = DescriptionGroup(text, [], [], [])
            buckets[text] = group
        group.incident_ids.append(str(row["incident_id"]))
        group.source_rows.append(int(row["source_row"]))
        group.severity_raws.append(str(row["severity_raw"]))
    return sorted(buckets.values(), key=lambda g: min(g.source_rows))


def detect_recycled_descriptions(incidents_df: pd.DataFrame) -> list[DescriptionGroup]:
    """Groups of 2+ incidents that share one description string.

    Recycled text means the description cannot be read as a record of what
    happened on that day, so any conclusion drawn from it is weaker than it
    looks. The AI layer classifies the text, so the caveat travels with it.
    """
    return [g for g in _group_by_description(incidents_df) if len(g.incident_ids) > 1]


def detect_severity_conflicts(incidents_df: pd.DataFrame) -> list[DescriptionGroup]:
    """Groups where one description carries more than one severity_rank.

    Identical words cannot describe two different severities. One of the two
    recorded ranks is wrong, and plain SQL finds it without an LLM.
    """
    df = incidents_df.reset_index(drop=True)
    ranks: dict[str, set[int]] = {}
    for _, row in df.iterrows():
        text = str(row["description"]).strip()
        rank = row["severity_rank"]
        if text == "" or rank is None or pd.isna(rank):
            continue
        ranks.setdefault(text, set()).add(int(rank))
    return [
        g
        for g in _group_by_description(df)
        if len(ranks.get(g.description, set())) > 1
    ]


def _month_starts(start: date, end: date) -> list[date]:
    """Every month start in [start, end], inclusive of both endpoints."""
    out: list[date] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(date(year, month, 1))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def detect_missing_fuel_months(
    fuel_df: pd.DataFrame, start: date, end: date
) -> list[date]:
    """Months in [start, end] with no fuel delivery at all.

    A month with no delivery is not a month with no combustion. Reporting its
    Scope 1 as zero states a wrong compliance figure as fact, so the month is
    logged and the API carries it to the consumer as a lower bound.
    """
    present: set[tuple[int, int]] = set()
    for _, row in fuel_df.iterrows():
        d = row["delivery_date"]
        if d is None or pd.isna(d):
            continue
        present.add((d.year, d.month))
    return [m for m in _month_starts(start, end) if (m.year, m.month) not in present]


def fuel_key(fuel_type: str) -> str | None:
    """Map a raw fuel type string to an emission factor key, or None."""
    text = fuel_type.lower()
    for key in FUEL_KEY_ORDER:
        if key in text:
            return key
    return None


def detect_unknown_fuel_types(fuel_df: pd.DataFrame, known: set[str]) -> list[int]:
    """Positional indices whose fuel type maps to no emission factor.

    The source holds only Diesel and Petrol (ULP), so this returns nothing
    today. It exists so a future file cannot add a fuel that silently emits
    zero: the row is rejected and logged instead.
    """
    out: list[int] = []
    for pos, (_, row) in enumerate(fuel_df.iterrows()):
        if fuel_key(str(row["fuel_type"])) not in known:
            out.append(pos)
    return out


@dataclass
class SupplierGroup:
    canonical_pos: int
    member_pos: list[int]
    reason: str


def detect_supplier_duplicates(df: pd.DataFrame) -> list[SupplierGroup]:
    """Group near-duplicate suppliers and pick a canonical row per group.

    Rows link if they share a canonical name key or a well-formed 11-digit ABN.
    Canonical selection: prefer a well-formed ABN, then the highest annual
    spend, then the earliest row.
    """
    n = len(df)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    by_name: dict[str, int] = {}
    by_abn: dict[str, int] = {}
    df = df.reset_index(drop=True)
    for pos, row in df.iterrows():
        key = canonical_name(str(row["supplier_name"]))
        if key in by_name:
            union(pos, by_name[key])
        else:
            by_name[key] = pos
        abn = row["abn_digits"]
        if abn and row["abn_well_formed"]:
            if abn in by_abn:
                union(pos, by_abn[abn])
            else:
                by_abn[abn] = pos

    groups: dict[int, list[int]] = {}
    for pos in range(n):
        groups.setdefault(find(pos), []).append(pos)

    out: list[SupplierGroup] = []
    for members in groups.values():
        if len(members) < 2:
            continue

        def rank(pos: int) -> tuple:
            row = df.iloc[pos]
            spend = row["fy_spend_aud"] if pd.notna(row["fy_spend_aud"]) else 0
            return (1 if row["abn_well_formed"] else 0, float(spend), -pos)

        canonical = max(members, key=rank)
        names = ", ".join(sorted(str(df.iloc[p]["supplier_name"]) for p in members))
        out.append(
            SupplierGroup(
                canonical_pos=canonical,
                member_pos=sorted(members),
                reason=f"Grouped as one entity: {names}.",
            )
        )
    return out


def detect_supplier_category_conflicts(
    df: pd.DataFrame, groups: list[SupplierGroup]
) -> list[SupplierGroup]:
    """Groups whose members disagree on category.

    One entity cannot sit in two spend categories at once. Left alone, the two
    Ironline rows split one supplier's spend across `Fuel supply` and `Fuel`,
    which understates both.
    """
    df = df.reset_index(drop=True)
    out: list[SupplierGroup] = []
    for group in groups:
        categories = {
            str(df.iloc[p]["category"]).strip()
            for p in group.member_pos
            if str(df.iloc[p]["category"]).strip() != ""
        }
        if len(categories) > 1:
            out.append(group)
    return out
