#!/usr/bin/env python3
"""
Final script v4: strict duplicate prevention with shrinking pool and conflict logging.

- Destination main!F <- actual sum from ppchange_transactions!J
- Destination main!G <- source id from ppchange_transactions!A
- Each source_id can only be used once across all rows and both passes.
- Pass 1: fill rows with empty G (match by email, date ±2d, sum ±10%).
- Pass 2: recheck rows; update if G empty/invalid OR F differs >10% from source J.
- IDs once assigned are remembered (shrinking pool). No reuse across passes.
- Conflicts (row had match but ID already used) are logged.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Set, Dict

import pandas as pd
from dateutil import parser as dateparser
import gspread
from google.oauth2.service_account import Credentials

# ====================== CONFIG ======================
SRC_SPREADSHEET_ID = "1f06BwiD1Tvu02FRN9IoAp7a3OQwccTOuA1uqkOnn_xE"  # <-- put your source spreadsheet ID here
DST_SPREADSHEET_ID = "1Axn_zDUwly8vNX5JrvLYjrLIpzFvaFqH3MTT53b3umQ"  # <-- put your destination spreadsheet ID here
SRC_SHEET_NAME = "ppchange_transactions"
DST_SHEET_NAME = "main"
CREDENTIALS_PATH = "credentials.json"  # Path to service account JSON
# ====================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def auth_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def to_float(val) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s == "":
        return None
    s = s.replace("\u00a0", " ").replace("\u202f", " ").strip()
    try:
        if "," in s and s.count(",") == 1 and "." not in s:
            return float(s.replace(" ", "").replace(",", "."))
    except Exception:
        pass
    s2 = s.replace(" ", "")
    if "," in s2 and "." in s2:
        if s2.rfind(",") < s2.rfind("."):
            s2 = s2.replace(",", "")
        else:
            s2 = s2.replace(".", "").replace(",", ".")
    else:
        s2 = s2.replace(",", "")
    try:
        return float(s2)
    except Exception:
        return None


def to_date(val) -> Optional[pd.Timestamp]:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)) and 0 < float(val) < 1000000:
        base = datetime(1899, 12, 30)
        return pd.Timestamp(base + timedelta(days=float(val)))
    try:
        dt = dateparser.parse(str(val), dayfirst=True, fuzzy=True)
        return pd.Timestamp(dt)
    except Exception:
        return None


def load_sheet_as_df(sh: gspread.Spreadsheet, tab_name: str) -> pd.DataFrame:
    ws = sh.worksheet(tab_name)
    values = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
    if not values:
        return pd.DataFrame()
    header = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=header)
    return df


def normalize_email(val: str) -> str:
    return (val or "").strip().lower()


def pick_col(df: pd.DataFrame, candidates):
    for name in candidates:
        if name in df.columns:
            return name
    return None


def best_match(
    src_df: pd.DataFrame,
    target_email: str,
    target_date: pd.Timestamp,
    target_sum: float,
    used_ids: Set[str],
) -> Optional[pd.Series]:
    if pd.isna(target_date) or target_sum is None:
        return None
    email = normalize_email(target_email)
    if not email:
        return None
    cand = src_df[src_df["email_norm"] == email].copy()
    if cand.empty:
        return None
    cand["days_diff"] = (cand["date"] - target_date).abs().dt.days
    eps = max(abs(target_sum), 1e-9)
    cand["sum_diff_abs"] = (cand["sum"] - target_sum).abs()
    cand["rel_sum_diff"] = cand["sum_diff_abs"] / eps
    cand = cand[(cand["days_diff"] <= 2) & (cand["rel_sum_diff"] <= 0.10)]
    cand = cand[~cand["source_id"].isin(used_ids)]
    if cand.empty:
        return None
    cand = cand.sort_values(by=["days_diff", "rel_sum_diff"])
    return cand.iloc[0]


def get_used_ids(main_values: List[List[str]]) -> Set[str]:
    used = set()
    for row in main_values[1:]:
        if len(row) >= 7:
            val = str(row[6]).strip()
            if val:
                used.add(val)
    return used


def run_pass(
    dst_sh: gspread.Spreadsheet,
    rows: List[List[str]],
    src_df: pd.DataFrame,
    used_ids: Set[str],
    check_mismatch: bool = False,
) -> Tuple[int, List[str]]:
    updates = []
    conflicts = []

    def safe_get(row, idx):
        return row[idx] if idx < len(row) else ""

    for i, row in enumerate(rows, start=2):
        tgt_date = to_date(safe_get(row, 0))  # A
        tgt_sum = to_float(safe_get(row, 5))  # F
        tgt_id = str(safe_get(row, 6)).strip()  # G
        tgt_email = safe_get(row, 7)  # H

        if tgt_date is None or tgt_sum is None or not str(tgt_email).strip():
            continue

        if not check_mismatch and tgt_id:
            continue

        match = best_match(src_df, tgt_email, tgt_date, tgt_sum, used_ids)
        if match is None:
            continue

        src_sum = float(match["sum"])
        src_id = str(match["source_id"])

        if check_mismatch:
            needs_update = False
            if not tgt_id or tgt_id not in src_df["source_id"].values:
                needs_update = True
            elif tgt_sum is not None:
                diff_ratio = abs(tgt_sum - src_sum) / max(abs(src_sum), 1e-9)
                if diff_ratio > 0.10:
                    needs_update = True
            if not needs_update:
                continue

        if src_id in used_ids:
            conflicts.append(f"Row {i}: wanted {src_id} but already used")
            continue

        updates.append((i, src_sum, src_id))
        used_ids.add(src_id)

    if not updates:
        return 0, conflicts

    body = {"valueInputOption": "USER_ENTERED", "data": []}
    for row_idx, new_sum, new_id in updates:
        body["data"].append(
            {"range": f"{DST_SHEET_NAME}!F{row_idx}", "values": [[new_sum]]}
        )
        body["data"].append(
            {"range": f"{DST_SHEET_NAME}!G{row_idx}", "values": [[new_id]]}
        )

    if hasattr(dst_sh, "values_batch_update"):
        dst_sh.values_batch_update(body)
    else:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{dst_sh.id}/values:batchUpdate"
        dst_sh.client.request("post", url, json=body)

    print(
        f"Pass ({'mismatch' if check_mismatch else 'normal'}) wrote {len(updates)} rows"
    )
    return len(updates), conflicts


def main():
    gc = auth_client()
    src_sh = gc.open_by_key(SRC_SPREADSHEET_ID)
    dst_sh = gc.open_by_key(DST_SPREADSHEET_ID)

    # Load source
    src_df = load_sheet_as_df(src_sh, SRC_SHEET_NAME)
    if src_df.empty:
        print("No source data")
        return
    src_id_col = (
        pick_col(
            src_df,
            [
                c
                for c in src_df.columns
                if c.strip().lower() in ["id", "txid", "transaction id", "a"]
            ],
        )
        or src_df.columns[0]
    )
    src_date_col = (
        pick_col(
            src_df,
            [c for c in src_df.columns if c.strip().lower() in ["date", "дата", "d"]],
        )
        or src_df.columns[3]
    )
    src_email_col = (
        pick_col(
            src_df,
            [
                c
                for c in src_df.columns
                if c.strip().lower() in ["email", "e-mail", "mail", "f"]
            ],
        )
        or src_df.columns[5]
    )
    src_sum_col = (
        pick_col(
            src_df,
            [
                c
                for c in src_df.columns
                if c.strip().lower() in ["sum", "amount", "final", "j", "сума"]
            ],
        )
        or src_df.columns[9]
    )

    src_df = src_df.rename(
        columns={
            src_id_col: "source_id",
            src_date_col: "date_raw",
            src_email_col: "email_raw",
            src_sum_col: "sum_raw",
        }
    )
    src_df["date"] = src_df["date_raw"].apply(to_date)
    src_df["email_norm"] = src_df["email_raw"].apply(normalize_email)
    src_df["sum"] = src_df["sum_raw"].apply(to_float)
    src_df = src_df[["source_id", "date", "email_norm", "sum"]]

    # Destination
    main_ws = dst_sh.worksheet(DST_SHEET_NAME)
    main_values = main_ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
    rows = main_values[1:]
    used_ids = get_used_ids(main_values)

    total = 0
    conflicts_all: List[str] = []

    updated, conflicts = run_pass(dst_sh, rows, src_df, used_ids, check_mismatch=False)
    total += updated
    conflicts_all.extend(conflicts)

    # Reload after first pass
    main_values = main_ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
    rows = main_values[1:]
    used_ids = get_used_ids(main_values)

    updated, conflicts = run_pass(dst_sh, rows, src_df, used_ids, check_mismatch=True)
    total += updated
    conflicts_all.extend(conflicts)

    print(f"All done. Total rows updated across 2 passes: {total}")
    if conflicts_all:
        print("Conflicts (skipped due to duplicate IDs):")
        for msg in conflicts_all:
            print("  " + msg)


if __name__ == "__main__":
    main()
