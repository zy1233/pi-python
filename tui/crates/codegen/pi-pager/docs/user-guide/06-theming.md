# Theming and Appearance

`zypi` takes all its colors from a theme. You can change the theme while `zypi` runs. You can also let `zypi` follow the light or dark mode of your operating system.

## Themes

| Theme | Name in settings | Description | Needs truecolor |
| --- | --- | --- | --- |
| zypi Night | `zypinight` | Neutral dark theme with a magenta accent. This is the default. | No |
| zypi Day | `zypiday` | Light theme for bright terminals. | No |
| Tokyo Night | `tokyonight` | Dark theme with blue backgrounds. | Yes |
| Rose Pine Moon | `rosepine-moon` | Muted dark theme with mauve accents. | Yes |
| Oscura Midnight | `oscura-midnight` | Deep dark theme. | Yes |
| Auto | `auto` | Follows the light or dark mode of your system. | No |

The names are not case-sensitive. `zypi` also accepts the older names `groknight`, `grok-night`, `grokday`, and `grok-day`.

If your terminal does not support truecolor, `zypi` uses `zypinight` instead of a theme that needs truecolor. Run `zypi doctor` to see the color level that `zypi` detects.

If the `NO_COLOR` environment variable is set, `zypi` shows no colors.

## Change the theme

In `zypi`, run `/theme` (alias `/t`). A list of themes opens. When you move through the list, `zypi` shows each theme. Press `Enter` to choose a theme. Press `Esc` to go back to the old theme.

To choose a theme by name, type the name after the command:

```
/theme zypiday
```

You can also use the settings. Press `F2` or run `/settings`.

To set the theme in a file, write it in `~/.pi-python/config.toml`:

```toml
[ui]
theme = "tokyonight"
```

## Follow the system appearance

Set the theme to `auto`:

```toml
[ui]
theme = "auto"
```

In `auto` mode, a dark system uses `zypinight` and a light system uses `zypiday`. To use other themes, set `auto_dark_theme` and `auto_light_theme`:

```toml
[ui]
theme = "auto"
auto_dark_theme = "tokyonight"
auto_light_theme = "zypiday"
```

On an SSH connection, in tmux, and in other places without a desktop, `zypi` cannot ask the system. In these cases `zypi` reads these environment variables:

- `GROK_APPEARANCE` or `LC_GROK_APPEARANCE`: `dark` or `light`.
- `COLORFGBG`: the color pair that many terminals set.

`zypi wrap ssh user@host` copies the appearance of your local system to the remote shell. See [Terminal Support and Troubleshooting](21-terminal-support.md).

To force a theme for one run, set `GROK_THEME` (or `LC_GROK_THEME`) to a theme name or to `auto`.

## Minimal mode

Minimal mode (`zypi --minimal`) uses the colors of your terminal. It ignores the theme. `/theme` and the theme settings are not available in this mode.

To make minimal mode the default, write this in `~/.pi-python/config.toml`:

```toml
[ui]
screen_mode = "minimal"
```

## Alternate screen

By default, `zypi` uses the full screen of the terminal (the alternate screen) where this works. It runs inline in tmux control mode and in Zellij. To change this, set the policy in `~/.pi-python/pager.toml`:

```toml
[terminal]
alt_screen = "auto"    # "auto", "always", or "never"
```

To start one run inline, use `zypi --no-alt-screen`.

## Right-to-left text

Many terminals reorder right-to-left text (Arabic, Persian) themselves. `zypi` does not reorder it by default. If such text reads backwards in the scrollback, set this in `pager.toml`:

```toml
[scrollback.display]
rtl_bidi = true
```

If the text was correct before, set the value back to `false`. Your terminal does the reordering.
