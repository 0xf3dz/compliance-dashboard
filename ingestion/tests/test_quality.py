"""Unit tests for the cross-row detectors.

Each test builds a small DataFrame by hand and asserts one detector. No
database and no CSV read, so a failure names the detector, not the fixture.
Values are taken from data/ wherever a real one exists.
"""

from datetime import date

import pandas as pd
import pytest

from ingestion.quality import (
    POWER_KEYWORDS,
    SITE_DIP_TRAILING,
    QualityLog,
    _find_power_incident,
    detect_duplicate_fuel_rows,
    detect_missing_fuel_months,
    detect_recycled_descriptions,
    detect_scale_errors,
    detect_severity_conflicts,
    detect_site_wide_dip,
    detect_supplier_category_conflicts,
    detect_supplier_duplicates,
    detect_unknown_fuel_types,
)


def fuel_frame(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "invoice_no": "INV-00000",
        "delivery_date": date(2025, 1, 1),
        "fuel_type": "Diesel",
        "quantity_l": 1000.0,
        "cost_aud": 1000.0,
        "site_area": "Processing Plant",
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def elec_frame(rows: list[dict]) -> pd.DataFrame:
    defaults = {"meter_id": "MTR-01", "period": date(2025, 1, 1), "consumption_kwh": 1000.0}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def incident_frame(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "incident_id": "INC-0000",
        "incident_date": date(2025, 1, 1),
        "type_code": "OTH",
        "severity_raw": "Low",
        "severity_rank": 1,
        "description": "Something happened.",
        "source_row": 1,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


def supplier_frame(rows: list[dict]) -> pd.DataFrame:
    defaults = {
        "supplier_name": "Acme Pty Ltd",
        "abn_digits": None,
        "abn_well_formed": False,
        "category": "Other",
        "fy_spend_aud": 0.0,
    }
    return pd.DataFrame([{**defaults, **r} for r in rows])


class TestQualityLog:
    def test_add_records_a_row_and_counts_it(self):
        log = QualityLog()
        log.add("fuel_deliveries.csv", "duplicate_row", "rejected", source_row=7)
        assert log.counts() == {("fuel_deliveries.csv", "duplicate_row"): 1}

    def test_unknown_action_is_rejected(self):
        # The schema constrains action to three values. Failing here, in pure
        # Python, is cheaper than failing on INSERT halfway through a load.
        log = QualityLog()
        with pytest.raises(AssertionError):
            log.add("fuel_deliveries.csv", "duplicate_row", "ignored")


class TestDetectDuplicateFuelRows:
    def test_second_identical_row_is_rejected_and_first_is_kept(self):
        df = fuel_frame(
            [
                {"invoice_no": "INV-40641", "quantity_l": 96595.0, "cost_aud": 182946.64},
                {"invoice_no": "INV-40641", "quantity_l": 96595.0, "cost_aud": 182946.64},
            ]
        )
        assert detect_duplicate_fuel_rows(df) == [1]

    def test_same_quantity_and_cost_on_a_different_invoice_is_kept(self):
        # INV-40967 and INV-40729 share quantity and cost but are two real
        # deliveries on two dates. Rejecting either would erase Scope 1.
        df = fuel_frame(
            [
                {
                    "invoice_no": "INV-40967",
                    "delivery_date": date(2025, 6, 3),
                    "quantity_l": 71053.0,
                    "cost_aud": 132182.58,
                },
                {
                    "invoice_no": "INV-40729",
                    "delivery_date": date(2025, 9, 18),
                    "quantity_l": 71053.0,
                    "cost_aud": 132182.58,
                },
            ]
        )
        assert detect_duplicate_fuel_rows(df) == []


class TestDetectScaleErrors:
    def test_reading_at_one_tenth_of_one_percent_is_flagged(self):
        # MTR-07 falls from ~250,000 kWh to ~250 kWh.
        df = elec_frame(
            [
                {"meter_id": "MTR-07", "period": date(2025, 9, 1), "consumption_kwh": 250_000.0},
                {"meter_id": "MTR-07", "period": date(2025, 10, 1), "consumption_kwh": 250.0},
            ]
        )
        assert detect_scale_errors(df) == [1]

    def test_reading_at_ninety_percent_is_left_alone(self):
        df = elec_frame(
            [
                {"meter_id": "MTR-01", "period": date(2025, 1, 1), "consumption_kwh": 1_000_000.0},
                {"meter_id": "MTR-01", "period": date(2025, 2, 1), "consumption_kwh": 900_000.0},
            ]
        )
        assert detect_scale_errors(df) == []


class TestDetectSiteWideDip:
    def test_month_at_thirty_five_percent_of_trailing_mean_is_flagged(self):
        df = elec_frame(
            [
                {"period": date(2025, 1, 1), "consumption_kwh": 1_000_000.0},
                {"period": date(2025, 2, 1), "consumption_kwh": 1_000_000.0},
                {"period": date(2025, 3, 1), "consumption_kwh": 1_000_000.0},
                {"period": date(2025, 4, 1), "consumption_kwh": 350_000.0},
            ]
        )
        dips = detect_site_wide_dip(df, incident_frame([]))
        assert [d.period for d in dips] == [date(2025, 4, 1)]
        assert dips[0].trailing_mean == 1_000_000.0
        assert dips[0].resolved_incident is None
        assert "35%" in dips[0].detail

    def test_dip_names_the_power_incident_that_explains_it(self):
        df = elec_frame(
            [
                {"period": date(2026, 1, 1), "consumption_kwh": 1_000_000.0},
                {"period": date(2026, 2, 1), "consumption_kwh": 1_000_000.0},
                {"period": date(2026, 3, 1), "consumption_kwh": 1_000_000.0},
                {"period": date(2026, 4, 1), "consumption_kwh": 350_000.0},
            ]
        )
        incidents = incident_frame(
            [
                {
                    "incident_id": "INC-2026-131",
                    "incident_date": date(2026, 4, 12),
                    "type_code": "ELE",
                    "description": "Substation trip took the plant off grid supply.",
                }
            ]
        )
        dips = detect_site_wide_dip(df, incidents)
        assert dips[0].resolved_incident == "INC-2026-131"
        assert "INC-2026-131" in dips[0].detail

    def test_no_dip_when_history_is_shorter_than_the_trailing_window(self):
        # With fewer months than SITE_DIP_TRAILING there is no baseline, and a
        # guess would be worse than silence.
        rows = [
            {"period": date(2025, m, 1), "consumption_kwh": 1_000_000.0}
            for m in range(1, SITE_DIP_TRAILING + 1)
        ]
        rows[-1]["consumption_kwh"] = 1.0
        assert detect_site_wide_dip(elec_frame(rows), incident_frame([])) == []


class TestFindPowerIncident:
    @pytest.mark.parametrize("keyword", POWER_KEYWORDS)
    def test_each_keyword_matches(self, keyword):
        incidents = incident_frame(
            [
                {
                    "incident_id": "INC-2026-131",
                    "incident_date": date(2026, 3, 14),
                    "type_code": "OTH",
                    "description": f"Event involving {keyword} at the plant.",
                }
            ]
        )
        assert _find_power_incident(incidents, date(2026, 3, 1)) == "INC-2026-131"

    def test_type_code_ele_matches_without_a_keyword(self):
        incidents = incident_frame(
            [
                {
                    "incident_id": "INC-2026-140",
                    "incident_date": date(2026, 3, 14),
                    "type_code": "ELE",
                    "description": "Switchboard fault.",
                }
            ]
        )
        assert _find_power_incident(incidents, date(2026, 3, 1)) == "INC-2026-140"

    def test_unrelated_description_returns_none(self):
        incidents = incident_frame(
            [
                {
                    "incident_id": "INC-2026-150",
                    "incident_date": date(2026, 3, 14),
                    "type_code": "DUS",
                    "description": "Dust exceedance recorded at crusher.",
                }
            ]
        )
        assert _find_power_incident(incidents, date(2026, 3, 1)) is None

    def test_incident_in_another_month_does_not_match(self):
        incidents = incident_frame(
            [
                {
                    "incident_id": "INC-2026-131",
                    "incident_date": date(2026, 2, 14),
                    "type_code": "ELE",
                    "description": "Substation trip.",
                }
            ]
        )
        assert _find_power_incident(incidents, date(2026, 3, 1)) is None


RECYCLED = "Hydrocarbon sheen observed in V-drain near fuel farm, spill kit deployed."


class TestDetectRecycledDescriptions:
    def test_three_rows_sharing_one_description_form_one_group(self):
        df = incident_frame(
            [
                {"incident_id": "INC-2025-009", "source_row": 9, "description": RECYCLED},
                {"incident_id": "INC-2025-010", "source_row": 10, "description": RECYCLED},
                {"incident_id": "INC-2025-013", "source_row": 13, "description": RECYCLED},
                {"incident_id": "INC-2025-020", "source_row": 20, "description": "Unique text."},
            ]
        )
        groups = detect_recycled_descriptions(df)
        assert len(groups) == 1
        assert groups[0].description == RECYCLED
        assert groups[0].incident_ids == ["INC-2025-009", "INC-2025-010", "INC-2025-013"]
        assert groups[0].source_rows == [9, 10, 13]

    def test_all_unique_descriptions_produce_no_group(self):
        df = incident_frame(
            [
                {"incident_id": "INC-1", "source_row": 1, "description": "One."},
                {"incident_id": "INC-2", "source_row": 2, "description": "Two."},
            ]
        )
        assert detect_recycled_descriptions(df) == []


class TestDetectSeverityConflicts:
    def test_same_text_with_two_ranks_is_a_conflict(self):
        # The real pattern: one group recorded as "2" and as "Low".
        df = incident_frame(
            [
                {
                    "incident_id": "INC-2025-002",
                    "source_row": 2,
                    "description": RECYCLED,
                    "severity_raw": "2",
                    "severity_rank": 2,
                },
                {
                    "incident_id": "INC-2025-014",
                    "source_row": 14,
                    "description": RECYCLED,
                    "severity_raw": "Low",
                    "severity_rank": 1,
                },
            ]
        )
        groups = detect_severity_conflicts(df)
        assert len(groups) == 1
        assert sorted(set(groups[0].severity_raws)) == ["2", "Low"]

    def test_same_text_with_one_rank_is_not_a_conflict(self):
        df = incident_frame(
            [
                {
                    "incident_id": "INC-2025-002",
                    "source_row": 2,
                    "description": RECYCLED,
                    "severity_raw": "2",
                    "severity_rank": 2,
                },
                {
                    "incident_id": "INC-2025-014",
                    "source_row": 14,
                    "description": RECYCLED,
                    "severity_raw": "Medium",
                    "severity_rank": 2,
                },
            ]
        )
        assert detect_severity_conflicts(df) == []


class TestDetectMissingFuelMonths:
    def test_november_2025_is_reported_missing(self):
        months = [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 7)]
        rows = [
            {"delivery_date": date(y, m, 15)} for y, m in months if not (y == 2025 and m == 11)
        ]
        gaps = detect_missing_fuel_months(fuel_frame(rows), date(2025, 1, 1), date(2026, 6, 1))
        assert gaps == [date(2025, 11, 1)]

    def test_a_complete_window_reports_no_gap(self):
        months = [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 7)]
        rows = [{"delivery_date": date(y, m, 15)} for y, m in months]
        assert detect_missing_fuel_months(fuel_frame(rows), date(2025, 1, 1), date(2026, 6, 1)) == []


class TestDetectUnknownFuelTypes:
    def test_lpg_row_is_returned(self):
        df = fuel_frame(
            [
                {"fuel_type": "Diesel"},
                {"fuel_type": "LPG"},
                {"fuel_type": "Petrol (ULP)"},
            ]
        )
        assert detect_unknown_fuel_types(df, {"diesel", "petrol"}) == [1]

    def test_the_two_real_fuel_types_are_known(self):
        df = fuel_frame([{"fuel_type": "Diesel"}, {"fuel_type": "Petrol (ULP)"}])
        assert detect_unknown_fuel_types(df, {"diesel", "petrol"}) == []


IRONLINE = supplier_frame(
    [
        {
            "supplier_name": "Ironline Fuel Distributors Pty Ltd",
            "abn_digits": "63004085616",
            "abn_well_formed": True,
            "category": "Fuel supply",
            "fy_spend_aud": 8_940_000.0,
        },
        {
            "supplier_name": "Ironline Fuel Distributors P/L",
            "abn_digits": None,
            "abn_well_formed": False,
            "category": "Fuel",
            "fy_spend_aud": 1_212_000.0,
        },
    ]
)

BLACKWOOD = supplier_frame(
    [
        {
            "supplier_name": "Blackwood Heavy Maintenance",
            "abn_digits": "84112334908",
            "abn_well_formed": True,
            "category": "Fleet maintenance",
            "fy_spend_aud": 2_150_000.0,
        },
        {
            "supplier_name": "Blackwood Heavy Maintanence",
            "abn_digits": "84112334908",
            "abn_well_formed": True,
            "category": "Fleet maintenance",
            "fy_spend_aud": 415_000.0,
        },
    ]
)


class TestDetectSupplierDuplicates:
    def test_typo_names_group_on_the_shared_abn(self):
        # The two Blackwood names do not collapse to one key, so only the ABN
        # can link them.
        groups = detect_supplier_duplicates(BLACKWOOD)
        assert len(groups) == 1
        assert groups[0].member_pos == [0, 1]
        assert groups[0].canonical_pos == 0  # higher spend wins

    def test_pty_ltd_and_pl_group_on_the_canonical_name_key(self):
        # The P/L row carries no ABN, so only the name key can link them.
        groups = detect_supplier_duplicates(IRONLINE)
        assert len(groups) == 1
        assert groups[0].member_pos == [0, 1]
        assert groups[0].canonical_pos == 0  # well-formed ABN wins

    def test_unrelated_suppliers_are_not_grouped(self):
        df = supplier_frame(
            [
                {"supplier_name": "Apex Drill & Blast Services"},
                {"supplier_name": "Delta Comms & IT"},
            ]
        )
        assert detect_supplier_duplicates(df) == []


class TestDetectSupplierCategoryConflicts:
    def test_fuel_supply_against_fuel_is_a_conflict(self):
        groups = detect_supplier_duplicates(IRONLINE)
        conflicts = detect_supplier_category_conflicts(IRONLINE, groups)
        assert len(conflicts) == 1
        assert conflicts[0].member_pos == [0, 1]

    def test_matching_categories_are_not_a_conflict(self):
        groups = detect_supplier_duplicates(BLACKWOOD)
        assert detect_supplier_category_conflicts(BLACKWOOD, groups) == []
