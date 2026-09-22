# LOCKSS AU monitoring

Download archival-unit status from the LOCKSS configuration service through an
SSH tunnel and convert its timestamps to readable UTC dates.

Requires **Python 3.10+ and OpenSSH** on macOS or Linux. Tested with LOCKSS 2.0
beta2 on macOS.

## Setup

Clone the repository, replacing `REPOSITORY_URL` with its Git URL:

```sh
git clone REPOSITORY_URL au_monitoring
cd au_monitoring
cp .env.example .env
chmod 600 .env
```

Edit `.env`, replacing the example hostname, SSH user, and UI credentials:

```dotenv
LOCKSS_SSH_HOST=user@lockss.example.org
LOCKSS_SSH_KEY_PATH=
LOCKSS_UI_USERNAME=example-user
LOCKSS_UI_PASSWORD='your-config-service-password'
LOCKSS_REMOTE_PORT=24621
LOCKSS_HTTP_TIMEOUT=120
LOCKSS_REPORTS_DIR=reports
```

The UI credentials are your LOCKSS configuration service login. The SSH account
must allow port forwarding. Confirm SSH access and verify the server's host key
before running the script:

```sh
ssh user@lockss.example.org
```

Leave `LOCKSS_SSH_KEY_PATH` blank to use your SSH configuration and agent, or set
it to a local private key such as `~/.ssh/id_ed25519`. For a passphrase-protected
key, load it into your agent first:

```sh
ssh-add ~/.ssh/id_ed25519
```

Keep private keys outside this repository. `.env` and generated reports are
excluded from Git.

## Generate a report

```sh
python3 lockss_report.py
```

The script opens a temporary SSH tunnel to the node's configuration service,
authenticates, downloads the CSV, and closes the tunnel. Each run prints the
output path and saves a new dated folder:

```text
reports/20260922T120000.123456Z/
  raw.csv
  status-UTC.csv
```

`raw.csv` is the original download. `status-UTC.csv` converts **Last Poll**,
**Last Crawl Start**, and **Last Successful Crawl** to UTC dates such as
`2026-09-09 17:23:54.653`. Other values, column order, blanks, and `-1` are preserved.
Previous reports are kept.

Share `status-UTC.csv` or import it into your organization's spreadsheet tab.
For a fresh snapshot, replace that tab's contents, including any leftover rows
from the previous report.

## Configuration

`LOCKSS_REMOTE_PORT` defaults to `24621`, `LOCKSS_HTTP_TIMEOUT` to `120` seconds,
and `LOCKSS_REPORTS_DIR` to `reports`. `.env` is loaded from beside the script;
relative key and output paths in it are resolved from that directory.

Use literal `KEY=value` entries, with single quotes around passwords as shown.
Values are not expanded as shell code; do not `source .env` or use inline comments.

Command-line options override environment variables, which override `.env`.
Command-line paths are relative to your current directory. Common options:

```sh
python3 lockss_report.py --help
python3 lockss_report.py --env-file .env.organization
python3 lockss_report.py --ssh-key ~/.ssh/id_ed25519
python3 lockss_report.py --input /path/to/raw.csv --output-dir /path/to/reports
```

`--input` converts an existing raw CSV locally.

## Tests

```sh
python3 -m unittest -v
```
