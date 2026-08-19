"""Tests for the AI grounding guard.

_grounded is the whole anti-hallucination guarantee: a finding reaches the
database only when its evidence_quote is a verbatim substring of the incident
description. The guard runs in pure Python, so these tests need no API key and
no network, and they run in CI on every push.

The fixture is the real INC-2025-127 description and the quote the model
actually returned for it.
"""

from ingestion.ai.classify import _grounded

DESCRIPTION = (
    "Operator raised concerns about repeated verbal abuse from supervisor "
    "over several weeks, feeling anxious before shift."
)

REAL_QUOTE = (
    "repeated verbal abuse from supervisor over several weeks, "
    "feeling anxious before shift"
)


def finding(quote):
    return {
        "ai_category": "Psychosocial hazard",
        "is_psychosocial": True,
        "severity_mismatch": True,
        "evidence_quote": quote,
        "rationale": "Sustained verbal abuse reported by the operator.",
    }


def test_verbatim_substring_passes():
    assert _grounded(finding(REAL_QUOTE), DESCRIPTION) is True


def test_whole_description_passes():
    assert _grounded(finding(DESCRIPTION), DESCRIPTION) is True


def test_paraphrase_fails():
    # The meaning is right and the words are not. This is the failure mode the
    # guard exists to catch.
    assert _grounded(
        finding("the operator was verbally abused by their supervisor for weeks"),
        DESCRIPTION,
    ) is False


def test_empty_quote_fails():
    assert _grounded(finding(""), DESCRIPTION) is False


def test_missing_evidence_quote_key_fails():
    assert _grounded({"ai_category": "Psychosocial hazard"}, DESCRIPTION) is False


def test_none_finding_fails():
    assert _grounded(None, DESCRIPTION) is False


def test_one_character_difference_fails():
    # "abuse" -> "abused". A near-verbatim quote is still not a quote.
    assert _grounded(
        finding("repeated verbal abused from supervisor"), DESCRIPTION
    ) is False


def test_case_change_fails():
    # Pins the guard as case-sensitive. Any normalisation before the substring
    # test would let a rewritten quote through.
    assert _grounded(
        finding("Repeated Verbal Abuse From Supervisor"), DESCRIPTION
    ) is False


def test_quote_from_another_incident_fails():
    assert _grounded(
        finding("Dust exceedance recorded at crusher during shift change"),
        DESCRIPTION,
    ) is False
