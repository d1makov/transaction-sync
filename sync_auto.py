"""
PPChange API — full hourly refresh into `ppchange_transactions_auto`.

Why this exists
---------------
`api_transactions.py` is append-only: it only inserts rows that appeared in the
last hour and never touches the ones already stored. So if a transaction's
status changes (e.g. `розблоковано` -> `_blocked`) or it is removed from the API
altogether (a foreign transaction that later gets deleted from ppchange), the
row in the sheet stays frozen and wrong.

This script instead rewrites the ENTIRE list from the API on every run into a
separate tab `ppchange_transactions_auto`, in a SINGLE batch write, so the tab
always mirrors the live API state (status changes and deletions included)
without burning a request per row.

It also refreshes the "зведене" tab: C14 = total of column J (summa) for the
current month, C15 = total for the previous month, grouped by transaction date.
This is the pure ppchange monthly sum — the same month boundaries as the
existing F14/F15 formulas, without their extra `2pex_transactions_aggregation`,
`*0,91` and `-N3` terms.
"""
import os
from datetime import datetime, timezone

import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

API_URL = os.getenv("PPCHANGE_API_URL")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "admin")
AUTO_WORKSHEET_NAME = os.getenv("AUTO_WORKSHEET_NAME", "ppchange_transactions_auto")
SUMMARY_WORKSHEET_NAME = os.getenv("SUMMARY_WORKSHEET_NAME", "зведене")
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH", "credentials.json")

if not API_URL:
    raise ValueError("PPCHANGE_API_URL environment variable is required")

# Same 11-column layout as `ppchange_transactions` (A..K). `currency` from the
# API is intentionally dropped to stay compatible with that layout.
HEADER = [
    "id", "id_2", "user_id", "timestamp", "description", "paypal_email",
    "total", "fee", "commission", "summa", "status",
]
# Columns G..J (0-based 6..9) are numeric. The sheet's locale uses a comma
# decimal separator, so we send them as comma-strings with USER_ENTERED and the
# sheet parses them back into real numbers (matches api_transactions.py).
COMMA_COLS = (6, 7, 8, 9)

SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def fetch_data():
    resp = requests.get(API_URL, timeout=120)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"API returned non-success: {payload.get('status')}")
    return payload.get("data", []) or []


def to_float(s):
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def tx_date(tx):
    """Transaction datetime (UTC) from the unix timestamp, or None if unusable."""
    try:
        return datetime.utcfromtimestamp(int(tx["timestamp"]))
    except (TypeError, ValueError, KeyError):
        return None


def build_matrix(transactions):
    rows = [HEADER]
    for tx in transactions:
        d = tx_date(tx)
        row = [
            tx.get("id", ""),
            tx.get("transaction_id", ""),
            tx.get("user_id", ""),
            d.strftime("%Y-%m-%d %H:%M:%S") if d else "",
            tx.get("description", ""),
            tx.get("paypal_email", ""),
            tx.get("total", ""),
            tx.get("fee", ""),
            tx.get("commission", ""),
            tx.get("summa", ""),
            tx.get("status", ""),
        ]
        for idx in COMMA_COLS:
            row[idx] = str(row[idx]).replace(".", ",")
        rows.append(row)
    return rows


def prev_ym(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def month_sums(transactions):
    """(current_month_sum, previous_month_sum) of column J (summa), by tx date."""
    today = datetime.now(timezone.utc)
    cur = (today.year, today.month)
    prev = prev_ym(*cur)
    cur_sum = prev_sum = 0.0
    for tx in transactions:
        d = tx_date(tx)
        if d is None:
            continue
        key = (d.year, d.month)
        if key == cur:
            cur_sum += to_float(tx.get("summa"))
        elif key == prev:
            prev_sum += to_float(tx.get("summa"))
    return round(cur_sum, 2), round(prev_sum, 2)


def comma_str(value):
    """Number -> comma-decimal string so the comma-locale sheet stores a number."""
    return f"{value:.2f}".replace(".", ",")


def write_auto(spreadsheet, matrix):
    try:
        ws = spreadsheet.worksheet(AUTO_WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(
            title=AUTO_WORKSHEET_NAME, rows=len(matrix), cols=len(HEADER)
        )
        print(f"Created worksheet '{AUTO_WORKSHEET_NAME}'")
    # Resize to the exact shape: drops any stale trailing rows left by a run that
    # had more transactions (i.e. reflects deletions), then one batch write.
    ws.resize(rows=len(matrix), cols=len(HEADER))
    ws.update(matrix, "A1", value_input_option="USER_ENTERED")


def write_summary(spreadsheet, cur_sum, prev_sum):
    z = spreadsheet.worksheet(SUMMARY_WORKSHEET_NAME)
    z.update(
        [[comma_str(cur_sum)], [comma_str(prev_sum)]],
        "C14:C15",
        value_input_option="USER_ENTERED",
    )


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, SCOPE)
    client = gspread.authorize(creds)

    transactions = fetch_data()
    print(f"Fetched {len(transactions)} transactions from API")

    spreadsheet = client.open(GOOGLE_SHEET_NAME)

    matrix = build_matrix(transactions)
    write_auto(spreadsheet, matrix)
    print(f"Full refresh: wrote {len(matrix) - 1} rows to '{AUTO_WORKSHEET_NAME}'")

    cur_sum, prev_sum = month_sums(transactions)
    write_summary(spreadsheet, cur_sum, prev_sum)
    print(f"зведене C14 (current month) = {cur_sum}; C15 (previous month) = {prev_sum}")


if __name__ == "__main__":
    main()
