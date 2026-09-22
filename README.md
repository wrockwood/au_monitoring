# LOCKSS AU monitoring CSV

Download the LOCKSS configuration service's archival-unit status CSV through
SSH and convert its timestamp columns to readable UTC dates. Requires
**Python 3.10+ and OpenSSH** on macOS or Linux. No pip packages, Google account,
API credentials, service accounts, or installer repository are needed.

This is a standalone CSV-only tool. It never opens or writes a spreadsheet.
Its retrieval protocol was verified against LOCKSS 2.0 beta2; other versions
may use different authentication or CSV layouts. Tests have been run on macOS;
Linux is a supported code target but has not been verified live. Windows is
not currently supported.

## Setup

Clone this repository and enter its directory. Replace `REPOSITORY_URL` with
the Git URL supplied by its maintainer:

```sh
git clone REPOSITORY_URL au_monitoring
cd au_monitoring
cp .env.example .env
chmod 600 .env
```

Edit `.env` with your own organization's settings:

```dotenv
LOCKSS_SSH_HOST=root@lockss.example.org
LOCKSS_UI_USERNAME=example-user
LOCKSS_UI_PASSWORD='your-config-service-password'
LOCKSS_REMOTE_PORT=24621
LOCKSS_HTTP_TIMEOUT=120
LOCKSS_REPORTS_DIR=reports
```

`LOCKSS_UI_USERNAME` and `LOCKSS_UI_PASSWORD` are the configuration service's
web-interface login, separate from SSH credentials. `LOCKSS_SSH_HOST` is
`user@hostname` or an alias from your SSH configuration. First confirm that
ordinary SSH works using your local key or agent and a verified host key:

```sh
ssh root@lockss.example.org
```

Replace the example hostname and user with the account your organization uses.
The tool needs only SSH forwarding and UI read access; it runs no remote shell
commands and does not install software on the node. It uses your local SSH key
or agent and never copies a private key from the server.

## Retrieve a report

```sh
python3 lockss_report.py
```

The script opens its own temporary loopback tunnel to remote `localhost:24621`,
logs in via `/j_security_check`, and downloads:

```text
/DaemonStatus?table=ArchivalUnitStatusTable&output=csv
```

The tunnel closes afterward. An existing interactive tunnel can stay open.
The password is not placed in command-line arguments or printed. Session
cookies stay in memory. No server configuration is changed.

Each successful run prints the converted CSV's path and creates a dated folder:

```text
reports/20260922T120000.123456Z/
  raw.csv
  status-UTC.csv
```

- `raw.csv` retains the original downloaded bytes.
- `status-UTC.csv` converts `Last Poll`, `Last Crawl Start`, and `Last Successful
  Crawl` from 13-digit Unix milliseconds to `YYYY-MM-DD HH:mm:ss.SSS` in **UTC**.
  For example, `1788974634653` becomes `2026-09-09 17:23:54.653`.
- Blanks and `-1` remain unchanged. Headers, column order, row order, and all
  other cell values are preserved. CSV quoting and line endings may normalize.

Directories are private (mode `700`) and reports are mode `600`. A failed
download or validation does not publish a partial report or replace earlier
reports. Reports remain until you remove them. There is no automatic retention.

## Import or deliver the CSV

Give `status-UTC.csv` to your administrators or import it into **your own**
existing spreadsheet tab. No spreadsheet IDs or tab names are needed by this
tool because it produces a file only.

For a shared multi-tab spreadsheet, select your organization's tab, then use
your spreadsheet application's option to replace **only that tab's contents**.
Do not replace the entire spreadsheet, append to the previous snapshot, or
delete/recreate the tab if other tabs reference it. Clear any leftover old data
rows when the new report is shorter. Check the headers, row count, dates, and
`-1` values after import. The dates in this file are UTC; there is no conversion
to the spreadsheet's local time zone by this tool.

## Configuration details

The default `.env` is beside these scripts, even when run from another directory.
It is independent of any credentials or settings in a parent directory.

| Variable | Meaning | Default |
| --- | --- | --- |
| `LOCKSS_SSH_HOST` | SSH destination, including user if needed | Required for live retrieval |
| `LOCKSS_UI_USERNAME` | Config-service web username | Required for live retrieval |
| `LOCKSS_UI_PASSWORD` | Config-service web password | Required for live retrieval |
| `LOCKSS_REMOTE_PORT` | Config-service HTTP port on the remote node | `24621` |
| `LOCKSS_HTTP_TIMEOUT` | HTTP socket timeout in seconds, not an overall deadline | `120` |
| `LOCKSS_REPORTS_DIR` | Output directory | `reports` beside the scripts |

Values are read as data, not shell code. **Do not `source .env`.**

- Use uppercase names exactly as shown. Unknown or duplicate keys fail without
  displaying their values.
- Unquoted values preserve everything after `=`, including spaces, `#`, `=`,
  dollar signs, and backslashes. Single quotes remove only the outer quotes.
- Double-quoted values use JSON escapes: `\\` means a backslash and `\"` means a
  literal double quote. Neither quoting style expands variables or commands.
- Blank lines and full-line comments beginning with `#` are allowed. Inline
  comments, `export`, multiline values, and NUL characters are not supported.
- The file must belong to you and have no group/other permissions (`chmod 600`).
- Explicit CLI options override process environment variables; environment
  variables override `.env`; `.env` overrides built-in defaults. There are no
  CLI options for entering passwords.
- Relative configured output paths are relative to the scripts' directory.
  Explicit CLI paths are relative to your current shell directory.

Examples:

```sh
python3 lockss_report.py --help
python3 lockss_report.py --env-file .env.organization
python3 lockss_report.py --host root@lockss.example.org --remote-port 24621 --timeout 180
python3 lockss_report.py --input /path/to/raw.csv --output-dir /path/to/reports
```

Offline `--input` conversion needs no SSH or UI credentials. It expects raw
epoch values, not already-converted dates. A valid `.env`, if present, is still
read for output defaults. An explicitly selected missing settings file fails.

## Verification

```sh
python3 -m unittest -v
```

Tests use synthetic records and a local HTTP server, without accessing a LOCKSS
node or any external service.

## Sharing and updates

Share this repository's Git URL. Each organization creates its own private
`.env`; credentials and downloaded reports are excluded from Git. Keep
`.env.example` synthetic and never commit private SSH keys or production data.

To receive updates from the configured remote:

```sh
git pull --ff-only
python3 -m unittest -v
```

This repository is independent of the original Google Sheets updater. It needs
no files, credentials, or Python dependencies from that parent project.
