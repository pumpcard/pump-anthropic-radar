"""RadarClient auth and pagination. requests.get is stubbed."""

from __future__ import annotations

import pytest

from anthropic_radar.client import ANTHROPIC_VERSION, AnthropicRadarError, RadarClient


class _Resp:
    def __init__(self, payload: dict, status: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status
        self.text = text or str(payload)

    def json(self) -> dict:
        return self._payload


def test_admin_endpoint_requires_an_admin_key() -> None:
    client = RadarClient(api_key="sk-ant-api")
    with pytest.raises(AnthropicRadarError, match="Admin API key"):
        client.get("/v1/organizations/me")


def test_get_sends_the_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_get(url: str, headers: dict | None = None, params: dict | None = None, timeout: float = 0):
        seen["url"] = url
        seen["headers"] = headers
        seen["params"] = params
        return _Resp({"id": "org_1", "name": "Acme"})

    monkeypatch.setattr("anthropic_radar.client.requests.get", fake_get)
    client = RadarClient(admin_key="sk-ant-admin")
    payload = client.get("/v1/organizations/me", params={"limit": 1})

    assert payload["name"] == "Acme"
    assert seen["url"] == "https://api.anthropic.com/v1/organizations/me"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["x-api-key"] == "sk-ant-admin"
    assert headers["anthropic-version"] == ANTHROPIC_VERSION
    assert seen["params"] == {"limit": 1}


def test_paginate_follows_next_page_then_after_id(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {"data": [{"id": "a"}], "has_more": True, "next_page": "p2"},
        {"data": [{"id": "b"}], "has_more": True, "last_id": "b"},
        {"data": [{"id": "c"}], "has_more": False},
    ]
    calls: list[dict] = []

    def fake_get(url: str, headers: dict | None = None, params: dict | None = None, timeout: float = 0):
        calls.append(dict(params or {}))
        return _Resp(pages[len(calls) - 1])

    monkeypatch.setattr("anthropic_radar.client.requests.get", fake_get)
    client = RadarClient(admin_key="sk-ant-admin")
    ids = [item["id"] for item in client.paginate("/v1/organizations/users")]

    assert ids == ["a", "b", "c"]
    assert calls[1]["page"] == "p2"
    assert calls[2]["after_id"] == "b"


def test_http_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, headers: dict | None = None, params: dict | None = None, timeout: float = 0):
        return _Resp({}, status=401, text="unauthorized")

    monkeypatch.setattr("anthropic_radar.client.requests.get", fake_get)
    client = RadarClient(admin_key="sk-ant-admin")
    with pytest.raises(AnthropicRadarError, match="401"):
        client.get("/v1/organizations/me")
