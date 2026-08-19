"""Pure normalisation functions for the raw Ironbark CSVs.

Every function takes raw string/number input and returns a normalised value.
No database, no pandas: this is the graded correctness core and the pytest
surface.
"""

from __future__ import annotations

import re
from datetime import date, datetime

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_MONYY = re.compile(r"^[A-Za-z]{3}-\d{2}$")

_SEVERITY = {
    "1": ("Low", 1),
    "2": ("Medium", 2),
    "3": ("High", 3),
    "low": ("Low", 1),
    "medium": ("Medium", 2),
    "high": ("High", 3),
}

_ABN_WEIGHTS = [10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19]


def parse_delivery_date(s: str) -> tuple[date, bool]:
    """Parse a fuel delivery date across the four source formats.

    Returns (date, imputed). `Mon-YY` has no day, so it maps to the first of
    the month with imputed=True. `DD/MM/YYYY` is read day-first (Australian).
    """
    t = s.strip()
    if _ISO.match(t):
        return datetime.strptime(t, "%Y-%m-%d").date(), False
    if _DMY.match(t):
        return datetime.strptime(t, "%d/%m/%Y").date(), False
    if _MONYY.match(t):
        # %y maps 00-68 -> 2000-2068, correct for 25/26.
        return datetime.strptime(t, "%b-%y").date().replace(day=1), True
    raise ValueError(f"unrecognised delivery date format: {s!r}")


def normalise_quantity(qty: float | str, unit: str) -> float:
    """Normalise a fuel quantity to litres. kL x 1000; L/litres x 1."""
    q = float(qty)
    u = unit.strip().lower()
    if u == "kl":
        return q * 1000.0
    if u in ("l", "litre", "litres"):
        return q
    raise ValueError(f"unrecognised fuel unit: {unit!r}")


def parse_cost(s: str | float | None) -> float | None:
    """Strip a currency string of `$` and thousands commas -> float."""
    if s is None:
        return None
    t = str(s).strip().replace("$", "").replace(",", "")
    if t == "":
        return None
    return float(t)


def parse_incident_date(s: str) -> date:
    """Parse an incident date. Source is DD/MM/YYYY (day-first)."""
    return datetime.strptime(s.strip(), "%d/%m/%Y").date()


def normalise_severity(raw: str | int) -> tuple[str, int]:
    """Map the two severity scales to one ordinal (text, rank)."""
    key = str(raw).strip().lower()
    if key not in _SEVERITY:
        raise ValueError(f"unrecognised severity: {raw!r}")
    return _SEVERITY[key]


def abn_checksum(digits: str) -> bool:
    """Return True if `digits` (11 numeric chars) passes the ATO ABN checksum."""
    if not (digits.isdigit() and len(digits) == 11):
        return False
    n = [int(c) for c in digits]
    n[0] -= 1
    return sum(a * b for a, b in zip(n, _ABN_WEIGHTS)) % 89 == 0


def validate_abn(s: str | None) -> tuple[str | None, bool, bool]:
    """Validate an ABN.

    Returns (digits, well_formed, checksum_ok):
      - digits: whitespace-stripped value, or None when empty/missing.
      - well_formed: True iff exactly 11 numeric digits after stripping spaces.
      - checksum_ok: True iff the ATO weighted-modulus checksum also passes.

    The two flags are separate on purpose. Every 11-digit ABN in suppliers.csv
    is synthetic and fails the real checksum, so a single strict flag would
    mark all 12 suppliers and bury the one real fault, TerraForm Rehab's
    7-digit value. `well_formed` reports structure and drives the data quality
    report. `checksum_ok` records the real ATO result for traceability.
    """
    if s is None:
        return None, False, False
    digits = re.sub(r"\s+", "", str(s))
    if digits == "":
        return None, False, False
    well_formed = digits.isdigit() and len(digits) == 11
    return digits, well_formed, abn_checksum(digits)


_LEGAL_SUFFIX = re.compile(r"\b(pty|ltd|proprietary|limited|co|inc)\b")


def canonical_name(s: str) -> str:
    """Collapse a supplier name to a comparison key.

    Lowercase, drop legal suffixes (`pty ltd`, `p/l`) and punctuation, collapse
    whitespace. Used only to group supplier near-duplicates.
    """
    t = s.strip().lower()
    t = t.replace("p/l.", " ").replace("p/l", " ")
    t = _LEGAL_SUFFIX.sub(" ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()
