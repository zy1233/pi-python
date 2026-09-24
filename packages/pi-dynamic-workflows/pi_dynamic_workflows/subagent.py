"""HarnessSubagentExecutor — spawn real subagents via independent AgentHarness instances."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from pi_dynamic_workflows.model_routing import resolve_tier
from pi_dynamic_workflows.runtime import AgentResult, SubagentExecutor

logger = logging.getLogger(__name__)


def _parse_model_id(model_id: str, parent_model: Any) -> Any:
    """Parse ``"provider/model_id"`` into a ``Model``, falling back to parent settings."""
    from pi_agent_core.types import Model

    parts = model_id.split("/", 1)
    if len(parts) == 2:
        return Model(
            provider=parts[0],
            model_id=parts[1],
            api=parent_model.api,
            context_window=parent_model.context_window,
        )
    return Model(
        provider=parent_model.provider,
        model_id=model_id,
        api=parent_model.api,
        context_window=parent_model.context_window,
    )


def _extract_text(message: Any) -> str:
    """Pull plain text from an AssistantMessage."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


class HarnessSubagentExecutor(SubagentExecutor):
    """Spawn real subagents backed by independent AgentHarness instances.

    Each ``run_agent()`` call creates a fresh in-memory session and an
    ``AgentHarness`` that runs a single prompt-to-completion cycle.
    When *worktree_manager* is provided, each subagent runs in an
    isolated git worktree that is cleaned up after completion.
    """

    def __init__(
        self,
        *,
        stream_fn: Any,
        parent_model: Any,
        cwd: str = ".",
        get_api_key: Any | None = None,
        tiers: dict[str, str] | None = None,
        worktree_manager: Any | None = None,
    ) -> None:
        self._stream_fn = stream_fn
        self._parent_model = parent_model
        self._cwd = cwd
        self._get_api_key = get_api_key
        self._tiers = tiers
        self._worktree_manager = worktree_manager

    def _resolve_model(self, tier: str | None, model: str | None) -> Any:
        if model:
            return _parse_model_id(model, self._parent_model)
        if tier:
            resolved = resolve_tier(tier, self._tiers)
            if resolved:
                return _parse_model_id(resolved, self._parent_model)
        return self._parent_model

    async def run_agent(
        self,
        prompt: str,
        *,
        tier: str | None = None,
        model: str | None = None,
        label: str | None = None,
        phase: str | None = None,
        timeout_ms: float | None = None,
        schema: dict[str, Any] | None = None,
        cwd: str | None = None,
    ) -> AgentResult:
        from pi_agent_harness.agent_harness import AgentHarness
        from pi_agent_harness.session.memory_storage import MemorySessionStorage
        from pi_agent_harness.session.session import Session

        start = time.monotonic()
        effective_model = self._resolve_model(tier, model)
        effective_cwd = cwd or self._cwd

        from pi_agent_core.coding_tools import create_all_tools
        from pi_agent_harness.env import LocalExecutionEnv

        wt_path: str | None = None
        if self._worktree_manager is not None:
            try:
                wt_path = await self._worktree_manager.create()
                effective_cwd = wt_path
            except Exception as wt_exc:
                logger.warning(
                    "Worktree creation failed — cannot guarantee isolation",
                    exc_info=True,
                )
                return AgentResult(
                    error=f"worktree creation failed: {wt_exc}",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

        storage = await MemorySessionStorage.create()
        session = Session(storage)
        env = LocalExecutionEnv(effective_cwd)
        tools = list(create_all_tools(effective_cwd).values())

        harness = AgentHarness(
            session=session,
            model=effective_model,
            stream_fn=self._stream_fn,
            get_api_key=self._get_api_key,
            env=env,
            tools=tools,
        )

        prompt_text = prompt
        if phase:
            prompt_text = f"[Phase: {phase}]\n\n{prompt}"
        if schema:
            schema_str = json.dumps(schema, indent=2)
            prompt_text += (
                f"\n\nRespond with JSON matching this schema:\n```json\n{schema_str}\n```"
            )

        try:
            if timeout_ms:
                task = asyncio.ensure_future(harness.prompt(prompt_text))
                done, _ = await asyncio.wait([task], timeout=timeout_ms / 1000)
                if not done:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                    elapsed = (time.monotonic() - start) * 1000
                    return AgentResult(error="timeout", duration_ms=elapsed)
                assistant = task.result()
            else:
                assistant = await harness.prompt(prompt_text)
        except Exception as exc:
            logger.warning("Subagent failed: %s", exc, exc_info=True)
            return AgentResult(error=str(exc), duration_ms=(time.monotonic() - start) * 1000)

        text = _extract_text(assistant)
        usage = getattr(assistant, "usage", None)
        tokens = 0
        if usage:
            tokens = (getattr(usage, "input", 0) or 0) + (getattr(usage, "output", 0) or 0)

        structured = None
        if schema and text:
            with contextlib.suppress(json.JSONDecodeError):
                structured = json.loads(text)

        if wt_path is not None and self._worktree_manager is not None:
            try:
                await self._worktree_manager.apply_changes(wt_path)
            except Exception:
                logger.warning(
                    "Worktree apply_changes failed for %s — changes may be lost",
                    wt_path,
                    exc_info=True,
                )
            try:
                await self._worktree_manager.cleanup(wt_path)
            except Exception:
                logger.debug(
                    "Worktree cleanup failed for %s",
                    wt_path,
                    exc_info=True,
                )

        return AgentResult(
            text=text,
            structured=structured,
            tokens_used=tokens,
            duration_ms=(time.monotonic() - start) * 1000,
        )
