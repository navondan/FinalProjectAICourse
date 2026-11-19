"""The two replaceable ports. The mocks are the required implementations for this project."""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from email import message_from_string, policy
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Protocol

PROVIDER_DIR = Path("workflows/renewal")


class DeliveryUncertain(RuntimeError):
    """The send may or may not have happened (timeout, lost ack). Never report it as sent."""


class DeliveryFailed(RuntimeError):
    """The send definitely did not happen; safe to retry later."""


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    message_key: str
    channel: str
    recipient: str
    subject: str
    body: str
    headers: dict[str, str] = field(default_factory=dict)


class NotificationAdapter(Protocol):
    """Internal side: approval/clearance events in, internal notifications out."""

    def receive_events(self) -> list[dict[str, Any]]: ...

    def send(self, message: OutboundMessage) -> None: ...


class ProviderEmailAdapter(Protocol):
    """Supplier side: renewal requests out, supplier replies in."""

    def send_request(self, message: OutboundMessage) -> None: ...

    def receive_reply(self) -> str: ...


@dataclass
class MockNotificationAdapter:
    events: list[dict[str, Any]] = field(default_factory=list)
    sent: list[OutboundMessage] = field(default_factory=list)
    uncertain_keys: set[str] = field(default_factory=set)
    failing_keys: set[str] = field(default_factory=set)
    outbox_file: Path | None = None

    @classmethod
    def from_jsonl(cls, path: Path, *, event_ids: Iterable[str] | None = None, **kwargs: Any) -> MockNotificationAdapter:
        wanted = set(event_ids) if event_ids is not None else None
        events = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if wanted is not None:
            events = [event for event in events if event["event_id"] in wanted]
        return cls(events=events, **kwargs)

    def receive_events(self) -> list[dict[str, Any]]:
        return list(self.events)

    def send(self, message: OutboundMessage) -> None:
        _deliver(self, message)


@dataclass
class MockProviderEmailAdapter:
    """Replies are chosen by explicit fixture id, by seeded weighted draw, or given raw."""

    data_dir: Path
    fixture_id: str | None = None
    seed: int | None = None
    raw_reply: str | None = None
    sent: list[OutboundMessage] = field(default_factory=list)
    uncertain_keys: set[str] = field(default_factory=set)
    failing_keys: set[str] = field(default_factory=set)
    outbox_file: Path | None = None

    def manifest(self) -> list[dict[str, Any]]:
        path = self.data_dir / PROVIDER_DIR / "provider_reply_manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def send_request(self, message: OutboundMessage) -> None:
        _deliver(self, message)

    def receive_reply(self) -> str:
        if self.raw_reply is not None:
            return self.raw_reply
        manifest = self.manifest()
        if self.fixture_id is not None:
            entry = next((item for item in manifest if item["fixture_id"] == self.fixture_id), None)
            if entry is None:
                raise KeyError(f"Unknown provider reply fixture {self.fixture_id!r}")
        else:
            rng = random.Random(self.seed)
            entry = rng.choices(manifest, weights=[item["demo_weight"] for item in manifest])[0]
        return (self.data_dir / PROVIDER_DIR / entry["path"]).read_text(encoding="utf-8")


def _deliver(adapter: Any, message: OutboundMessage) -> None:
    if message.message_key in adapter.failing_keys:
        raise DeliveryFailed(f"transport refused {message.message_key}")
    if message.message_key in adapter.uncertain_keys:
        raise DeliveryUncertain(f"no acknowledgement for {message.message_key}")
    adapter.sent.append(message)
    if adapter.outbox_file is not None:
        adapter.outbox_file.parent.mkdir(parents=True, exist_ok=True)
        with adapter.outbox_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(message), ensure_ascii=False) + "\n")


@dataclass(frozen=True, slots=True)
class ParsedReply:
    message_id: str
    in_reply_to: str | None
    fixture_id: str | None
    subject: str
    received_on: str
    body: str
    raw: str


def parse_reply(raw: str) -> ParsedReply:
    message = message_from_string(raw.strip() + "\n", policy=policy.default)
    message_id = (message.get("Message-ID") or "").strip()
    if not message_id:
        raise ValueError("Provider reply has no Message-ID; it cannot be de-duplicated")
    date_header = message.get("Date")
    received_on = parsedate_to_datetime(date_header).date().isoformat() if date_header else ""
    body_part = message.get_body(preferencelist=("plain",)) if message.is_multipart() else message
    body = body_part.get_content() if body_part is not None else ""
    return ParsedReply(
        message_id=message_id,
        in_reply_to=(message.get("In-Reply-To") or "").strip() or None,
        fixture_id=(message.get("X-NovaOps-Fixture-ID") or "").strip() or None,
        subject=str(message.get("Subject") or ""),
        received_on=received_on,
        body=body.strip(),
        raw=raw,
    )
