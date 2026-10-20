"""Schema registry client (Confluent-compatible REST API, served by Redpanda locally)."""

from __future__ import annotations

import json

import httpx

from .contracts import EVENT_TYPES, EventType, json_schema

CONTENT_TYPE = "application/vnd.schemaregistry.v1+json"


class SchemaRegistry:
    def __init__(self, url: str, client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.http = client or httpx.Client(timeout=10)

    def set_compatibility(self, subject: str, level: str = "BACKWARD") -> None:
        response = self.http.put(
            f"{self.url}/config/{subject}", json={"compatibility": level}, headers={"Content-Type": CONTENT_TYPE}
        )
        response.raise_for_status()

    def register(self, event: EventType) -> int:
        """Register the payload schema (idempotent: an identical schema returns its existing id)."""
        body = {"schemaType": "JSON", "schema": json.dumps(json_schema(event))}
        response = self.http.post(
            f"{self.url}/subjects/{event.subject}/versions", json=body, headers={"Content-Type": CONTENT_TYPE}
        )
        if response.status_code == 409:
            raise RuntimeError(f"{event.subject}: schema change is not BACKWARD compatible: {response.text}")
        response.raise_for_status()
        return int(response.json()["id"])

    def register_all(self) -> dict[str, int]:
        ids = {}
        for event in EVENT_TYPES:
            self.set_compatibility(event.subject)
            ids[event.subject] = self.register(event)
        return ids
