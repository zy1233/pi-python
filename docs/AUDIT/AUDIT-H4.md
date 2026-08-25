# AUDIT-H4：Phase 3 H4（Skills / Prompt Templates / System Prompt / LocalExecutionEnv）实现审计（2026-08-05）

> 审计范围：`packages/pi-agent-harness/pi_agent_harness/{skills.py, prompt_templates.py,
> system_prompt.py, frontmatter.py, env.py}`，以及 `agent_harness.py` 中 H4 相关方法
> （`skill` / `prompt_from_template` / `_create_turn_state` 系统提示注入）、
> `types.py` 中 H4 新增类型（`Skill` / `SkillDiagnostic` / `SkillLoadResult` /
> `PromptTemplate` / `FileSystem` / `Shell` / `ExecutionEnv` / `FileInfo` / `ExecResult`），
> 以及 `pyproject.toml` 依赖声明与 `__init__.py` 公共导出。
>
> 对照 Phase 3 设计文档
> （`docs/specs/2026-07-03-phase3-agent-harness-design.md` §6/§8/§9/§10）
> 与上游 [earendil-works/pi](https://github.com/earendil-works/pi)
> `packages/agent/src/harness/{skills.ts, prompt-templates.ts, env/}` 逐段核对。
>
> 验证方式：`.venv-audit`（Python 3.12）全量 pytest 308 通过、`ruff check` 全绿；
> 对每个可疑点结合代码审查与设计文档逐段比对。
>
> 状态图例：`[ ]` 待修复 · `[~]` 记录性（设计文档批准的偏差、低影响或无需行动）。

## 总体结论

H4 主体忠实：`load_skills` 的目录递归 + SKILL.md 哨兵停止 + `.gitignore`/`.ignore`/
`.fdignore` 逐级叠加 + frontmatter 解析 + 校验诊断（name ≤64 kebab-case + description
必填 ≤1024 + 与目录/文件名一致）、`format_skills_for_system_prompt`（agentskills.io
XML 块 + `disableModelInvocation` 过滤）、`format_skill_invocation`（全文 +
"References are relative to" 包装）、`load_prompt_templates`（非递归直接 `.md` +
frontmatter description 降级首行截 60）、`substitute_args`（`$1..$n` / `$@` /
`$ARGUMENTS` / `${@:N}` / `${@:N:L}`）、`parse_command_args`（shlex 单双引号分词）、
`build_harness_system_prompt`（skills 自动注入）、`LocalExecutionEnv`（pathlib +
asyncio.to_thread 文件操作 + subprocess_shell 命令执行 + cleanup 尽力而为）、
依赖声明（`PyYAML>=6` + `pathspec>=0.12`）均与设计文档和上游一致。

审计发现 1 个实质偏差、3 个中等偏差、5 个低影响偏差/nits，以及若干测试缺口。

---

## 一、实现偏差（实现 ≠ 文档承诺）

### H4-1. `exec()` 的 `signal` 仅在启动前检查，不支持执行中中断（实质）

- [x] **已修复（2026-08-05）** ·（实质 · 代码审查）
- 位置：`env.py` `LocalExecutionEnv.exec` L126–137
- 问题：设计 §8.1 的 `Shell.exec` 包含 `signal` 参数，pi 的实现会在进程运行期间监听
  signal 的 abort 事件并终止进程。当前实现仅在启动前检查 `signal.aborted`：
  ```python
  if signal is not None and getattr(signal, "aborted", False):
      raise ExecutionError("aborted", "Execution aborted before start")
  ```
  进程启动后即使 signal 触发 abort，也不会终止进程——只能等 timeout 或自然结束。
  对于长时间运行的命令，abort 语义形同虚设。
- 修复：使用 `asyncio.create_task` 监听 `signal.wait_aborted()`，在 signal
  触发时调用 `proc.kill()`（`contextlib.suppress(ProcessLookupError)`）；进程结束后
  取消监听 task，若 `aborted` 标志为真则抛出 `ExecutionError("aborted")`。
  回归测试 ×2（`test_local_execution_env_exec_signal_abort_before_start` /
  `test_local_execution_env_exec_signal_abort_mid_execution`）。

---

## 二、中等偏差

### H4-2. `on_stdout`/`on_stderr` 为批量一次性回调，非流式实时输出（中）

- [x] **已修复（2026-08-05）** ·（中 · 代码审查）
- 位置：`env.py` `LocalExecutionEnv.exec`
- 问题：设计 §8.1 `on_stdout`/`on_stderr` 回调与 pi 的实现均为**逐行流式**——进程执行
  期间实时回调（pi 用 `readline` 循环）。原实现等进程结束后一次性将全部 stdout/stderr
  传入回调。
- 修复：用 `asyncio.StreamReader.readline()` 循环替换 `proc.communicate()`，在
  两个并发的 `_read_stream` 协程中逐行读取 stdout/stderr，每读一行立即调用回调；
  同时收集完整输出用于 `ExecResult`。通过 `asyncio.gather` 并发两路读取避免死锁。
  回归测试 ×2（`test_local_execution_env_exec_streams_stdout_per_line` /
  `test_local_execution_env_exec_streams_stderr_per_line`）。

### H4-3. `Skill` 和 `PromptTemplate` 类型未从包顶层导出（中）

- [x] **已修复（2026-08-05）** ·（中 · 代码审查）
- 位置：`__init__.py` 导出列表
- 问题：`Skill` 和 `PromptTemplate` 是构造 `AgentHarnessResources` 的必备类型，
  但只能通过 `from pi_agent_harness.types import Skill` 访问。`__all__` 列表中已导出
  `SkillDiagnostic` 和 `SkillLoadResult` 但遗漏了这两个。
- 修复：在 `__init__.py` 的 import 和 `__all__` 中增加 `Skill` 和 `PromptTemplate`。
  回归测试 ×1（`test_skill_and_prompt_template_importable_from_package`）。

### H4-4. `SkillDiagnostic.code` 的 `parse_failed` 从未使用（中）

- [x] **已修复（2026-08-05）** ·（中 · 代码审查）
- 位置：`skills.py` `_read_skill_file`
- 问题：设计 §6.1 诊断码列 `parse_failed` 用于 frontmatter 解析失败；types.py
  也声明了该 code。但 `_read_skill_file` 将读取和解析放在同一 try/except 中，
  YAML `ScannerError` 等解析错误被报为 `read_failed`，`parse_failed` 永远不触发。
- 修复：拆分 try/except 为两步——`read_text_file` 异常报 `read_failed`，
  `parse_frontmatter` 异常单独捕获报 `parse_failed`。
  回归测试 ×1（`test_load_skills_yaml_parse_failure_uses_parse_failed_code`）。

---

## 三、低影响偏差 / Nits

### H4-5. `LocalExecutionEnv.cwd` 和路径返回值在 Windows 上非 POSIX 风格

- [~] 记录性 ·（低 · 代码审查）
- 位置：`env.py` `LocalExecutionEnv.__init__` / `absolute_path` / `canonical_path`
- 问题：设计 §附录"风险与缓解"明确 "FileSystem 协议统一返回 POSIX 风格（内部
  PurePosixPath 归一），仅 LocalExecutionEnv 边界转换"。实现中 `cwd` 存储为
  `str(Path(cwd).resolve())`（Windows 上为反斜杠路径），`absolute_path` 和
  `canonical_path` 同样返回 OS 原生路径。仅 `file_info` 内的 `_display_path`
  使用 `as_posix()` 返回 POSIX 风格。
- 影响：在 Windows 上，技能/模板路径在系统提示中显示为反斜杠风格（如
  `C:\skills\writer\SKILL.md`），与 pi 的 POSIX 统一风格不一致。跨平台
  session 文件互读时路径格式可能不兼容。
- 缓解：当前测试在 Windows 上通过是因为 pathlib 自动处理了分隔符；若需严格
  POSIX 兼容，需在 `absolute_path` / `canonical_path` / `cwd` 返回值上调用
  `PurePosixPath` 或 `.as_posix()` 转换。

### H4-6. `exec()` 超时终止未使用进程组 kill

- [x] **已修复（2026-08-05）** ·（低 · 代码审查）
- 位置：`env.py` `LocalExecutionEnv.exec`
- 问题：设计 §8.2 写 "超时 kill 进程组"；原实现仅调用 `proc.kill()` 终止主进程，
  在 Unix 上子进程可能成为孤儿。
- 修复：新增模块级 `_kill_process_tree` 辅助函数——Unix 上以 `start_new_session=True`
  启动进程（创建独立进程组），超时/abort 时调用 `os.killpg(proc.pid, SIGKILL)`
  终止整个进程组；Windows 上保持 `proc.kill()`（`TerminateProcess` 已终止进程树）。
  timeout / signal abort / CancelledError 三条 kill 路径统一改用
  `_kill_process_tree`。

### H4-7. `_validate_skill_metadata` 对非 SKILL.md 文件的 name 匹配校验过于严格

- [~] 记录性 ·（低 · 代码审查）
- 位置：`skills.py` `_validate_skill_metadata`、`_load_skill_path` L96–97
- 问题：设计 §6.1 写 "校验...与父目录名一致"，指的是 SKILL.md 文件的 name 应与
  父目录名一致。但对于根目录下的直接 `.md` 文件，`default_name` 被设为文件名 stem
  （去 `.md`），校验变成 "name 必须与文件名一致"。这在语义上合理（不与 pi 矛盾），
  但设计文档措辞只提及目录名。
- 影响：无功能问题；校验行为比设计更全面。

### H4-8. `frontmatter.py` 对 `---` 结束标记的匹配不够严格

- [~] 记录性 ·（低 · 代码审查）
- 位置：`frontmatter.py` `parse_frontmatter` L13
- 问题：`text.find("\n---", 4)` 会匹配 `\n---` 后有任意后续字符的行
  （如 `\n---extra`），而标准 frontmatter 要求结束行为单独的 `---`（仅跟换行）。
  pi 的实现使用 `gray-matter` npm 包处理更严格的匹配。
- 影响：极端边缘情况——正常 SKILL.md 文件不太可能在 frontmatter 分隔行后紧跟
  非换行字符。实际使用中不会触发。

### H4-9. `load_prompt_templates` 异常未包装为诊断

- [~] 记录性 ·（低 · 代码审查）
- 位置：`prompt_templates.py` `load_prompt_templates` L18–24
- 问题：`load_prompt_templates` 中 `env.file_info` / `env.list_dir` /
  `_load_template_file` 的异常未捕获，直接向上传播。而 `load_skills` 的同类操作
  均包装为 `SkillDiagnostic`（warning 不阻断）。设计 §6.3 未明确指定诊断行为，
  但与 skills 的容错模式不一致。
- 影响：单个模板文件读取失败会终止整个加载流程。pi 的 `load_prompt_templates`
  同样不做异常包装（与 skills 不同），因此这是忠实移植。

---

## 四、测试缺口（对照 §9 H4 交付判据与 §10 测试策略）

| 缺口 | 说明 | 状态 |
|------|------|------|
| skills `disableModelInvocation` 属性验证 | `load_skills` 读取 `disable-model-invocation` / `disableModelInvocation` 双键 | ✅ `test_format_skills_for_system_prompt_and_invocation` |
| skills ignore 文件多级继承 | 子目录继承父目录 `.ignore` 规则 | ✅ `test_load_skills_inherits_ignore_rules_to_child_directories` |
| skills SKILL.md 停止深入语义 | 含 SKILL.md 的目录不继续递归子目录 | ✅ `test_load_skills_stops_recursion_at_skill_md` |
| skills frontmatter YAML 解析失败 | 恶意/损坏的 YAML → 诊断 | ✅ `test_load_skills_yaml_parse_failure_produces_diagnostic` |
| skills `load_sourced_skills` | 带来源标签的批量加载 | ✅ `test_load_sourced_skills_groups_by_source` |
| prompt templates `${@:N}` / `${@:N:L}` 语法 | range slice 替换 | ✅ `test_substitute_args_range_slice_syntax` |
| `LocalExecutionEnv.read_binary_file` | 二进制文件读取 | ✅ `test_local_execution_env_read_binary_file` |
| `LocalExecutionEnv.create_dir` / `remove` | 目录创建与删除 | ✅ `test_local_execution_env_create_dir_and_remove` |
| `LocalExecutionEnv.create_temp_dir` / `create_temp_file` / `cleanup` | 临时文件生命周期 | ✅ `test_local_execution_env_temp_lifecycle` |
| `LocalExecutionEnv.canonical_path` / `absolute_path` / `exists` | 路径解析与存在性检查 | ✅ `test_local_execution_env_path_resolution` |
| `LocalExecutionEnv.file_info` not found | 缺失文件抛 `FileError` | ✅ `test_local_execution_env_file_info_not_found` |
| `LocalExecutionEnv.exec` timeout | 超时终止进程 | ✅ `test_local_execution_env_exec_timeout` |
| `LocalExecutionEnv.exec` signal abort (pre-start) | 启动前 abort 检查 | ✅ `test_local_execution_env_exec_signal_abort_before_start` |
| `LocalExecutionEnv.exec` signal abort (mid-execution) | 执行中 signal 中断（H4-1 回归） | ✅ `test_local_execution_env_exec_signal_abort_mid_execution` |
| `system_prompt` 回调模式 | `system_prompt` 传 callable 时接收正确参数 | ✅ `test_system_prompt_callback_receives_correct_params` |
| `system_prompt` 无 skills 时不注入 | resources 无 skills → 系统提示无 `<skills>` 块 | ✅ `test_system_prompt_no_skills_omits_skills_block` |

---

## 五、记录性（无需行动）

- [~] `parse_command_args` 直接使用 `shlex.split`——与设计 §6.3 "支持单双引号的
  shell 风格分词"一致；shlex 在 Windows 上默认 `posix=True`，与 pi 的 shell 分词
  行为对齐。
- [~] `substitute_args` 的 regex 将 `$1..$n` 限定为 `$([0-9]+)`，不支持
  `$10` 以上的双位数定位参数——pi 同样行为，非偏差。
- [~] `build_harness_system_prompt` 在 `base_prompt` 为空时默认
  "You are a helpful assistant."——设计未明确指定默认值，但 `_create_turn_state`
  中也有同样默认值，行为一致。
- [~] `_xml_escape` 实现仅转义 `& " < >`——标准 XML 属性转义，与 pi 一致。
- [~] `frontmatter.py` 使用 `yaml.safe_load`——安全且与 pi 的 `gray-matter`
  YAML 模式对齐。
- [~] `skills.py` 结果按 `skill.name` 字母排序——pi 同样行为。
- [~] `prompt_templates.py` 结果按 `template.name` 字母排序——pi 同样行为。
- [~] `pyproject.toml` 正确声明 `pathspec>=0.12` 和 `PyYAML>=6`，对齐设计
  §6.4 依赖要求。

---

## 修复优先级

| 级别 | 项目 | 状态 |
|------|------|------|
| 实质 | H4-1 exec signal 执行中中断 | ✅ 已修复（2026-08-05，回归 +2） |
| 中 | H4-2 on_stdout/on_stderr 流式回调 | ✅ 已修复（2026-08-05，回归 +2） |
| 中 | H4-3 Skill/PromptTemplate 导出缺失 | ✅ 已修复（2026-08-05，回归 +1） |
| 中 | H4-4 parse_failed 诊断码未使用 | ✅ 已修复（2026-08-05，回归 +1） |
| 低 | H4-5 Windows POSIX 路径 | [~] 记录性 |
| 低 | H4-6 进程组 kill | ✅ 已修复（2026-08-05） |
| 低 | H4-7 name 匹配校验范围 | [~] 记录性 |
| 低 | H4-8 frontmatter 结束标记 | [~] 记录性 |
| 低 | H4-9 模板加载异常 | [~] 记录性（忠实移植） |
| 测试 | 第四节缺口（16 项） | ✅ 已补（2026-08-05，+15） |

## 验证状态

审计时全量 pytest 308 通过（`.venv-audit` Python 3.12 / Windows），`ruff check` 全绿。
第一轮修复（H4-1）+ 测试补全后全量 pytest 323 通过，H4 专项测试从 6 增至 21 个。
第二轮修复（H4-2/H4-3/H4-4/H4-6）后全量 pytest 327 通过，H4 专项测试 25 个全通过
（`test_harness_h4_resources_env.py`）。所有可修复偏差已关闭。
