import os
import time
import glob
import requests
import pandas as pd
from zoneinfo import ZoneInfo

API_BASE = "https://api.trello.com/1"

MEN_BOARD_SHORTLINK = "OLSdLzxK"
WOMEN_BOARD_SHORTLINK = "HdHx0FLI"

INPUT_DIR = "input"
DRY_RUN = False

COL_NAME = "Name"
COL_GENDER = "Guy or Girl?"
COL_TIMESTAMP = "Timestamp"

CENTRAL_TZ = ZoneInfo("America/Chicago")


def safe_str(x) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x).strip()


def norm_name(s: str) -> str:
    return " ".join(safe_str(s).lower().split())


def trello_get(path: str, params: dict):
    url = f"{API_BASE}{path}"
    while True:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "2"))
            time.sleep(retry_after)
            continue
        r.raise_for_status()
        return r.json()


def trello_put(path: str, params: dict):
    url = f"{API_BASE}{path}"
    while True:
        r = requests.put(url, params=params, timeout=30)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "2"))
            time.sleep(retry_after)
            continue
        r.raise_for_status()
        return r.json()


def get_board_id(shortlink: str, auth: dict) -> str:
    data = trello_get(f"/boards/{shortlink}", {**auth, "fields": "id"})
    return safe_str(data.get("id"))


def gender_to_board_shortlink(gender_raw: str) -> str | None:
    g = safe_str(gender_raw).lower()
    if g == "guy":
        return MEN_BOARD_SHORTLINK
    if g == "girl":
        return WOMEN_BOARD_SHORTLINK
    return None


def parse_timestamp_to_central(ts_value):
    """
    Returns (submission_date_str, unix_seconds) based on Timestamp converted to America/Chicago.

    submission_date_str format: M/D/YYYY (no leading zeros)
    """
    if ts_value is None:
        return None, None
    try:
        if pd.isna(ts_value):
            return None, None
    except Exception:
        pass

    dt = pd.to_datetime(ts_value)

    # If tz-naive, interpret it as Central time already.
    # If tz-aware, convert to Central.
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.to_pydatetime().replace(tzinfo=CENTRAL_TZ)
    else:
        dt = dt.to_pydatetime().astimezone(CENTRAL_TZ)

    submission_date_str = f"{dt.month}/{dt.day}/{dt.year}"
    unix_seconds = int(dt.timestamp())
    return submission_date_str, unix_seconds


def strip_submission_and_id(desc: str) -> str:
    """
    Removes any lines beginning with:
      Submission date:
      ID:
    Keeps all other lines intact.
    """
    d = desc or ""
    lines = d.splitlines()
    kept = []
    for line in lines:
        if line.startswith("Submission date:"):
            continue
        if line.startswith("ID:"):
            continue
        kept.append(line)

    out = "\n".join(kept).strip()
    return out


def build_new_desc(old_desc: str, submission_date_str: str, unix_id: int) -> str:
    cleaned = strip_submission_and_id(old_desc)

    if cleaned:
        cleaned = cleaned.rstrip() + "\n\n"

    cleaned += f"Submission date: {submission_date_str}\n"
    cleaned += f"ID: {unix_id}"
    return cleaned


def fetch_cards_by_name(board_id: str, auth: dict) -> dict[str, list[dict]]:
    """
    Returns: normalized_name -> list of cards
    card dict includes id, name, desc
    """
    cards = trello_get(
        f"/boards/{board_id}/cards",
        {**auth, "fields": "id,name,desc"},
    )

    out: dict[str, list[dict]] = {}
    for c in cards:
        nm = safe_str(c.get("name"))
        if not nm:
            continue
        key = norm_name(nm)
        out.setdefault(key, []).append(
            {
                "id": safe_str(c.get("id")),
                "name": nm,
                "desc": c.get("desc") or "",
            }
        )
    return out


def main():
    key = safe_str(os.environ.get("TRELLO_API_KEY"))
    token = safe_str(os.environ.get("TRELLO_TOKEN"))
    if not key or not token:
        raise SystemExit("Missing credentials. Set environment variables TRELLO_API_KEY and TRELLO_TOKEN.")

    auth = {"key": key, "token": token}

    excel_files = glob.glob(f"{INPUT_DIR}/*.xlsx")
    if len(excel_files) != 1:
        raise SystemExit(f"Expected exactly 1 Excel file in '{INPUT_DIR}/', found {len(excel_files)}")

    excel_path = excel_files[0]
    df = pd.read_excel(excel_path)

    for required in [COL_NAME, COL_GENDER, COL_TIMESTAMP]:
        if required not in df.columns:
            raise SystemExit(f"Missing required column: {required}")

    men_board_id = get_board_id(MEN_BOARD_SHORTLINK, auth)
    women_board_id = get_board_id(WOMEN_BOARD_SHORTLINK, auth)

    print("Fetching cards from Men board")
    men_cards = fetch_cards_by_name(men_board_id, auth)

    print("Fetching cards from Women board")
    women_cards = fetch_cards_by_name(women_board_id, auth)

    not_found = []
    multiple = []
    would_update = 0
    skipped = 0

    for i, row in df.iterrows():
        name = safe_str(row.get(COL_NAME))
        if not name:
            skipped += 1
            continue

        board_shortlink = gender_to_board_shortlink(row.get(COL_GENDER))
        if not board_shortlink:
            print(f'[SKIP] Row {i + 2}: {name} (invalid gender "{safe_str(row.get(COL_GENDER))}")')
            skipped += 1
            continue

        submission_date_str, unix_id = parse_timestamp_to_central(row.get(COL_TIMESTAMP))
        if not submission_date_str or unix_id is None:
            print(f"[SKIP] Row {i + 2}: {name} (bad Timestamp)")
            skipped += 1
            continue

        key_name = norm_name(name)

        if board_shortlink == MEN_BOARD_SHORTLINK:
            matches = men_cards.get(key_name, [])
        else:
            matches = women_cards.get(key_name, [])

        if not matches:
            print(f"[NOT FOUND] {name}")
            not_found.append(name)
            continue

        if len(matches) > 1:
            print(f"[MULTIPLE] {name} ({len(matches)} matches on that board)")
            multiple.append(name)
            continue

        card = matches[0]
        old_desc = card["desc"]
        new_desc = build_new_desc(old_desc, submission_date_str, unix_id)

        if DRY_RUN:
            print(f"\n[DRY RUN] Would update: {name}")
            print(f"  Card ID: {card['id']}")
            print(f"  New Submission date: {submission_date_str}")
            print(f"  New ID: {unix_id}")
            if old_desc.strip() == new_desc.strip():
                print("  No change needed (already matches after normalization).")
            else:
                print("  Description would be replaced.")
            would_update += 1
            continue

        trello_put(f"/cards/{card['id']}/desc", {**auth, "value": new_desc})
        print(f"[UPDATED] {name}")

    print("\nDone.")
    print(f"Dry run: {DRY_RUN}")
    print(f"Would update: {would_update}")
    print(f"Skipped: {skipped}")
    print(f"Not found: {len(not_found)}")
    print(f"Multiple: {len(multiple)}")


if __name__ == "__main__":
    main()