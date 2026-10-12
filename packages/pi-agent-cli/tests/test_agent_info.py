"""ACP ``agentInfo``: the agent reports the version of the package it ships in (plan decision 16).

The version used to be written into ``agent.py`` (``0.1.0``) and no longer matched the package.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest
from packaging.version import Version

from pi_agent_cli import agent as agent_module
from pi_agent_cli.agent import PiAcpAgent
from pi_agent_cli.config import CliConfig
from pi_agent_core.tests.mock_stream import mock_text_stream

DISTRIBUTION = "pi-agent-cli-lc"


@pytest.mark.asyncio
async def test_initialize_reports_the_version_of_the_installed_package(tmp_path: Path):
    agent = PiAcpAgent(
        stream_fn=mock_text_stream,
        home=tmp_path,
        config=CliConfig(permission="ask", provider="mock", model_id="mock"),  # type: ignore[arg-type]
    )
    response = await agent.initialize(protocol_version=1)

    assert response.agent_info is not None
    assert response.agent_info.name == "pi-agent-cli"
    assert response.agent_info.version == importlib.metadata.version(DISTRIBUTION)
    # What a client reads from the wire.
    assert response.model_dump(by_alias=True)["agentInfo"]["version"] == response.agent_info.version


def test_installed_version_falls_back_to_a_valid_version_without_package_metadata(
    monkeypatch: pytest.MonkeyPatch,
):
    def not_installed(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", not_installed)

    version = agent_module._installed_version()

    assert version == "0.0.0+unknown"
    Version(version)  # a client may parse it, so it must be a valid version
