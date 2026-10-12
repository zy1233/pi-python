# Terminal Support and Troubleshooting

`zypi` is a full-screen terminal program. It needs terminal support for color, clipboard, keyboard input, mouse input, and full-screen display. Terminals, multiplexers, containers, and SSH connections handle these features in different ways.

## Find and fix terminal problems

Run `zypi doctor` in your shell. It checks the terminal, the multiplexer, color support, keyboard and newline behavior, and the clipboard routes. It does not start the agent. For a report that a program can read, run `zypi doctor --json`.

When `zypi doctor` finds a tmux setting that is not healthy, it can fix the setting. To list the fixes that are available, run:

```
zypi doctor fix
```

Apply one named fix at a time. For example:

```
zypi doctor fix tmux-clipboard
zypi doctor fix dcs-passthrough --yes
```

`--yes` applies the change without a question. These tmux options can be fixed:

- `terminal.tmux-clipboard`: `set -g set-clipboard on`
- `terminal.dcs-passthrough`: `set -wg allow-passthrough on`
- `terminal.tmux-extended-keys`: `set -g extended-keys on`
- `terminal.tmux-truecolor`: `set -as terminal-features ",*:RGB"`

A tmux fix changes only the persistent tmux configuration file on the computer that hosts the tmux server. This is also true for a remote session. Plain tmux uses `$HOME/.tmux.conf`. Byobu-tmux uses its `BYOBU_CONFIG_DIR`. If `BYOBU_CONFIG_DIR` is not set, the fix stops.

`zypi` does not run `tmux source-file` and does not change the running tmux server. After a fix, reload the tmux configuration with the command that `zypi` shows, or detach and attach again. Then run `zypi doctor` again.

The scan checks only direct global assignments. Check files that tmux sources, conditions, plugins, and generated setup yourself.

## Detected terminals

`zypi` finds these terminals from environment variables:

- Apple Terminal, Ghostty, iTerm2, Warp, WezTerm, Kitty, Alacritty, Rio, and foot.
- The integrated terminals of VS Code, Cursor, Windsurf, and Zed.
- The terminals of JetBrains IDEs.
- Terminals that are based on VTE, such as GNOME Terminal, GNOME Console, and Tilix.
- Windows Terminal.

The detection has these limits:

- In tmux, the variables that name the outer terminal can be missing.
- Over SSH, most terminal variables are not forwarded.
- The global environment of tmux shows the first client that attached to the server. This client is not always the current terminal.

## Common problems

### Colors are wrong or truecolor is missing

Run `zypi doctor`. A fully supported setup shows `color truecolor` and `themes all`. If it does not, `zypi doctor` shows the limit that it found and the fix.

In tmux, two questions are separate: what color `zypi` sends, and what color tmux passes on. The `color` line answers the first question. For the second question, look at the tmux client. If the client is not marked `RGB`, tmux changes each 24-bit color to the nearest color that the terminfo of the outer terminal lists. That can be as few as eight colors. The themes then look washed out, but `color` still reads `truecolor`. `zypi doctor` reports this as `terminal.tmux-truecolor`. Reload the tmux configuration, then detach and attach again. Do both steps. The server reads a new option only at reload. A client fixes its color depth only when it attaches.

See [Theming and Appearance](06-theming.md) for the themes.

### Clipboard problems

`zypi` copies text through up to three routes. `zypi doctor` shows them under **Clipboard**:

- **native**: the clipboard of the local operating system.
- **tmux**: the tmux paste buffer, when `zypi` runs in tmux.
- **OSC 52**: an escape sequence that can cross tmux, containers, and SSH.

Each copy is also saved in a backup file. The message on the screen names the file when `zypi` cannot confirm that the copy arrived. To choose the path of the file, set the `GROK_COPY_FILE` environment variable. A path that starts with `~` is allowed.

#### Wayland

New Wayland compositors let a program change the clipboard when the terminal does not have the focus. Old compositors can need the focus until the copy message shows. `zypi` shows a warning at start when this applies.

`GROK_CLIPBOARD_NO_DATA_CONTROL=1` turns off the data-control route. Copies then use command-line clipboard tools. Use it only as a fallback.

#### OSC 52

On Linux, and across tmux, SSH, or containers that have no display, `zypi` sends OSC 52 when that route is on. A terminal that does not know OSC 52 can show the encoded text. To turn this route off, set `GROK_CLIPBOARD_NO_OSC52=1` before you start `zypi`. `zypi doctor` then shows `osc 52 off`. The native and tmux routes do not change.

#### Linux X11 selections

X11 has two selections: PRIMARY and CLIPBOARD.

- A middle click without a modifier key reads PRIMARY only when `DISPLAY` is set. Under XWayland, `xclip` or `xsel` must be on `PATH`.
- `Ctrl+V` reads CLIPBOARD. It does not read PRIMARY.
- `Shift+Insert` pastes the text that the terminal selected.

#### SSH and selected text

A `zypi` process on a remote computer cannot read the selection of your local terminal. Use `Shift+Insert` of the terminal. Or hold `Shift` while you click the middle button, if your terminal uses this gesture to bypass mouse reporting.

#### Apple Terminal over SSH

Apple Terminal does not support OSC 52. A copy on a remote computer cannot reach your local clipboard. The backup file still holds the text.

To forward the clipboard, start the SSH connection from your local computer through `zypi wrap`:

```
zypi wrap ssh user@host
```

You can wrap the shell of a container or a pod in the same way:

```
zypi wrap docker exec -it my-container bash
```

`zypi wrap` also restores the terminal modes after a connection drops. For repeated SSH use, run `zypi doctor fix ssh-wrap`. It shows the command, the file that it changes, and the cases where you must bypass the alias.

> **Warning**: `zypi wrap` is experimental. It can fail in some setups.

#### iTerm2

iTerm2 can ask for permission before it lets a program use the clipboard through OSC 52. `zypi doctor` shows the setting to check in the `terminal.iterm2-clipboard-permission` recommendation.

### The full screen does not start

Zellij and tmux control mode limit the alternate screen. In these cases `zypi` runs inline. To see which condition `zypi` found, run `zypi doctor`. To test inline mode, run `zypi --no-alt-screen`. To set the policy, use `[terminal] alt_screen` in `~/.pi-python/pager.toml`. See [Theming and Appearance](06-theming.md).

### Zellij keys interfere with `zypi`

Zellij can take Ctrl and Alt keys before they reach `zypi`. On Zellij 0.41 or later, use the **Unlock-First (non-colliding)** preset:

1. Press `Ctrl+o`, then `c`.
2. Open **Change Mode Behavior**.
3. Select **Unlock-First (non-colliding)**.
4. Press `Enter`.

When you need the pane or session controls of Zellij, press `Ctrl+g`.

### Some key chords do not arrive in WezTerm

WezTerm ships with the Kitty keyboard protocol off. Then `zypi` cannot tell some chords apart, for example `Ctrl+Enter` and `Enter`. Run `zypi doctor`. The `terminal.wezterm-kitty` finding shows the setting and the restart step. Over SSH, `zypi doctor` shows only the workaround that can work in the current session.

### Shift+Enter does not insert a new line in VS Code

The terminals of VS Code, Cursor, Windsurf, and Zed use xterm.js. xterm.js implements the Kitty keyboard protocol only in part, so `zypi` does not use the protocol there. `Shift+Enter` can then arrive as the same code as `Enter`. This also applies to VS Code over SSH when `TERM_PROGRAM` is not forwarded. Press `Alt+Enter` to insert a new line. `zypi doctor` reports `terminal.newline-fallback` with the reason and the workaround.

### Mouse scrolling does not work

If `zypi` does not get mouse input, turn on mouse reporting in the terminal:

- **Apple Terminal**: **View → Allow Mouse Reporting** (`Cmd+R`).
- **iTerm2**: **Settings → Profiles → Terminal → Enable mouse reporting**.

### Byobu with GNU screen

Byobu on GNU screen has limited support. `zypi doctor` reports `terminal.byobu-screen` and tells you how to change to the tmux backend of Byobu.
