"""Run on macOS or Linux: python3 -m unittest discover -s tests -v."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMMAND = 'bash "$HOME/.claude/statusline/statusline.sh"'
STATUS_LINE = {"type": "command", "command": COMMAND, "refreshInterval": 5}


@unittest.skipUnless(os.uname().sysname in ("Darwin", "Linux"), "Requires macOS or Linux")
class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="statusline test ")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.env = dict(os.environ, HOME=str(self.home))
        self.env.pop("CLAUDE_CONFIG_DIR", None)
        self.env["HERDR_PLUGIN_STATE_DIR"] = str(self.home / "plugin state")
        self.settings = self.home / ".claude/settings.json"
        self.target = self.home / ".claude/statusline/statusline.sh"

    def setup_action(self, success=True):
        result = subprocess.run(
            ["/bin/bash", str(ROOT / "scripts/setup.sh")], env=self.env,
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def fake_command(self, bin_dir, name, script):
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(0o700)

    def backups(self):
        directory = Path(self.env["HERDR_PLUGIN_STATE_DIR"]) / "setup-backups"
        return list(directory.iterdir()) if directory.exists() else []

    def write_settings(self, text):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(text)

    def test_first_install_and_render(self):
        result = self.setup_action()
        self.assertIn("Backup:", result.stdout)
        self.assertEqual(json.loads(self.settings.read_text()), {"statusLine": STATUS_LINE})
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.target.read_bytes(), (ROOT / "statusline.sh").read_bytes())
        backup, = self.backups()
        self.assertTrue((backup / "settings.json.absent").is_file())
        self.assertTrue((backup / "statusline.sh.absent").is_file())
        payload = {"model": {"display_name": "Sonnet"}, "rate_limits": {
            "five_hour": {"used_percentage": 23}, "seven_day": {"used_percentage": 41},
        }}
        result = subprocess.run(COMMAND, shell=True, input=json.dumps(payload),
                                env=self.env, text=True, capture_output=True, check=True)
        self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", result.stdout).strip(),
                         "[Sonnet] · 5h 23% · 7d 41%")

    def test_preserves_other_settings_and_backs_up_originals(self):
        original = '{\n  "permissions": {"allow": ["Read"]}, "statusLine": {"command": "old"}, "env": {"EXAMPLE": "value"}\n}\n'
        self.write_settings(original)
        self.settings.chmod(0o640)
        self.target.parent.mkdir(parents=True)
        self.target.write_text("old renderer\n")
        self.setup_action()
        current = json.loads(self.settings.read_text())
        previous = json.loads(original)
        previous["statusLine"] = STATUS_LINE
        self.assertEqual(current, previous)
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o640)
        backup, = self.backups()
        self.assertEqual((backup / "settings.json").read_text(), original)
        self.assertEqual((backup / "statusline.sh").read_text(), "old renderer\n")
        self.assertEqual((backup / "settings.json").stat().st_mode & 0o777, 0o600)

    def test_linux_setup_preserves_permissions_with_gnu_stat(self):
        self.write_settings('{}')
        self.settings.chmod(0o640)
        bin_dir = self.home / 'bin'
        bin_dir.mkdir()
        self.fake_command(bin_dir, 'uname', "printf 'Linux\\n'\n")
        self.fake_command(bin_dir, 'stat', "if [ \"$1\" = '-c' ] && [ \"$2\" = '%a' ]; then\n"
                                           "  printf '640\\n'\n"
                                           "else\n  printf 'File: filesystem details\\n'\n  exit 1\nfi\n")
        self.env['PATH'] = str(bin_dir) + os.pathsep + os.environ.get('PATH', os.defpath)
        self.setup_action()
        self.assertEqual(json.loads(self.settings.read_text())['statusLine'], STATUS_LINE)
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o640)

    def test_unreadable_permissions_fail_with_a_message(self):
        self.write_settings('{}')
        bin_dir = self.home / 'bin'
        bin_dir.mkdir()
        self.fake_command(bin_dir, 'stat', "printf 'unreadable\\n'\nexit 1\n")
        self.env['PATH'] = str(bin_dir) + os.pathsep + os.environ.get('PATH', os.defpath)
        result = self.setup_action(success=False)
        self.assertIn('Cannot read the permissions of', result.stderr)
        self.assertEqual(json.loads(self.settings.read_text()), {})

    def test_repeated_setup_is_noop(self):
        self.setup_action()
        before = self.settings.read_bytes(), self.settings.stat().st_mtime_ns, self.target.stat().st_mtime_ns
        self.assertIn("Already configured", self.setup_action().stdout)
        after = self.settings.read_bytes(), self.settings.stat().st_mtime_ns, self.target.stat().st_mtime_ns
        self.assertEqual(before, after)
        self.assertEqual(len(self.backups()), 1)

    def test_custom_config_directory_with_spaces(self):
        custom = self.home / "custom config"
        self.env["CLAUDE_CONFIG_DIR"] = str(custom)
        self.setup_action()
        command = f'bash "{custom}/statusline/statusline.sh"'
        self.assertEqual(json.loads((custom / "settings.json").read_text())["statusLine"],
                         dict(STATUS_LINE, command=command))
        self.assertTrue((custom / "statusline/statusline.sh").is_file())
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.target.exists())

    def test_rejects_install_paths_the_shell_would_expand(self):
        self.env["CLAUDE_CONFIG_DIR"] = str(self.home / "bad$dir")
        result = self.setup_action(success=False)
        self.assertIn("Unsupported character", result.stderr)
        self.assertFalse((self.home / "bad$dir").exists())

    def test_legacy_install_is_replaced_and_left_in_place(self):
        legacy = self.home / ".claude-personal/statusline.sh"
        legacy.parent.mkdir()
        legacy.write_text("legacy renderer\n")
        self.write_settings(json.dumps({"statusLine": {
            "type": "command", "command": 'bash "$HOME/.claude-personal/statusline.sh"',
            "refreshInterval": 5}}))
        self.setup_action()
        self.assertEqual(json.loads(self.settings.read_text())["statusLine"], STATUS_LINE)
        self.assertEqual(legacy.read_text(), "legacy renderer\n")

    def test_rejects_invalid_or_non_object_settings_without_changes(self):
        for invalid in ('{broken', '[]', 'null', '{}\n{}'):
            with self.subTest(invalid=invalid):
                self.write_settings(invalid)
                self.setup_action(success=False)
                self.assertEqual(self.settings.read_text(), invalid)
                self.assertFalse(self.target.exists())
                self.assertEqual(self.backups(), [])

    def test_rejects_settings_symlink(self):
        other = self.home / "other.json"
        other.write_text('{}')
        self.settings.parent.mkdir()
        self.settings.symlink_to(other)
        self.setup_action(success=False)
        self.assertEqual(other.read_text(), '{}')
        self.assertFalse(self.target.exists())

    def test_rejects_script_symlink(self):
        other = self.home / "other.sh"
        other.write_text('old renderer')
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(other)
        self.setup_action(success=False)
        self.assertEqual(other.read_text(), 'old renderer')
        self.assertFalse(self.settings.exists())

    def test_rejects_directory_in_place_of_settings(self):
        self.settings.mkdir(parents=True)
        self.setup_action(success=False)
        self.assertFalse(self.target.exists())

    def test_missing_jq_does_not_modify_settings(self):
        self.write_settings('{}')
        isolated_bin = self.home / 'bin'
        isolated_bin.mkdir()
        # Some macOS versions ship jq in /usr/bin. Expose only uname.
        (isolated_bin / 'uname').symlink_to('/usr/bin/uname')
        self.env['PATH'] = str(isolated_bin)
        result = self.setup_action(success=False)
        self.assertIn('Install jq', result.stderr)
        self.assertEqual(self.settings.read_text(), '{}')
        self.assertFalse(self.target.exists())

    def test_backup_fallback_without_herdr_environment(self):
        self.env.pop('HERDR_PLUGIN_STATE_DIR')
        self.setup_action()
        self.assertEqual(len(list((self.home / '.claude/statusline/state/setup-backups').iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
