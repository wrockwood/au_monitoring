import csv
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

import lockss_report as report


HEADERS = ["AU", "Content Size", *report.DATE_COLUMNS, "Status"]
ROW = ['Synthetic AU, "one"\ncontinued', "1788974634653", "1788974634653", "-1", "", "OK"]


def raw_csv(row=None):
    output = io.StringIO(newline="")
    csv.writer(output).writerows([HEADERS, ROW if row is None else row])
    return output.getvalue()


@contextmanager
def server(mode="success"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, body=b"", **headers):
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/LoginForm":
                self.respond(200, b"<form></form>", Content_Type="text/html")
            elif self.headers.get("Cookie") == "session=authenticated":
                self.respond(200, raw_csv().encode(), Content_Type="text/csv")
            else:
                self.respond(302, Location="/LoginForm", Set_Cookie="session=pending; Path=/")

        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            valid = (self.path == "/j_security_check"
                     and self.headers.get("Cookie") == "session=pending"
                     and values == {"j_username": ["example-user"], "j_password": ["p&=+# word"]})
            if mode == "external":
                self.respond(307, Location="http://example.invalid/collect")
            elif valid and mode == "success":
                self.respond(302, Location=report.REPORT_PATH, Set_Cookie="session=authenticated; Path=/")
            else:
                self.respond(302, Location="/LoginForm")

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}"
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


class ReportTests(unittest.TestCase):
    def test_tunnel_passes_selected_key_as_one_argument_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "example key"
            key.write_text("synthetic test placeholder, not a key")
            for selected in (None, key):
                process = MagicMock()
                process.poll.return_value = None

                def start(command, **kwargs):
                    Path(command[command.index("-S") + 1]).touch()
                    return process

                with self.subTest(selected=selected), \
                     patch.object(report.subprocess, "Popen", side_effect=start) as popen, \
                     patch.object(report.subprocess, "run", return_value=MagicMock(returncode=0)):
                    with report.ssh_tunnel("user@node.example.org", 24621, selected):
                        command = popen.call_args.args[0]
                        if selected is None:
                            self.assertNotIn("-i", command)
                            self.assertNotIn("IdentitiesOnly=yes", command)
                        else:
                            self.assertEqual(command[command.index("-i") + 1], str(key))
                            self.assertIn("IdentitiesOnly=yes", command)
                        self.assertEqual(command[-1], "user@node.example.org")
                        self.assertIn("BatchMode=yes", command)
                        self.assertIn("StrictHostKeyChecking=yes", command)
                    process.terminate.assert_called_once()
                    process.wait.assert_called_once_with(timeout=5)

    def test_invalid_key_path_fails_before_starting_ssh(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(report.subprocess, "Popen") as popen:
            for key in (Path(temporary), Path(temporary) / "missing"):
                with self.subTest(key=key), self.assertRaises(report.ReportError):
                    with report.ssh_tunnel("user@node.example.org", 24621, key):
                        self.fail("Invalid key accepted")
            popen.assert_not_called()

    def test_conversion_preserves_non_dates_and_sentinels(self):
        output = io.StringIO(newline="")
        self.assertEqual(report.convert_csv(io.StringIO(raw_csv()), output), (1, 1))
        rows = list(csv.reader(io.StringIO(output.getvalue())))
        expected = ROW.copy()
        expected[2] = "2026-09-09T17:23:54.653Z"
        self.assertEqual(rows, [HEADERS, expected])

    def test_invalid_csv_rejected(self):
        for text in ("<html>login</html>", ",".join(HEADERS), raw_csv(ROW[:-1]),
                     raw_csv([*ROW[:2], "1788974634", *ROW[3:]]),
                     raw_csv([*ROW[:2], "not-a-date", *ROW[3:]]),
                     raw_csv().replace("Last Poll", "Last Crawl Start")):
            with self.subTest(text=text), self.assertRaises(report.ReportError):
                report.convert_csv(io.StringIO(text), io.StringIO())

    def test_form_login_cookie_and_archive(self):
        with server() as base, tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result, rows, count = report.create_report(
                output, base_url=base, credentials=("example-user", "p&=+# word"))
            self.assertEqual((rows, count), (1, 1))
            self.assertEqual((result.parent / "raw.csv").read_bytes(), raw_csv().encode())
            self.assertEqual(result.name, "status.csv")
            self.assertIn("2026-09-09T17:23:54.653Z", result.read_text())
            self.assertEqual(result.stat().st_mode & 0o777, 0o600)
            self.assertEqual(result.parent.stat().st_mode & 0o777, 0o700)

    def test_login_failure_and_external_redirect_publish_nothing(self):
        for mode in ("failure", "external"):
            with self.subTest(mode=mode), server(mode) as base, tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                with self.assertRaises(report.ReportError):
                    report.create_report(output, base_url=base,
                                         credentials=("example-user", "p&=+# word"))
                self.assertEqual(list(output.iterdir()), [])

    def test_offline_runs_are_distinct_and_bad_input_leaves_history_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input.csv"
            source.write_text(raw_csv())
            output = root / "reports"
            first, _, _ = report.create_report(output, source=source)
            second, _, _ = report.create_report(output, source=source)
            self.assertNotEqual(first, second)
            source.write_text("not,csv\n")
            with self.assertRaises(report.ReportError):
                report.create_report(output, source=source)
            self.assertEqual(set(output.iterdir()), {first.parent, second.parent})

    def test_incomplete_response_rejected(self):
        from email.message import Message

        response = io.BytesIO(b"short")
        response.headers = Message()
        response.headers["Content-Type"] = "text/csv"
        response.headers["Content-Length"] = "100"
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(report.ReportError):
            report.save_response(response, Path(temporary) / "raw.csv")


if __name__ == "__main__":
    unittest.main()
