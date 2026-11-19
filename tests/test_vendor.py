"""Workflow 3 — vendor extractor. Deterministic: a fake converse client, real schema."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from novaops.workflows.vendor.extractor import (
    TOOL_NAME,
    VendorExtractionError,
    build_result,
    extract_vendor_record,
    field_names,
    load_result_schema,
    normalize_field,
    source_type_for,
    tool_spec,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "novaops-enterprise-agent-dataset"
SOURCES = DATA_DIR / "workflows" / "vendor" / "sources"
SCHEMA = load_result_schema(DATA_DIR)

# What a well-behaved model proposes for each source: raw, un-normalized values.
PROPOSALS: dict[str, dict[str, Any]] = {
    "formal_vendor_intake": {
        "record": {
            "vendor_name": "HelioDesk",
            "legal_name": "HelioDesk Technologies Ltd.",
            "primary_contact_name": "Ava Patel",
            "primary_contact_email": "ava.patel@heliodesk.example",
            "service_category": "Customer support quality assurance SaaS",
            "internal_owner_department": "Customer Success",
            "annual_cost_usd": "USD 28,800",
            "contract_start_date": "October 1, 2026",
            "contract_end_date": "September 30, 2027",
            "renewal_type": "Manual renewal",
            "payment_terms": "Net 30 days from invoice",
            "data_sensitivity": "confidential",
            "security_review_required": "Yes",
            "website": "https://www.heliodesk.example",
            "contact_phone": "+44 20 7946 0281",
            "headquarters": "London, United Kingdom",
            "implementation_notes": "Pilot covers twenty SupportDesk agents.",
            "logo": "purple lighthouse",
        },
        "conflicts": [],
        "follow_up_questions": [],
    },
    "procurement_email_chain": {
        "record": {
            "vendor_name": "RoutePilot",
            "legal_name": "RoutePilot Systems Inc.",
            "primary_contact_name": "Miguel Santos",
            "primary_contact_email": "miguel.santos@routepilot.example",
            "service_category": "SaaS spend and renewal analytics platform",
            "internal_owner_department": "Finance",
            "annual_cost_usd": 18600,
            "contract_start_date": "15 November 2026",
            "contract_end_date": "2027-11-14",
            "renewal_type": "auto-renews annually",
            "payment_terms": None,
            "data_sensitivity": "Internal",
            "security_review_required": False,
            "website": None,
            "contact_phone": None,
            "headquarters": None,
            "implementation_notes": None,
        },
        "conflicts": [],
        "follow_up_questions": [
            {"field": "payment_terms", "question": "What are RoutePilot's invoice payment terms?"},
            {"field": "website", "question": "What is the website?"},
        ],
    },
    "discovery_call_transcript": {
        "record": {
            "vendor_name": "CipherNest",
            "legal_name": "not stated",
            "primary_contact_name": "Nora Fischer",
            "primary_contact_email": "nora.fischer@ciphernest.example",
            "service_category": "Secure managed file-transfer software",
            "internal_owner_department": "IT",
            "annual_cost_usd": 31200.0,
            "contract_start_date": "2026-12-01",
            "contract_end_date": "2027-11-30",
            "renewal_type": "manual",
            "payment_terms": "Net 45",
            "data_sensitivity": "Restricted",
            "security_review_required": None,
            "website": None,
            "contact_phone": "+49 30 5557 0184",
            "headquarters": None,
            "implementation_notes": "Three-week technical setup.",
        },
        "conflicts": [],
        "follow_up_questions": [],
    },
}


class FakeConverse:
    def __init__(self, proposal: dict[str, Any], *, call_tool: bool = True) -> None:
        self.proposal = proposal
        self.call_tool = call_tool
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        content = (
            [{"toolUse": {"toolUseId": "t1", "name": TOOL_NAME, "input": self.proposal}}]
            if self.call_tool
            else [{"text": "I think the vendor is fine."}]
        )
        return {
            "output": {"message": {"role": "assistant", "content": content}},
            "stopReason": "tool_use" if self.call_tool else "end_turn",
            "usage": {"inputTokens": 900, "outputTokens": 300},
        }


def _extract(name: str, client: FakeConverse | None = None) -> dict[str, Any]:
    client = client or FakeConverse(PROPOSALS[name])
    return extract_vendor_record(
        (SOURCES / f"{name}.md").read_text(encoding="utf-8"),
        source_id=name,
        source_type=source_type_for(name),
        client=client,
        model_id="test-model",
        schema=SCHEMA,
    )


@pytest.mark.parametrize(
    ("name", "expected_missing"),
    [
        ("formal_vendor_intake", []),
        ("procurement_email_chain", ["payment_terms"]),
        ("discovery_call_transcript", ["legal_name", "security_review_required"]),
    ],
)
def test_sources_produce_0_1_2_missing_required_fields(name, expected_missing):
    result = _extract(name)
    assert result["missing_required_fields"] == expected_missing
    assert [item["field"] for item in result["follow_up_questions"]] == expected_missing


def test_every_schema_key_present_with_null_for_unstated():
    required, optional = field_names(SCHEMA)
    record = _extract("procurement_email_chain")["record"]
    assert set(record) == set(required) | set(optional)
    assert record["website"] is None and record["headquarters"] is None


def test_optional_omissions_create_no_follow_up():
    result = _extract("procurement_email_chain")
    assert result["record"]["contact_phone"] is None
    assert all(item["field"] == "payment_terms" for item in result["follow_up_questions"])


def test_false_is_a_stated_decision_not_a_missing_field():
    result = _extract("procurement_email_chain")
    assert result["record"]["security_review_required"] is False
    assert "security_review_required" not in result["missing_required_fields"]


def test_normalizes_dates_cost_and_enums():
    record = _extract("formal_vendor_intake")["record"]
    assert record["annual_cost_usd"] == 28800
    assert record["contract_start_date"] == "2026-10-01"
    assert record["contract_end_date"] == "2027-09-30"
    assert record["renewal_type"] == "manual"
    assert record["data_sensitivity"] == "Confidential"
    assert record["security_review_required"] is True
    assert _extract("procurement_email_chain")["record"]["renewal_type"] == "automatic"
    assert _extract("discovery_call_transcript")["record"]["annual_cost_usd"] == 31200


def test_irrelevant_detail_outside_schema_is_dropped():
    result = _extract("formal_vendor_intake")
    assert "logo" not in result["record"]
    assert "lighthouse" not in json.dumps(result)


def test_exactly_one_forced_tool_call():
    client = FakeConverse(PROPOSALS["formal_vendor_intake"])
    _extract("formal_vendor_intake", client)
    assert len(client.requests) == 1
    tool_config = client.requests[0]["toolConfig"]
    assert tool_config["toolChoice"] == {"tool": {"name": TOOL_NAME}}
    assert client.requests[0]["inferenceConfig"]["temperature"] == 0.0


def test_no_tool_call_fails_without_retry():
    client = FakeConverse(PROPOSALS["formal_vendor_intake"], call_tool=False)
    with pytest.raises(VendorExtractionError, match="did not call"):
        _extract("formal_vendor_intake", client)
    assert len(client.requests) == 1


def test_missing_fields_are_recomputed_not_trusted():
    proposal = json.loads(json.dumps(PROPOSALS["formal_vendor_intake"]))
    proposal["record"]["payment_terms"] = None
    proposal["missing_required_fields"] = []
    result = build_result(
        proposal, source_id="x", source_type="formal_document", schema=SCHEMA
    )
    assert result["missing_required_fields"] == ["payment_terms"]


def test_unnormalizable_value_becomes_null_and_is_reported():
    value, problem = normalize_field("contract_start_date", "sometime next spring")
    assert value is None and "contract_start_date" in problem
    value, problem = normalize_field("primary_contact_email", "call Nora")
    assert value is None and problem


def test_local_schema_validation_rejects_bad_source_type():
    with pytest.raises(VendorExtractionError, match="source_type"):
        build_result(
            PROPOSALS["formal_vendor_intake"],
            source_id="x",
            source_type="fax",
            schema=SCHEMA,
        )


def test_tool_schema_has_no_refs_or_extension_keys():
    rendered = json.dumps(tool_spec(SCHEMA))
    assert "$ref" not in rendered and "x-crm-required" not in rendered


def test_vendor_module_runs_without_the_agent_graph():
    code = (
        "import sys, novaops.workflows.vendor.service, novaops.workflows.vendor.extractor;"
        "bad=[m for m in sys.modules if m.startswith(('novaops.agent','langgraph','langchain'))];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
