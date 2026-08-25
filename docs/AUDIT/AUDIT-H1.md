# AUDIT-H1：Phase 3 H1（session 层）实现审计（2026-07-03）

> 审计范围：`packages/pi-agent-harness/pi_agent_harness/{types.py, session/}` 全部 H1 源码，
> 对照 Phase 3 设计文档（`docs/specs/2026-07-03-phase3-agent-harness-design.md`
> §3/§8/§9/§10）与上游 [earendil-works/pi](https://github.com/earendil-works/pi)
> `packages/agent/src/harness/session/` 六个 TS 源文件 + `harness/types.ts` 逐行核对。
>
> 验证方式：`.venv-audit`（Python 3.12）运行全量 pytest 与 ruff；对三个疑点编写探针脚本
> 运行时实证（未知 entry 类型解析、落盘 JSON 键序、短 entry id 的 65 秒窗口回退行为）。
>
> 状态图例：`[x]` 已修复 · `[ ]` 待处理 · `[~]` 记录性（文档已批准的偏差或无需行动）。

## 总体结论

H1 主体忠实：11 种 entry 模型、错误层级、`SessionStorage` 协议、leaf 由末行推导、
路径回放（含 compaction 分段回放）、label 缓存、JSONL 校验文案、ISO 毫秒时间戳、
`exclude_none` 对齐 JS 省略 `undefined` 的语义——均与 pi 逐行等价；fixture 互读与
往返单测符合 §9 交付判据。审计发现 2 个实质偏差、4 个中等偏差、若干 nits；
实质项与字节序项已于当日修复（commit `63e4e8c`），H1-3/H1-4 于 2026-07-21 修复，
其余（H1-5/H1-6/H1-8）于 2026-07-28 全部收尾——**本审计无遗留待办**。

---

## 一、已修复

### H1-1. 未知 entry 类型被拒读——前向兼容破坏（2026-07-03，commit `63e4e8c`）

- [x] **已修复** ·（实质 · 探针实证）
- 位置：`session/jsonl_storage.py` `parse_entry_line`
- 问题：pi 只校验 `type/id/parentId/timestamp`（+ leaf 的 `targetId` 类型）后接受任意
  `type` 字符串；Python 走 pydantic discriminated union，未知类型抛 `invalid_entry`。
  新版 pi / 其他实现写入新 entry 种类后整个会话文件打不开。
- 修复：基础字段校验保持，union 校验失败回退 `SessionTreeEntryBase`
  （`extra="allow"` 保留全部原始字段）——回放忽略、写回保留、可作 parent 链节点。
  配套将 `_leaf_id_after_entry` 从 `isinstance(LeafEntry)` 改为 `type == "leaf"` 判断
  （对齐 pi 的字符串判别，兼容回退 entry），并删除 `memory_repo.py` 中的重复副本。

### H1-2. fork 语义三处偏离 pi（2026-07-03，commit `63e4e8c`）

- [x] **已修复** ·（实质 · 上游源码核对）
- 位置：`session/jsonl_repo.py` / `memory_repo.py`（现共享 `session/repo_utils.py`）
- 问题（对照上游 `repo-utils.ts` `getEntriesToFork`）：
  1. 默认 `position`：pi 为 `"before"`，Python 曾为 `"at"`；
  2. pi 的 `"before"` 要求目标是 **user message** entry（否则 `invalid_fork_target`），
     Python 曾不校验；
  3. 不传 `entryId` 时 pi 复制**全部 entries**（整棵树含分支与 leaf 记录），
     Python 曾取当前叶子 path-to-root，丢弃其他分支。
- 修复：新增 `session/repo_utils.py::get_entries_to_fork` 忠实移植，两 repo 共用；
  测试覆盖默认 before / 显式 at / 非 user-message 报错 / 全树复制 + leaf 位置保持。

### H1-7. 根 entry 键序不符 pi 字节布局（2026-07-03，commit `63e4e8c`）

- [x] **已修复** ·（中 · 探针实证）
- 位置：`session/jsonl_storage.py` `_entry_to_json`
- 问题：`exclude_none` 剔除 `parentId=null`（每个会话文件的首 entry 必然如此）与 leaf 的
  `targetId=null` 后再回填，键被挤到行尾——互读无碍，但设计方向决策 2 的"字节兼容"
  在逐字节意义上不成立。
- 修复：按模型字段声明序重建 dict（未知外来字段排尾）。实测根 entry 键序为
  `type, id, parentId, timestamp, message`，与 pi 字面量 `JSON.stringify` 输出一致。

### H1-3. `JsonlSessionRepo` 不支持注入 FileSystem（2026-07-21）

- [x] **已修复** ·（中 · 代码审查）
- 位置：`session/jsonl_repo.py`
- 问题：设计写 `JsonlSessionRepo(env, dir)`（pi 亦为 `{fs, sessionsRoot}` 注入式）；实现只收
  目录、内部硬编码 `_PathJsonlStorageFs`，`list`/`delete`/`create` 的存在性检查绕过文件系统抽象。
- 修复：新增窄协议 `JsonlRepoFs`（`JsonlStorageFs` + `exists`/`list_dir`/`remove`）；构造器改为
  `JsonlSessionRepo(directory, fs=None)`，默认 `LocalExecutionEnv(Path.cwd())` 保持相对路径语义；
  全部 repo 文件系统操作改走 `self._fs`；导出 `JsonlRepoFs`；新增内存 fake 端到端单测与
  `list` 缺目录返回 `[]` 回归断言。

### H1-4. §8.1 完整协议未定义（2026-07-21）

- [x] **已修复** ·（中 · 代码审查）
- 位置：`types.py`；注解：`agent_harness.py`、`skills.py`、`prompt_templates.py`
- 问题：只有 4 方法 mini `FileSystem` Protocol；§8.1 完整 `FileSystem`/`Shell`/`ExecutionEnv`
  不存在；`skills.py`/`prompt_templates.py` 的 `env` 为 `Any`。
- 修复：在 `types.py` 定义三个 `@runtime_checkable` 完整协议（签名对齐 `LocalExecutionEnv`）；
  删除 mini `FileSystem`；`AgentHarness.env` 改为 `ExecutionEnv | None`，skills/templates 改为
  `FileSystem`；从 `pi_agent_harness` 与 shim 导出三协议；新增 `isinstance` 协议一致性单测。

### H1-5. `branch_summary.fromId` 落盘值与 pi 不同（2026-07-28）

- [x] **已修复** ·（中 · 上游源码核对）· 决策：**对齐 pi，丢弃覆盖能力**
- 位置：`session/session.py` `move_to`（H3 `agent_harness.py` `navigate_tree` 为调用方）
- 问题：pi 的 `Session.moveTo` 固定写 `fromId = entryId ?? "root"`（移动目标），summary
  参数根本没有 `fromId` 键（`{summary, details, usage?, fromHook}`，`navigateTree` 亦不传）；
  Python 曾接受 summary dict 的 `fromId` 覆盖，且 `navigate_tree` 传 `old_leaf_id`
  （被放弃的分支）。同一操作两边落盘值不同，pi 生态 UI 若用 `fromId` 定位会指向不同节点。
- 修复：`move_to` 忽略 summary dict 中的 `fromId`，固定写 `entry_id ?? "root"`；
  `navigate_tree` 不再传 `fromId`。依据 AGENTS.md"忠实移植"原则选对齐而非记录偏差；
  新增单测断言覆盖尝试被忽略、`move_to(None)` 写 `"root"`。

### H1-6. Repo 细节行为与 pi 不一致（容错/排序/幂等）（2026-07-28）

- [x] **已修复** ·（低-中 · 上游源码核对）
- 位置：`session/jsonl_repo.py` / `memory_repo.py`
- 三点（对照上游 `jsonl-repo.ts` / `memory-repo.ts`）：
  1. `list()`：pi 对单文件 `invalid_session` 跳过继续（目录混入非 session 文件不致命），
     Python 曾一个坏文件炸整个 `list()`——现 try/except 跳过 `invalid_session`，
     其他错误码（`not_found`/`storage`）仍上抛；
  2. `list()` 排序：pi 按 `createdAt` 降序（`new Date(...)` 数值比较），Python 曾按文件名
     升序——现按 header `createdAt` 毫秒值降序，无法解析的时间戳回退 0（对应 JS `NaN`
     不炸排序），扫描仍按文件名序保证平局确定性；
  3. `delete()`：pi 幂等（`remove(path, {force: true})` / `Map.delete`，缺文件不报错），
     Python 曾抛 `not_found`——两 repo 均改为无检查直删，`JsonlRepoFs.remove` 协议注明
     force 语义，内存 fake 同步 `pop(..., None)`。
- 新增单测：坏文件/空文件/非 `.jsonl` 混入目录的 `list()`、`createdAt` 降序（文件名序
  故意与时间序相反）、两 repo 重复删除与删除不存在会话。

### H1-8. Nits（2026-07-28，攒批处理完成）

- [x] `append_session_name` 只把 `[\r\n]+` 换空格后 trim（不再折叠 tab/连续空格）；
  `get_session_name` 对全空白名返回 `None`（对齐 pi `name?.trim() || undefined`）。
- [x] `MemorySessionStorage.create` 删除被静默忽略的 `cwd` 参数（pi 的
  `InMemorySessionStorage` 元数据即 `{id, createdAt}`），全部测试调用点同步。
- [x] uuid 格式：`uuid7()` 改输出 36 位连字符规范格式（对齐 pi `uuidv7()`）；短 entry id
  从前 8 位改为**随机尾 8 位**（对齐上游 `uuidv7().slice(-8)` 及其注释——前缀是时间戳
  派生毫秒间几乎不变；此举同时消除审计原记录的 65 秒窗口回退现象，上游已同样修正）。
- [x] fixture：新增 `pi-v3-session-tree.jsonl`（compaction + 放弃分支 + leaf 移动 +
  branch_summary + label），单测断言分段回放消息序列、label 缓存、离路径 entry 保留。
- [x] `pi_agent_core/harness/__pycache__/` 拆包前旧字节码已清理（本地未跟踪文件）。

---

## 二、记录性（无需行动）

- [~] **目录布局 / 文件名 vs pi**：pi 按 `sessionsRoot/--encoded-cwd--/<ts>_<id>.jsonl`
  组织；Python 平铺 `<dir>/<ts>-<id>.jsonl` + 读 header 过滤——设计文档 §3.6 明确批准的
  偏差。注意含义：pi 生态工具按目录约定扫描时找不到 Python 写的会话，
  字节兼容只到"给定文件路径可读"这一层。
- [~] `append_entry` 增加重复 id 检查（pi 无）——无害的防御性增强，保留。
- [~] `JsonlSessionRepo.create` 对已存在路径抛错（pi 不检查）——同上，保留。

## 验证状态

修复后全量 pytest 通过（2026-07-03 提交时 239 个，P6 批次仍在并行落地；2026-07-28
收尾时 282 个）、`ruff check` 与 `ruff format --check` 全绿。相关新增单测：未知 entry
宽容读取 + 写回保留、fork 四语义（before/at/非 user 报错/全树）、根 entry 与 leaf 键序
断言、注入 fs 端到端 + 协议 isinstance（H1-3/H1-4）、`fromId` 固定写移动目标、`list`
容错与降序、幂等删除、会话名空白语义、树形 fixture 分段回放（H1-5/H1-6/H1-8）。
