#!/bin/bash
# Herdr setup action for the Claude Code status line.
set -euo pipefail
umask 077

fail() { printf 'Setup failed: %s\n' "$*" >&2; exit 1; }
case "$(uname -s)" in
  Darwin|Linux) ;;
  *) fail 'This status line requires macOS or Linux.' ;;
esac
command -v jq >/dev/null 2>&1 || fail 'Install jq first (brew install jq, or your Linux package manager).'

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_script="$root/statusline.sh"
config_dir=${CLAUDE_CONFIG_DIR:-"$HOME/.claude"}
settings="$config_dir/settings.json"
target_dir="$HOME/.claude-personal"
target="$target_dir/statusline.sh"
command_text='bash "$HOME/.claude-personal/statusline.sh"'

[ -f "$source_script" ] || fail 'The plugin is missing statusline.sh.'
# Do not replace symlinks, directories, or other special files.
for path in "$settings" "$target"; do
  [ ! -L "$path" ] || fail "Refusing to replace a symlink: $path"
  if [ -e "$path" ] && [ ! -f "$path" ]; then
    fail "Expected a regular file: $path"
  fi
done
if [ -f "$settings" ]; then
  jq -e -s 'length == 1 and (.[0] | type == "object")' "$settings" >/dev/null 2>&1 \
    || fail "Settings must contain one valid JSON object: $settings"
fi

same_settings=false
same_script=false
if [ -f "$settings" ] && jq -e --arg command "$command_text" \
  '.statusLine == {type: "command", command: $command, refreshInterval: 5}' "$settings" >/dev/null; then
  same_settings=true
fi
if [ -f "$target" ] && cmp -s "$source_script" "$target"; then
  same_script=true
fi
if [ "$same_settings" = true ] && [ "$same_script" = true ]; then
  printf 'Already configured: %s\n' "$settings"
  exit 0
fi

mkdir -p "$config_dir" "$target_dir"
backup_root="${HERDR_PLUGIN_STATE_DIR:-$target_dir/state}/setup-backups"
mkdir -p "$backup_root"
backup=$(mktemp -d "$backup_root/$(date +%Y%m%d-%H%M%S).XXXXXX")
# Snapshot both files, even when only one needs to change. Keep absence markers
# so a user can restore a first-time installation without guessing.
if [ -f "$settings" ]; then
  cp -p "$settings" "$backup/settings.json"
  chmod 600 "$backup/settings.json"
else
  touch "$backup/settings.json.absent"
fi
if [ -f "$target" ]; then
  cp -p "$target" "$backup/statusline.sh"
  chmod 600 "$backup/statusline.sh"
else
  touch "$backup/statusline.sh.absent"
fi
printf 'Settings: %s\nScript: %s\n' "$settings" "$target" > "$backup/locations.txt"

settings_tmp=''
script_tmp=''
cleanup() {
  [ -z "$settings_tmp" ] || rm -f "$settings_tmp"
  [ -z "$script_tmp" ] || rm -f "$script_tmp"
  return 0
}
trap cleanup EXIT

# Prepare replacements before modifying either installed file. Each rename is
# atomic, but the pair is not a transaction; the backup covers partial failures.
if [ "$same_settings" = false ]; then
  settings_tmp=$(mktemp "$config_dir/.statusline-settings.XXXXXX")
  if [ -f "$settings" ]; then
    jq --arg command "$command_text" \
      '.statusLine = {type: "command", command: $command, refreshInterval: 5}' \
      "$backup/settings.json" > "$settings_tmp"
    # GNU first, BSD second. Separate captures discard any failed stdout.
    mode=$(stat -c %a -- "$settings" 2>/dev/null) ||
      mode=$(stat -f %Lp -- "$settings" 2>/dev/null)
    chmod "$mode" "$settings_tmp"
  else
    jq -n --arg command "$command_text" \
      '{statusLine: {type: "command", command: $command, refreshInterval: 5}}' > "$settings_tmp"
  fi
fi
if [ "$same_script" = false ]; then
  script_tmp=$(mktemp "$target_dir/.statusline-script.XXXXXX")
  cp "$source_script" "$script_tmp"
  chmod 700 "$script_tmp"
fi
if [ -n "$script_tmp" ]; then
  mv -f "$script_tmp" "$target"
  script_tmp=''
fi
if [ -n "$settings_tmp" ]; then
  mv -f "$settings_tmp" "$settings"
  settings_tmp=''
fi
printf 'Configured: %s\nInstalled: %s\nBackup: %s\nStart or restart Claude Code to use the status line.\n' \
  "$settings" "$target" "$backup"
