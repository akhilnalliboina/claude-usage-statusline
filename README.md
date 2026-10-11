# Claude usage status line

A small Bash status-line mod for Claude Code. See the model, an estimated prompt-cache countdown, context tokens, session cost, and 5-hour / 7-day usage percentages in one line.

![Status-line examples](docs/statusline.png)

*Rendered examples from the script's output using sample session data, not a live Claude Code session.*

```text
[Sonnet] · cache 58:00 · ctx 42K · ~$0.12/turn · session $1.42 · 5h 23% · 7d 41%
```

This is the original personal script, published unchanged. It shows usage percentages, not usage bars, pace markers, or rate-limit reset countdowns.

## Requirements

- macOS. The script uses BSD `stat -f %m`; it is not Linux-compatible as written.
- Bash, `jq`, and the standard macOS command-line tools.
- Claude Code with [status-line JSON input](https://code.claude.com/docs/en/statusline). Usage percentages appear only when Claude Code supplies `rate_limits` data.

Install `jq` with Homebrew if needed:

```sh
brew install jq
```

## Install

1. Clone this repository and copy the script to its expected location:

   ```sh
   git clone https://github.com/akhilnalliboina/claude-usage-statusline.git
   mkdir -p "$HOME/.claude-personal"
   cp claude-usage-statusline/statusline.sh "$HOME/.claude-personal/statusline.sh"
   chmod +x "$HOME/.claude-personal/statusline.sh"
   ```

   If you already have a script at that path, back it up before copying.

2. Add the following `statusLine` property to `~/.claude/settings.json`. If you use a custom `CLAUDE_CONFIG_DIR`, edit its `settings.json` instead. Merge this with your existing settings; do not replace the whole file. Back up an existing `statusLine` property if you want to restore it later.

   ```json
   {
     "statusLine": {
       "type": "command",
       "command": "bash \"$HOME/.claude-personal/statusline.sh\"",
       "refreshInterval": 5
     }
   }
   ```

3. Start Claude Code. The command reads session JSON from stdin and writes one ANSI-colored line to stdout. The refresh interval lets the cache countdown update while idle.

This mod uses Claude Code's main `statusLine` setting. It is not a `/plugin install` package: [plugin default settings](https://code.claude.com/docs/en/plugins-reference#settings) do not support the main `statusLine` property.

## What the fields mean

| Field | Meaning |
| --- | --- |
| `[Sonnet]` | Model display name supplied by Claude Code. |
| `cache 58:00` | Estimated time left in a one-hour prompt-cache window, based on the transcript file's modification time. Turns yellow in the last five minutes and red as `cache cold` after expiry. |
| `ctx 42K` | Input tokens plus cache-creation and cache-read tokens from the current usage data. `K` is rounded down to thousands. |
| `~$0.12/turn` | Last observed increase in total session cost. An approximation, not an exact per-turn billing measurement. |
| `session $1.42` | Total session cost supplied by Claude Code. |
| `5h 23%` / `7d 41%` | Percentage consumed in the 5-hour and 7-day usage windows, rounded to a whole percentage. |

Missing fields are omitted. Zero-cost sessions do not show cost fields. The cache countdown is a heuristic: transcript updates do not prove that the prompt cache is warm. The first observed cost establishes a baseline; the per-turn estimate appears after a later increase.

## Privacy and local state

The script makes no network requests and does not read credentials or transcript contents. It reads JSON from Claude Code, checks the transcript file's modification time, and stores the last observed cost values in:

```text
~/.claude-personal/state/statusline-cost-<session_id>
```

State files contain two numbers: total cost and the last observed increase. They persist until you remove them. Treat stdin as trusted Claude Code session data; this unchanged script is not a general-purpose parser for untrusted input.

## Quick check

```sh
printf '%s\n' '{"model":{"display_name":"Sonnet"},"rate_limits":{"five_hour":{"used_percentage":23},"seven_day":{"used_percentage":41}}}' \
  | bash "$HOME/.claude-personal/statusline.sh"
```

Expected text, with ANSI styling:

```text
[Sonnet] · 5h 23% · 7d 41%
```

## Remove

Remove the `statusLine` property from your settings, or restore your previous status-line command. You can then delete the installed script and its `statusline-cost-*` state files. Leave other files in `~/.claude-personal` intact.
