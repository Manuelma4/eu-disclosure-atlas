"""Message bus: a tiny producer interface, implemented by Kafka (confluent-kafka) and by a fake in tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class Bus(Protocol):
    def produce(self, topic: str, key: bytes, value: bytes, headers: list[tuple[str, bytes]]) -> None: ...

    def flush(self) -> None:
        """Block until every produced message is acknowledged; raise if any delivery failed."""
        ...


class DeliveryError(RuntimeError):
    pass


@dataclass
class KafkaBus:
    """Idempotent Kafka producer: acks from all in-sync replicas, no duplicates on retries, ordered per key."""

    bootstrap: str
    client_id: str = "disclosure-atlas-publisher"
    extra: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        try:
            from confluent_kafka import Producer
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise RuntimeError("Kafka publishing needs the events extra: pip install -e '.[events]'") from exc
        self._errors: list[str] = []
        self._producer = Producer({
            "bootstrap.servers": self.bootstrap,
            "client.id": self.client_id,
            "enable.idempotence": True,
            "acks": "all",
            "compression.type": "zstd",
            "linger.ms": 20,
            "delivery.timeout.ms": 120_000,
            **(self.extra or {}),
        })

    def _on_delivery(self, err: Any, msg: Any) -> None:
        if err is not None:
            self._errors.append(f"{msg.topic()}: {err}")

    def produce(self, topic: str, key: bytes, value: bytes, headers: list[tuple[str, bytes]]) -> None:
        while True:
            try:
                self._producer.produce(topic, key=key, value=value, headers=headers, on_delivery=self._on_delivery)
                break
            except BufferError:  # local queue full: serve delivery reports, then retry
                self._producer.poll(0.5)
        self._producer.poll(0)

    def flush(self) -> None:
        remaining = self._producer.flush(60)
        if remaining:
            raise DeliveryError(f"{remaining} messages not acknowledged after 60 s")
        if self._errors:
            errors, self._errors = self._errors, []
            raise DeliveryError("; ".join(errors[:5]))
