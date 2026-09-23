"""Built-in workflow patterns (5 curated patterns from upstream)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_MULTI_PERSPECTIVES = [
    "technical",
    "product",
    "security",
    "user experience",
    "maintainability",
]


@dataclass
class BuiltinWorkflowInvocation:
    script: str
    name: str


@dataclass
class BuiltinWorkflowDescriptor:
    name: str
    description: str
    resolve: Any  # (args) -> BuiltinWorkflowInvocation


def _require_str(args: dict[str, Any], key: str, pattern_name: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(
            f'Built-in workflow "{pattern_name}" requires args.{key} to be a non-empty string.'
        )
    return v


def _require_str_list(args: dict[str, Any], key: str, pattern_name: str) -> list[str]:
    v = args.get(key)
    if not isinstance(v, list) or not v or not all(isinstance(s, str) and s.strip() for s in v):
        raise ValueError(
            f'Built-in workflow "{pattern_name}" requires args.{key} '
            "to be a non-empty list of non-empty strings."
        )
    return v


def _as_record(args: Any) -> dict[str, Any]:
    return args if isinstance(args, dict) else {}


# ---------------------------------------------------------------------------
# Pattern generators — produce Python workflow scripts
# ---------------------------------------------------------------------------


def generate_deep_research() -> str:
    return """\
meta = {"name": "deep_research", "description": "Research a question with cross-checked sources"}

async def main():
    question = args["question"]
    phase("Generate search angles")
    angles = await agent(
        f"Generate 5 diverse search angles for researching: {question}\\n"
        "Return each angle on a new line, numbered 1-5.",
        tier="small",
    )

    angle_list = [line.strip() for line in (angles or "").split("\\n") if line.strip() and line.strip()[0].isdigit()]
    if not angle_list:
        angle_list = [question]

    phase("Research")
    findings = await parallel([
        lambda a=angle: agent(
            f"Research this angle thoroughly: {a}\\n"
            f"Original question: {question}\\n"
            "Provide detailed findings with specific facts, data, and sources.",
            tier="medium",
        )
        for angle in angle_list[:5]
    ])

    phase("Cross-check")
    combined = "\\n\\n---\\n\\n".join(f"Angle: {a}\\nFindings: {f}" for a, f in zip(angle_list, findings) if f)
    verified = await agent(
        f"Cross-check these research findings for accuracy and consistency:\\n\\n{combined}\\n\\n"
        "Flag any contradictions, unsupported claims, or areas needing more evidence. "
        "Rate confidence for each finding.",
        tier="big",
    )

    phase("Synthesize")
    report = await agent(
        f"Synthesize a comprehensive research report on: {question}\\n\\n"
        f"Research findings:\\n{combined}\\n\\n"
        f"Cross-check results:\\n{verified}\\n\\n"
        "Write a well-structured report with an executive summary, key findings, "
        "confidence levels, and areas for further research.",
        tier="big",
    )

    result(report)
"""


def generate_adversarial_review() -> str:
    return """\
meta = {"name": "adversarial_review", "description": "Investigate then cross-check with skeptical reviewers"}

async def main():
    task = args["task"]
    reviewers = args.get("reviewers", 3)

    phase("Investigate")
    investigation = await agent(
        f"Thoroughly investigate: {task}\\n"
        "Provide detailed findings with evidence and reasoning.",
        tier="big",
    )

    phase("Adversarial review")
    reviews = await parallel([
        lambda i=i: agent(
            f"You are Reviewer #{i+1}. Critically review this investigation:\\n\\n{investigation}\\n\\n"
            "Be skeptical. Challenge assumptions, find gaps, identify weak evidence, "
            "and suggest what was missed. Rate each finding as: confirmed / questionable / refuted.",
            tier="medium",
        )
        for i in range(reviewers)
    ])

    phase("Synthesize")
    all_reviews = "\\n\\n---\\n\\n".join(f"Reviewer #{i+1}:\\n{r}" for i, r in enumerate(reviews) if r)
    synthesis = await agent(
        f"Synthesize the investigation and all reviewer feedback:\\n\\n"
        f"Original investigation:\\n{investigation}\\n\\n"
        f"Reviews:\\n{all_reviews}\\n\\n"
        "Produce a final report with confidence-rated findings. "
        "Only include findings that survived adversarial review.",
        tier="big",
    )
    result(synthesis)
"""


def generate_code_review() -> str:
    return """\
meta = {"name": "code_review", "description": "Multi-angle parallel code review"}

async def main():
    diff = args["diff"]
    diff_source = args.get("diff_source", "unknown")

    phase("Parallel review")
    angles = [
        ("correctness", "Find bugs, logic errors, off-by-one mistakes, race conditions, and unhandled edge cases."),
        ("security", "Find security vulnerabilities: injection, auth bypass, data exposure, unsafe deserialization."),
        ("performance", "Find performance issues: N+1 queries, unnecessary allocations, missing caching, O(n²) loops."),
        ("simplification", "Find unnecessary complexity: dead code, over-abstraction, code that could be simplified."),
        ("reuse", "Find code duplication and opportunities to use existing utilities or extract shared helpers."),
    ]

    findings = await parallel([
        lambda name=name, instruction=instruction: agent(
            f"Review this diff as a {name} specialist:\\n\\n```diff\\n{diff[:20000]}\\n```\\n\\n{instruction}\\n"
            "List each finding with file, line, severity (critical/major/minor), and a fix suggestion.",
            tier="medium",
        )
        for name, instruction in angles
    ])

    phase("Verify and rank")
    combined = "\\n\\n---\\n\\n".join(
        f"**{name}** reviewer:\\n{f}"
        for (name, _), f in zip(angles, findings)
        if f
    )
    verified = await agent(
        f"You received findings from {len(angles)} code reviewers. Verify and rank them:\\n\\n{combined}\\n\\n"
        "Remove duplicates and false positives. Rank remaining findings by impact. "
        "Output a numbered list, most critical first, with actionable fix suggestions.",
        tier="big",
    )
    result(verified)
"""


def generate_multi_perspective(topic: str, perspectives: list[str]) -> str:
    persp_list = repr(perspectives)
    return f"""\
meta = {{"name": "multi_perspective", "description": "Analyze from multiple perspectives"}}

async def main():
    topic = args.get("topic", {topic!r})
    perspectives = args.get("perspectives", {persp_list})

    phase("Parallel analysis")
    analyses = await parallel([
        lambda p=p: agent(
            f"Analyze this topic from a {{p}} perspective:\\n\\n{{topic}}\\n\\n"
            f"Focus exclusively on {{p}} concerns, trade-offs, and implications. "
            "Be specific and provide actionable insights.",
            tier="medium",
        )
        for p in perspectives
    ])

    phase("Synthesize")
    combined = "\\n\\n---\\n\\n".join(
        f"**{{p}}** perspective:\\n{{a}}"
        for p, a in zip(perspectives, analyses)
        if a
    )
    synthesis = await agent(
        f"Synthesize these multi-perspective analyses into a unified assessment:\\n\\n{{combined}}\\n\\n"
        "Identify consensus, tensions, and trade-offs. "
        "Provide a balanced recommendation that accounts for all perspectives.",
        tier="big",
    )
    result(synthesis)
"""


def generate_codebase_audit(scope: str, checks: list[str]) -> str:
    return f"""\
meta = {{"name": "codebase_audit", "description": "Parallel codebase audit"}}

async def main():
    scope = args.get("scope", {scope!r})
    checks = args.get("checks", {checks!r})

    phase("Parallel checks")
    findings = await parallel([
        lambda check=check: agent(
            f"Audit the codebase (scope: {{scope}}) for: {{check}}\\n\\n"
            "Examine the code thoroughly. List each finding with file path, "
            "line numbers, severity, and a recommended fix.",
            tier="medium",
        )
        for check in checks
    ])

    phase("Cross-validate")
    combined = "\\n\\n---\\n\\n".join(
        f"**{{check}}**:\\n{{f}}"
        for check, f in zip(checks, findings)
        if f
    )
    validated = await agent(
        f"Cross-validate and compile these audit findings:\\n\\n{{combined}}\\n\\n"
        "Remove false positives, merge overlapping findings, and produce "
        "a prioritized report with actionable recommendations.",
        tier="big",
    )
    result(validated)
"""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _resolve_multi_perspective(args: Any) -> BuiltinWorkflowInvocation:
    a = _as_record(args)
    return BuiltinWorkflowInvocation(
        script=generate_multi_perspective(
            _require_str(a, "topic", "multi-perspective"),
            a.get("perspectives", DEFAULT_MULTI_PERSPECTIVES),
        ),
        name="multi-perspective",
    )


def _resolve_codebase_audit(args: Any) -> BuiltinWorkflowInvocation:
    a = _as_record(args)
    return BuiltinWorkflowInvocation(
        script=generate_codebase_audit(
            _require_str(a, "scope", "codebase-audit"),
            _require_str_list(a, "checks", "codebase-audit"),
        ),
        name="codebase-audit",
    )


BUILTIN_WORKFLOWS: dict[str, BuiltinWorkflowDescriptor] = {
    "deep-research": BuiltinWorkflowDescriptor(
        name="deep-research",
        description=(
            "Research a question across the web with "
            "cross-checked sources. args: { question: str }."
        ),
        resolve=lambda args: BuiltinWorkflowInvocation(
            script=generate_deep_research(),
            name="deep-research",
        ),
    ),
    "adversarial-review": BuiltinWorkflowDescriptor(
        name="adversarial-review",
        description=(
            "Investigate then cross-check with skeptical reviewers. "
            "args: { task: str, reviewers?: int }."
        ),
        resolve=lambda args: BuiltinWorkflowInvocation(
            script=generate_adversarial_review(),
            name="adversarial-review",
        ),
    ),
    "code-review": BuiltinWorkflowDescriptor(
        name="code-review",
        description="Multi-angle parallel code review. args: { diff: str }.",
        resolve=lambda args: BuiltinWorkflowInvocation(
            script=generate_code_review(),
            name="code-review",
        ),
    ),
    "multi-perspective": BuiltinWorkflowDescriptor(
        name="multi-perspective",
        description=(
            "Analyze a topic from several perspectives, then synthesize. "
            "args: { topic: str, perspectives?: list[str] }."
        ),
        resolve=_resolve_multi_perspective,
    ),
    "codebase-audit": BuiltinWorkflowDescriptor(
        name="codebase-audit",
        description=(
            "Run parallel checks against a codebase scope. args: { scope: str, checks: list[str] }."
        ),
        resolve=_resolve_codebase_audit,
    ),
}

BUILTIN_WORKFLOW_NAMES = list(BUILTIN_WORKFLOWS.keys())


def resolve_builtin_workflow(name: str, args: Any = None) -> BuiltinWorkflowInvocation | None:
    desc = BUILTIN_WORKFLOWS.get(name)
    if desc is None:
        return None
    return desc.resolve(args)
