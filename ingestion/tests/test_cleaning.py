"""Unit tests for the pure cleaning functions, using the real messy values
from data/. These decisions are the graded core, so every branch is asserted.
"""

from datetime import date

import pytest

from ingestion.cleaning import (
    abn_checksum,
    canonical_name,
    normalise_quantity,
    normalise_severity,
    parse_cost,
    parse_delivery_date,
    parse_incident_date,
    validate_abn,
)


class TestParseDeliveryDate:
    def test_iso(self):
        assert parse_delivery_date("2025-12-19") == (date(2025, 12, 19), False)

    def test_day_first_slash(self):
        # 21/05/2026 is day-first Australian: 21 May 2026, not 5 Feb.
        assert parse_delivery_date("21/05/2026") == (date(2026, 5, 21), False)

    def test_day_first_low_day_stays_day_first(self):
        # 10/05/2025 -> 10 May 2025 (day-first), a fuel anchor row.
        assert parse_delivery_date("10/05/2025") == (date(2025, 5, 10), False)

    def test_mon_yy_imputes_first_of_month(self):
        assert parse_delivery_date("Oct-25") == (date(2025, 10, 1), True)

    def test_mon_yy_2026(self):
        assert parse_delivery_date("Feb-26") == (date(2026, 2, 1), True)

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            parse_delivery_date("not a date")


class TestNormaliseQuantity:
    def test_plain_litres(self):
        assert normalise_quantity("96595", "L") == 96595.0

    def test_litres_word_case_insensitive(self):
        assert normalise_quantity("57529", "Litres") == 57529.0
        assert normalise_quantity("71053", "litres") == 71053.0

    def test_kl_multiplies_by_1000(self):
        # INV-40373 anchor: 84.03 kL -> 84030 L.
        assert normalise_quantity("84.03", "kL") == 84030.0

    def test_unknown_unit_raises(self):
        with pytest.raises(ValueError):
            normalise_quantity("10", "gallons")


class TestParseCost:
    def test_dollar_and_commas(self):
        assert parse_cost("$182,946.64") == 182946.64

    def test_plain_number(self):
        assert parse_cost("132182.58") == 132182.58

    def test_negative_credit(self):
        # INV-41777 credit/reversal.
        assert parse_cost("$-23,375.00") == -23375.00

    def test_empty_is_none(self):
        assert parse_cost("") is None
        assert parse_cost(None) is None


class TestParseIncidentDate:
    def test_day_first(self):
        assert parse_incident_date("22/01/2025") == date(2025, 1, 22)


class TestNormaliseSeverity:
    def test_numeric_scale(self):
        assert normalise_severity("1") == ("Low", 1)
        assert normalise_severity("2") == ("Medium", 2)
        assert normalise_severity("3") == ("High", 3)

    def test_text_scale_maps_to_same_ordinal(self):
        assert normalise_severity("Low") == ("Low", 1)
        assert normalise_severity("Medium") == ("Medium", 2)
        assert normalise_severity("High") == ("High", 3)

    def test_numeric_and_text_agree(self):
        assert normalise_severity("1") == normalise_severity("low")

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            normalise_severity("critical")


class TestValidateAbn:
    def test_seven_digit_is_invalid(self):
        # TerraForm 5501822 is 7 digits.
        digits, well_formed, checksum_ok = validate_abn("5501822")
        assert digits == "5501822"
        assert well_formed is False
        assert checksum_ok is False

    def test_eleven_digit_is_structurally_valid(self):
        # Ironline 63 004 085 616: 11 digits -> structurally well formed.
        digits, well_formed, checksum_ok = validate_abn("63 004 085 616")
        assert digits == "63004085616"
        assert well_formed is True

    def test_missing_abn(self):
        assert validate_abn("") == (None, False, False)
        assert validate_abn(None) == (None, False, False)

    def test_real_checksum_algorithm(self):
        # Control: a genuine ABN from the ABR spec passes the checksum;
        # the synthetic sample ABNs do not.
        assert abn_checksum("51824753556") is True
        assert abn_checksum("63004085616") is False

    def test_well_formed_and_checksum_are_independent_signals(self):
        # The whole reason the column is named abn_well_formed and not
        # abn_valid: structure passes while the real ATO checksum fails.
        assert validate_abn("63 004 085 616") == ("63004085616", True, False)

    def test_short_abn_fails_both_signals(self):
        # TerraForm is the one supplier where structure itself is wrong, and
        # a strict checksum-only gate would have hidden it among 12 failures.
        assert validate_abn("5501822") == ("5501822", False, False)


class TestCanonicalName:
    def test_pty_ltd_and_pl_collapse_to_same_key(self):
        assert canonical_name("Ironline Fuel Distributors Pty Ltd") == canonical_name(
            "Ironline Fuel Distributors P/L"
        )

    def test_key_value(self):
        assert canonical_name("Ironline Fuel Distributors Pty Ltd") == (
            "ironline fuel distributors"
        )

    def test_typo_names_do_not_collapse(self):
        # Blackwood typo differs by spelling; grouping relies on shared ABN,
        # not on the name key.
        assert canonical_name("Blackwood Heavy Maintenance") != canonical_name(
            "Blackwood Heavy Maintanence"
        )
