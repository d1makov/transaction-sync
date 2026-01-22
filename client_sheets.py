import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import re
import project.only_one, project.only_two

# CONFIG
ADMIN_SHEET_NAME = "admin"  # Change this to your real admin sheet name
ADMIN_TAB_NAME = "ppchange_transactions"  # Or automate by datetime
CLIENT_SHEET_NAMES = ["1.2"]  # ["1.1", "1.2", "1.3"]  # Extend as needed

# Google Sheets auth
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)


# Helpers
def parse_date(date_str):
    try:
        return datetime.strptime(date_str.strip(), "%d.%m.%Y")
    except:
        return None


def normalize_email(email):
    return email.strip().lower()


# Load admin data
admin_ws = client.open(ADMIN_SHEET_NAME).worksheet(ADMIN_TAB_NAME)
admin_data = admin_ws.get_all_records()
admin_entries = []
for row in admin_data:
    admin_entries.append(
        {
            "date": parse_date(str(row["date"])),
            "sum": float(str(row["sum"]).replace(",", ".")),
            "email": normalize_email(row["email"]),
            "final": row["final"],
            "id": str(row["id"]),
        }
    )


# Match logic
def find_matching_admin_row(date, amount, email):
    for entry in admin_entries:
        if not entry["date"] or not date:
            continue
        date_diff = abs((entry["date"] - date).days)
        if (
            date_diff <= 2
            and abs(entry["sum"] - amount) <= entry["sum"] * 0.10
            and entry["email"] == email
        ):
            return entry
    return None


# Sync each client sheet
for sheet_name in CLIENT_SHEET_NAMES:
    try:
        print(f"Processing client sheet: {sheet_name}")
        ws = client.open(sheet_name).worksheet(ADMIN_TAB_NAME)
        rows = ws.get_all_values()
        headers = rows[1]  # Assuming row 2 has headers
        updates = []

        for i, row in enumerate(rows[2:], start=3):  # data starts from row 3
            date_str = row[0].strip()
            sum_str = row[1].strip().replace(",", ".")
            email = normalize_email(row[7]) if len(row) > 7 else ""

            final_col = row[5].strip() if len(row) > 5 else ""
            id_col = row[6].strip() if len(row) > 6 else ""

            if final_col or id_col or not (date_str and sum_str and email):
                continue  # Skip filled or incomplete

            date = parse_date(date_str)
            try:
                amount = float(sum_str)
            except:
                continue

            match = find_matching_admin_row(date, amount, email)
            if match:
                ws.update(f"F{i}", str(match["final"]))
                ws.update(f"G{i}", str(match["id"]))
                print(f"Updated row {i} in sheet {sheet_name}")

    except Exception as e:
        print(f"Error processing sheet {sheet_name}: {e}")
