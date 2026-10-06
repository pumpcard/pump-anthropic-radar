"""Public package surface."""

from anthropic_radar import (
    AnthropicRadarError,
    Finding,
    FindingEngine,
    RadarClient,
    RunConfig,
    Runner,
    RunResult,
    Severity,
    __version__,
)


def test_version() -> None:
    assert __version__ == "0.0.1"


def test_public_names_are_exported() -> None:
    assert RadarClient.__name__ == "RadarClient"
    assert AnthropicRadarError.__name__ == "AnthropicRadarError"
    assert Runner.__name__ == "Runner"
    assert RunConfig.__name__ == "RunConfig"
    assert RunResult.__name__ == "RunResult"
    assert Finding.__name__ == "Finding"
    assert FindingEngine.__name__ == "FindingEngine"
    assert Severity.HIGH.value == "HIGH"
