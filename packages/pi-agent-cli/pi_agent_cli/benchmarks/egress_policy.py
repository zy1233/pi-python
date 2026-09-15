"""Egress network policy management for benchmark task execution.

Implements network isolation aligning with FrontierHarness Eval:
- no-network: strictly air-gapped container (--network none) for DeepSWE tasks.
- allowlist: governed egress allowing package registries, source hosts, and provider hosts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from pi_agent_cli.benchmarks.models import BenchmarkTask

# Official host allowlist from frontier-harness-eval/eval (scripts/providers.sh)
OFFICIAL_ALLOWED_HOSTS: list[str] = [
    "astral.sh",
    "*.astral.sh",
    "github.com",
    "*.github.com",
    "*.githubusercontent.com",
    "*.supabase.co",
    "pypi.org",
    "*.pythonhosted.org",
    "*.npmjs.org",
    "*.ubuntu.com",
    "*.debian.org",
    "*.pytorch.org",
    "*.ecr.aws",
    "*.cloudfront.net",
]


@dataclass
class EgressPolicy:
    """Network egress policy for a benchmark container."""

    mode: str  # "no-network" | "allowlist" | "open"
    scope: str = "runtime"
    allowed_hosts: list[str] = field(default_factory=list)
    proxy_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert policy to dict representation for run metadata."""
        return {
            "mode": self.mode,
            "scope": self.scope,
            "allowed_hosts": sorted(list(set(self.allowed_hosts))),
            "proxy_url": self.proxy_url,
        }

    def docker_run_args(self) -> list[str]:
        """Generate docker run CLI flags for this policy."""
        if self.mode == "no-network":
            return ["--network", "none"]

        args: list[str] = []
        if self.proxy_url:
            args.extend(
                [
                    "-e",
                    f"http_proxy={self.proxy_url}",
                    "-e",
                    f"https_proxy={self.proxy_url}",
                    "-e",
                    f"HTTP_PROXY={self.proxy_url}",
                    "-e",
                    f"HTTPS_PROXY={self.proxy_url}",
                    "-e",
                    "no_proxy=127.0.0.1,localhost,::1",
                    "-e",
                    "NO_PROXY=127.0.0.1,localhost,::1",
                ]
            )
        return args


def resolve_task_egress_policy(
    task: BenchmarkTask,
    *,
    egress_mode: str = "auto",
    provider_host: str | None = None,
    custom_proxy: str | None = None,
) -> EgressPolicy:
    """Resolve the egress policy for a benchmark task.

    Args:
        task: BenchmarkTask instance with metadata.
        egress_mode: "auto" (default), "no-network", "allowlist", or "open".
        provider_host: Optional model provider hostname to allow.
        custom_proxy: Optional explicit proxy URL.
    """
    meta = task.metadata or {}
    agent_sec = meta.get("agent", {})
    env_sec = meta.get("environment", {})
    verifier_sec = meta.get("verifier", {})

    is_no_network = (
        agent_sec.get("network_mode") == "no-network"
        or env_sec.get("allow_internet") is False
        or verifier_sec.get("network_mode") == "no-network"
    )

    if egress_mode == "no-network" or (egress_mode == "auto" and is_no_network):
        return EgressPolicy(mode="no-network", allowed_hosts=[])

    if egress_mode == "open":
        return EgressPolicy(mode="open", allowed_hosts=["*"])

    # allowlist / proxy mode
    allowed = list(OFFICIAL_ALLOWED_HOSTS)
    if provider_host and provider_host not in allowed:
        allowed.insert(0, provider_host)

    proxy_url = (
        custom_proxy
        or os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or "http://172.20.35.30:10809"
    )

    return EgressPolicy(
        mode="allowlist",
        scope="runtime",
        allowed_hosts=allowed,
        proxy_url=proxy_url,
    )
