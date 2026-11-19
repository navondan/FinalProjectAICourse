"""Deterministic gate between an extracted supplier reply and any database write.

The model's extraction is a proposal from untrusted text. A reply is recordable only if the
supplier says it is final AND every term matches the internally approved scope AND every
extracted value is literally present in the email.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_NOT_FINAL = re.compile(
    r"not\s+(?:a\s+)?final|conditional\s+on|subject\s+to|signature\s+(?:is\s+)?(?:still\s+)?required"
    r"|until\s+(?:both\s+)?(?:conditions|these)|will\s+send\s+the\s+final|no\s+(?:provider\s+)?confirmation"
    r"|cannot\s+approve|declin",
    re.IGNORECASE,
)

NEXT_OWNER = {
    "conditional": "IT owner + Finance: resolve the supplier's stated conditions; nothing recorded",
    "rejected": "approver: decide the next step; the current contract is NOT cancelled",
    "ambiguous": "procurement: ask the supplier for final written confirmation and terms",
    "mismatch": "approver: terms differ from the approved scope; a new internal decision is required",
    "incomplete": "procurement: ask the supplier for the missing terms",
}


def evaluate_reply(
    extracted: Mapping[str, Any],
    *,
    reply_text: str,
    contract_id: str,
    approved_scope: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    category = "record"
    decision = extracted.get("decision")

    if decision != "approved":
        reasons.append(f"supplier decision is '{decision}', not a final approval")
        category = decision if decision in NEXT_OWNER else "ambiguous"
    if extracted.get("conditions"):
        reasons.append("supplier conditions outstanding: " + "; ".join(extracted["conditions"]))
        category = "conditional" if category == "record" else category
    if extracted.get("missing_fields"):
        reasons.append("reply is missing: " + ", ".join(extracted["missing_fields"]))
        category = "incomplete" if category == "record" else category
    if _NOT_FINAL.search(reply_text):
        reasons.append(f"reply text says it is not final: '{_NOT_FINAL.search(reply_text).group(0)}'")
        category = "ambiguous" if category == "record" else category

    if category == "record":
        checks = [
            (extracted.get("contract_id") == contract_id, f"contract {extracted.get('contract_id')} != {contract_id}"),
            (bool(extracted.get("confirmation_id")), "no supplier confirmation id"),
            (
                extracted.get("new_seat_limit") == approved_scope.get("seat_limit"),
                f"seats {extracted.get('new_seat_limit')} != approved {approved_scope.get('seat_limit')}",
            ),
            (
                isinstance(extracted.get("annual_cost_usd"), int)
                and extracted["annual_cost_usd"] <= int(approved_scope.get("max_annual_cost_usd", -1)),
                f"annual cost USD {extracted.get('annual_cost_usd')} exceeds approved ceiling "
                f"USD {approved_scope.get('max_annual_cost_usd')}",
            ),
            (
                extracted.get("term_start_date") == approved_scope.get("term_start_date")
                and extracted.get("term_end_date") == approved_scope.get("term_end_date"),
                f"term {extracted.get('term_start_date')}..{extracted.get('term_end_date')} != approved "
                f"{approved_scope.get('term_start_date')}..{approved_scope.get('term_end_date')}",
            ),
        ]
        mismatches = [message for ok, message in checks if not ok]
        if mismatches:
            reasons.extend(mismatches)
            category = "mismatch"
        else:
            ungrounded = _ungrounded(extracted, reply_text)
            if ungrounded:
                reasons.append("extracted values not found in the email: " + ", ".join(ungrounded))
                category = "mismatch"

    return {
        "verdict": "record" if category == "record" else "escalate",
        "category": category,
        "reasons": reasons,
        "next_owner": None if category == "record" else NEXT_OWNER[category],
    }


def _ungrounded(extracted: Mapping[str, Any], text: str) -> list[str]:
    digits = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    missing = []
    for key in ("contract_id", "confirmation_id", "term_start_date", "term_end_date"):
        if str(extracted.get(key)) not in text:
            missing.append(key)
    for key in ("new_seat_limit", "annual_cost_usd"):
        if not re.search(rf"(?<!\d){extracted.get(key)}(?!\d)", digits):
            missing.append(key)
    return missing
