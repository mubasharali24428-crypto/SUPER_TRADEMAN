"""Tests for trading.core.money and trading.risk.metrics.

Golden values are computed independently by hand / decimal REPL and pinned
EXACTLY (Decimal equality) so any drift in money math fails loudly.
"""

from __future__ import annotations

import decimal
from decimal import Decimal

import pytest

from trading.core import (
    check_min_notional,
    decimal_from_float,
    quantize_to_step,
)
from trading.risk.metrics import intended_r, realized_r


# --------------------------------------------------------------------------
# quantize_to_step — step rounding edge cases
# --------------------------------------------------------------------------


class TestQuantizeToStep:
    def test_step_0_001_truncates_dust(self):
        assert quantize_to_step("1.23456", "0.001") == Decimal("1.234")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # dust strictly below one step is truncated away entirely
            ("0.999", "0.99"),
            ("0.009", "0.00"),
            ("123.456", "123.45"),
            ("123.459", "123.45"),
        ],
    )
    def test_step_0_01(self, value, expected):
        assert quantize_to_step(value, "0.01") == Decimal(expected)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("7.9", Decimal("7")), ("7.0001", Decimal("7")), ("-7.9", Decimal("-7"))],
    )
    def test_step_1(self, value, expected):
        assert quantize_to_step(value, "1") == expected

    def test_value_exactly_on_step_is_unchanged(self):
        assert quantize_to_step("0.125", "0.001") == Decimal("0.125")
        assert quantize_to_step("123.45", "0.01") == Decimal("123.45")
        assert quantize_to_step(7, 1) == Decimal("7")

    def test_dust_below_step_quantizes_to_zero(self):
        assert quantize_to_step("0.0009", "0.001") == Decimal("0")
        assert quantize_to_step("0.004", "0.01") == Decimal("0")

    def test_never_rounds_up_even_when_closer_to_next_step(self):
        assert quantize_to_step("0.126", "0.001") == Decimal("0.126")
        assert quantize_to_step("0.1269", "0.001") == Decimal("0.126")

    def test_negative_values_truncate_toward_zero_ccxt_convention(self):
        assert quantize_to_step("-0.129", "0.01") == Decimal("-0.12")

    def test_float_input_goes_through_str_conversion(self):
        # 0.30000000000000004 must be seen as the str repr, not raw binary.
        assert quantize_to_step(0.1 + 0.2, "0.01") == Decimal("0.30")

    def test_result_is_exact_multiple_of_step(self):
        q = quantize_to_step("123.456", "0.001")
        assert (q / Decimal("0.001")) == q / Decimal("0.001")  # sanity
        assert q % Decimal("0.001") == Decimal("0.000")

    def test_invalid_steps_raise(self):
        for bad in ("0", "-0.01"):
            with pytest.raises(ValueError):
                quantize_to_step("1.0", bad)
        with pytest.raises(ValueError):
            quantize_to_step(float("nan"), "0.01")


# --------------------------------------------------------------------------
# check_min_notional — boundary behavior
# --------------------------------------------------------------------------


class TestCheckMinNotional:
    def test_exactly_at_boundary_passes(self):
        # Exact Decimal math: 3 * 333.33 == 999.99 >= 999.99 -> True.
        assert check_min_notional("3", "333.33", "999.99") is True

    def test_one_dust_below_boundary_fails(self):
        assert check_min_notional("3", "333.32", "999.99") is False

    def test_above_and_below(self):
        assert check_min_notional("0.01", "50000", "500") is True
        assert check_min_notional("0.009", "50000", "500") is False

    def test_float_inputs_avoid_binary_artifact(self):
        # Exact product 3 * 333.33 == 999.99 lands ON the boundary; float
        # multiplication misses it entirely (rounds up to ~999.99000000000001):
        assert Decimal(3 * 333.33) != Decimal("999.99")
        # The helper converts via str() internally, so floats land EXACTLY on it:
        assert check_min_notional(3, 333.33, 999.99) is True

    def test_zero_min_notional_always_passes(self):
        assert check_min_notional("0.000001", "0.000001", "0") is True

    def test_negative_min_notional_rejected(self):
        with pytest.raises(ValueError):
            check_min_notional("1", "1", "-1")


# --------------------------------------------------------------------------
# decimal_from_float — float-artifact avoidance
# --------------------------------------------------------------------------


class TestDecimalFromFloat:
    def test_classic_artifact_case(self):
        s = 0.1 + 0.2
        assert str(s) == "0.30000000000000004"
        # Raw Decimal(float) drags in the invisible 50-digit binary expansion;
        # ours returns exactly the human-visible repr of the same double:
        assert Decimal(s) != Decimal(str(s))
        assert decimal_from_float(s) == Decimal("0.30000000000000004")
        # For a literal like 0.1 the artifact is fully invisible after conversion:
        assert len(str(Decimal(0.1))) > 20
        assert decimal_from_float(0.1) == Decimal("0.1")
        # And through the rounding pipeline the artifact never reaches orders:
        assert quantize_to_step(s, "0.01") == Decimal("0.30")

    def test_tenth(self):
        assert Decimal(0.1) != Decimal("0.1")
        assert decimal_from_float(0.1) == Decimal("0.1")

    def test_integral_floats(self):
        assert decimal_from_float(5.0) == Decimal("5.0")
        assert decimal_from_float(2.5) == Decimal("2.5")

    def test_precision_context_pinned_at_import(self):
        assert decimal.getcontext().prec == 28


# --------------------------------------------------------------------------
# R-multiples — golden values, intended vs realized kept separate
# --------------------------------------------------------------------------


class TestIntendedR:
    def test_golden_five_r(self):
        # Long from 100, stop 98 => risk/unit = 2; exit 110 => +10/2 = 5R.
        assert intended_r(100, 98, 110) == Decimal("5")

    def test_golden_loss(self):
        # Exit 97 from arrival 100, stop 98 => -3/2 = -1.5R.
        assert intended_r(100, 98, 97) == Decimal("-1.5")

    def test_short_position_symmetry(self):
        # Short arrival 200, stop 206 (risk 6), exit 182 => +18/6 = +3R.
        assert intended_r(200, 206, 182) == Decimal("3")

    def test_zero_risk_denominator_raises(self):
        with pytest.raises(ValueError):
            intended_r(100, 100, 105)


class TestRealizedR:
    def test_golden_no_cost_matches_intended_when_fills_are_clean(self):
        # Clean fills at arrival, zero fees => identical to intended_r.
        assert realized_r(100, 0, 98, 110) == Decimal("5")
        assert realized_r(100, 0, 98, 110) == intended_r(100, 98, 110)

    def test_known_drift_case_intended_vs_realized(self):
        """THE drift case: intended ~5R but realized ~4.17R after costs.

        Entry slipped: arrival 100.00, fill VWAP 100.32 (stop still 98).
        Fees 0.005 quote/unit. Exit 110.
          intended: (110 - 100.00) / |100.00 - 98|   = 5R exactly
          realized: (110 - 0.005 - 100.32)/|100.32-98| = 9.675/2.32
                    = 4.170258620689655172413793103...
        Both numbers are computed here from their own functions and asserted
        separately; they are NOT equal and must never be conflated.
        """
        intended = intended_r(Decimal("100"), Decimal("98"), Decimal("110"))
        realized = realized_r(
            fill_vwap=Decimal("100.32"),
            fees_paid=Decimal("0.005"),
            initial_stop=Decimal("98"),
            exit_price=Decimal("110"),
        )
        assert intended == Decimal("5")
        assert realized == Decimal("9.675") / Decimal("2.32")
        assert realized == Decimal("4.170258620689655172413793103")
        assert realized != intended
        assert round(realized, 2) == Decimal("4.17")
        # The gap itself is pinned: ~0.83R eaten by slippage+fees.
        assert round(intended - realized, 2) == Decimal("0.83")

    def test_fees_reduce_numerator_only(self):
        # vwap 100, stop 95 (risk 5), exit 110, fees 1/unit => (110-1-100)/5.
        assert realized_r(100, 1, 95, 110) == Decimal("1.8")

    def test_accepts_mixed_numeric_inputs(self):
        assert realized_r(100.0, 0.0, 98.0, 108.0) == Decimal("4")
