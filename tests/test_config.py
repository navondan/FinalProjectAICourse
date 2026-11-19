from datetime import date
from pathlib import Path

import pytest

from novaops.core.config import PROJECT_ROOT, Settings


def test_settings_use_reproducible_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AWS_REGION",
        "BEDROCK_MODEL_ID",
        "BEDROCK_EMBEDDING_MODEL_ID",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "NOVAOPS_DATA_DIR",
        "NOVAOPS_DATABASE_PATH",
        "NOVAOPS_CHECKPOINT_PATH",
        "NOVAOPS_INDEX_PATH",
        "NOVAOPS_MCP_URL",
        "NOVAOPS_AS_OF_DATE",
        "NOVAOPS_CHUNK_WORDS",
        "NOVAOPS_OVERLAP_WORDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env(load_env_file=False)

    assert settings.as_of_date == date(2026, 7, 1)
    assert settings.data_dir == PROJECT_ROOT / "novaops-enterprise-agent-dataset"
    assert settings.database_path == PROJECT_ROOT / "data" / "novaops.sqlite3"
    assert settings.chunk_words == 320
    assert settings.overlap_words == 80
    assert settings.mcp_url == "http://127.0.0.1:9876/mcp"
    assert settings.langfuse_public_key is None


def test_settings_resolve_relative_paths_from_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVAOPS_DATA_DIR", "custom-data")
    monkeypatch.setenv("NOVAOPS_AS_OF_DATE", "2026-08-15")

    settings = Settings.from_env(load_env_file=False)

    assert settings.data_dir == PROJECT_ROOT / "custom-data"
    assert settings.as_of_date == date(2026, 8, 15)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("NOVAOPS_AS_OF_DATE", "yesterday", "YYYY-MM-DD"),
        ("NOVAOPS_CHUNK_WORDS", "0", "positive integer"),
        ("NOVAOPS_CHUNK_WORDS", "not-a-number", "positive integer"),
    ],
)
def test_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        Settings.from_env(load_env_file=False)


def test_settings_require_overlap_smaller_than_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOVAOPS_CHUNK_WORDS", "80")
    monkeypatch.setenv("NOVAOPS_OVERLAP_WORDS", "80")

    with pytest.raises(ValueError, match="smaller than"):
        Settings.from_env(load_env_file=False)


def test_absolute_paths_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    absolute_path = Path("C:/novaops-data")
    monkeypatch.setenv("NOVAOPS_DATA_DIR", str(absolute_path))

    settings = Settings.from_env(load_env_file=False)

    assert settings.data_dir == absolute_path
