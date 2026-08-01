# Positional Trading Assistant

Prototype 1 automates a manual MarketSmith India login, downloads the
**1-Month Minervini** screen as CSV, validates it, and prints its stock symbols.

This prototype intentionally does not include financial extraction, C++ rules,
email, or Telegram integration.

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

## 2. Export the screen

```bash
source .venv/bin/activate
python automation/export_screen.py
```

The script reuses the saved session, opens **1-Month Minervini**, selects
**Export**, and stores the CSV in `data/incoming/`. If MarketSmith changes its
authenticated interface, the script reports the failed step and attempts to save
`logs/export_failure.png` for diagnosis.

## 3. Validate and print symbols

```bash
source .venv/bin/activate
python automation/read_csv.py
```

The newest CSV in `data/incoming/` must be non-empty, parseable, contain rows,
have named columns, and include a recognized symbol column. Symbols are normalized
to uppercase, validated, de-duplicated in first-seen order, and printed one per line.
