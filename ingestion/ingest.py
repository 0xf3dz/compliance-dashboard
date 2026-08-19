"""Ingestion pipeline: read the raw CSVs, clean and normalise them, run the
quality detectors, and load everything into PostgreSQL.

Run:  python -m ingestion.ingest

Idempotent: the source-data schema is dropped and recreated on every run.
incident_ai_findings is not, so AI findings survive a re-ingest.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pandas as pd

from ingestion import cleaning
from ingestion.db import apply_ai_schema, apply_schema, get_conn
from ingestion.quality import (
    QualityLog,
    detect_duplicate_fuel_rows,
    detect_missing_fuel_months,
    detect_recycled_descriptions,
    detect_scale_errors,
    detect_severity_conflicts,
    detect_site_wide_dip,
    detect_supplier_category_conflicts,
    detect_supplier_duplicates,
    detect_unknown_fuel_types,
    fuel_key,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

FUEL = "fuel_deliveries.csv"
ELEC = "electricity_meter_readings.csv"
INCIDENTS = "incident_register.csv"
SUPPLIERS = "suppliers.csv"
FACTORS = "emission_factors.csv"

# The reporting window the client supplied, 18 months inclusive. A month in
# this range with no fuel row is a gap in the data, not a month with no
# combustion.
WINDOW_START = date(2025, 1, 1)
WINDOW_END = date(2026, 6, 1)


def _read_csv(name: str) -> pd.DataFrame:
    df = pd.read_csv(
        DATA_DIR / name, encoding="utf-8-sig", dtype=str, keep_default_na=False
    )
    df.columns = [c.strip() for c in df.columns]  # strip stray header spaces
    return df


# --------------------------------------------------------------------------
# emission factors (loaded verbatim)
# --------------------------------------------------------------------------
def load_emission_factors(conn) -> tuple[int, set[str]]:
    """Load the factor table and return the Scope 1 fuel keys it defines.

    The returned set is what load_fuel checks a delivery against, so a fuel
    type with no factor is rejected instead of emitting zero.
    """
    df = _read_csv(FACTORS)
    rows = []
    known_fuel_keys: set[str] = set()
    for _, r in df.iterrows():
        scope = int(r["scope"])
        unit = r["unit"].strip()
        rows.append(
            (r["activity"], scope, unit, float(r["kg_co2e_per_unit"]), r["source"])
        )
        if scope == 1 and unit.upper() == "L":
            key = fuel_key(r["activity"])
            if key is not None:
                known_fuel_keys.add(key)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO emission_factors (activity, scope, unit, kg_co2e_per_unit, source)"
            " VALUES (%s, %s, %s, %s, %s)",
            rows,
        )
    return len(rows), known_fuel_keys


# --------------------------------------------------------------------------
# fuel deliveries
# --------------------------------------------------------------------------
def load_fuel(conn, log: QualityLog, known_fuel_keys: set[str]) -> int:
    df = _read_csv(FUEL)
    records = []
    for i, r in df.iterrows():
        source_row = i + 1
        d, imputed = cleaning.parse_delivery_date(r["Delivery Date"])
        unit = r["Unit"].strip()
        qty_l = cleaning.normalise_quantity(r["Quantity"], unit)
        cost = cleaning.parse_cost(r["Cost (AUD)"])
        is_credit = qty_l < 0
        records.append(
            {
                "invoice_no": r["Invoice No"].strip(),
                "delivery_date": d,
                "date_imputed": imputed,
                "fuel_type": r["Fuel Type"].strip(),
                "quantity_l": qty_l,
                "cost_aud": cost,
                "site_area": r["Site Area"].strip(),
                "is_credit": is_credit,
                "source_row": source_row,
                "_raw_date": r["Delivery Date"].strip(),
                "_raw_unit": unit,
                "_raw_qty": r["Quantity"].strip(),
                "_imputed": imputed,
                "_kl": unit.lower() == "kl",
                "_credit": is_credit,
            }
        )

    clean_df = pd.DataFrame(records)
    duplicate_pos = set(detect_duplicate_fuel_rows(clean_df))
    unknown_pos = set(detect_unknown_fuel_types(clean_df, known_fuel_keys))

    # A month with no delivery is not a month with zero Scope 1. Log it so the
    # API can carry it to the dashboard as a lower bound.
    for month in detect_missing_fuel_months(clean_df, WINDOW_START, WINDOW_END):
        log.add(
            FUEL,
            "missing_fuel_month",
            "flagged",
            record_ref=month.strftime("%Y-%m"),
            field="delivery_date",
            raw_value="0 deliveries",
            resolution=f"Scope 1 for {month:%Y-%m} is a lower bound, not zero.",
            detail="No fuel delivery at all in this month, while every other "
            "month in the 18-month window has one. Fuel was still burnt; the "
            "invoice is missing from the export.",
        )

    kept = []
    for pos, rec in enumerate(records):
        ref = rec["invoice_no"]
        if pos in duplicate_pos:
            log.add(
                FUEL,
                "duplicate_row",
                "rejected",
                source_row=rec["source_row"],
                record_ref=ref,
                detail="Exact duplicate of an earlier row (invoice, date, "
                "quantity, cost, site all identical). First copy kept.",
            )
            continue
        if pos in unknown_pos:
            log.add(
                FUEL,
                "unknown_fuel_type",
                "rejected",
                source_row=rec["source_row"],
                record_ref=ref,
                field="fuel_type",
                raw_value=rec["fuel_type"],
                resolution="Row rejected; no emission factor exists for it.",
                detail="Loading it would add litres that convert to zero kg "
                "and understate Scope 1 without any visible sign.",
            )
            continue
        if rec["_imputed"]:
            log.add(
                FUEL,
                "imputed_date",
                "flagged",
                source_row=rec["source_row"],
                record_ref=ref,
                field="delivery_date",
                raw_value=rec["_raw_date"],
                resolution=f"Imputed to first of month: {rec['delivery_date']}.",
                detail="Mon-YY source has no day; imputed to month start.",
            )
        if rec["_kl"]:
            log.add(
                FUEL,
                "unit_conversion",
                "fixed",
                source_row=rec["source_row"],
                record_ref=ref,
                field="quantity_l",
                raw_value=f"{rec['_raw_qty']} kL",
                resolution=f"Converted kL to L (x1000): {rec['quantity_l']:.0f} L.",
                detail="Mixed units in source; normalised to litres.",
            )
        if rec["_credit"]:
            log.add(
                FUEL,
                "negative_quantity",
                "flagged",
                source_row=rec["source_row"],
                record_ref=ref,
                field="quantity_l",
                raw_value=rec["_raw_qty"],
                resolution="Retained as credit/reversal; netted into Scope 1.",
                detail="Negative quantity treated as a fuel credit (is_credit=true).",
            )
        kept.append(rec)

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO fuel_deliveries (invoice_no, delivery_date, date_imputed,"
            " fuel_type, quantity_l, cost_aud, site_area, is_credit, source_row)"
            " VALUES (%(invoice_no)s, %(delivery_date)s, %(date_imputed)s,"
            " %(fuel_type)s, %(quantity_l)s, %(cost_aud)s, %(site_area)s,"
            " %(is_credit)s, %(source_row)s)",
            kept,
        )
    return len(kept)


# --------------------------------------------------------------------------
# incidents (loaded before electricity so the dip detector can cite them)
# --------------------------------------------------------------------------
def load_incidents(conn, log: QualityLog) -> tuple[int, pd.DataFrame]:
    df = _read_csv(INCIDENTS)

    raw_sev = df["severity"].astype(str).str.strip()
    has_numeric = raw_sev.str.fullmatch(r"\d+").any()
    has_text = raw_sev.str.fullmatch(r"[A-Za-z]+").any()
    if has_numeric and has_text:
        log.add(
            INCIDENTS,
            "inconsistent_severity_scale",
            "fixed",
            field="severity",
            detail="Two severity scales in source (numeric 1/2/3 and text "
            "Low/Medium). Mapped to one ordinal: 1=Low, 2=Medium, 3=High.",
        )

    raw_id = df["incident_id"].str.strip()
    counts = raw_id.value_counts()
    duplicated = set(counts[counts > 1].index)

    records = []
    for i, r in df.iterrows():
        source_row = i + 1
        iid = r["incident_id"].strip()
        d = cleaning.parse_incident_date(r["incident_date"])
        norm, rank = cleaning.normalise_severity(r["severity"])
        records.append(
            {
                "incident_id": iid,
                "incident_date": d,
                "location": r["location"].strip(),
                "type_code": r["type_code"].strip(),
                "severity_raw": r["severity"].strip(),
                "severity_norm": norm,
                "severity_rank": rank,
                "description": r["description"].strip(),
                "source_row": source_row,
            }
        )

    logged_dupes: set[str] = set()
    for rec in records:
        iid = rec["incident_id"]
        if iid in duplicated and iid not in logged_dupes:
            logged_dupes.add(iid)
            dates = ", ".join(
                str(x["incident_date"]) for x in records if x["incident_id"] == iid
            )
            log.add(
                INCIDENTS,
                "duplicate_incident_id",
                "flagged",
                record_ref=iid,
                field="incident_id",
                raw_value=iid,
                resolution="Surrogate PK used; both distinct incidents retained.",
                detail=f"incident_id reused for distinct incidents (dates: {dates}).",
            )

    clean_df = pd.DataFrame(records)

    # Boilerplate text reused across incidents. The description is the only
    # free-text evidence the AI layer has, so its reliability travels with it.
    for group in detect_recycled_descriptions(clean_df):
        ids = ", ".join(group.incident_ids)
        rows = ", ".join(str(n) for n in group.source_rows)
        log.add(
            INCIDENTS,
            "recycled_description",
            "flagged",
            source_row=group.source_rows[0],
            record_ref=group.incident_ids[0],
            field="description",
            raw_value=group.description,
            resolution="All members retained; text is not evidence of a "
            "distinct event.",
            detail=f"{len(group.incident_ids)} incidents share this exact "
            f"description: {ids} (source rows {rows}).",
        )

    # Identical words cannot describe two different severities. Plain grouping
    # answers the assignment's severity-inconsistency requirement with no LLM.
    for group in detect_severity_conflicts(clean_df):
        severities = sorted(set(group.severity_raws))
        ids = ", ".join(group.incident_ids)
        log.add(
            INCIDENTS,
            "inconsistent_severity_for_identical_text",
            "flagged",
            source_row=group.source_rows[0],
            record_ref=group.incident_ids[0],
            field="severity",
            raw_value=" / ".join(severities),
            resolution="Both retained; at least one recorded severity is wrong.",
            detail=f"One description carries severities {' and '.join(severities)} "
            f"across {ids}.",
        )

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO incidents (incident_id, incident_date, location, type_code,"
            " severity_raw, severity_norm, severity_rank, description, source_row)"
            " VALUES (%(incident_id)s, %(incident_date)s, %(location)s, %(type_code)s,"
            " %(severity_raw)s, %(severity_norm)s, %(severity_rank)s, %(description)s,"
            " %(source_row)s)",
            records,
        )
    return len(records), clean_df


# --------------------------------------------------------------------------
# electricity
# --------------------------------------------------------------------------
def load_electricity(conn, log: QualityLog, incidents_df: pd.DataFrame) -> int:
    df = _read_csv(ELEC)
    records = []
    for i, r in df.iterrows():
        period = pd.Period(r["period"].strip(), freq="M").to_timestamp().date()
        records.append(
            {
                "meter_id": r["meter_id"].strip(),
                "meter_description": r["meter_description"].strip(),
                "period": period,
                "consumption_kwh": float(r["consumption"]),
                "unit": r["unit"].strip(),
                "is_flagged": False,
                "source_row": i + 1,
                "_raw": r["consumption"].strip(),
            }
        )
    clean_df = pd.DataFrame(records)

    # scale errors (MTR-07 collapse)
    for pos in detect_scale_errors(clean_df):
        rec = records[pos]
        rec["is_flagged"] = True
        log.add(
            ELEC,
            "suspected_scale_error",
            "flagged",
            source_row=rec["source_row"],
            record_ref=f"{rec['meter_id']} {rec['period']}",
            field="consumption_kwh",
            raw_value=rec["_raw"],
            resolution="Kept raw; not corrected (compliance figure).",
            detail="Reading below 5% of this meter's maximum; ~1000x collapse "
            "suspected. Reported as unreliable, never silently corrected.",
        )

    # site-wide dip (explained by the substation outage)
    for dip in detect_site_wide_dip(clean_df, incidents_df):
        log.add(
            ELEC,
            "expected_anomaly",
            "flagged",
            record_ref=str(dip.period),
            field="consumption_kwh",
            raw_value=f"{dip.site_total:,.0f} kWh",
            resolution=(
                f"Explained by incident {dip.resolved_incident}."
                if dip.resolved_incident
                else "Unexplained; no power incident found this month."
            ),
            detail=dip.detail,
        )

    # missing meter id in the sequence
    nums = sorted(
        int(m.group(1))
        for mid in clean_df["meter_id"].unique()
        if (m := re.search(r"(\d+)", mid))
    )
    if nums:
        present = set(nums)
        for n in range(min(nums), max(nums) + 1):
            if n not in present:
                log.add(
                    ELEC,
                    "missing_meter_id",
                    "flagged",
                    record_ref=f"MTR-{n:02d}",
                    field="meter_id",
                    detail=f"MTR-{n:02d} absent from the meter sequence "
                    f"(present: MTR-{min(nums):02d}..MTR-{max(nums):02d}).",
                )

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO electricity_readings (meter_id, meter_description, period,"
            " consumption_kwh, unit, is_flagged, source_row)"
            " VALUES (%(meter_id)s, %(meter_description)s, %(period)s,"
            " %(consumption_kwh)s, %(unit)s, %(is_flagged)s, %(source_row)s)",
            records,
        )
    return len(records)


# --------------------------------------------------------------------------
# suppliers
# --------------------------------------------------------------------------
def load_suppliers(conn, log: QualityLog) -> int:
    df = _read_csv(SUPPLIERS)
    records = []
    for i, r in df.iterrows():
        raw_abn = r["abn"]
        digits, well_formed, checksum_ok = cleaning.validate_abn(raw_abn)
        spend = r["fy_spend_aud"]
        records.append(
            {
                "supplier_name": r["supplier_name"].strip(),
                "abn": (raw_abn or "").strip() or None,
                "abn_well_formed": well_formed,
                "abn_checksum_ok": checksum_ok,
                "category": r["category"].strip(),
                "fy_spend_aud": float(spend) if spend not in (None, "") else None,
                "canonical_supplier_id": None,
                "source_row": i + 1,
                "abn_digits": digits,
            }
        )
    clean_df = pd.DataFrame(records)

    for rec in records:
        ref = rec["supplier_name"]
        if rec["abn"] is None:
            log.add(
                SUPPLIERS,
                "missing_abn",
                "flagged",
                source_row=rec["source_row"],
                record_ref=ref,
                field="abn",
                detail="Supplier has no ABN; kept for reference.",
            )
        elif not rec["abn_well_formed"]:
            log.add(
                SUPPLIERS,
                "malformed_abn",
                "flagged",
                source_row=rec["source_row"],
                record_ref=ref,
                field="abn",
                raw_value=rec["abn"],
                detail=f"ABN is not 11 digits ({len(rec['abn_digits'] or '')} "
                "digits); kept for reference.",
            )

    # insert first to get surrogate ids, then wire canonical references
    insert_cols = [
        "supplier_name", "abn", "abn_well_formed", "abn_checksum_ok", "category",
        "fy_spend_aud", "source_row",
    ]
    ids: list[int] = []
    with conn.cursor() as cur:
        for rec in records:
            cur.execute(
                "INSERT INTO suppliers (supplier_name, abn, abn_well_formed,"
                " abn_checksum_ok, category, fy_spend_aud, source_row)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                tuple(rec[c] for c in insert_cols),
            )
            ids.append(cur.fetchone()[0])

    groups = detect_supplier_duplicates(clean_df)
    with conn.cursor() as cur:
        for g in groups:
            canonical_id = ids[g.canonical_pos]
            canonical_name = records[g.canonical_pos]["supplier_name"]
            for pos in g.member_pos:
                if pos == g.canonical_pos:
                    continue
                cur.execute(
                    "UPDATE suppliers SET canonical_supplier_id = %s WHERE id = %s",
                    (canonical_id, ids[pos]),
                )
                rec = records[pos]
                log.add(
                    SUPPLIERS,
                    "duplicate_supplier",
                    "flagged",
                    source_row=rec["source_row"],
                    record_ref=rec["supplier_name"],
                    resolution=f"Canonical entity: {canonical_name}.",
                    detail=g.reason,
                )

    # One entity cannot sit in two spend categories. Left alone, the split
    # understates the supplier in both.
    for g in detect_supplier_category_conflicts(clean_df, groups):
        categories = sorted(
            {records[p]["category"] for p in g.member_pos if records[p]["category"]}
        )
        names = ", ".join(records[p]["supplier_name"] for p in g.member_pos)
        log.add(
            SUPPLIERS,
            "supplier_category_conflict",
            "flagged",
            source_row=records[g.canonical_pos]["source_row"],
            record_ref=records[g.canonical_pos]["supplier_name"],
            field="category",
            raw_value=" / ".join(categories),
            resolution=f"Canonical category: "
            f"{records[g.canonical_pos]['category']}.",
            detail=f"One entity recorded under {' and '.join(categories)} "
            f"across {names}. Spend is split between two categories.",
        )
    return len(records)


# --------------------------------------------------------------------------
# data quality rows
# --------------------------------------------------------------------------
def write_quality(conn, log: QualityLog) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO data_quality_issues (source_file, source_row, record_ref,"
            " issue_type, field, raw_value, action, resolution, detail)"
            " VALUES (%(source_file)s, %(source_row)s, %(record_ref)s, %(issue_type)s,"
            " %(field)s, %(raw_value)s, %(action)s, %(resolution)s, %(detail)s)",
            log.rows,
        )
    return len(log.rows)


def main() -> None:
    log = QualityLog()
    with get_conn() as conn:
        apply_schema(conn)
        apply_ai_schema(conn)
        n_factors, known_fuel_keys = load_emission_factors(conn)
        n_fuel = load_fuel(conn, log, known_fuel_keys)
        n_inc, incidents_df = load_incidents(conn, log)
        n_elec = load_electricity(conn, log, incidents_df)
        n_sup = load_suppliers(conn, log)
        n_issues = write_quality(conn, log)
        conn.commit()

    print("Ingestion complete.")
    print("  Rows loaded per table:")
    print(f"    emission_factors      {n_factors}")
    print(f"    fuel_deliveries       {n_fuel}")
    print(f"    incidents             {n_inc}")
    print(f"    electricity_readings  {n_elec}")
    print(f"    suppliers             {n_sup}")
    print(f"    data_quality_issues   {n_issues}")
    print("  data_quality_issues by (file, issue_type):")
    for (fname, itype), c in sorted(log.counts().items()):
        print(f"    {fname:28} {itype:36} {c}")


if __name__ == "__main__":
    main()
