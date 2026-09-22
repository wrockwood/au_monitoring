#!/usr/bin/env python3
"""Download LOCKSS AU status through an SSH tunnel and format ISO 8601 datetimes."""

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from http.client import HTTPException
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)
from lockss_config import ROOT, ReportError, configured_path, load_settings


DATE_COLUMNS = ("Last Poll", "Last Crawl Start", "Last Successful Crawl")
REPORT_PATH = "/DaemonStatus?table=ArchivalUnitStatusTable&output=csv"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_DOWNLOAD = 64 * 1024 * 1024


def configured_credentials(settings: dict) -> tuple[str, str]:
    if not settings.get("LOCKSS_UI_USERNAME") or not settings.get("LOCKSS_UI_PASSWORD"):
        raise ReportError("Set nonempty LOCKSS_UI_USERNAME and LOCKSS_UI_PASSWORD in .env.")
    return settings["LOCKSS_UI_USERNAME"], settings["LOCKSS_UI_PASSWORD"]


@contextmanager
def ssh_tunnel(host: str, remote_port: int, key_path: Path | None = None):
    """Own one SSH process and loopback listener, leaving existing tunnels alone."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", host):
        raise ReportError("Use an SSH hostname or user@hostname, without options or spaces.")
    if key_path is not None and (not key_path.is_file() or not os.access(key_path, os.R_OK)):
        raise ReportError("The SSH key path must point to a readable local private-key file.")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    # A short private path also fits macOS's Unix socket path limit.
    with tempfile.TemporaryDirectory(prefix="lockss-", dir="/tmp") as temporary:
        control = str(Path(temporary) / "ssh")
        command = [
            "ssh", "-T", "-N", "-M", "-S", control,
            "-o", "ControlPersist=no", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2",
        ]
        if key_path is not None:
            command.extend(["-i", str(key_path), "-o", "IdentitiesOnly=yes"])
        command.extend(["-L", f"127.0.0.1:{port}:localhost:{remote_port}", host])
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None:
                    raise ReportError("SSH tunnel failed. Check key/agent access and known_hosts with ssh.")
                if Path(control).exists():
                    check = subprocess.run(
                        ["ssh", "-S", control, "-O", "check", host],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=2, check=False,
                    )
                    if check.returncode == 0:
                        break
                if time.monotonic() >= deadline:
                    raise ReportError("Timed out opening the SSH tunnel.")
                time.sleep(0.1)
            yield f"http://127.0.0.1:{port}"
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


class SameOriginRedirect(HTTPRedirectHandler):
    def __init__(self, base_url: str):
        self.origin = urlsplit(base_url).netloc

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "http" or target.netloc != self.origin:
            raise ReportError("Refusing a redirect outside the local SSH tunnel.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_report(base_url: str, credentials: tuple[str, str], target: Path,
                    timeout: float = 120) -> None:
    """Perform the Jetty form login, retaining session cookies only in memory."""
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()),
                          SameOriginRedirect(base_url))
    report_url = base_url + REPORT_PATH
    # Request the protected resource first so Jetty records the login destination.
    with opener.open(report_url, timeout=timeout) as initial:
        needs_login = urlsplit(initial.url).path.split(";", 1)[0] == "/LoginForm"
        if not needs_login:
            save_response(initial, target)
            return
    body = urlencode({"j_username": credentials[0], "j_password": credentials[1]}).encode()
    request = Request(base_url + "/j_security_check", data=body,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    with opener.open(request, timeout=timeout) as logged_in:
        path = urlsplit(logged_in.url).path.split(";", 1)[0]
        if path in ("/LoginForm", "/LoginError"):
            raise ReportError("LOCKSS login failed; check the config-service credentials.")
        if path == "/DaemonStatus":
            save_response(logged_in, target)
            return
    with opener.open(report_url, timeout=timeout) as response:
        save_response(response, target)


def save_response(response, target: Path) -> None:
    if response.headers.get_content_type() == "text/html":
        raise ReportError("LOCKSS returned an HTML page instead of CSV; check login and report access.")
    size = 0
    with target.open("wb") as output:
        os.fchmod(output.fileno(), 0o600)
        while chunk := response.read(64 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise ReportError("Report exceeds the 64 MiB download limit.")
            output.write(chunk)
    expected_length = response.headers.get("Content-Length")
    if expected_length is not None and (not expected_length.isdigit() or size != int(expected_length)):
        raise ReportError("LOCKSS returned an incomplete response; no report was published.")


def convert_csv(source, destination) -> tuple[int, int]:
    """Preserve all cells except the three named timestamp columns."""
    reader = csv.reader(source, strict=True)
    writer = csv.writer(destination, lineterminator="\n")
    headers = next(reader, None)
    if not headers or headers.count("AU") != 1 or any(headers.count(h) != 1 for h in DATE_COLUMNS):
        raise ReportError("CSV must contain AU and each of the three expected date columns exactly once.")
    positions = [headers.index(column) for column in DATE_COLUMNS]
    writer.writerow(headers)
    rows = converted = 0
    for row_number, row in enumerate(reader, 2):
        if len(row) != len(headers):
            raise ReportError(f"CSV row {row_number} has an unexpected number of columns.")
        for index in positions:
            value = row[index]
            if value in ("", "-1"):
                continue
            if not re.fullmatch(r"[0-9]{13}", value):
                raise ReportError(f"Expected epoch milliseconds in row {row_number}, {headers[index]}.")
            instant = EPOCH + timedelta(milliseconds=int(value))
            row[index] = instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            converted += 1
        writer.writerow(row)
        rows += 1
    if not rows:
        raise ReportError("CSV contains no archival units; no report was published.")
    return rows, converted


def create_report(output_dir: Path, *, source: Path | None = None,
                  base_url: str | None = None, credentials=None,
                  timeout: float = 120) -> tuple[Path, int, int]:
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=output_dir))
    try:
        raw = staging / "raw.csv"
        if source is not None:
            shutil.copyfile(source, raw)
            raw.chmod(0o600)
        else:
            download_report(base_url, credentials, raw, timeout)
        result = staging / "status.csv"
        with raw.open(encoding="utf-8-sig", newline="") as original, result.open(
            "w", encoding="utf-8", newline=""
        ) as converted:
            os.fchmod(converted.fileno(), 0o600)
            rows, count = convert_csv(original, converted)
        run_dir = output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        staging.rename(run_dir)
        return run_dir / result.name, rows, count
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Private settings file (default: .env beside the script)")
    parser.add_argument("--host", help="Override LOCKSS_SSH_HOST")
    parser.add_argument("--ssh-key", type=Path, help="Override LOCKSS_SSH_KEY_PATH (local private key)")
    parser.add_argument("--remote-port", type=int, help="Override LOCKSS_REMOTE_PORT (default: 24621)")
    parser.add_argument("--output-dir", type=Path, help="Override LOCKSS_REPORTS_DIR (default: reports)")
    parser.add_argument("--input", type=Path, help="Convert an existing raw CSV without connecting")
    parser.add_argument("--timeout", type=float, help="Override LOCKSS_HTTP_TIMEOUT (default: 120 seconds)")
    args = parser.parse_args()
    try:
        settings = load_settings(args.env_file)
        args.host = args.host if args.host is not None else settings.get("LOCKSS_SSH_HOST")
        try:
            args.remote_port = args.remote_port if args.remote_port is not None else int(settings.get("LOCKSS_REMOTE_PORT", "24621"))
            args.timeout = args.timeout if args.timeout is not None else float(settings.get("LOCKSS_HTTP_TIMEOUT", "120"))
        except ValueError:
            raise ReportError("LOCKSS_REMOTE_PORT and LOCKSS_HTTP_TIMEOUT must be numeric.") from None
        if not 1 <= args.remote_port <= 65535 or not 0 < args.timeout < float("inf"):
            raise ReportError("Use a valid remote port and a positive finite timeout.")
        if not args.input and not args.host:
            raise ReportError("Supply --host or set LOCKSS_SSH_HOST in .env.")
        args.output_dir = args.output_dir or configured_path(settings, "LOCKSS_REPORTS_DIR", "reports")
        if args.input:
            result, rows, count = create_report(args.output_dir, source=args.input)
        else:
            credentials = configured_credentials(settings)
            key_path = args.ssh_key.expanduser() if args.ssh_key is not None else None
            if key_path is None and settings.get("LOCKSS_SSH_KEY_PATH"):
                key_path = configured_path(settings, "LOCKSS_SSH_KEY_PATH", "")
            with ssh_tunnel(args.host, args.remote_port, key_path) as base_url:
                result, rows, count = create_report(args.output_dir, base_url=base_url,
                                                  credentials=credentials, timeout=args.timeout)
        print(f"Saved {rows} archival units; formatted {count} timestamps as ISO 8601 datetimes.")
        print(result)
        return 0
    except HTTPError as error:
        print(f"Error: LOCKSS returned HTTP {error.code}; check credentials and report access.", file=sys.stderr)
    except (URLError, HTTPException, TimeoutError, subprocess.TimeoutExpired):
        print("Error: Could not reach LOCKSS through the SSH tunnel, or the request timed out.", file=sys.stderr)
    except ReportError as error:
        print(f"Error: {error}", file=sys.stderr)
    except (OSError, UnicodeError, csv.Error):
        print("Error: Could not read/write a file or parse CSV. Check paths, permissions, and UTF-8 input.", file=sys.stderr)
    except KeyboardInterrupt:
        print("Interrupted; temporary report and SSH tunnel cleaned up.", file=sys.stderr)
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
