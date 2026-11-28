# pi-chat-gateway-py

Use the [zycode](https://github.com/zy1233/zycode) coding agent from a chat app. Version 0.1 speaks
to **Feishu / Lark** and is for one person: the owner of the bot.

The gateway (`zycode-chat`) is one more standard [ACP](https://agentclientprotocol.com) client,
like the VS Code extension and `zypi`. It keeps a long connection to Feishu (it connects out, so
no public port is needed), turns your messages into prompts, and starts one `zycode-agent` per
project on your machine. The agent does the work there; the chat shows it and asks you to approve.

Design: [docs/specs/2026-11-28-chat-gateway-design.md](https://github.com/zy1233/zycode/blob/main/docs/specs/2026-11-28-chat-gateway-design.md).

Status: alpha, not published on PyPI yet. It is tested against the real agent with a fake chat
platform, and the Feishu adapter is tested with hand-built events and a fake SDK client. It has
**not** been run against a live Feishu tenant yet (see [Not verified](#not-verified)).

## What it does

- **Tasks from the chat.** Send the bot a direct message. The answer is a card that the gateway
  updates while the agent works (status, one line per tool call, the answer), at most once a
  second.
- **Approvals on your phone.** When the agent asks before a tool call, a card shows the whole
  command, path, code or script, with **Allow once** and **Reject**. A text too long for one card
  follows in full in more cards; nothing is cut silently. Only the owner (and, in a bound group,
  the listed `approvers`) can approve. An approval nobody answers in 30 minutes is rejected.
- **Steer.** A message sent while a turn runs is added to it (`pi/session/steer`): ⏳ when it is
  queued, ✅ when the model read it, ❌ with a note when the turn ended first.
- **`/btw`.** A side question answered in its own card from a copy of the session, never saved
  in it (`pi/session/btw`).
- **Commands:** `/new`, `/project <name>`, `/resume`, `/stop`, `/btw <question>` (`/btw end`),
  `/mode ask|auto`, `/model`, `/thinking`, `/status`, `/help`. Any other `/command` goes to the
  agent (its `/skill:<name>` commands, say).
- **Groups (optional).** A group listed under `[[channels]]` is bound to one project. Mention the
  bot to start; each mention starts a thread with its own session. Approvals of group sessions
  go to the owner's direct chat.

## Safety rules

These are fixed, not options:

1. **Never `always-approve`.** `start_mode` is `ask` (default) or `auto` (only with
   `allow_auto = true`). If `agent.toml` puts a session in `always-approve`, the gateway puts it
   back to `ask` and says so; a session it cannot put back is not used. The gateway never sends
   `pi/yolo_mode_changed`, which would move every session of an agent process.
2. **No project trust from the chat.** When the agent asks whether to trust the project (its
   extensions, saved workflows, prompt files or skills), the gateway answers no and tells you to
   trust the project on the machine (in `zypi` or VS Code). Trusting runs the project's code.
3. **Only the owner is heard.** Messages from anyone else, or from another tenant, are dropped
   without an answer. The owner pairs with a one-time code printed in the machine's terminal.
4. **The credentials stay out of the agent.** They live in `<home>/chat/credentials.toml`
   (mode 600; a looser file is refused), not in `local.env`, which the agent loads into its own
   environment. The agents start without any variable that holds a credential.
5. **Nothing the platform could fetch.** Links in answers are shown as code and Markdown images
   as text, so the platform's servers never fetch a URL the model wrote (a link preview would
   send whatever the URL carries). Card tags such as `<at id=all>` are shown as text. Known
   secret values and common token formats are redacted (best effort).
6. **Tool output is not posted.** A tool call shows as one line; its output stays on the machine.

What is posted to the chat (answers, commands, paths, code) is stored on Feishu's servers. The
gateway is not a sandbox: the agent runs on your machine with your permissions, and Auto mode
does not guard what a call reads.

## Set up

1. Install it next to the agent (Python 3.11+). In a checkout of the repository:

   ```bash
   pip install -e "./packages/pi-chat-gateway[feishu]"
   ```

   The agent must be 1.2.0 or later (steer and `/btw`). The gateway uses `zycode-agent` if it
   finds one next to itself or on `PATH`, else `python -m pi_agent_cli` of its own environment;
   `agent_command` in `chat.toml` overrides both. The agent needs a model in
   `~/.pi-python/agent.toml` as usual (`zycode-agent --setup`).

2. In the Feishu (or Lark) developer console, create an app in your tenant:
   - add the **Bot** capability;
   - grant the permissions to receive the messages users send to the bot (direct chats and
     @mentions in groups), to send messages as the app, to read message resources (images) and
     to add reactions (use the names the console shows);
   - under events and callbacks, choose **receive through a long connection**, subscribe to
     `im.message.receive_v1`, and add the callback `card.action.trigger`;
   - publish a version and have it approved in your tenant.

3. Run `zycode-chat setup`. It writes `~/.pi-python/chat.toml` (the first time) and
   `~/.pi-python/chat/credentials.toml`, then reads the bot's identity to test the credentials.

4. Run `zycode-chat run`, then `zycode-chat pair` in another terminal and send the code it prints
   to the bot in a direct chat: `/pair ABCD-EFGH`. The code is good for 10 minutes and one use.

To run it as a service, start `zycode-chat run` from a systemd user unit, a launchd agent or a
Windows scheduled task. With `env = "login-shell"` (the default on Linux and macOS) the agents get
the environment of your login shell, so `bash` finds the same programs as in a terminal.

## `chat.toml`

```toml
[gateway]
start_mode = "ask"               # ask | auto (needs allow_auto = true)
allow_auto = false
approval_timeout_minutes = 30
approvals_in_dm = true
idle_agent_minutes = 20          # an idle agent process is stopped, and started again when needed
max_active_turns = 3
show_thinking = false
env = "login-shell"              # login-shell | inherit
language = "zh"                  # zh | en

[[projects]]
name = "zycode"
path = "/home/me/work/zycode"    # absolute; the chat picks projects by name only

[[platforms]]
id = "feishu"
type = "feishu"
domain = "feishu"                # feishu (open.feishu.cn) | lark (open.larksuite.com)
# tenant = "..."                 # empty: the tenant of the owner, learned at pairing

[[channels]]                     # optional
platform = "feishu"
chat_id = "oc_..."
project = "zycode"
users = []                       # besides the owner, who may talk to the agent there
approvers = []                   # besides the owner, who may approve there
```

The gateway keeps its own state in `~/.pi-python/chat/`: `state.sqlite` (the owner, the pairing
codes as hashes, which chat maps to which session, the ids of handled events; no message text),
`audit.log` (pairings, approvals, refusals) and `logs/` (the gateway's log and each agent's
stderr). The conversations stay in the agent's session files.

## Not verified

- A live Feishu or Lark tenant: the cards, the callback over the long connection, the reaction
  emoji types (`OnIt`, `DONE`, `CrossMark`) and the permission names have not been tried there.
- Whether Feishu previews links inside a card; the gateway neutralizes links either way.
- Windows: the credentials file is not checked for permissions there (ACLs), and the login-shell
  environment is not read (the gateway's own environment is used).
- The same session continued in the chat and in VS Code or `zypi` at the same time: there is no
  lock across processes yet, so the session tree can fork. Do not continue one session in two
  places at once.

## Tests

```bash
pytest packages/pi-chat-gateway/tests
```

The tests start real agents with the mock LLM and with the scripted tool agent of
`packages/pi-agent-cli/tests`; nothing goes to the network. The Feishu tests need `lark-oapi`.
