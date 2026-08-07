# Positional Trading Assistant

The automation reuses a manual MarketSmith India login and exports three stock
screens as CSV. **Build Your Screen** is the only primary candidate source.
**Mark Minervini 1-Month** and **Mark Minervini 5-Month** are independent
technical-signal sources; membership in either screen does not control or alter
the independent C++ WON engine. Overall Minervini confirmation is YES when either
timeframe is YES, NO only when both are NO, and UNKNOWN otherwise.

The export layer only acquires screen membership. It does not embed financial
extraction or C++ screening rules, and it does not send email or Telegram messages.

## Falcon Stocks local dashboard

After installing the pinned requirements, start the localhost-only dashboard:

```bash
source .venv/bin/activate
uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The **Run Analysis** action exports both screens
fresh, processes every Build Your Screen candidate, and presents the existing
WON, positional-score, Minervini, and ranking results.

For optional phone testing on the same LAN, run:

```bash
uvicorn web.app:app --host 0.0.0.0 --port 8000
```

This second command exposes the unauthenticated development dashboard to devices
that can reach the computer on the local network. It is not intended for internet
exposure.

## Prerequisites

- Ubuntu 24.04 under WSL2 with WSLg
- Python 3.12
- Playwright Chromium and its Linux dependencies

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
playwright install --with-deps chromium
```

## 1. Save a login session

```bash
source .venv/bin/activate
python automation/login.py
```

Log in manually in the visible browser. Return to the terminal and press Enter
only after login is complete. The script saves browser storage state to
`auth/marketsmith_state.json`. This file contains sensitive session data and is
excluded from Git. The scripts never request or store an email address or password.

## 2. Export a screen

```bash
source .venv/bin/activate
python -m automation.export_screen build-your-screen
python -m automation.export_screen minervini
python -m automation.export_screen minervini-5-months
```

The commands reuse the same saved session and select **Export** in a visible
browser. Build Your Screen exports are stored in
`data/incoming/build_your_screen/`; Minervini exports are stored separately in
`data/incoming/minervini_1_month/` and `data/incoming/minervini_5_months/`.
If MarketSmith changes its authenticated
interface, the exporter reports the failed step and attempts to save
`logs/export_failure.png` and `logs/export_failure.html` for diagnosis.

## 3. Validate and print symbols

```bash
source .venv/bin/activate
python -m automation.read_csv data/incoming/build_your_screen/<downloaded-file>.csv
python -m automation.read_csv data/incoming/minervini_1_month/<downloaded-file>.csv
python -m automation.read_csv data/incoming/minervini_5_months/<downloaded-file>.csv
```

Pass the explicit downloaded file path to the strict parser. Each CSV must be
non-empty, parseable, contain data rows, and match the expected 11-column schema.
Symbols are normalized to uppercase, validated, de-duplicated in first-seen order,
and printed with their company names. Downstream processing should likewise receive
an explicit screen directory or file path; it must not scan the shared
`data/incoming/` root.
