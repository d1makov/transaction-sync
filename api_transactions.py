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


def get_existing_ids():
    try:
        # Get all values from the first column (IDs).
        # Assuming the first row is the header, so we skip it.
        all_ids = sheet.col_values(1)[1:]
        return set(all_ids)
    except Exception as e:
        print("Error reading sheet:", e)
        return set()


def insert_new_rows(transactions, existing_ids):
    new_rows = []
    for tx in transactions:
        # Convert tx["id"] to string for comparison.
        if str(tx["id"]) not in existing_ids:
            row = [
                tx["id"],
                tx["transaction_id"],
                tx["user_id"],
                datetime.utcfromtimestamp(int(tx["timestamp"])).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                tx["description"],
                tx["paypal_email"],
                tx["total"],
                tx["fee"],
                tx["commission"],
                tx["summa"],
                tx["status"],
            ]
            # For columns G (index 6) through J (index 9), replace "." with ","
            for idx in [6, 7, 8, 9]:
                row[idx] = str(row[idx]).replace(".", ",")
            new_rows.append(row)

    if new_rows:
        # Inserts new records at row 2, moving the older ones lower.
        sheet.insert_rows(new_rows, row=2, value_input_option="USER_ENTERED")
        print(f"Inserted {len(new_rows)} new rows.")
    else:
        print("No new transactions to insert.")


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
    existing_ids = get_existing_ids()
    insert_new_rows(transactions, existing_ids)
    # normalize_emails()
    # update_email_sums()


if __name__ == "__main__":
    main()
