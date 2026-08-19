"""Prompt, taxonomy, and tool schema for incident classification."""

PROMPT_VERSION = "v1"

# Fixed category taxonomy. The model MUST choose exactly one.
CATEGORIES = [
    "Vehicle / mobile plant interaction",
    "Slip / trip / fall",
    "Dust / air quality",
    "Environmental / hydrocarbon spill",
    "Equipment / mechanical failure",
    "Electrical / power",
    "Dropped object",
    "Psychosocial hazard",
    "Fatigue",
    "Other",
]

SYSTEM_PROMPT = """\
You are a mining safety classifier for compliance software at Ironbark Ridge \
Resources. You read a single incident description and return structured JSON.

Rules:
1. Choose exactly one category from the provided list.
2. Set is_psychosocial=true when the description shows a psychosocial hazard \
(bullying, verbal abuse, exclusion, harassment, sustained overload/overtime, \
stress, or similar), even if the original type code hides it as "OTH" or other.
3. Set severity_mismatch=true when the injury or harm the description states is \
clearly worse than the recorded severity rank (1=Low, 2=Medium, 3=High). \
Example: a fracture requiring surgery or a lost-time injury recorded as rank 1.
4. evidence_quote MUST be an exact, verbatim substring copied from the \
description. Do not paraphrase. Do not add or change any character. This quote \
is the audit trail: every finding must trace back to the source text.
5. rationale: one short sentence explaining the decision.

Never invent facts that are not in the description."""


def build_user_prompt(incident_id: str, type_code: str, severity_rank: int,
                      description: str) -> str:
    return (
        f"Incident ID: {incident_id}\n"
        f"Original type code: {type_code}\n"
        f"Recorded severity rank: {severity_rank} "
        f"(1=Low, 2=Medium, 3=High)\n"
        f"Description:\n{description}\n\n"
        f"Categories to choose from: {CATEGORIES}\n"
        f"Return the classification via the record_classification tool."
    )


# Anthropic tool schema forcing structured output.
CLASSIFY_TOOL = {
    "name": "record_classification",
    "description": "Record the structured classification of one incident.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ai_category": {"type": "string", "enum": CATEGORIES},
            "is_psychosocial": {"type": "boolean"},
            "severity_mismatch": {"type": "boolean"},
            "mismatch_detail": {
                "type": ["string", "null"],
                "description": "Why severity is understated, or null.",
            },
            "evidence_quote": {
                "type": "string",
                "description": "Verbatim substring of the description.",
            },
            "rationale": {"type": "string"},
        },
        "required": [
            "ai_category",
            "is_psychosocial",
            "severity_mismatch",
            "evidence_quote",
            "rationale",
        ],
    },
}
