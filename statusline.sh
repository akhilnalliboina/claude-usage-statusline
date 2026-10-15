#!/bin/bash
# Status line: model, prompt cache countdown, and 5h / 7d usage as plain percentages.
# Claude Code runs this on every refresh (settings.json: statusLine.refreshInterval) and
# sends the session data as JSON on stdin.

# --herdr-bar reads only the last numeric usage snapshot, never credentials or logs.
usage_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline/state"
usage_file="$usage_dir/herdr-usage.json"
if [ "${1:-}" = --herdr-bar ]; then
  if [ -f "$usage_file" ] && [ ! -L "$usage_file" ]; then
    jq -r --argjson now "$(date +%s)" '
      def percentage: type == "number" and . >= 0 and . <= 100;
      select(type == "object")
      | select(.observed_at | type == "number")
      | select(.observed_at > 0 and .observed_at <= $now)
      | . as $snapshot
      | [
          (if (.five_hour | percentage) then "5h \(.five_hour | round)%" else empty end),
          (if (.seven_day | percentage) then "7d \(.seven_day | round)%" else empty end)
        ]
      | select(length > 0)
      | "CC " + join(" · ") +
        (if $now - $snapshot.observed_at > 1800 then " (stale)" else "" end)
    ' "$usage_file" 2>/dev/null || true
  fi
  exit 0
fi

input=$(cat)

# Numbers from Claude Code and the saved state use a dot. A locale with a decimal comma
# (de_DE, fr_FR) would make printf reject them and print wrong values. LC_ALL wins over
# LC_NUMERIC, so it is the one to set.
export LC_ALL=C

# The prompt cache lives one hour after the last model call. Set STATUSLINE_CACHE_TTL (seconds)
# if yours is shorter. Anything that is not a positive whole number falls back to one hour.
CACHE_TTL=${STATUSLINE_CACHE_TTL:-3600}
[[ $CACHE_TTL =~ ^[1-9][0-9]{0,6}$ ]] || CACHE_TTL=3600
WARN_AT=300    # Show the countdown in yellow when this many seconds are left.

DIM='\033[2m'; YELLOW='\033[33m'; RED='\033[31m'; RESET='\033[0m'

# One jq call reads every field. The unit separator (0x1f) is not whitespace, so read keeps
# empty fields in place; a missing value becomes an empty string.
IFS=$'\x1f' read -r model transcript session_id cost ctx_tokens five seven < <(
  printf '%s' "$input" | jq -r '[
    (.model.display_name // ""),
    (.transcript_path // ""),
    (.session_id // ""),
    (.cost.total_cost_usd // ""),
    (.context_window.current_usage
      | if . then (.input_tokens + .cache_creation_input_tokens + .cache_read_input_tokens) else "" end),
    (.rate_limits.five_hour.used_percentage // ""),
    (.rate_limits.seven_day.used_percentage // "")
  ] | map(tostring) | join("\u001f")' 2>/dev/null
)

# Save only valid usage readings. Missing/invalid input must not make old data
# look fresh. A private temporary file and rename keep concurrent readers safe.
# Cache failures must never break Claude Code rendering.
(
  umask 077
  snapshot=$(printf '%s' "$input" | jq -ce --argjson now "$(date +%s)" '
    def percentage: type == "number" and . >= 0 and . <= 100;
    select(type == "object")
    | {observed_at: $now,
       five_hour: .rate_limits.five_hour.used_percentage,
       seven_day: .rate_limits.seven_day.used_percentage}
    | select((.five_hour | percentage) or (.seven_day | percentage))
    | if (.five_hour | percentage) then . else .five_hour = null end
    | if (.seven_day | percentage) then . else .seven_day = null end
  ') || exit 0
  [ ! -L "$usage_dir" ] && [ ! -L "$usage_file" ] || exit 0
  mkdir -p "$usage_dir" || exit 0
  temporary=$(mktemp "$usage_dir/.herdr-usage.XXXXXX") || exit 0
  trap 'rm -f "$temporary"' EXIT
  printf '%s\n' "$snapshot" > "$temporary" && mv -f "$temporary" "$usage_file"
) 2>/dev/null

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
    # A new session is the moment to drop state left by sessions that ended long ago.
    find "$state_dir" -maxdepth 1 -name 'statusline-cost-*' -mtime +7 -exec rm -f {} + 2>/dev/null
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
