"""Everything the gateway itself says in the chat, in each language it speaks.

The words of the agent (answers, Auto notices, the trust explanation) are not here: the agent
writes them, and the gateway only passes them on. Keep the two tables with the same keys;
``test_texts.py`` checks that every placeholder of one language is in the other.
"""

from __future__ import annotations

from typing import Final

LANGUAGES: Final = ("zh", "en")

_ZH: Final[dict[str, str]] = {
    "title": "zycode",
    "status_thinking": "思考中",
    "status_running": "运行中",
    "status_waiting": "等待审批",
    "status_done": "完成",
    "status_cancelled": "已停止",
    "status_error": "出错",
    "status_notice": "通知",
    "tools_more": "……另有 {count} 个工具调用",
    "answer_continued": "（回答较长，后半部分见下面的消息）",
    "answer_part": "（续 {index}/{total}）",
    "footer": "{stop} · 用量 {used}",
    "footer_context": "{stop} · 上下文 {used}/{size}",
    "approval_title": "需要审批",
    "approval_tool": "工具：{title}",
    "approval_full_below": "完整内容超过卡片长度，后面附了全文。",
    "approval_full_part": "审批内容全文（{index}/{total}）",
    "approval_allowed": "已允许 · {who} · {time}",
    "approval_rejected": "已拒绝 · {who} · {time}",
    "approval_timeout": "超时，已自动拒绝",
    "approval_cancelled": "已取消",
    "approval_sent_to_dm": "等待所有者审批（审批卡片已发到所有者的私聊）",
    "approval_forbidden": "你不能审批这个请求",
    "approval_gone": "这个审批已经结束",
    "approval_ok": "已记录",
    "btn_allow_once": "允许一次",
    "btn_reject": "拒绝",
    "trust_rejected": (
        "这个项目自带的扩展、skills 或提示文件没有加载：聊天里不能信任项目。"
        "要启用，请在本机用 zypi 或 VS Code 打开这个项目并选择信任，"
        "或者把它列进 agent.toml 的 [extensions] trusted_projects。"
    ),
    "steer_queued": "已排队，agent 走到下一步时会读到这条。",
    "steer_not_delivered": "这条消息没有送达模型（这一轮已经结束）：{preview}。需要的话请重发。",
    "busy_later": "agent 正在处理别的事，请稍后再发。",
    "too_busy": "同时进行的任务太多（上限 {limit}），请稍后再发。",
    "stop_sent": "已请求停止。",
    "nothing_running": "现在没有在运行的任务。",
    "new_session": "已开新会话。项目：{project}",
    "no_projects": "还没有可用的项目。请在本机的 chat.toml 里加上 [[projects]]。",
    "choose_project": "有多个项目，请先选一个：/project <名字>。可选：{names}",
    "unknown_project": "没有这个项目：{name}。可选：{names}",
    "project_set": "项目：{name}。之后的消息在这个项目里开新会话。",
    "project_fixed": "这个群绑定的项目是 {name}，不能切换。",
    "resume_title": "选择要恢复的会话",
    "resume_none": "这个项目还没有会话。",
    "resume_recent": "（10 分钟内有改动，可能正在别处使用）",
    "resume_button": "恢复",
    "resume_done": "已恢复会话：{title}",
    "resume_failed": "没有恢复这个会话：{error}",
    "btw_title": "BTW · 不进入会话",
    "btw_usage": "用法：/btw <问题>；/btw end 结束这次旁路问答",
    "btw_ended": "已结束这次旁路问答。",
    "btw_unsupported": "这个 agent 不支持 /btw（需要 1.2.0 或更新的版本）。",
    "btw_failed": "旁路问答出错：{error}",
    "mode_set": "模式：{mode}",
    "mode_current": "当前模式：{mode}。可选：ask{auto}",
    "mode_refused_always": "聊天里不能进入 always-approve。",
    "mode_refused_auto": "配置没有允许 auto（chat.toml 的 allow_auto）。",
    "mode_unknown": "不认识的模式：{mode}。可选：ask{auto}",
    "mode_forced_back": "会话处于 {mode}，网关已把它切回 {target}。聊天里只用 ask 或 auto。",
    "mode_unsafe": "会话处于 {mode}，而且没能切回 ask，所以没有把消息交给它。",
    "option_list": "{label}：{current}。可选：{values}",
    "option_set": "{label}：{value}",
    "option_missing": "这个 agent 没有公布 {label} 配置项。",
    "option_unknown": "{label} 没有这个值：{value}。可选：{values}",
    "label_model": "模型",
    "label_thinking": "思考强度",
    "status_line": "项目：{project}\n会话：{session}\n模式：{mode}\n状态：{state}",
    "state_idle": "空闲",
    "state_busy": "运行中",
    "session_none": "（还没有）",
    "help": (
        "直接发消息就是给 agent 派任务；任务进行中再发一条会作为补充（steer）交给它。\n"
        "/new 开新会话 · /project <名字> 选项目 · /resume 恢复会话 · /stop 停止\n"
        "/btw <问题> 旁路提问（不进入会话） · /mode ask|auto · /model · /thinking · /status"
    ),
    "help_agent_commands": "agent 的命令：{commands}",
    "pair_ok": "已配对：你是这个机器人的所有者。",
    "pair_bad": "配对码不对或已过期。请在本机运行 zycode-chat pair 取一个新的。",
    "pair_locked": "配对失败的次数太多，请 1 小时后再试。",
    "pair_taken": "这个机器人已经有所有者。要换人，请先在本机运行 zycode-chat unpair。",
    "pair_dm_only": "请在和机器人的私聊里配对。",
    "image_too_large": "图片太大（上限 {limit} MB），没有交给 agent。",
    "image_failed": "图片没有下载下来，没有交给 agent。",
    "file_unsupported": "目前只收文字和图片，文件没有交给 agent。",
    "agent_failed": "agent 出错：{error}",
    "agent_exited": "agent 进程退出了。下一条消息会重新启动它。",
    "agent_start_failed": "agent 没能启动：{error}",
    "turn_interrupted": "上一轮被中断了（agent 退出）。",
    "notice_from_agent": "agent 的消息",
}

_EN: Final[dict[str, str]] = {
    "title": "zycode",
    "status_thinking": "Thinking",
    "status_running": "Running",
    "status_waiting": "Waiting for approval",
    "status_done": "Done",
    "status_cancelled": "Stopped",
    "status_error": "Error",
    "status_notice": "Notice",
    "tools_more": "... and {count} more tool calls",
    "answer_continued": "(The answer is long: the rest is in the messages below.)",
    "answer_part": "(part {index}/{total})",
    "footer": "{stop} · usage {used}",
    "footer_context": "{stop} · context {used}/{size}",
    "approval_title": "Approval needed",
    "approval_tool": "Tool: {title}",
    "approval_full_below": "The full text does not fit on the card; it follows below.",
    "approval_full_part": "Full text of the request ({index}/{total})",
    "approval_allowed": "Allowed · {who} · {time}",
    "approval_rejected": "Rejected · {who} · {time}",
    "approval_timeout": "Timed out, rejected",
    "approval_cancelled": "Cancelled",
    "approval_sent_to_dm": "Waiting for the owner (the approval card went to the owner's direct chat)",
    "approval_forbidden": "You cannot approve this request",
    "approval_gone": "This request is closed",
    "approval_ok": "Recorded",
    "btn_allow_once": "Allow once",
    "btn_reject": "Reject",
    "trust_rejected": (
        "The extensions, skills or prompt files of this project were not loaded: a project "
        "cannot be trusted from the chat. To use them, open the project in zypi or VS Code on "
        "this machine and trust it there, or list it under [extensions] trusted_projects in "
        "agent.toml."
    ),
    "steer_queued": "Queued: the agent reads it at its next step.",
    "steer_not_delivered": (
        "This message did not reach the model (the turn ended first): {preview}. "
        "Send it again if you still need it."
    ),
    "busy_later": "The agent is busy with something else. Send it again in a moment.",
    "too_busy": "Too many tasks at once (limit {limit}). Send it again later.",
    "stop_sent": "Asked the agent to stop.",
    "nothing_running": "Nothing is running.",
    "new_session": "New session. Project: {project}",
    "no_projects": "No project is set up. Add [[projects]] to chat.toml on the machine.",
    "choose_project": "There are several projects. Pick one first: /project <name>. Choices: {names}",
    "unknown_project": "No such project: {name}. Choices: {names}",
    "project_set": "Project: {name}. The next message starts a session there.",
    "project_fixed": "This group is bound to the project {name}; it cannot change.",
    "resume_title": "Pick a session to resume",
    "resume_none": "This project has no sessions yet.",
    "resume_recent": "(changed in the last 10 minutes: it may be open elsewhere)",
    "resume_button": "Resume",
    "resume_done": "Resumed: {title}",
    "resume_failed": "Could not resume the session: {error}",
    "btw_title": "BTW · not saved in the session",
    "btw_usage": "Usage: /btw <question>; /btw end ends the side chat",
    "btw_ended": "Side chat ended.",
    "btw_unsupported": "This agent has no /btw (it needs version 1.2.0 or later).",
    "btw_failed": "The side chat failed: {error}",
    "mode_set": "Mode: {mode}",
    "mode_current": "Mode: {mode}. Choices: ask{auto}",
    "mode_refused_always": "always-approve cannot be turned on from the chat.",
    "mode_refused_auto": "auto is not allowed by the configuration (allow_auto in chat.toml).",
    "mode_unknown": "Unknown mode: {mode}. Choices: ask{auto}",
    "mode_forced_back": (
        "The session was in {mode}; the gateway put it back to {target}. "
        "The chat uses ask or auto only."
    ),
    "mode_unsafe": (
        "The session is in {mode} and could not be put back to ask, so the message was "
        "not given to it."
    ),
    "option_list": "{label}: {current}. Choices: {values}",
    "option_set": "{label}: {value}",
    "option_missing": "This agent does not offer a {label} option.",
    "option_unknown": "{label} has no value {value}. Choices: {values}",
    "label_model": "Model",
    "label_thinking": "Thinking level",
    "status_line": "Project: {project}\nSession: {session}\nMode: {mode}\nState: {state}",
    "state_idle": "idle",
    "state_busy": "running",
    "session_none": "(none yet)",
    "help": (
        "Send a message to give the agent a task; a message sent while it works is added to "
        "the task (steer).\n"
        "/new new session · /project <name> pick a project · /resume resume · /stop stop\n"
        "/btw <question> side question (not saved) · /mode ask|auto · /model · /thinking · "
        "/status"
    ),
    "help_agent_commands": "Commands of the agent: {commands}",
    "pair_ok": "Paired: you are the owner of this bot.",
    "pair_bad": "Wrong or expired code. Run zycode-chat pair on the machine for a new one.",
    "pair_locked": "Too many failed attempts. Try again in an hour.",
    "pair_taken": "This bot has an owner. Run zycode-chat unpair on the machine first.",
    "pair_dm_only": "Pair in a direct chat with the bot.",
    "image_too_large": "The image is too large (limit {limit} MB); it was not given to the agent.",
    "image_failed": "The image could not be downloaded; it was not given to the agent.",
    "file_unsupported": "Only text and images are taken for now; the file was not passed on.",
    "agent_failed": "The agent failed: {error}",
    "agent_exited": "The agent process exited. The next message starts it again.",
    "agent_start_failed": "The agent did not start: {error}",
    "turn_interrupted": "The last turn was interrupted (the agent exited).",
    "notice_from_agent": "From the agent",
}

TABLES: Final[dict[str, dict[str, str]]] = {"zh": _ZH, "en": _EN}


class Texts:
    """The strings of one language. ``t("key", name=value)`` formats one of them."""

    def __init__(self, language: str = "zh") -> None:
        if language not in TABLES:
            raise ValueError(f"unknown language {language!r}; choose one of {', '.join(LANGUAGES)}")
        self.language = language
        self._table = TABLES[language]

    def __call__(self, key: str, **values: object) -> str:
        return self._table[key].format(**values)
