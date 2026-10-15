# Claude usage status line

![Claude Code status line in a live session](docs/statusline.png)

Model, estimated prompt-cache countdown, context tokens, session cost, and 5-hour / 7-day usage percentages in one line.

## Install with herdr

Requires **macOS or Linux**, **herdr 0.7.5+**, **Claude Code**, and **jq**. Install jq with `brew install jq` on macOS or your Linux package manager (for example, `sudo apt install jq` on Ubuntu/Debian).

1. Install the plugin:
   ```sh
   herdr plugin install akhilnalliboina/claude-usage-statusline
   ```
2. Run setup from a running herdr session:
   ```sh
   herdr plugin action invoke akhilnalliboina.claude-usage-statusline.setup
   ```
3. Start or restart Claude Code.

Setup backs up existing files and changes only Claude Code's `statusLine` setting. Installing the plugin alone does not change settings. By default it renders inside Claude Code, including Claude Code running in herdr. The optional `--herdr-bar` command exposes cached usage for Herdr's native tab bar. To update, reinstall the plugin and rerun setup.

**Upgrading from 0.2.x:** earlier versions installed to `~/.claude-personal/`. Rerunning setup points `statusLine` at the new location and leaves the old directory untouched, so you can delete it. Cost estimates start from a fresh baseline.

## Install without herdr

```sh
git clone https://github.com/akhilnalliboina/claude-usage-statusline.git
cd claude-usage-statusline
bash scripts/setup.sh
```

Start or restart Claude Code after setup. For a custom profile, run this from the checkout instead:

```sh
CLAUDE_CONFIG_DIR="$HOME/.claude-work" bash scripts/setup.sh
```

The herdr action reads `CLAUDE_CONFIG_DIR` from the herdr server's environment, not necessarily from the shell invoking the action.

## What you see

```text
[Sonnet] · cache 58:00 · ctx 42K · ~$0.12/turn · session $1.42 · 5h 23% · 7d 41%
```

| Field | Meaning |
| --- | --- |
| `[Sonnet]` | Model display name. |
| `cache 58:00` | Estimated time left in a one-hour cache window, based on transcript modification time. Yellow in the last five minutes; red as `cache cold` after expiry. |
| `ctx 42K` | Input + cache-creation + cache-read tokens. `K` is rounded down to thousands. |
| `~$0.12/turn` | Last observed increase in session cost, not exact per-turn billing. Appears after an increase from the first observed cost. |
| `session $1.42` | Total session cost reported by Claude Code. |
| `5h 23%` / `7d 41%` | Percentage consumed in each usage window, rounded to a whole percentage. |

Missing fields are omitted. Usage requires Claude Code to supply `rate_limits` data. Zero-cost sessions omit cost fields. The cache countdown is a heuristic; transcript updates do not prove that the cache is warm. Usage is shown as percentages, not bars.

The first cost observation establishes a baseline. Repeated refreshes keep showing only the session total until the cost increases.

The script uses [Claude Code's status-line JSON input](https://code.claude.com/docs/en/statusline). It is not a Claude Code `/plugin install` package: [plugin settings](https://code.claude.com/docs/en/plugins-reference#settings) do not support the main `statusLine` property. The renderer and setup support GNU `stat` on Linux and BSD `stat` on macOS.

## Optional Herdr tab-bar usage

The renderer saves the latest valid usage snapshot for its Claude profile. Add a
command entry to your existing `[ui] tab_bar_right` list in Herdr's `config.toml`:

```toml
{ type = "command", command = "bash \"$HOME/.claude/statusline/statusline.sh\" --herdr-bar", interval_seconds = 30, timeout_seconds = 5 },
```

For a personal profile, set the profile explicitly in the command:

```toml
{ type = "command", command = "CLAUDE_CONFIG_DIR=\"$HOME/.claude-personal\" bash \"$HOME/.claude-personal/statusline/statusline.sh\" --herdr-bar", interval_seconds = 30, timeout_seconds = 5 },
```

Rerun setup and restart Claude Code after upgrading, then run
`herdr server reload-config`. After Claude supplies usage data, the entry shows:

```text
CC 5h 23% · 7d 41%
```

This is the latest reported snapshot, not a live API query or an aggregation of
sessions. Multiple sessions in one profile share the cache; the last valid payload
wins. Missing windows are omitted. Missing or invalid usage does not refresh the
cache. Readings older than 30 minutes have a `(stale)` suffix. An absent or damaged
cache produces no bar entry. Refreshes from an idle Claude session can repeat the
same reading; the timestamp records when the payload was received, not when the
vendor measured usage. No network requests or credential reads are added.

## Cache TTL

The countdown assumes a one-hour prompt cache. If yours is shorter, set `STATUSLINE_CACHE_TTL` (seconds) in the environment Claude Code runs in, for example `STATUSLINE_CACHE_TTL=300`. Invalid values fall back to 3600.

## Safety and local files

- Usage state lives in `statusline/state/herdr-usage.json` under the Claude profile. It stores only the receive timestamp and numeric usage percentages, not the full input, session IDs, or credentials. Writes use private files and atomic rename. Cache failures do not interrupt status-line rendering. The bar command ignores cache symlinks.
- The renderer makes no network requests and reads neither credentials nor transcript contents. It reads trusted Claude Code JSON and the transcript's modification time.
- Setup installs `~/.claude/statusline/statusline.sh` and merges `statusLine` into `~/.claude/settings.json`. With `CLAUDE_CONFIG_DIR` set, both live under that directory instead. Other settings are preserved.
- Setup rejects invalid or non-object JSON, symlinks, and non-file targets. An identical script and setting make setup a no-op.
- Setup prints a backup path containing the original files, `.absent` markers for missing files, and `locations.txt`. Herdr backups use its plugin state directory; direct setup uses `~/.claude/statusline/state/setup-backups/` unless `HERDR_PLUGIN_STATE_DIR` is set.
- Backups can contain private settings or credentials. They stay local. Keep them private and delete them when no longer needed.
- Cost state lives in `~/.claude/statusline/state/statusline-cost-<session_id>` (under `$CLAUDE_CONFIG_DIR` when set). Each file contains total cost and the last increase. The renderer deletes `statusline-cost-*` files older than 7 days when a new session starts. State from older versions is read as is; an old incorrect whole-session estimate is hidden until the next cost increase.
- Close other settings editors before setup. Each file replacement is atomic, but the pair is not one transaction. Backups cover partial failures.

To inspect the herdr setup result and backup path:

```sh
herdr plugin log list --plugin akhilnalliboina.claude-usage-statusline
```

## Remove or restore

1. Use the setup backup to restore the previous `statusLine` property, or remove it if there was none. Do not overwrite unrelated settings changed since setup.
2. Restore the previous script if backed up. If `statusline.sh.absent` exists, delete the installed script. You can remove this script's cost state files; leave other files in the `statusline` directory intact.
3. If installed through herdr, run:
   ```sh
   herdr plugin uninstall akhilnalliboina.claude-usage-statusline
   ```

Uninstalling does not restore settings or remove the copied script. Herdr retains plugin state; delete old backups yourself when no longer needed.

## Development

```sh
bash -n statusline.sh scripts/setup.sh
python3 -B -m unittest discover -s tests -v
```

Tests run on macOS and Linux with isolated home directories. Renderer tests cover missing fields, usage rounding, cache colors and expiry, context formatting, cost tracking, and portable `stat` handling. GitHub Actions runs both setup and renderer tests on macOS and Ubuntu, with no expected failures.

For local plugin testing, use `herdr plugin link "$PWD"` and `herdr plugin action list --plugin akhilnalliboina.claude-usage-statusline`. Unlink it before installing the GitHub version. Plugin setup has also been tested in an isolated herdr 0.9.3 server.

## License

[MIT](LICENSE).
