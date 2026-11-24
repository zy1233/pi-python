# Authoring & Iterating Agent Skills

Reference guide for designing, structuring, validating, and testing skills in `pi-python` (`zycode`), aligned with the `agentskills.io` open standard and proven agent-skill practices.

## 1. `pi-python` Skill Loader Contract

Exact rules enforced by `pi_agent_harness.skills` and `pi_agent_cli`:

| Aspect | Rule in `pi-python` |
|---|---|
| Entry file | `<skill-name>/SKILL.md` (preferred) or standalone `<skill-name>.md` |
| Frontmatter opening | File must start with `---\n` (LF) and close with `\n---` |
| `name` | Required (or inferred from folder/stem if omitted, but always write it explicitly). Regex: `^[a-z0-9]+(?:-[a-z0-9]+)*$`, max 64 chars, must equal parent directory name (or file stem) |
| `description` | Required, non-empty after `.strip()`, max 1024 chars |
| `disable-model-invocation` | Optional boolean (also accepts `disableModelInvocation`). When `true`, skill loads into `AgentHarnessResources` but is omitted from `<available_skills>` |
| Other frontmatter fields | Optional metadata (`license`, `compatibility`, `metadata`, `allowed-tools`) is allowed by YAML parsing and ignored by the loader |
| Recursion sentinel | When a directory contains `SKILL.md`, subdirectory recursion stops there (keep `references/`, `scripts/`, `assets/` inside cleanly) |
| Ignore files | `.gitignore`, `.ignore`, and `.fdignore` are read and inherited down the directory tree |
| System prompt gating | `<available_skills>` is injected **only** when the `read` tool is active in the session |
| Path resolution | The system prompt tells the model to resolve relative paths inside a skill against the skill directory (parent of `SKILL.md`) |

## 2. Three-Level Progressive Disclosure

Skills share the context window with conversation history, project files (`AGENTS.md`), and tool schemas. Load information only when needed:

1. **Level 1 — Metadata (always loaded)**: `name` + `description` inside `<available_skills>`. Keep descriptions rich enough to route accurately without exceeding ~1024 characters.
2. **Level 2 — `SKILL.md` body (loaded on trigger)**: Keep under **500 lines**. Include the core workflow, invariants, decision branches, and explicit pointers to Level 3 files.
3. **Level 3 — Bundled resources (loaded conditionally)**:
   - `references/`: Deep API docs, language/framework checklists, error tables, schemas. Keep references **one level deep** (`references/topic.md`, not nested `references/a/b/c.md`), and add a brief table of contents to files over ~150 lines. State *when* the agent should read each file (e.g., *"Read `references/migration-rules.md` before editing schema files"*).
   - `scripts/`: Deterministic helpers for fragile or repetitive mechanics (validators, formatters, parsers). Keep scripts small, self-contained, and documented with exact CLI flags. **Portability note**: `zycode-agent` standalone binaries may run on machines without Python installed—avoid depending on Python scripts under `scripts/` unless the skill itself targets a Python project/environment.
   - `assets/`: Output templates, boilerplates, config examples, or static assets copied/adapted into the user's workspace (not read into context unless needed).

**Do not add clutter**: Skip `README.md`, `CHANGELOG.md`, `INSTALLATION.md`, or empty `scripts/`/`references/`/`assets/` directories. Every file in a skill folder should earn its place.

## 3. Writing High-Signal Trigger Descriptions

The model sees **only** `<name>`, `<description>`, and `<location>` before deciding to read `SKILL.md`. Agents tend to *under-trigger* when descriptions are too abstract.

- Write in **third person** (e.g., *"Extract and validate..."*, not *"I can help you..."*).
- Include both **what the skill does** and **when to use it** (user intents, file types, tool names, or symptom keywords).
- Mention nearby exclusions if the skill could over-trigger on adjacent tasks.

**Weak vs. strong examples:**

- **Too vague (under-triggers)**:
  `description: Helps with database tasks.`
- **Strong (clear intent + triggers)**:
  `description: Safely author, review, and verify SQL schema migrations with rollback checks. Use when adding or altering tables, columns, indexes, or constraints, or reviewing migration files before release.`
- **Too broad (over-triggers)**:
  `description: Assists with writing, debugging, and refactoring any Python code.`
- **Well-bounded**:
  `description: Author and validate pi-python Agent Skills (SKILL.md) and agent.toml [skills] configuration. Use when creating, updating, installing, importing, or troubleshooting skills.`

## 4. Calibrating Degrees of Freedom

Match instruction specificity to how fragile the task is:

| Freedom Level | When to Use | What to Write |
|---|---|---|
| **High** (principles & heuristics) | Multiple valid designs exist; context varies (code review, architecture, writing) | Clear goals, quality criteria, trade-offs, and anti-patterns |
| **Medium** (templates & ordered steps) | A standard workflow exists, with project-specific parameters | Numbered workflow steps, parameterized templates, decision tables |
| **Low** (exact scripts & strict checks) | One small deviation breaks data or fails validation (migrations, binary formats, security gates) | Exact command sequences, mandatory verification gates, fallback rules |

## 5. Authoring Style & Portability

- **Assume model intelligence**: Do not explain standard git, JSON, HTTP, or basic language syntax. Spend tokens only on project-specific workflows, invariants, gotchas, and tool usage.
- **Explain the *why***: When adding a strict rule (*"Never modify `config.toml` for `[skills]`"*), add a short reason (*"because the Rust TUI `zypi` parses `config.toml` with a strict schema and may fail to start"*). Models follow motivated rules far better than bare `ALWAYS`/`NEVER` shouts.
- **Prefer imperative steps and concrete examples**: One short realistic example clarifies edge cases better than three paragraphs of prose.
- **Keep paths portable**: Never hardcode `/Users/alice/...` or `C:\Users\alice\...`. Refer to `$PI_HOME` (default `~/.pi-python`), `<project>`, or paths relative to the skill directory.
- **Respect `pi-python` security boundaries**: Never instruct a skill to bypass project trust, silently edit `~/.pi-python/agent.toml` without user intent, or read secrets (`local.env`, `.env`) unnecessarily.

## 6. Suggested `SKILL.md` Skeleton

```markdown
---
name: my-skill-name
description: What this skill accomplishes and exact scenarios/keywords that trigger it.
---

# My Skill Name

One-sentence summary of the capability and scope.

## When to Use / Preconditions

- Required tools, files, or environment assumptions.
- Optional pointer to `references/...` for deep details.

## Workflow

1. Step 1 (gather / inspect).
2. Step 2 (execute / transform).
3. Step 3 (verify / report).

## Guardrails & Edge Cases

- Key pitfalls and why they matter.
- Recovery steps when verification fails.
```

## 7. Pre-Delivery Checklist & Iteration Loop

### Validation Checklist

1. `SKILL.md` starts with `---\n` (LF) and valid YAML frontmatter.
2. `name` is lowercase kebab-case (`^[a-z0-9]+(?:-[a-z0-9]+)*$`), `<= 64` chars, and matches the directory name (or file stem).
3. `description` is non-empty, `<= 1024` chars, and states both capability and trigger conditions.
4. Body is under 500 lines; heavy material lives in 1-level-deep `references/`.
5. All relative links (`references/...`, `scripts/...`, `assets/...`) resolve to existing files and are not hidden by `.gitignore` / `.ignore` / `.fdignore`.
6. No unused boilerplate directories or secret/personal paths.

### Evaluation & Iteration Loop

After writing the skill, verify it in practice rather than assuming it works:

1. **Test triggering**: Suggest 2–3 realistic prompts that should invoke the skill and 1–2 out-of-scope prompts that should *not* trigger it.
2. **Observe execution in a new session**: Check whether the agent reads `SKILL.md` without prompting, loads `references/` only when needed, and completes the workflow cleanly.
3. **Refine based on observed gaps**:
   - Did not trigger → sharpen keywords and scenarios in `description`.
   - Triggered on unrelated tasks → narrow `description` and state boundaries.
   - Repeated mistakes or wasted turns → tighten ambiguous workflow steps, explain *why*, or move reference noise out of `SKILL.md` into `references/`.
