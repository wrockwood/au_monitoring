from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lockss_config as config
import lockss_report as report


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = self.root / ".env"
        self.write_env("")

    def write_env(self, text):
        self.env.write_text(text)
        self.env.chmod(0o600)

    def test_quotes_and_password_punctuation_are_literal(self):
        password = 'example # = $HOME ${VAR} $(echo nope) " \\ \' '
        for encoded in (json.dumps(password), "'" + password + "'"):
            self.write_env("LOCKSS_UI_USERNAME=example-user\nLOCKSS_UI_PASSWORD=" + encoded + "\n")
            values = config.read_env(self.env)
            self.assertEqual(report.configured_credentials(values), ("example-user", password))
        self.write_env("LOCKSS_UI_PASSWORD=word # is literal\n")
        self.assertEqual(config.read_env(self.env)["LOCKSS_UI_PASSWORD"], "word # is literal")

    def test_bad_settings_rejected_without_echoing_secret(self):
        for text in ("PASSWORD=secret-marker", "LOCKSS_UI_PASSWORD=a\nLOCKSS_UI_PASSWORD=secret-marker",
                     'LOCKSS_UI_PASSWORD="secret-marker', "LOCKSS_UI_PASSWORD='secret-marker",
                     'LOCKSS_UI_PASSWORD="secret-marker\\n"'):
            self.write_env(text)
            with self.assertRaises(config.ReportError) as caught:
                config.read_env(self.env)
            self.assertNotIn("secret-marker", str(caught.exception))
        self.env.chmod(0o644)
        with self.assertRaises(config.ReportError):
            config.read_env(self.env)

    def test_process_environment_overrides_file_without_modifying_environment(self):
        self.write_env("LOCKSS_UI_USERNAME=file-user\nLOCKSS_UI_PASSWORD=file-password\n")
        with patch.dict("os.environ", {"LOCKSS_UI_USERNAME": "env-user"}, clear=True):
            values = config.load_settings(self.env)
            self.assertEqual(report.configured_credentials(values), ("env-user", "file-password"))
            import os
            self.assertNotIn("LOCKSS_UI_PASSWORD", os.environ)

    def test_explicit_missing_env_and_partial_credentials_fail_closed(self):
        with self.assertRaises(config.ReportError):
            config.load_settings(self.root / "missing")
        with self.assertRaises(config.ReportError):
            report.configured_credentials({"LOCKSS_UI_USERNAME": "example"})

    def test_report_cli_uses_env_and_cli_overrides(self):
        self.write_env("LOCKSS_SSH_HOST=root@node.example.org\nLOCKSS_UI_USERNAME=example-user\n"
                       "LOCKSS_UI_PASSWORD=example-password\nLOCKSS_REMOTE_PORT=24621\n"
                       "LOCKSS_HTTP_TIMEOUT=120\nLOCKSS_SSH_KEY_PATH=keys/example key\n")
        result = self.root / "status-UTC.csv"
        @contextmanager
        def tunnel(host, port, key_path):
            self.assertEqual((host, port), ("root@override.example.org", 25000))
            self.assertEqual(key_path, expected_key)
            yield "http://127.0.0.1:1234"
        argv = ["report", "--env-file", str(self.env), "--host", "root@override.example.org",
                "--remote-port", "25000", "--timeout", "30"]
        cases = [
            ({}, [], config.ROOT / "keys/example key"),
            ({"LOCKSS_SSH_KEY_PATH": "~/.ssh/example"}, [], Path.home() / ".ssh/example"),
            ({"LOCKSS_SSH_KEY_PATH": ""}, [], None),
            ({}, ["--ssh-key", "cli key"], Path("cli key")),
        ]
        for environment, extra_args, expected_key in cases:
            with self.subTest(extra_args=extra_args, environment=environment), \
                 patch.dict("os.environ", environment, clear=True), patch("sys.argv", argv + extra_args), \
                 patch.object(report, "ssh_tunnel", tunnel), \
                 patch.object(report, "create_report", return_value=(result, 1, 3)) as create, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(report.main(), 0)
                self.assertEqual(create.call_args.kwargs["credentials"], ("example-user", "example-password"))
                self.assertEqual(create.call_args.kwargs["timeout"], 30)

    def test_relative_paths_are_based_on_project_not_working_directory(self):
        with patch.object(config, "ROOT", self.root):
            self.assertEqual(config.configured_path({"LOCKSS_REPORTS_DIR": "private/reports"},
                                                   "LOCKSS_REPORTS_DIR", "unused"),
                             self.root / "private/reports")
