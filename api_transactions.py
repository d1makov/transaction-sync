"""
PPChange API Transaction Sync
Fetches transactions and syncs to Google Sheets
"""
import os
import requests
import json
import time
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# CONFIG from environment variables
API_URL = os.getenv("PPCHANGE_API_URL")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "admin")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "ppchange_transactions")
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH", "credentials.json")
EMAIL_LIST_STR = os.getenv("EMAIL_LIST", "")
email_list = [e.strip() for e in EMAIL_LIST_STR.split(",") if e.strip()]

# Validate required config
if not API_URL:
    raise ValueError("PPCHANGE_API_URL environment variable is required")

# Google Sheets setup
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
client = gspread.authorize(creds)
sheet = client.open(GOOGLE_SHEET_NAME).worksheet(WORKSHEET_NAME)


def fetch_data():
    try:
        response = requests.get(API_URL)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        print("Error fetching data:", e)
        return []


def load_existing_rows():
    try:
        values = sheet.get_all_values()
    except Exception as e:
        print("Error reading sheet:", e)
        return {}
    by_id = {}
    for row_num, row in enumerate(values[1:], start=2):
        if not row or not row[0]:
            continue
        by_id[row[0]] = (row_num, row[:13])
    return by_id


def build_row(tx):
    row = [
        tx["id"],
        tx["transaction_id"],
        tx["user_id"],
        datetime.utcfromtimestamp(int(tx["timestamp"])).strftime("%Y-%m-%d %H:%M:%S"),
        tx["description"],
        tx["paypal_email"],
        tx["total"],
        tx["fee"],
        tx["commission"],
        tx["summa"],
        tx.get("currency", ""),
        tx["status"],
    ]
    for idx in (6, 7, 8, 9):
        row[idx] = str(row[idx]).replace(".", ",")
    return [str(c) for c in row]


# Column indices (in build_row output) whose values come back from Sheets as
# numbers — Sheets strips trailing zeros under USER_ENTERED, so "90,00" round-trips
# as "90". Compare these as floats, not strings.
NUMERIC_COLS = (6, 7, 8, 9)


def _cells_equal(new_cell, old_cell, numeric):
    if not numeric:
        return new_cell == old_cell
    try:
        return float(new_cell.replace(",", ".") or "0") == float(
            old_cell.replace(",", ".") or "0"
        )
    except ValueError:
        return new_cell == old_cell


def _rows_equal(new_row, old_row):
    return all(
        _cells_equal(n, o, i in NUMERIC_COLS)
        for i, (n, o) in enumerate(zip(new_row, old_row))
    )


def sync_rows(transactions, existing_by_id):
    new_rows = []
    updates = []

    for tx in transactions:
        tx_id = str(tx["id"])
        new_row = build_row(tx)

        if tx_id not in existing_by_id:
            new_rows.append(new_row)
            continue

        row_num, old_row = existing_by_id[tx_id]
        old_row = (old_row + [""] * 13)[:13]
        old_api_cols = old_row[:12]
        old_m = old_row[12]

        if _rows_equal(new_row, old_api_cols):
            continue

        if not _cells_equal(new_row[9], old_api_cols[9], numeric=True):
            try:
                new_summa = float(new_row[9].replace(",", "."))
                old_summa = float(old_api_cols[9].replace(",", ".")) if old_api_cols[9] else 0.0
                diff_cell = f"{new_summa - old_summa:.2f}".replace(".", ",")
            except ValueError:
                diff_cell = old_m
        else:
            diff_cell = old_m
        updates.append((row_num, new_row, diff_cell))

    if updates:
        payload = [
            {"range": f"A{row_num}:M{row_num}", "values": [new_row + [diff_cell]]}
            for row_num, new_row, diff_cell in updates
        ]
        sheet.batch_update(payload, value_input_option="USER_ENTERED")
        print(f"Updated {len(updates)} changed rows.")

    if new_rows:
        sheet.insert_rows(new_rows, row=2, value_input_option="USER_ENTERED")
        print(f"Inserted {len(new_rows)} new rows.")

    if not updates and not new_rows:
        print("No changes.")


def update_email_sums():
    # Get all rows from the transactions sheet (skip header)
    transactions = sheet.get_all_values()[1:]
    # Create a dictionary to keep sums per email
    email_sums = {email: 0.0 for email in email_list}

    # Iterate over each transaction row
    for row in transactions:
        # Ensure row has enough columns. (Expected: F is at index 5, J is at index 9)
        if len(row) < 10:
            continue
        email = row[5].strip()
        if email in email_list:
            # Replace comma (used as decimal sep) back to a period for conversion
            summa_str = row[9].replace(",", ".").strip()
            try:
                amount = float(summa_str)
                email_sums[email] += amount
            except ValueError:
                print(f"Unable to convert '{summa_str}' to float for email {email}.")

    # Open the "main" worksheet
    main_sheet = client.open(GOOGLE_SHEET_NAME).worksheet("main")
    # Retrieve all rows from main
    main_values = main_sheet.get_all_values()

    # Build a mapping of email addresses (in column A) to their row number in main.
    email_to_row = {}
    for idx, row in enumerate(main_values, start=1):
        if row and row[0].strip() in email_list:
            email_to_row[row[0].strip()] = idx

    # Update column B in main with the computed sums for each email.
    cells_to_update = []
    for email, total in email_sums.items():
        if email in email_to_row:
            row_num = email_to_row[email]
            cell = main_sheet.cell(row_num, 2)
            cell.value = total
            cells_to_update.append(cell)
            print(f"Prepared update for row {row_num} for {email} with sum {total}")
        else:
            print(f"Email {email} not found in main worksheet.")
    if cells_to_update:
        main_sheet.update_cells(cells_to_update)
        print("Batch update completed.")
        if cells_to_update:
            main_sheet.update_cells(cells_to_update)
            print("Batch update completed.")
        # Insert modified code here (the $PLACEHOLDER$ code)


def normalize_emails():
    try:
        records = sheet.get_all_values()
        for i, row in enumerate(records, start=1):
            if i == 1 or len(row) < 6:
                continue
            normalized = row[5].strip().lower()
            if row[5] != normalized:
                sheet.update_cell(i, 6, normalized)
                print(f"Normalized email for row {i}")
    except Exception as e:
        print("Error normalizing emails:", e)


def main():
    transactions = fetch_data()
    existing_by_id = load_existing_rows()
    sync_rows(transactions, existing_by_id)
    # normalize_emails()
    # update_email_sums()


if __name__ == "__main__":
    main()
