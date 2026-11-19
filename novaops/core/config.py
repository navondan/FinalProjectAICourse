"""Environment-backed project configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default)).expanduser()
    return value if value.is_absolute() else PROJECT_ROOT / value


def _positive_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {raw_value!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {raw_value!r}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    aws_region: str
    bedrock_model_id: str
    bedrock_embedding_model_id: str
    langfuse_public_key: str | None
    langfuse_secret_key: str | None
    langfuse_host: str
    data_dir: Path
    database_path: Path
    checkpoint_path: Path
    index_path: Path
    mcp_url: str
    as_of_date: date
    chunk_words: int
    overlap_words: int

    @classmethod
    def from_env(cls, *, load_env_file: bool = True) -> Settings:
        if load_env_file:
            load_dotenv(PROJECT_ROOT / ".env", override=False)

        try:
            as_of_date = date.fromisoformat(os.environ.get("NOVAOPS_AS_OF_DATE", "2026-07-01"))
        except ValueError as exc:
            raise ValueError("NOVAOPS_AS_OF_DATE must use YYYY-MM-DD format") from exc

        chunk_words = _positive_int("NOVAOPS_CHUNK_WORDS", 320)
        overlap_words = _positive_int("NOVAOPS_OVERLAP_WORDS", 80)
        if overlap_words >= chunk_words:
            raise ValueError("NOVAOPS_OVERLAP_WORDS must be smaller than NOVAOPS_CHUNK_WORDS")

        return cls(
            aws_region=os.environ.get("AWS_REGION", "us-east-1"),
            bedrock_model_id=os.environ.get(
                "BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0"
            ),
            bedrock_embedding_model_id=os.environ.get(
                "BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"
            ),
            langfuse_public_key=os.environ.get("LANGFUSE_PUBLIC_KEY") or None,
            langfuse_secret_key=os.environ.get("LANGFUSE_SECRET_KEY") or None,
            langfuse_host=os.environ.get(
                "LANGFUSE_HOST", "https://cloud.langfuse.com"
            ),
            data_dir=_path_from_env(
                "NOVAOPS_DATA_DIR", "novaops-enterprise-agent-dataset"
            ),
            database_path=_path_from_env(
                "NOVAOPS_DATABASE_PATH", "data/novaops.sqlite3"
            ),
            checkpoint_path=_path_from_env(
                "NOVAOPS_CHECKPOINT_PATH", "data/checkpoints.sqlite3"
            ),
            index_path=_path_from_env("NOVAOPS_INDEX_PATH", "data/index"),
            mcp_url=os.environ.get(
                "NOVAOPS_MCP_URL", "http://127.0.0.1:9876/mcp"
            ),
            as_of_date=as_of_date,
            chunk_words=chunk_words,
            overlap_words=overlap_words,
        )
