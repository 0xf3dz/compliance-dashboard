"""AI classification of incident descriptions (Anthropic Claude).

Run:  python -m ingestion.ai.classify [--force]

For each incident, Claude returns a structured classification. A grounding
guard rejects any response whose evidence_quote is not a verbatim substring of
the incident description; it retries once, then skips the incident and logs it
to data_quality_issues as ai_ungrounded. Only substring-verified findings reach
incident_ai_findings. Nothing is ever fabricated.

Repeat runs are free. Each finding stores the SHA-256 of the description it was
built from, so an unchanged incident is skipped without an API call. --force
deletes every finding and reclassifies from scratch.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from ingestion.ai.prompts import (
    CLASSIFY_TOOL,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from ingestion.db import apply_ai_schema, get_conn

try:  # the SDK is optional at import time so the grounding test needs no key
    import anthropic
except ImportError:  # pragma: no cover - exercised only on a bare interpreter
    anthropic = None

PRIMARY_MODEL = "claude-sonnet-4-6"
FALLBACK_MODEL = "claude-sonnet-4-5-20250929"

# Six concurrent calls finish 42 incidents in a few seconds and stay well
# inside the account rate limit. Serial runs took about 165 s.
MAX_WORKERS = 6

# Three attempts with 2, 4, 8 second sleeps. Longer than any 429 window the
# API applies to a batch this small.
MAX_ATTEMPTS = 3


def _sha256(text: str) -> str:
    """Digest of the description a finding was built from.

    api/src/routes/incidents.ts recomputes this in SQL, so a finding whose
    source text changed shows as stale instead of quietly wrong.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetch_incidents(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_row, incident_id, type_code, severity_rank, description"
            " FROM incidents ORDER BY source_row"
        )
        return cur.fetchall()


def _existing_digests(conn) -> dict[int, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT source_row, description_sha256 FROM incident_ai_findings")
        return {row[0]: row[1] for row in cur.fetchall()}


def _classify_one(client, model, incident_id, type_code, severity_rank, desc):
    """Call Claude once; return the tool-input dict or None if no tool use."""
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=0,
        system=SYSTEM_PROMPT,
        tools=[CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "record_classification"},
        messages=[
            {
                "role": "user",
                "content": build_user_prompt(
                    incident_id, type_code, severity_rank, desc
                ),
            }
        ],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_classification":
            return block.input
    return None


def _grounded(finding: dict, description: str) -> bool:
    quote = (finding or {}).get("evidence_quote", "")
    return bool(quote) and quote in description


def _is_retryable(exc: Exception) -> bool:
    """True for a rate limit or a server fault, which a later attempt may pass."""
    if anthropic is None:
        return False
    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    return False


def _call_with_backoff(client, model, incident):
    """One classification call, retried on 429 and on 5xx."""
    _source_row, incident_id, type_code, severity_rank, description = incident
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _classify_one(
                client, model, incident_id, type_code, severity_rank or 0, description
            )
        except Exception as exc:  # noqa: BLE001 - re-raised below when fatal
            if not _is_retryable(exc):
                raise
            last = exc
            time.sleep(2 ** attempt)
    raise last  # type: ignore[misc]


def _classify_with_grounding(client, model, incident):
    """Classify one incident, retrying once when the quote is not verbatim.

    Returns (finding, last_candidate). `finding` is None when both attempts
    produced a quote that is not a verbatim substring of the description.
    """
    description = incident[4]
    candidate = None
    for _attempt in range(2):  # one retry
        candidate = _call_with_backoff(client, model, incident)
        if candidate and _grounded(candidate, description):
            return candidate, candidate
    return None, candidate


def _resolve_model(client, incident) -> tuple[str, tuple]:
    """Classify the first incident, and learn which model this account serves.

    The draft spent a whole API call probing the model before doing any work.
    This does the same job with the first real classification: try the primary
    model, and switch to the fallback only when the account returns 404.
    """
    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            return model, _classify_with_grounding(client, model, incident)
        except Exception as exc:  # noqa: BLE001
            if anthropic is None or not isinstance(exc, anthropic.NotFoundError):
                raise
            if model == PRIMARY_MODEL:
                print(
                    f"Model {PRIMARY_MODEL} is not served on this account; "
                    f"falling back to {FALLBACK_MODEL}.",
                    file=sys.stderr,
                )
    print(
        f"Neither {PRIMARY_MODEL} nor {FALLBACK_MODEL} is served on this "
        "account. Set a model your key can reach and run again.",
        file=sys.stderr,
    )
    sys.exit(1)


UPSERT = """
INSERT INTO incident_ai_findings (incident_id, source_row, description_sha256,
    ai_category, is_psychosocial, severity_mismatch, mismatch_detail,
    evidence_quote, rationale, model, prompt_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_row) DO UPDATE SET
    incident_id        = EXCLUDED.incident_id,
    description_sha256 = EXCLUDED.description_sha256,
    ai_category        = EXCLUDED.ai_category,
    is_psychosocial    = EXCLUDED.is_psychosocial,
    severity_mismatch  = EXCLUDED.severity_mismatch,
    mismatch_detail    = EXCLUDED.mismatch_detail,
    evidence_quote     = EXCLUDED.evidence_quote,
    rationale          = EXCLUDED.rationale,
    model              = EXCLUDED.model,
    prompt_version     = EXCLUDED.prompt_version,
    created_at         = now()
"""

UNGROUNDED_INSERT = """
INSERT INTO data_quality_issues (source_file, source_row, record_ref,
    issue_type, field, raw_value, action, detail)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete every finding and reclassify, ignoring stored digests",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ANTHROPIC_API_KEY is not set. The AI layer needs a valid key.\n"
            "Layers 1, 2 and 4 run without it; the AI panel shows an empty "
            "state until this step runs. No findings are fabricated.",
            file=sys.stderr,
        )
        sys.exit(1)

    if anthropic is None:
        print("anthropic SDK not installed. Run pip install -r "
              "ingestion/requirements.txt", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    with get_conn() as conn:
        apply_ai_schema(conn)

        if args.force:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM incident_ai_findings")
            conn.commit()
            existing: dict[int, str] = {}
        else:
            existing = _existing_digests(conn)

        incidents = _fetch_incidents(conn)
        digests = {row[0]: _sha256(row[4]) for row in incidents}
        todo = [row for row in incidents if existing.get(row[0]) != digests[row[0]]]
        skipped_unchanged = len(incidents) - len(todo)

        if not todo:
            print(
                f"Nothing to do. 0 written, {skipped_unchanged} skipped_unchanged, "
                "0 ungrounded_skipped."
            )
            return

        model, first = _resolve_model(client, todo[0])
        print(
            f"Classifying {len(todo)} incidents with model {model} "
            f"(prompt {PROMPT_VERSION}, {MAX_WORKERS} workers)."
        )

        results: dict[int, tuple] = {todo[0][0]: (todo[0], *first)}
        errors: list[tuple[str, Exception]] = []

        if len(todo) > 1:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                futures = {
                    pool.submit(_classify_with_grounding, client, model, inc): inc
                    for inc in todo[1:]
                }
                for future, inc in futures.items():
                    try:
                        results[inc[0]] = (inc, *future.result())
                    except Exception as exc:  # noqa: BLE001
                        errors.append((inc[1], exc))

        # Clear this run's ungrounded rows before writing new ones, so a repeat
        # run cannot stack duplicates the way the draft did.
        run_rows = sorted(results)
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM data_quality_issues WHERE issue_type = 'ai_ungrounded'"
                " AND source_row = ANY(%s)",
                (run_rows,),
            )

        written = 0
        ungrounded = 0
        for source_row in run_rows:  # ascending, so output is deterministic
            incident, finding, candidate = results[source_row]
            _sr, incident_id, _type_code, _rank, description = incident

            if finding is None:
                ungrounded += 1
                bad_quote = "" if candidate is None else candidate.get(
                    "evidence_quote", ""
                )
                with conn.cursor() as cur:
                    cur.execute(
                        UNGROUNDED_INSERT,
                        (
                            "incident_register.csv",
                            source_row,
                            incident_id,
                            "ai_ungrounded",
                            "evidence_quote",
                            bad_quote,
                            "flagged",
                            "AI evidence_quote was not a verbatim substring of "
                            "the description after one retry; finding discarded.",
                        ),
                    )
                print(f"  ungrounded, skipped: {incident_id}")
                continue

            with conn.cursor() as cur:
                cur.execute(
                    UPSERT,
                    (
                        incident_id,
                        source_row,
                        _sha256(description),
                        finding["ai_category"],
                        finding["is_psychosocial"],
                        finding["severity_mismatch"],
                        finding.get("mismatch_detail"),
                        finding["evidence_quote"],
                        finding["rationale"],
                        model,
                        PROMPT_VERSION,
                    ),
                )
            written += 1
        conn.commit()

    print(
        f"Done. {written} written, {skipped_unchanged} skipped_unchanged, "
        f"{ungrounded} ungrounded_skipped."
    )
    if errors:
        print(f"{len(errors)} incidents failed and were not written:", file=sys.stderr)
        for incident_id, exc in errors:
            print(f"  {incident_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
