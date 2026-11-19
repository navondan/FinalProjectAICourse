"""Traced entry point for the vendor extractor; usable without the agent graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import boto3

from novaops.core.config import Settings
from novaops.core.tracing import trace_workflow_run
from novaops.workflows.vendor.extractor import (
    extract_vendor_record,
    load_result_schema,
    source_type_for,
)


def run_vendor_extraction(
    settings: Settings,
    document: str,
    *,
    source_id: str,
    source_type: str | None = None,
    source_name: str | None = None,
    client: Any | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """One trace, one model call, one validated record."""
    resolved_type = source_type or source_type_for(source_name or source_id)
    schema = load_result_schema(settings.data_dir)
    bedrock = client or boto3.client("bedrock-runtime", region_name=settings.aws_region)
    with trace_workflow_run(
        settings,
        name="novaops.vendor",
        session_id=session_id or f"vendor-{source_id}",
        request_id=source_id,
        tags=["vendor", "workflow-3"],
        input={"source_id": source_id, "source_type": resolved_type},
    ) as root:
        result = extract_vendor_record(
            document,
            source_id=source_id,
            source_type=resolved_type,
            client=bedrock,
            model_id=settings.bedrock_model_id,
            schema=schema,
        )
        root.update(
            output=result,
            metadata={
                "workflow": "vendor",
                "status": "completed",
                "missing_required_count": len(result["missing_required_fields"]),
            },
        )
    return result


def run_vendor_file(settings: Settings, path: Path, **kwargs: Any) -> dict[str, Any]:
    return run_vendor_extraction(
        settings,
        path.read_text(encoding="utf-8"),
        source_id=kwargs.pop("source_id", path.stem),
        source_name=path.stem,
        **kwargs,
    )
