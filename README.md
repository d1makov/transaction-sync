# transaction-sync

Syncs PPChange partner-API transactions into the `admin` Google Sheet. Deployed
on Contabo at `/opt/ppchange_api`, run by root cron.

There are **two independent scripts** writing to **two separate tabs**. They do
not touch each other's tab.

| Script | Tab | Cron | Log | Behaviour |
|---|---|---|---|---|
| `api_transactions.py` | `ppchange_transactions` | `0 * * * *` | `/var/log/ppchange_api.log` | **Append-only** — inserts only rows whose `id` is new, at the top. |
| `sync_auto.py` | `ppchange_transactions_auto` | `15 * * * *` | `/var/log/ppchange_auto.log` | **Full refresh** — rewrites the entire API list every run. |

## Why two scripts

`api_transactions.py` only ever *adds* new rows and never revisits old ones. So
if a transaction's `status` changes (`розблоковано` ↔ `_blocked`) or it is later
deleted from the API (a foreign transaction that gets purged), the stored row
stays frozen and wrong.

`sync_auto.py` fixes that: on every run it fetches the **whole** transaction
list and rewrites the entire `ppchange_transactions_auto` tab in a **single
batch write** (`worksheet.resize()` to the exact size, then one
`worksheet.update()`), so the tab always mirrors live API state — status changes
and deletions included — without spending one request per row.

`ppchange_transactions` (and its consumers — `evolt_auto`, `transactions_mapping`,
`active-paypals`) is left untouched. Nothing consumes `ppchange_transactions_auto`
yet; migrating those consumers to it is a possible follow-up.

## Column layout (both tabs, A–K)

`id · id_2 · user_id · timestamp · description · paypal_email · total · fee ·
commission · summa · status`

- The API's `currency` field is intentionally **dropped** to keep the layout
  compatible with `ppchange_transactions`.
- The sheet's locale uses a **comma** decimal separator, so numeric columns
  `total, fee, commission, summa` (G–J) are written as comma-strings with
  `USER_ENTERED`; the sheet parses them back into real numbers.
- `timestamp` (D) is written as `YYYY-MM-DD HH:MM:SS` (UTC).

## Monthly totals in the `зведене` tab

`sync_auto.py` also writes the monthly sum of column **J (`summa`)**, grouped by
transaction date, into the `зведене` tab:

- **C14** — current month total
- **C15** — previous month total

### ⚠️ Month-boundary semantics (do not "fix" this)

The totals use the **true full calendar month**: every transaction is bucketed
by its `(year, month)`, so each one is counted **exactly once**, and the last
day of the month is included in full.

This deliberately does **not** replicate the existing `зведене` **F14/F15**
formulas. Those use
`SUMIFS(...; D:D; ">="&EOMONTH(TODAY();-1)+1; D:D; "<="&EOMONTH(TODAY();0))`.
Because column D stores a datetime *with a time component*, the `<= EOMONTH(...)`
upper bound resolves to `last-day 00:00`, so **any transaction on the last day of
the month with a time later than midnight is silently dropped** — it falls into a
one-day gap between consecutive months (F15 ends at `30th 00:00`, F14 starts at
`1st 00:00`). Verified example (June 2026): F-formula method = `188 268.10`,
true full month = `206 209.58`; the missing `17 941.48` is exactly the 19
transactions dated the 30th after midnight.

Neither method double-counts (no shared inclusive boundary — the two formulas are
a full day apart), but the F14/F15 method **under-counts** the last day. C14/C15
intentionally use the correct full-month sum.

## Configuration

See `.env.example`. `sync_auto.py` adds two optional vars (defaults shown there):
`AUTO_WORKSHEET_NAME=ppchange_transactions_auto`,
`SUMMARY_WORKSHEET_NAME=зведене`.

## Cron

```
0  * * * * cd /opt/ppchange_api && /opt/ppchange_api/venv/bin/python api_transactions.py >> /var/log/ppchange_api.log 2>&1
15 * * * * cd /opt/ppchange_api && /opt/ppchange_api/venv/bin/python sync_auto.py        >> /var/log/ppchange_auto.log 2>&1
```

## Deployment & operations (read before changing anything)

Deployment is **fully driven by `.github/workflows/deploy.yml`** (GitHub Actions,
`appleboy/ssh-action`, secrets `SERVER_HOST` / `SERVER_USER` / `SERVER_PASSWORD`).
It runs on **every push to `main`** (and via manual `workflow_dispatch`) and, on
the server, it:

1. `git pull`s (or clones) into `/opt/ppchange_api`;
2. creates the venv and `pip install`s `requirements.txt`;
3. **overwrites `.env`** from secrets (`PPCHANGE_API_URL`, `EMAIL_LIST`,
   `GOOGLE_CREDENTIALS_JSON`, plus hardcoded `GOOGLE_SHEET_NAME`/`WORKSHEET_NAME`/
   `CREDENTIALS_PATH`);
4. **overwrites the crontab** with both hourly jobs.

### ⚠️ Gotchas — do not relearn these the hard way

- **Never edit the server crontab by hand.** Every push rewrites it from
  deploy.yml, silently discarding manual entries. Change the schedule **only** in
  `deploy.yml` (and mirror it in `setup_cron.sh`), then push.
- **The crontab cleanup matches by SCRIPT NAME, not by path.** It used to be
  `grep -v "ppchange_api"`, which also deleted the `sync_auto.py` line because its
  path (`/opt/ppchange_api`) contains that substring — so `sync_auto` silently
  never ran. Keep the `grep -v "api_transactions.py" | grep -v "sync_auto.py"`
  form. If you add a third job, add its own `grep -v` and `echo`.
- **`.env` is regenerated from secrets on every deploy.** Editing `.env` on the
  server does not stick. To add a new env var you must add it to the `.env`
  here-doc in deploy.yml **and** create the matching GitHub secret. The optional
  `AUTO_WORKSHEET_NAME` / `SUMMARY_WORKSHEET_NAME` are intentionally *not* in that
  here-doc — the script falls back to code defaults; only add them if you need to
  point at different tabs.
- **A stale-looking sheet usually means a cron/deploy problem, not a code bug.**
  First check: `crontab -l`, the log mtimes (`ls -l /var/log/ppchange_*.log`), and
  the latest deploy run (`gh run list`).

### Manual catch-up run

```bash
ssh <server>
cd /opt/ppchange_api
./venv/bin/python api_transactions.py   # append new rows to ppchange_transactions
./venv/bin/python sync_auto.py          # full refresh + зведене C14/C15
```
