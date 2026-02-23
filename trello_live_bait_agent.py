# trello_live_bait_agent.py
# Reads the single .xlsx in ./input, routes each row to Men/Women boards by gender,
# skips duplicates by ID (from Timestamp, stored in description as "ID: <unix>") across BOTH boards,
# and (in non-dry-run) creates a card with:
# - IG formatted as clickable Instagram link(s)
# - Phone included (auto-detected phone column)
# - Submission date from Timestamp (formatted M/D/YYYY in America/Chicago)
# - ID as unix timestamp seconds in America/Chicago
#
# DRY_RUN is ON: it will NOT create cards, it will only print what it would do.

import os
import time
import glob
import re
import requests
import pandas as pd
from zoneinfo import ZoneInfo

API_BASE = "https://api.trello.com/1"

MEN_BOARD_SHORTLINK = "OLSdLzxK"
WOMEN_BOARD_SHORTLINK = "HdHx0FLI"
TARGET_LIST_NAME = "Applicants"

INPUT_DIR = "input"

DRY_RUN = False
CENTRAL_TZ = ZoneInfo("America/Chicago")

# Excel column names (known)
COL_NAME = "Name"
COL_IG = "Instagram"
COL_FROM = "Where are you from?"
COL_ABOUT = "What's your vibe?\n\nExample: “A golden retriever in human form”"
COL_LOOKING = "What kind of person are you looking for?"
COL_WHY = "Why do you want to be on Live Bait?"
COL_GENDER = "Guy or Girl?"
COL_TIMESTAMP = "Timestamp"

# Phone column is often named differently, we auto-detect it.
PHONE_COL_HINTS = [
    "phone",
    "phone number",
    "phonenumber",
    "mobile",
    "cell",
    "cell phone",
    "telephone",
]


def trello_get(path, params):
    url = f"{API_BASE}{path}"
    while True:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "2"))
            time.sleep(retry_after)
            continue
        r.raise_for_status()
        return r.json()


def trello_post(path, params):
    url = f"{API_BASE}{path}"
    while True:
        r = requests.post(url, params=params, timeout=30)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "2"))
            time.sleep(retry_after)
            continue
        r.raise_for_status()
        return r.json()


def safe_str(x) -> str:
    try:
        if x is None or pd.isna(x):
            return ""
    except Exception:
        if x is None:
            return ""
    return str(x).strip()


def detect_phone_column(df: pd.DataFrame) -> str | None:
    """
    Find the most likely phone column by header name, case-insensitive.
    Returns column name or None.
    """
    lowered = {c: str(c).strip().lower() for c in df.columns}
    # Prefer exact-ish matches first
    for hint in PHONE_COL_HINTS:
        for col, col_l in lowered.items():
            if col_l == hint:
                return col
    # Then contains matches
    for hint in PHONE_COL_HINTS:
        for col, col_l in lowered.items():
            if hint in col_l:
                return col
    return None


def norm_handle(raw: str) -> str:
    """
    Extract a single Instagram handle from:
      @handle
      handle
      instagram.com/handle
      https://www.instagram.com/handle/
    Returns "" if not plausible.
    """
    s = safe_str(raw)
    if not s:
        return ""

    m = re.search(r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]+)", s, re.IGNORECASE)
    if m:
        return m.group(1).strip(" @/")

    if s.startswith("@"):
        s = s[1:].strip()

    s = s.strip().strip("/").strip()

    if re.fullmatch(r"[A-Za-z0-9._]{1,30}", s):
        return s

    return ""


def norm_handles(raw_value: str) -> list[str]:
    """
    Extract multiple Instagram handles separated by commas OR whitespace.
    Dedupes (case-insensitive).
    """
    s = safe_str(raw_value)
    if not s:
        return []

    parts = re.split(r"[,\s]+", s)
    out: list[str] = []
    seen = set()

    for p in parts:
        if not p:
            continue
        h = norm_handle(p)
        if not h:
            continue
        k = h.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(h)

    return out


def ig_markdown(raw_ig: str) -> str:
    """
    Returns IG line content as markdown links:
      [@handle](https://www.instagram.com/handle), [@handle2](...)
    If no valid handles, returns the original trimmed value (may be blank).
    """
    handles = norm_handles(raw_ig)
    if not handles:
        return safe_str(raw_ig)
    return ", ".join([f"[@{h}](https://www.instagram.com/{h})" for h in handles])


def timestamp_to_submission_and_id(ts_value):
    """
    Uses the Excel Timestamp field.
    Converts to America/Chicago.
    Returns (submission_date_str, unix_seconds_id)

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
    unix_seconds_id = int(dt.timestamp())
    return submission_date_str, unix_seconds_id


def build_desc(row, phone_col: str | None) -> str:
    name = safe_str(row.get(COL_NAME))
    ig = ig_markdown(row.get(COL_IG))
    from_where = safe_str(row.get(COL_FROM))
    about = safe_str(row.get(COL_ABOUT))
    looking = safe_str(row.get(COL_LOOKING))
    why = safe_str(row.get(COL_WHY))
    phone = safe_str(row.get(phone_col)) if phone_col else ""

    submission_date_str, unix_id = timestamp_to_submission_and_id(row.get(COL_TIMESTAMP))

    return (
        f"Name: {name}\n"
        f"IG: {ig}\n"
        f"Phone: {phone}\n"
        f"From: {from_where}\n"
        f"About: {about}\n"
        f"Looking for: {looking}\n"
        f"Why: {why}\n"
        f"Submission date: {submission_date_str if submission_date_str else ''}\n"
        f"ID: {unix_id if unix_id is not None else ''}\n"
    )


def get_board_id(shortlink: str, auth: dict) -> str:
    return trello_get(f"/boards/{shortlink}", {**auth, "fields": "id"})["id"]


def get_list_id_by_name(board_id: str, list_name: str, auth: dict) -> str:
    lists = trello_get(f"/boards/{board_id}/lists", {**auth, "fields": "name"})
    for lst in lists:
        if safe_str(lst.get("name")).lower() == list_name.strip().lower():
            return lst["id"]
    raise SystemExit(f'Could not find a list named "{list_name}" on board {board_id}')


def extract_id_from_desc(desc: str) -> int | None:
    """
    Finds the first ID line in the description:
      ID: 123456
    Returns int or None.
    """
    if not desc:
        return None
    m = re.search(r"^ID:\s*(\d+)\s*$", desc, re.MULTILINE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def get_all_ids(board_id: str, auth: dict) -> dict[int, list[tuple[str, str, str]]]:
    """
    Returns dict: id_int -> list of (card_name, card_id, board_id)
    Pulls card desc to find IDs.
    """
    cards = trello_get(f"/boards/{board_id}/cards", {**auth, "fields": "name,desc"})
    out: dict[int, list[tuple[str, str, str]]] = {}
    for c in cards:
        nm = safe_str(c.get("name"))
        cid = safe_str(c.get("id"))
        desc = c.get("desc") or ""
        found_id = extract_id_from_desc(desc)
        if found_id is None:
            continue
        out.setdefault(found_id, []).append((nm, cid, board_id))
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

    # Validate required columns (Name is needed to create card name; Gender routes; Timestamp generates ID)
    for required in [COL_NAME, COL_GENDER, COL_TIMESTAMP]:
        if required not in df.columns:
            raise SystemExit(f"Missing required column: {required}")

    phone_col = detect_phone_column(df)
    if phone_col:
        print(f"Detected phone column: {phone_col}")
    else:
        print("No phone column detected. Phone will be left blank on created cards.")

    men_board_id = get_board_id(MEN_BOARD_SHORTLINK, auth)
    women_board_id = get_board_id(WOMEN_BOARD_SHORTLINK, auth)

    men_list_id = get_list_id_by_name(men_board_id, TARGET_LIST_NAME, auth)
    women_list_id = get_list_id_by_name(women_board_id, TARGET_LIST_NAME, auth)

    # Global duplicate map across both boards (by ID in description ONLY)
    print("Scanning existing card descriptions for IDs on Men board")
    men_ids = get_all_ids(men_board_id, auth)

    print("Scanning existing card descriptions for IDs on Women board")
    women_ids = get_all_ids(women_board_id, auth)

    global_ids: dict[int, list[tuple[str, str, str]]] = {}
    for d in [men_ids, women_ids]:
        for k, v in d.items():
            global_ids.setdefault(k, []).extend(v)

    created = 0
    dryrun_would_create = 0

    skipped_duplicates_by_id: list[tuple[str, int, list[str]]] = []
    skipped_bad_rows: list[tuple[int, str]] = []
    errors: list[tuple[str, str]] = []

    for i, row in df.iterrows():
        name = safe_str(row.get(COL_NAME, ""))
        if not name:
            skipped_bad_rows.append((i, "Missing name"))
            continue

        raw_gender = row.get(COL_GENDER, "")
        gender = safe_str(raw_gender).lower()
        if gender not in ["guy", "girl"]:
            skipped_bad_rows.append((i, f'Invalid gender "{raw_gender}"'))
            continue

        submission_date_str, unix_id = timestamp_to_submission_and_id(row.get(COL_TIMESTAMP))
        if submission_date_str is None or unix_id is None:
            skipped_bad_rows.append((i, "Missing or invalid Timestamp"))
            continue

        # Duplicate check across both boards by ID ONLY
        if unix_id in global_ids:
            found = global_ids[unix_id]
            found_locations = [
                f"board_id={b_id}, card_name={card_name}, card_id={cid}"
                for (card_name, cid, b_id) in found
            ]
            skipped_duplicates_by_id.append((name, unix_id, found_locations))
            continue

        desc = build_desc(row, phone_col)
        target_list_id = men_list_id if gender == "guy" else women_list_id
        target_board_id = men_board_id if gender == "guy" else women_board_id

        if DRY_RUN:
            dryrun_would_create += 1
            print("\n[DRY RUN] Would create card")
            print(f"  Name: {name}")
            print(f"  Gender: {gender}")
            print(f"  Board ID: {target_board_id}")
            print(f"  List ID: {target_list_id}")
            print(f"  Submission date: {submission_date_str}")
            print(f"  ID: {unix_id}")

            # Update in-memory IDs so we also catch duplicates within the same run
            global_ids.setdefault(unix_id, []).append((name, "", target_board_id))
            continue

        try:
            trello_post(
                "/cards",
                {
                    **auth,
                    "idList": target_list_id,
                    "name": name,
                    "desc": desc,
                },
            )
            created += 1

            # Track created ID in-memory
            global_ids.setdefault(unix_id, []).append((name, "", target_board_id))
        except Exception as e:
            errors.append((name, str(e)))

    print("\nDone.")
    print(f"Dry run: {DRY_RUN}")
    print(f"Would create: {dryrun_would_create}")
    print(f"Created: {created}")

    print(f"Skipped duplicates by ID: {len(skipped_duplicates_by_id)}")
    if skipped_duplicates_by_id:
        print("Duplicate IDs found (skipped):")
        for nm, uid, locs in skipped_duplicates_by_id:
            print(f"  - {nm} (ID: {uid})")
            for loc in locs:
                print(f"      {loc}")

    print(f"Skipped bad rows: {len(skipped_bad_rows)}")
    if skipped_bad_rows:
        print("Bad rows (skipped):")
        for idx, reason in skipped_bad_rows:
            print(f"  - Row {idx + 2}: {reason}")

    print(f"Errors: {len(errors)}")
    if errors:
        print("Errors:")
        for nm, msg in errors:
            print(f"  - {nm}: {msg}")


if __name__ == "__main__":
    main()