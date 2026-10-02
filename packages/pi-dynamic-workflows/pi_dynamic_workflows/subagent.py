"""HarnessSubagentExecutor — spawn real subagents via independent AgentHarness instances."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import time
import uuid
from typing import Any

from pi_dynamic_workflows.model_routing import resolve_tier
from pi_dynamic_workflows.paths import resolve_subagent_cwd
from pi_dynamic_workflows.runtime import AgentResult, SubagentExecutor

logger = logging.getLogger(__name__)


def _parse_model_id(model_id: str, parent_model: Any) -> Any:
    """Parse ``"provider/model_id"`` into a ``Model``, falling back to parent settings.

    A bare id (no ``/``) means "same provider as the parent". The endpoint (``base_url``)
    belongs to the provider, not the model: it carries over when the provider is the
    parent's and is dropped otherwise, so the parent's gateway never fronts another vendor.
    Model-specific capabilities (``supports_images``, ``reasoning``) are not inherited.
    """
    from pi_agent_core.types import Model

    provider, sep, name = model_id.partition("/")
    if not sep:
        provider, name = parent_model.provider, model_id
    same_provider = provider.casefold() == str(parent_model.provider).casefold()
    return Model(
        provider=parent_model.provider if same_provider else provider,
        model_id=name,
        api=parent_model.api,
        context_window=parent_model.context_window,
        base_url=getattr(parent_model, "base_url", None) if same_provider else None,
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

    A sub-agent's harness is new and carries none of the parent session's hooks, so by
    itself its ``bash``/``edit``/``write`` calls would run outside the parent's
    permission policy. *tool_call_gate* (the parent harness's ``check_tool_call``, via
    the bridge) closes that gap: every tool call a sub-agent makes is put through it
    before it runs. Time spent waiting for a verdict (a permission prompt) counts
    against ``timeout_ms``. With no gate there is no policy to inherit and sub-agents
    run unrestricted.
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
        tool_call_gate: Any | None = None,
    ) -> None:
        self._stream_fn = stream_fn
        self._parent_model = parent_model
        self._cwd = cwd
        self._get_api_key = get_api_key
        self._tiers = tiers
        self._worktree_manager = worktree_manager
        self._tool_call_gate = tool_call_gate

    def with_worktree_manager(self, worktree_manager: Any) -> HarnessSubagentExecutor:
        """The same executor, with each sub-agent running in a worktree of *worktree_manager*.

        A copy rather than a re-construction: nothing configured on this executor (the
        gate above all) can be dropped by forgetting to pass it along.
        """
        derived = copy.copy(self)
        derived._worktree_manager = worktree_manager
        return derived

    def _tool_call_hook(self, cwd: str, label: str | None) -> Any:
        """A ``tool_call`` hook that puts one sub-agent run's calls through the gate.

        Tool call ids come from the model (``call_1``, ...) and repeat across sub-agents
        and the parent session, while a permission prompt is keyed by id, so each run gets
        its own prefix. The gate's verdict is returned as is; if it raises, the harness
        fails the call (an error result), so "no answer" is never taken as "allowed".
        """
        gate = self._tool_call_gate
        run_id = f"subagent-{uuid.uuid4().hex[:8]}"
        origin: dict[str, Any] = {"kind": "subagent", "cwd": cwd}
        if label:
            origin["label"] = label

        async def authorize(event: Any) -> Any:
            return await gate(
                f"{run_id}:{event.toolCallId}",
                event.toolName,
                dict(event.input),
                origin=dict(origin),
            )

        return authorize

    def _resolve_model(self, tier: str | None, model: str | None) -> Any:
        if model:
            return _parse_model_id(model, self._parent_model)
        if tier:
            resolved = resolve_tier(tier, self._tiers, provider=self._parent_model.provider)
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
        start = time.monotonic()
        effective_model = self._resolve_model(tier, model)
        # Whoever calls this (the runtime does its own check first), the directory a
        # sub-agent works in is this executor's to vouch for: inside its project or not at all.
        effective_cwd = resolve_subagent_cwd(cwd, self._cwd) if cwd else self._cwd

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

        try:
            return await self._run_in(
                prompt,
                effective_model=effective_model,
                effective_cwd=effective_cwd,
                wt_path=wt_path,
                label=label,
                phase=phase,
                timeout_ms=timeout_ms,
                schema=schema,
                start=start,
            )
        finally:
            # The worktree this call created goes with the call, whatever ended it: an
            # answer, a timeout, an error, a cancel. Only an answer's changes were applied.
            if wt_path is not None:
                await self._remove_worktree(wt_path)

    async def _run_in(
        self,
        prompt: str,
        *,
        effective_model: Any,
        effective_cwd: str,
        wt_path: str | None,
        label: str | None,
        phase: str | None,
        timeout_ms: float | None,
        schema: dict[str, Any] | None,
        start: float,
    ) -> AgentResult:
        """One sub-agent run in *effective_cwd* (the worktree, when *wt_path* is set)."""
        from pi_agent_core.coding_tools import create_all_tools
        from pi_agent_harness.agent_harness import AgentHarness
        from pi_agent_harness.env import LocalExecutionEnv
        from pi_agent_harness.session.memory_storage import MemorySessionStorage
        from pi_agent_harness.session.session import Session

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
        if self._tool_call_gate is not None:
            harness.on("tool_call", self._tool_call_hook(effective_cwd, label))

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
                    # gather, not ``suppress(CancelledError)``: a cancel of *this* call while
                    # it waits here must still get through.
                    await asyncio.gather(task, return_exceptions=True)
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

        return AgentResult(
            text=text,
            structured=structured,
            tokens_used=tokens,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def _remove_worktree(self, wt_path: str) -> None:
        """Remove *wt_path*; a failure is logged, never raised over the run's own outcome."""
        if self._worktree_manager is None:
            return
        try:
            await self._worktree_manager.cleanup(wt_path)
        except Exception:
            logger.warning("Worktree cleanup failed for %s", wt_path, exc_info=True)
