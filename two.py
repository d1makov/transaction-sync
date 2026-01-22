import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
from datetime import datetime
import re

# Define your Google Sheet ID, row number, and start_id here
GOOGLE_SHEET_ID = "1V0MoGaML7T3MYMqju-GMA65UO2aibOSEm7YjVPe26_8"
ROW = 1  # Adjust this row number in sheet2 where to start matching
START_ID = 464607
RESULT_SHEET_NAME = "WorkingSheet2"  # Name of the new sheet for results

# Define the accounts dictionary
accounts = {
    "katerinamat6@gmail.com": ["katerinamat6@gmail.com", "katerinamat66@gmail.com"],
    "another@example.com": ["related3@example.com", "related4@example.com"],
    # Add more entries as needed
}


# Authenticate and access Google Sheets
def authenticate_and_open_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = ServiceAccountCredentials.from_json_keyfile_name(
        "client_secret.json", scope
    )
    client = gspread.authorize(credentials)
    return client.open_by_key(GOOGLE_SHEET_ID)


# Convert gspread worksheet to Pandas DataFrame with duplicate header handling
def worksheet_to_dataframe(worksheet):
    rows = worksheet.get_all_values()
    if not rows or len(rows) < 2:
        raise ValueError(
            "The worksheet is empty or does not have enough rows for headers and data."
        )
    headers = rows[0]
    data = rows[1:]
    unique_headers = []
    seen = set()
    for header in headers:
        if header in seen:
            count = sum(1 for h in unique_headers if h.startswith(header))
            unique_headers.append(f"{header}_dup{count + 1}")
        else:
            unique_headers.append(header)
            seen.add(header)
    return pd.DataFrame(data, columns=unique_headers)


# Write DataFrame back to gspread worksheet
def dataframe_to_worksheet(worksheet, dataframe):
    worksheet.clear()
    worksheet.update([dataframe.columns.values.tolist()] + dataframe.values.tolist())


# Convert sum and final columns to float
def convert_to_float(sheet_df, sum_column, final_column):
    for col in [sum_column, final_column]:
        sheet_df[col] = (
            sheet_df[col]
            .astype(str)
            .str.replace(",", ".")
            .str.strip()
            .replace("", "0.0")
            .replace(r"[^\d.-]", "0.0", regex=True)
        )
        sheet_df[col] = pd.to_numeric(sheet_df[col], errors="coerce").fillna(0.0)
    return sheet_df


# Match rows based on the given criteria
def find_matches(sheet1_df, sheet2_df):
    sheet1_df["match"] = False
    used_rows = set()

    for i, sheet2_row in sheet2_df.iterrows():
        if i < ROW - 1:
            continue

        for j, sheet1_row in sheet1_df.iterrows():
            if sheet1_row["id_1"] == START_ID:
                break

            if j in used_rows:
                continue

            try:
                sheet1_date = datetime.strptime(sheet1_row["date_1"], "%d.%m.%Y")
                sheet2_date = datetime.strptime(sheet2_row["date"], "%d.%m.%Y")
                date_diff = abs((sheet1_date - sheet2_date).days)
            except ValueError:
                continue

            sheet1_sum = sheet1_row["sum_1"]
            sheet2_sum = sheet2_row["sum"]
            if sheet2_sum != 0:
                sum_diff = abs(sheet1_sum - sheet2_sum) / sheet2_sum
            else:
                sum_diff = float("inf")

            sheet1_email = sheet1_row["email_1"]
            sheet2_email = sheet2_row["email"]

            # Check if the email is in the accounts dictionary
            for main_email, related_emails in accounts.items():
                if sheet1_email in related_emails:
                    sheet1_email = main_email
                if sheet2_email in related_emails:
                    sheet2_email = main_email

            if (
                date_diff <= 1 and sum_diff <= 0.10 and sheet1_email == sheet2_email
            ):  # Changed 0.06 to 0.10
                if (
                    sheet1_row["final_1"] != 0
                    and sheet1_row["id_1"] not in sheet2_df["id"].values
                ):
                    sheet2_df.at[i, "final"] = sheet1_row["final_1"]
                    sheet2_df.at[i, "id"] = sheet1_row["id_1"]
                sheet1_df.at[j, "match"] = True
                used_rows.add(j)
                break

    sheet1_df.loc[~sheet1_df.index.isin(used_rows), "match"] = False
    return sheet1_df, sheet2_df


# Check headers and rename columns as needed
def check_and_prepare_headers(sheet1_df):
    original_headers = [
        "ID",
        "ID 2",
        "Дата",
        "Опис",
        "Paypal",
        "Сумма",
        "Paypal fee",
        "Комiсiя",
        "Чистий залишок",
        "Доступно з",
    ]
    renamed_headers = ["id_1", "date_1", "name_1", "email_1", "sum_1", "final_1"]

    current_headers = list(sheet1_df.columns)
    print(f"Current headers in Sheet1: {current_headers}")

    if set(current_headers) == set(original_headers):
        sheet1_df = sheet1_df.drop(
            columns=["ID 2", "Paypal fee", "Комiсiя", "Доступно з"]
        )
        sheet1_df.columns = renamed_headers
        print("Headers have been renamed and unnecessary columns dropped.")
    elif set(current_headers) == set(renamed_headers):
        print("Headers are already renamed. No action taken.")
    else:
        raise KeyError(
            "Unexpected headers in Sheet1. Please check the input sheet and ensure headers match the expected format."
        )

    return sheet1_df


# Extract email addresses using regex
def extract_email_addresses(sheet_df, email_column):
    email_pattern = re.compile(r"[\w\.-]+@[\w\.-]+")
    sheet_df[email_column] = sheet_df[email_column].apply(
        lambda x: (email_pattern.search(x).group(0) if email_pattern.search(x) else "")
    )
    return sheet_df


# Update main function to include email extraction
def main():
    sheet = authenticate_and_open_sheet()
    sheet1 = sheet.worksheet("Sheet1")
    sheet2 = sheet.worksheet("Sheet2")

    try:
        working_sheet = sheet.worksheet(RESULT_SHEET_NAME)
    except gspread.WorksheetNotFound:
        working_sheet = sheet.add_worksheet(
            title=RESULT_SHEET_NAME, rows="1000", cols="20"
        )

    sheet1_df = worksheet_to_dataframe(sheet1)
    sheet2_df = worksheet_to_dataframe(sheet2)

    sheet1_df = check_and_prepare_headers(sheet1_df)
    sheet1_df = extract_email_addresses(sheet1_df, "email_1")

    if "final" not in sheet2_df.columns:
        sheet2_df["final"] = ""
    if "id" not in sheet2_df.columns:
        sheet2_df["id"] = ""

    sheet1_df = convert_to_float(sheet1_df, "sum_1", "final_1")
    sheet2_df = convert_to_float(sheet2_df, "sum", "final")

    sheet1_df, sheet2_df = find_matches(sheet1_df, sheet2_df)

    # Move the "id" column to the end
    columns = list(sheet2_df.columns)
    columns.append(columns.pop(columns.index("id")))
    sheet2_df = sheet2_df[columns]

    dataframe_to_worksheet(working_sheet, sheet2_df)
    print(f"Matching completed and results written to '{RESULT_SHEET_NAME}'.")


if __name__ == "__main__":
    main()
