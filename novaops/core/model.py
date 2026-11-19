"""The single Bedrock model-call boundary."""

from __future__ import annotations

from typing import Any

import boto3

from novaops.core.config import Settings


class BedrockModel:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.model_id = settings.bedrock_model_id
        self._client = client or boto3.client(
            "bedrock-runtime", region_name=settings.aws_region
        )

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> str:
        request: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            request["system"] = [{"text": system}]

        response = self._client.converse(**request)
        content = response.get("output", {}).get("message", {}).get("content", [])
        text = "\n".join(
            block["text"] for block in content if isinstance(block, dict) and "text" in block
        ).strip()
        if not text:
            raise RuntimeError(f"Bedrock model {self.model_id!r} returned no text")
        return text
