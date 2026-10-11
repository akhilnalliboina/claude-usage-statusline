"""Renderer regression tests through JSON stdin and ANSI stdout."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "statusline.sh"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
NOW = 1800000000


@unittest.skipUnless(os.uname().sysname in ("Darwin", "Linux"), "Requires macOS or Linux")
class RendererTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="renderer test ")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.env = dict(os.environ, HOME=str(self.home), LC_ALL="C")
        self.env.pop("BASH_ENV", None)
        # Freeze only the external clock. Bash, jq, stat, and file I/O stay real.
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        clock = bin_dir / "date"
        clock.write_text("#!/bin/sh\n[ \"$1\" = '+%s' ] || exit 2\nprintf '%s\\n' 1800000000\n")
        clock.chmod(0o700)
        self.env["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", os.defpath)

    def fake_command(self, name, script):
        path = self.home / "bin" / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(0o700)

    def render(self, payload):
        result = subprocess.run(
            ["/bin/bash", str(SCRIPT)], input=json.dumps(payload),
            env=self.env, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout.count("\n"), 1)
        return result.stdout

    def text(self, payload):
        return ANSI.sub("", self.render(payload)).rstrip("\n")

    def transcript(self, age):
        path = self.home / "transcript with spaces.jsonl"
        path.write_text("")
        os.utime(path, (NOW - age, NOW - age))
        return str(path)

    def cost(self, total, session="sample"):
        return {"session_id": session, "cost": {"total_cost_usd": total}}

    def test_missing_fields_render_an_empty_line(self):
        self.assertEqual(self.render({}), "\n")

    def test_null_fields_are_omitted(self):
        self.assertEqual(self.render({"model": None, "rate_limits": None,
                                      "context_window": None, "cost": None}), "\n")

    def test_model_and_usage_percentages(self):
        self.assertEqual(self.text({"model": {"display_name": "Sonnet"}, "rate_limits": {
            "five_hour": {"used_percentage": 23}, "seven_day": {"used_percentage": 41},
        }}), "[Sonnet] · 5h 23% · 7d 41%")

    def test_zero_usage_is_not_omitted(self):
        self.assertEqual(self.text({"rate_limits": {
            "five_hour": {"used_percentage": 0}, "seven_day": {"used_percentage": 0},
        }}), "5h 0% · 7d 0%")

    def test_usage_rounds_to_whole_percentages(self):
        self.assertEqual(self.text({"rate_limits": {
            "five_hour": {"used_percentage": 22.6}, "seven_day": {"used_percentage": 40.4},
        }}), "5h 23% · 7d 40%")

    def test_context_includes_cached_tokens(self):
        self.assertEqual(self.text({"context_window": {"current_usage": {
            "input_tokens": 2000, "cache_creation_input_tokens": 10000,
            "cache_read_input_tokens": 30000,
        }}}), "ctx 42K")

    def test_context_formats_and_truncates_thousands(self):
        for tokens, expected in ((0, ""), (999, "ctx 999"), (1000, "ctx 1K"), (1999, "ctx 1K")):
            with self.subTest(tokens=tokens):
                self.assertEqual(self.text({"context_window": {"current_usage": {
                    "input_tokens": tokens, "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }}}), expected)

    def test_warm_cache_is_dim_and_counts_down(self):
        self.assertEqual(self.render({"transcript_path": self.transcript(120)}),
                         "\x1b[2mcache 58:00\x1b[0m\n")

    def test_gnu_stat_mtime_is_supported(self):
        # Model GNU stat's interface, including non-numeric stdout on the
        # unsupported BSD invocation. Native GNU stat is also exercised in CI.
        self.fake_command("stat", "if [ \"$1\" = '-c' ] && [ \"$2\" = '%Y' ]; then\n"
                                  "  printf '%s\\n' 1799999880\n"
                                  "else\n  printf 'File: filesystem details\\n'\n  exit 1\nfi\n")
        self.assertEqual(self.render({"transcript_path": self.transcript(120)}),
                         "\x1b[2mcache 58:00\x1b[0m\n")

    def test_failed_gnu_stat_stdout_does_not_contaminate_bsd_result(self):
        self.fake_command("stat", "if [ \"$1\" = '-f' ] && [ \"$2\" = '%m' ]; then\n"
                                  "  printf '%s\\n' 1799999880\n"
                                  "else\n  printf 'not a timestamp\\n'\n  exit 1\nfi\n")
        self.assertEqual(self.render({"transcript_path": self.transcript(120)}),
                         "\x1b[2mcache 58:00\x1b[0m\n")

    def test_unreadable_stat_omits_cache_without_losing_other_fields(self):
        self.fake_command("stat", "printf 'not a timestamp\\n'\nexit 1\n")
        self.assertEqual(self.text({"model": {"display_name": "Sonnet"},
                                    "transcript_path": self.transcript(120)}), "[Sonnet]")

    def test_cache_warning_starts_at_five_minutes(self):
        for age, expected in ((3299, "\x1b[2mcache 5:01\x1b[0m\n"),
                              (3300, "\x1b[33mcache 5:00\x1b[0m\n"),
                              (3599, "\x1b[33mcache 0:01\x1b[0m\n")):
            with self.subTest(age=age):
                self.assertEqual(self.render({"transcript_path": self.transcript(age)}), expected)

    def test_cache_is_red_at_and_after_expiry(self):
        for age in (3600, 4000):
            with self.subTest(age=age):
                self.assertEqual(self.render({"transcript_path": self.transcript(age)}),
                                 "\x1b[31mcache cold\x1b[0m\n")

    def test_missing_transcript_file_omits_cache(self):
        self.assertEqual(self.text({"model": {"display_name": "Sonnet"},
                                    "transcript_path": str(self.home / "missing.jsonl")}), "[Sonnet]")

    def test_first_cost_observation_shows_only_session_total(self):
        self.assertEqual(self.text(self.cost(1.30)), "session $1.30")

    def test_cost_increase_is_remembered_across_renderer_processes(self):
        self.text(self.cost(1.30))
        self.assertEqual(self.text(self.cost(1.42)), "~$0.12/turn · session $1.42")
        self.assertEqual(self.text(self.cost(1.42)), "~$0.12/turn · session $1.42")
        self.assertEqual(self.text(self.cost(1.47)), "~$0.05/turn · session $1.47")

    def test_unchanged_baseline_does_not_show_per_turn_cost(self):
        self.text(self.cost(1.30))
        self.assertEqual(self.text(self.cost(1.30)), "session $1.30")

    def test_empty_or_damaged_state_is_treated_as_a_new_baseline(self):
        # An interrupted write can leave the state file empty or partial.
        for content in ("", "\n", "garbage x\n"):
            with self.subTest(content=content):
                state = self.home / ".claude-personal/state/statusline-cost-damaged"
                state.parent.mkdir(parents=True, exist_ok=True)
                state.write_text(content)
                self.assertEqual(self.text(self.cost(1.30, "damaged")), "session $1.30")
                self.assertEqual(self.text(self.cost(1.30, "damaged")), "session $1.30")
                self.assertEqual(self.text(self.cost(1.42, "damaged")), "~$0.12/turn · session $1.42")

    def test_zero_saved_total_is_treated_as_a_new_baseline(self):
        state = self.home / ".claude-personal/state/statusline-cost-zero"
        state.parent.mkdir(parents=True)
        state.write_text("0 0\n")
        self.assertEqual(self.text(self.cost(1.30, "zero")), "session $1.30")
        self.assertEqual(self.text(self.cost(1.30, "zero")), "session $1.30")

    def test_state_from_the_initial_cost_bug_hides_the_turn_estimate(self):
        # Older versions saved the first total as the last increase, e.g. "1.30 1.3000".
        state = self.home / ".claude-personal/state/statusline-cost-old"
        state.parent.mkdir(parents=True)
        state.write_text("1.30 1.3000\n")
        self.assertEqual(self.text(self.cost(1.30, "old")), "session $1.30")
        self.assertEqual(self.text(self.cost(1.42, "old")), "~$0.12/turn · session $1.42")

    def test_cost_state_write_leaves_no_temporary_files(self):
        self.text(self.cost(1.30))
        self.text(self.cost(1.42))
        state_dir = self.home / ".claude-personal/state"
        self.assertEqual(sorted(p.name for p in state_dir.iterdir()), ["statusline-cost-sample"])

    def test_sessions_do_not_share_cost_estimates(self):
        self.text(self.cost(1.30, "one"))
        self.text(self.cost(1.42, "one"))
        self.assertEqual(self.text(self.cost(3.00, "two")), "session $3.00")
        self.assertEqual(self.text(self.cost(1.42, "one")), "~$0.12/turn · session $1.42")

    def test_decimal_comma_locale_does_not_change_numbers(self):
        locales = subprocess.run(["locale", "-a"], text=True, capture_output=True).stdout.split()
        locale = next((name for name in ("de_DE.UTF-8", "de_DE.utf8", "fr_FR.UTF-8", "fr_FR.utf8")
                       if name in locales), None)
        if locale is None:
            self.skipTest("No decimal-comma locale is installed")
        self.env["LC_ALL"] = locale
        payload = dict(self.cost(1.30), rate_limits={"five_hour": {"used_percentage": 22.6}})
        self.assertEqual(self.text(payload), "session $1.30 · 5h 23%")
        payload["cost"]["total_cost_usd"] = 1.42
        self.assertEqual(self.text(payload), "~$0.12/turn · session $1.42 · 5h 23%")
        state = self.home / ".claude-personal/state/statusline-cost-sample"
        self.assertEqual(state.read_text(), "1.42 0.1200\n")

    def test_zero_cost_is_omitted(self):
        self.assertEqual(self.render(self.cost(0)), "\n")

    def test_cost_without_session_id_is_omitted(self):
        self.assertEqual(self.render({"cost": {"total_cost_usd": 1.30}}), "\n")

    def test_full_line_has_stable_field_order_and_separators(self):
        payload = dict(self.cost(1.30), model={"display_name": "Sonnet"},
                       transcript_path=self.transcript(120),
                       context_window={"current_usage": {"input_tokens": 2000,
                           "cache_creation_input_tokens": 10000, "cache_read_input_tokens": 30000}},
                       rate_limits={"five_hour": {"used_percentage": 23},
                                    "seven_day": {"used_percentage": 41}})
        self.text(payload)
        payload["cost"]["total_cost_usd"] = 1.42
        self.assertEqual(self.text(payload),
                         "[Sonnet] · cache 58:00 · ctx 42K · ~$0.12/turn · session $1.42 · 5h 23% · 7d 41%")


if __name__ == "__main__":
    unittest.main()
