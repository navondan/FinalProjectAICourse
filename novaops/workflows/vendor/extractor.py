"""Workflow 3 — vendor CRM extraction.

Document in, validated record out. One raw Bedrock ``converse`` call per source with a
forced ``submit_vendor_record`` tool call, then deterministic normalization and local JSON
Schema validation. No agent, no LangChain/LangGraph, no tools, no database writes: this
module must keep working if the whole agent graph is deleted.

The model *proposes* field values; code decides what is missing. ``missing_required_fields``
is recomputed from the normalized record, never copied from the model.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from novaops.core.tracing import workflow_step

TOOL_NAME = "submit_vendor_record"
SCHEMA_RELATIVE_PATH = Path("workflows/vendor/schemas/vendor_extraction_result.schema.json")
SOURCE_TYPES = {
    "formal_vendor_intake": "formal_document",
    "procurement_email_chain": "email_chain",
    "discovery_call_transcript": "call_transcript",
}
MAX_DOCUMENT_CHARS = 40_000

SYSTEM_PROMPT = """You extract a vendor CRM record from ONE procurement source document.

Rules:
- Use only facts the document states explicitly. If a field is not stated, use null.
  Never infer a value from the vendor's name, category, industry convention, or from what
  is "usually" true. A person saying they will confirm something later means it is unstated.
- vendor_name is the display name; legal_name is the registered legal company name only if
  the document gives it.
- Dates: YYYY-MM-DD. annual_cost_usd: an integer number of US dollars (no symbols, no
  words, no decimals).
- renewal_type: one of automatic, manual, usage_based, ambiguous, or null.
- data_sensitivity: one of Public, Internal, Confidential, Restricted, Highly Restricted, or null.
- security_review_required: true or false only if the document states the decision; else null.
- payment_terms: the invoice terms as written (for example "Net 30").
- implementation_notes: only implementation or rollout facts. Ignore marketing claims, awards,
  logos, demos, scheduling, colours, food, anecdotes and anything outside the schema.
- conflicts: list contradictions inside the document (empty list if none).
- follow_up_questions: one short, specific question per REQUIRED field you left null.
Call the submit_vendor_record tool exactly once."""

_NULL_TOKENS = {"", "null", "none", "n/a", "na", "unknown", "not stated", "not provided", "tbd"}
_DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FOLLOW_UP_TEMPLATES = {
    "vendor_name": "What vendor name should appear in the CRM?",
    "legal_name": "What is the vendor's registered legal company name for the contract?",
    "primary_contact_name": "Who is the vendor's primary contact for this engagement?",
    "primary_contact_email": "What email address should we use for the vendor's primary contact?",
    "service_category": "What service category does this vendor provide?",
    "internal_owner_department": "Which NovaOps department will own this vendor relationship?",
    "annual_cost_usd": "What is the annual contract cost in US dollars?",
    "contract_start_date": "What is the contract start date?",
    "contract_end_date": "What is the contract end date?",
    "renewal_type": "Does the contract renew automatically, manually, or based on usage?",
    "payment_terms": "What are the invoice payment terms (for example, Net 30)?",
    "data_sensitivity": "What data sensitivity classification applies to this vendor?",
    "security_review_required": "Has NovaOps decided whether a security review is required?",
}


class VendorExtractionError(RuntimeError):
    """The model did not return a record that passes local validation."""


def load_result_schema(data_dir: Path) -> dict[str, Any]:
    return json.loads((data_dir / SCHEMA_RELATIVE_PATH).read_text(encoding="utf-8"))


def field_names(schema: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return (required, optional) CRM field names from the schema's x-crm-required flags."""
    properties = schema["$defs"]["vendor_crm_record"]["properties"]
    required = [name for name, spec in properties.items() if spec.get("x-crm-required")]
    optional = [name for name, spec in properties.items() if not spec.get("x-crm-required")]
    return required, optional


def source_type_for(source_name: str) -> str:
    stem = Path(source_name).stem
    try:
        return SOURCE_TYPES[stem]
    except KeyError:
        raise ValueError(
            f"Unknown vendor source {source_name!r}; pass source_type explicitly "
            f"({sorted(set(SOURCE_TYPES.values()))})"
        ) from None


def tool_spec(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The forced tool: the record plus conflicts and follow-ups, with $refs inlined."""
    record = _inline_refs(schema["$defs"]["vendor_crm_record"], schema["$defs"])
    required_field = _inline_refs(schema["$defs"]["required_field_name"], schema["$defs"])
    return {
        "toolSpec": {
            "name": TOOL_NAME,
            "description": "Submit the vendor CRM record extracted from the source document.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "required": ["record", "conflicts", "follow_up_questions"],
                    "properties": {
                        "record": record,
                        "conflicts": {"type": "array", "items": {"type": "string"}},
                        "follow_up_questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["field", "question"],
                                "properties": {
                                    "field": required_field,
                                    "question": {"type": "string"},
                                },
                            },
                        },
                    },
                }
            },
        }
    }


def extract_vendor_record(
    document: str,
    *,
    source_id: str,
    source_type: str,
    client: Any,
    model_id: str,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract, normalize and validate one source. Exactly one model call."""
    if not document.strip():
        raise ValueError("Vendor source document must not be empty")
    if len(document) > MAX_DOCUMENT_CHARS:
        raise ValueError(f"Vendor source document exceeds {MAX_DOCUMENT_CHARS} characters")
    if source_type not in SOURCE_TYPES.values():
        raise ValueError(f"Unsupported source_type {source_type!r}")

    proposal = _call_model(document, source_type=source_type, client=client, model_id=model_id, schema=schema)
    with workflow_step("vendor.normalize_and_validate", input={"source_id": source_id}) as step:
        result = build_result(proposal, source_id=source_id, source_type=source_type, schema=schema)
        step.update(
            output={
                "missing_required_fields": result["missing_required_fields"],
                "conflicts": result["conflicts"],
            }
        )
    return result


def _call_model(
    document: str,
    *,
    source_type: str,
    client: Any,
    model_id: str,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    request = {
        "modelId": model_id,
        "system": [{"text": SYSTEM_PROMPT}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "text": f"Source type: {source_type}\n\n<document>\n{document}\n</document>"
                    }
                ],
            }
        ],
        "toolConfig": {
            "tools": [tool_spec(schema)],
            "toolChoice": {"tool": {"name": TOOL_NAME}},
        },
        "inferenceConfig": {"maxTokens": 1500, "temperature": 0.0},
    }
    with workflow_step(
        "vendor.converse",
        as_type="generation",
        model=model_id,
        input={"source_type": source_type, "document_chars": len(document)},
    ) as generation:
        response = client.converse(**request)
        usage = response.get("usage", {})
        tool_input = _tool_input(response)
        generation.update(
            output=tool_input,
            usage_details={
                "input": int(usage.get("inputTokens", 0)),
                "output": int(usage.get("outputTokens", 0)),
            },
        )
    return tool_input


def _tool_input(response: Mapping[str, Any]) -> dict[str, Any]:
    content = response.get("output", {}).get("message", {}).get("content", [])
    for block in content:
        tool_use = block.get("toolUse") if isinstance(block, dict) else None
        if tool_use and tool_use.get("name") == TOOL_NAME:
            payload = tool_use.get("input")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                raise VendorExtractionError("submit_vendor_record input is not an object")
            return payload
    raise VendorExtractionError(
        f"Model did not call {TOOL_NAME} (stopReason={response.get('stopReason')!r})"
    )


def build_result(
    proposal: Mapping[str, Any],
    *,
    source_id: str,
    source_type: str,
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Turn the model's proposal into a schema-valid result. Raises on invalid output."""
    required, optional = field_names(schema)
    raw_record = proposal.get("record")
    if not isinstance(raw_record, Mapping):
        raw_record = {}
    conflicts = [
        conflict.strip()
        for conflict in proposal.get("conflicts") or []
        if isinstance(conflict, str) and conflict.strip()
    ]

    record: dict[str, Any] = {}
    for name in [*required, *optional]:
        value, problem = normalize_field(name, raw_record.get(name))
        record[name] = value
        if problem:
            conflicts.append(problem)

    missing = [name for name in required if record[name] is None]
    model_questions: dict[str, str] = {}
    for item in proposal.get("follow_up_questions") or []:
        if not isinstance(item, Mapping):
            continue
        field, question = item.get("field"), item.get("question")
        if field in missing and isinstance(question, str) and question.strip():
            model_questions.setdefault(field, question.strip())
    follow_ups = [
        {"field": name, "question": model_questions.get(name, _FOLLOW_UP_TEMPLATES[name])}
        for name in missing
    ]

    result = {
        "source_id": source_id,
        "source_type": source_type,
        "record": record,
        "missing_required_fields": missing,
        "conflicts": conflicts,
        "follow_up_questions": follow_ups,
    }
    validate_result(result, schema)
    return result


def validate_result(result: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(result), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors[:5]
        )
        raise VendorExtractionError(f"Vendor result failed schema validation: {details}")


def normalize_field(name: str, value: Any) -> tuple[Any, str | None]:
    """Return (normalized value, problem). A value that cannot be normalized becomes null."""
    if isinstance(value, str) and value.strip().lower() in _NULL_TOKENS:
        return None, None
    if value is None:
        return None, None
    if name == "annual_cost_usd":
        return _normalize_cost(value)
    if name in {"contract_start_date", "contract_end_date"}:
        return _normalize_date(name, value)
    if name == "security_review_required":
        return _normalize_bool(value)
    if name == "renewal_type":
        return _normalize_renewal_type(value)
    if name == "data_sensitivity":
        return _normalize_sensitivity(value)
    if not isinstance(value, str):
        value = str(value)
    value = " ".join(value.split())
    if name == "primary_contact_email":
        value = value.strip("<>").lower()
        if not _EMAIL.match(value):
            return None, f"primary_contact_email {value!r} is not a valid email address"
    return value, None


def _normalize_cost(value: Any) -> tuple[int | None, str | None]:
    if isinstance(value, bool):
        return None, f"annual_cost_usd {value!r} is not an amount"
    if isinstance(value, (int, float)):
        return (int(round(value)), None) if value >= 0 else (None, "annual_cost_usd is negative")
    text = str(value).lower().replace(",", "").replace("usd", "").replace("$", "").strip()
    multiplier = 1
    if text.endswith("k"):
        multiplier, text = 1000, text[:-1].strip()
    try:
        amount = float(text) * multiplier
    except ValueError:
        return None, f"annual_cost_usd {value!r} could not be normalized to integer USD"
    return int(round(amount)), None


def _normalize_date(name: str, value: Any) -> tuple[str | None, str | None]:
    text = str(value).strip()
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date().isoformat(), None
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10]).isoformat(), None
    except ValueError:
        return None, f"{name} {value!r} could not be normalized to YYYY-MM-DD"


def _normalize_bool(value: Any) -> tuple[bool | None, str | None]:
    if isinstance(value, bool):
        return value, None
    text = str(value).strip().lower()
    if text in {"yes", "true", "required", "y"}:
        return True, None
    if text in {"no", "false", "not required", "n"}:
        return False, None
    return None, f"security_review_required {value!r} is not a yes/no decision"


def _normalize_renewal_type(value: Any) -> tuple[str | None, str | None]:
    text = str(value).strip().lower()
    for token, normalized in (
        ("ambiguous", "ambiguous"),
        ("usage", "usage_based"),
        ("manual", "manual"),
        ("auto", "automatic"),
    ):
        if token in text:
            return normalized, None
    return None, f"renewal_type {value!r} is not a supported renewal type"


def _normalize_sensitivity(value: Any) -> tuple[str | None, str | None]:
    allowed = ("Highly Restricted", "Public", "Internal", "Confidential", "Restricted")
    text = " ".join(str(value).split()).lower()
    for label in allowed:
        if text == label.lower():
            return label, None
    return None, f"data_sensitivity {value!r} is not a supported classification"


def _inline_refs(node: Any, definitions: Mapping[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            target = definitions[node["$ref"].rsplit("/", 1)[-1]]
            merged = {**copy.deepcopy(target), **{k: v for k, v in node.items() if k != "$ref"}}
            return _inline_refs(merged, definitions)
        return {
            key: _inline_refs(value, definitions)
            for key, value in node.items()
            if not key.startswith("x-")
        }
    if isinstance(node, list):
        return [_inline_refs(item, definitions) for item in node]
    return node
