"""Keep tests from seeing a developer's real Anthropic credentials."""

from __future__ import annotations

import pytest

_ANTHROPIC_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_ADMIN_KEY",
    "ANTHROPIC_RADAR_CONFIG_DIR",
    "PUMP_API_BASE",
    "PUMP_APP_BASE",
)


@pytest.fixture(autouse=True)
def _clear_anthropic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ANTHROPIC_ENV:
        monkeypatch.delenv(name, raising=False)
