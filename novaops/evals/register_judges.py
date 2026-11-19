"""Dry-run or register Langfuse LLM evaluators for NovaOps."""

from __future__ import annotations

import argparse
import json
from typing import Any

from langfuse import Langfuse
from langfuse.api.evaluators.types.create_evaluator_request import (
    CreateEvaluatorRequest_LlmAsJudge,
)

from novaops.core.config import Settings
from novaops.evals.judges import JUDGE_RUBRICS


def build_judge_requests(model_id: str) -> list[CreateEvaluatorRequest_LlmAsJudge]:
    requests = []
    for name, rubric in JUDGE_RUBRICS.items():
        requests.append(
            CreateEvaluatorRequest_LlmAsJudge(
                name=f"novaops-{name.replace('_', '-')}",
                description=f"NovaOps evaluation rubric: {name}",
                prompt=[
                    {
                        "role": "system",
                        "content": (
                            "Evaluate the NovaOps trace using the rubric. Return only "
                            "a numeric score between 0 and 1 and a concise reason."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Rubric:\n{rubric}\n\n"
                            "Trace input: {{input}}\n"
                            "Trace output: {{output}}\n"
                            "Trace metadata: {{metadata}}\n"
                            "Tool calls: {{tool_calls}}"
                        ),
                    },
                ],
                model_config_={"provider": "bedrock", "model": model_id},
                variable_mapping=[
                    {"variable": "input", "source": "input"},
                    {"variable": "output", "source": "output"},
                    {"variable": "metadata", "source": "metadata"},
                    {"variable": "tool_calls", "source": "tool_calls"},
                ],
                output_definition={
                    "data_type": "NUMERIC",
                    "min_value": 0.0,
                    "max_value": 1.0,
                    "score_value_instructions": (
                        "Return 0 for a material violation and 1 when the answer "
                        "satisfies the rubric."
                    ),
                },
            )
        )
    return requests


def register_judges(*, enable: bool = False) -> list[dict[str, Any]]:
    settings = Settings.from_env()
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise ValueError(
            "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to register judges"
        )
    requests = build_judge_requests(settings.bedrock_model_id)
    if not enable:
        return [
            request.model_dump(by_alias=True, exclude_none=True)
            for request in requests
        ]

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        tracing_enabled=False,
    )
    existing_page = client.api.evaluators.list(limit=100)
    existing_ids = {evaluator.name: evaluator.id for evaluator in existing_page.data}
    results = []
    for request in requests:
        evaluator_id = existing_ids.get(request.name)
        if evaluator_id is None:
            evaluator_id = client.api.evaluators.create(request=request).id
        results.append({"name": request.name, "id": evaluator_id})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    print(json.dumps(register_judges(enable=args.enable), indent=2))


if __name__ == "__main__":
    main()
