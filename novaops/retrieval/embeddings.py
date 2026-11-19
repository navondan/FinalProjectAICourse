"""Bedrock text embeddings used by the local FAISS index."""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError


class BedrockEmbedder:
    def __init__(
        self,
        model_id: str,
        region_name: str,
        *,
        client: Any | None = None,
    ) -> None:
        self.model_id = model_id
        self._client = client or boto3.client("bedrock-runtime", region_name=region_name)

    def embed(self, text: str) -> list[float]:
        try:
            response = self._client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({"inputText": text, "normalize": True}).encode("utf-8"),
                accept="application/json",
                contentType="application/json",
            )
        except (NoCredentialsError, PartialCredentialsError) as exc:
            raise RuntimeError(
                "AWS credentials are unavailable to the process running the NovaOps "
                "MCP server. Configure an AWS profile or the standard AWS credential "
                "chain in that process, then restart the server."
            ) from exc
        payload = json.loads(response["body"].read())
        embedding = payload.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise RuntimeError(
                f"Bedrock embedding response from {self.model_id!r} contained no embedding"
            )
        if any(not isinstance(value, (int, float)) for value in embedding):
            raise RuntimeError(
                f"Bedrock embedding response from {self.model_id!r} was not numeric"
            )
        return [float(value) for value in embedding]
