"""Private, literal .env configuration for the standalone CSV reporting tool."""

import json
import os
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parent
KEYS = frozenset({
    "LOCKSS_SSH_HOST", "LOCKSS_UI_USERNAME", "LOCKSS_UI_PASSWORD", "LOCKSS_REMOTE_PORT",
    "LOCKSS_HTTP_TIMEOUT", "LOCKSS_REPORTS_DIR",
})


class ReportError(Exception):
    """An operational failure safe to display without response or credential data."""


def read_env(path: Path) -> dict[str, str]:
    """Read literal KEY=value lines. Never execute, expand, or interpolate values."""
    result = {}
    with path.open(encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ReportError("The .env file must be owned by you with mode 600.")
        for number, line in enumerate(source, 1):
            line = line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or key not in KEYS or key in result:
                raise ReportError(f"Unknown, duplicate, or malformed .env entry on line {number}.")
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    raise ReportError(f"Invalid double-quoted .env value on line {number}.") from None
                if not isinstance(value, str):
                    raise ReportError(f"Expected a string on .env line {number}.")
            elif value.startswith("'"):
                if len(value) < 2 or not value.endswith("'"):
                    raise ReportError(f"Unclosed single-quoted .env value on line {number}.")
                value = value[1:-1]
            if any(character in value for character in ("\r", "\n", "\0")):
                raise ReportError(f"Multiline or NUL .env values are not supported (line {number}).")
            result[key] = value
    return result


def load_settings(path: Path | None = None) -> dict[str, str]:
    selected = path if path is not None else ROOT / ".env"
    if selected.exists():
        values = read_env(selected)
    elif path is not None:
        raise ReportError("The selected --env-file does not exist.")
    else:
        values = {}
    values.update({key: os.environ[key] for key in KEYS if key in os.environ})
    return values


def configured_path(settings: dict, key: str, default: str) -> Path:
    path = Path(settings.get(key, default)).expanduser()
    return path if path.is_absolute() else ROOT / path
