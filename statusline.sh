#!/bin/bash
# Status line: model, prompt cache countdown, and 5h / 7d usage as plain percentages.
# Claude Code runs this on every refresh (settings.json: statusLine.refreshInterval) and
# sends the session data as JSON on stdin.
input=$(cat)

# Numbers from Claude Code and the saved state use a dot. A locale with a decimal comma
# (de_DE, fr_FR) would make printf reject them and print wrong values. LC_ALL wins over
# LC_NUMERIC, so it is the one to set.
export LC_ALL=C

CACHE_TTL=3600 # The prompt cache lives one hour after the last model call.
WARN_AT=300    # Show the countdown in yellow when this many seconds are left.

DIM='\033[2m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'

model=$(printf '%s' "$input" | jq -r '.model.display_name // empty')
transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty')
session_id=$(printf '%s' "$input" | jq -r '.session_id // empty')
cost=$(printf '%s' "$input" | jq -r '.cost.total_cost_usd // empty')
ctx_tokens=$(printf '%s' "$input" | jq -r '.context_window.current_usage | if . then (.input_tokens + .cache_creation_input_tokens + .cache_read_input_tokens) else empty end')
five=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
seven=$(printf '%s' "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')

parts=()
[ -n "$model" ] && parts+=("${DIM}[${model}]${RESET}")

# The transcript file changes on every model call, so its age is the age of the cache.
# Nothing to show before the first reply (no file yet).
# Capture each attempt separately, so failed stat output cannot enter arithmetic.
# GNU stat uses -c; BSD stat uses -f. Omit cache if neither can read the file.
if [ -n "$transcript" ] && [ -f "$transcript" ] &&
  { modified=$(stat -c %Y -- "$transcript" 2>/dev/null) ||
    modified=$(stat -f %m -- "$transcript" 2>/dev/null); }; then
  left=$((CACHE_TTL - ($(date +%s) - modified)))
  if [ "$left" -le 0 ]; then
    parts+=("${RED}cache cold${RESET}")
  else
    color=$DIM
    [ "$left" -le "$WARN_AT" ] && color=$YELLOW
    parts+=("$(printf '%bcache %d:%02d%b' "$color" $((left / 60)) $((left % 60)) "$RESET")")
  fi
fi

# The context size now: the tokens the next model call sends, including cached ones.
if [ -n "$ctx_tokens" ] && [ "$ctx_tokens" -gt 0 ]; then
  if [ "$ctx_tokens" -ge 1000 ]; then
    parts+=("$(printf '%bctx %dK%b' "$DIM" $((ctx_tokens / 1000)) "$RESET")")
  else
    parts+=("$(printf '%bctx %d%b' "$DIM" "$ctx_tokens" "$RESET")")
  fi
fi

# The input has the total cost only. The cost of the last turn is the last rise of that total.
# The last seen total and the last rise are kept in a small file for each session, because
# this script starts fresh on every refresh and cannot remember anything itself.
if [ -n "$cost" ] && [ -n "$session_id" ] && [ "$(awk -v c="$cost" 'BEGIN { print (c > 0) }')" = 1 ]; then
  state_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/state"
  state_file="$state_dir/statusline-cost-$session_id"
  number='^[0-9]+([.][0-9]+)?$'
  last_total=''; last_turn=''; save=false
  [ -f "$state_file" ] && read -r last_total last_turn < "$state_file"
  [[ $last_turn =~ $number ]] || last_turn=0
  if ! [[ $last_total =~ $number ]] || [ "$(awk -v l="$last_total" 'BEGIN { print (l > 0) }')" != 1 ]; then
    # Missing, empty, damaged, or zero state: establish a baseline, not an increase from
    # zero, so the first observed session total is not shown as one turn's cost.
    last_total=$cost; last_turn=0; save=true
  elif [ "$(awk -v c="$cost" -v l="$last_total" 'BEGIN { print (c > l) }')" = 1 ]; then
    last_turn=$(awk -v c="$cost" -v l="$last_total" 'BEGIN { printf "%.4f", c - l }')
    save=true
  fi
  if [ "$save" = true ]; then
    # Write a temporary file and rename it, so a concurrent refresh never reads a partial file.
    mkdir -p "$state_dir"
    printf '%s %s\n' "$cost" "$last_turn" > "$state_file.$$" && mv -f "$state_file.$$" "$state_file"
  fi
  # A saved turn equal to the saved total is the older initial-cost bug (a baseline stored as an
  # increase from zero). A real turn cannot equal a total that had a baseline, so hide it.
  if [ "$(awk -v t="$last_turn" -v l="$last_total" 'BEGIN { d = t - l; print (t > 0 && (d > 0.00005 || d < -0.00005)) }')" = 1 ]; then
    parts+=("$(printf '%b~$%.2f/turn%b' "$DIM" "$last_turn" "$RESET")")
  fi
  parts+=("$(printf '%bsession $%.2f%b' "$DIM" "$cost" "$RESET")")
fi

[ -n "$five" ] && parts+=("$(printf '%b5h %.0f%%%b' "$DIM" "$five" "$RESET")")
[ -n "$seven" ] && parts+=("$(printf '%b7d %.0f%%%b' "$DIM" "$seven" "$RESET")")

sep="${DIM} · ${RESET}"
out=""
for part in "${parts[@]}"; do
  out+="${out:+$sep}$part"
done

printf '%b\n' "$out"
