import os
import time
import glob
import requests
import pandas as pd

API_BASE = "https://api.trello.com/1"

MEN_BOARD_SHORTLINK = "OLSdLzxK"
GIRLS_BOARD_SHORTLINK = "HdHx0FLI"

TARGET_LIST_NAME = "Applicants"

INPUT_DIR = "input"
excel_files = glob.glob(f"{INPUT_DIR}/*.xlsx")
if len(excel_files) != 1:
    raise SystemExit(f"Expected exactly 1 Excel file in '{INPUT_DIR}/', found {len(excel_files)}")
EXCEL_PATH = excel_files[0]

# Excel column names
COL_NAME = "Name"
COL_IG = "Instagram"
COL_FROM = "Where are you from?"
COL_ABOUT = "What's your vibe?\n\nExample: “A golden retriever in human form”"
COL_LOOKING = "What kind of person are you looking for?"
COL_WHY = "Why do you want to be on Live Bait?"
COL_GENDER = "Guy or Girl?"

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

def norm_name(s: str) -> str:
    return " ".join(str(s).strip().lower().split())

def build_desc(row) -> str:
    def val(col):
        x = row.get(col, "")
        if pd.isna(x):
            return ""
        return str(x).strip()

    return (
        f"Name: {val(COL_NAME)}\n"
        f"IG: {val(COL_IG)}\n"
        f"From: {val(COL_FROM)}\n"
        f"About: {val(COL_ABOUT)}\n"
        f"Looking for: {val(COL_LOOKING)}\n"
        f"Why: {val(COL_WHY)}\n"
    )

def get_board_id(shortlink: str, auth: dict) -> str:
    return trello_get(f"/boards/{shortlink}", {**auth, "fields": "id"})["id"]

def get_list_id_by_name(board_id: str, list_name: str, auth: dict) -> str:
    lists = trello_get(f"/boards/{board_id}/lists", {**auth, "fields": "name"})
    for lst in lists:
        if lst.get("name", "").strip().lower() == list_name.strip().lower():
            return lst["id"]
    raise SystemExit(f'Could not find a list named "{list_name}" on board {board_id}')

def get_all_card_names(board_id: str, auth: dict) -> dict:
    """
    Returns dict: normalized_name -> list of (card_name, card_id, board_id)
    """
    cards = trello_get(f"/boards/{board_id}/cards", {**auth, "fields": "name"})
    out = {}
    for c in cards:
        nm = c.get("name", "")
        if not nm:
            continue
        key = norm_name(nm)
        out.setdefault(key, []).append((nm, c.get("id", ""), board_id))
    return out

def main():
    key = os.environ.get("TRELLO_API_KEY", "").strip()
    token = os.environ.get("TRELLO_TOKEN", "").strip()
    if not key or not token:
        raise SystemExit("Missing credentials. Set environment variables TRELLO_API_KEY and TRELLO_TOKEN.")

    auth = {"key": key, "token": token}

    # Load Excel
    df = pd.read_excel(EXCEL_PATH)
    for required in [COL_NAME, COL_GENDER]:
        if required not in df.columns:
            raise SystemExit(f"Missing required column: {required}")

    men_board_id = get_board_id(MEN_BOARD_SHORTLINK, auth)
    girls_board_id = get_board_id(GIRLS_BOARD_SHORTLINK, auth)

    men_list_id = get_list_id_by_name(men_board_id, TARGET_LIST_NAME, auth)
    girls_list_id = get_list_id_by_name(girls_board_id, TARGET_LIST_NAME, auth)

    # Build global duplicate map across both boards
    men_names = get_all_card_names(men_board_id, auth)
    girls_names = get_all_card_names(girls_board_id, auth)

    global_names = {}
    for d in [men_names, girls_names]:
        for k, v in d.items():
            global_names.setdefault(k, []).extend(v)

    created = 0
    skipped_duplicates = []   # (name, found_locations)
    skipped_bad_rows = []     # (row_index, reason)
    errors = []               # (name, error)

    for i, row in df.iterrows():
        raw_name = row.get(COL_NAME, "")
        if pd.isna(raw_name) or not str(raw_name).strip():
            skipped_bad_rows.append((i, "Missing name"))
            continue

        name = str(raw_name).strip()
        name_key = norm_name(name)

        raw_gender = row.get(COL_GENDER, "")
        gender = "" if pd.isna(raw_gender) else str(raw_gender).strip().lower()

        if gender not in ["guy", "girl"]:
            skipped_bad_rows.append((i, f'Invalid gender "{raw_gender}"'))
            continue

        # Duplicate check across both boards
        if name_key in global_names:
            found = global_names[name_key]
            found_locations = [f"board_id={b_id}, card_name={card_name}" for (card_name, _card_id, b_id) in found]
            skipped_duplicates.append((name, found_locations))
            continue

        desc = build_desc(row)
        target_list_id = men_list_id if gender == "guy" else girls_list_id

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
            global_names[name_key] = [(name, "", men_board_id if gender == "guy" else girls_board_id)]
        except Exception as e:
            errors.append((name, str(e)))

    print("\nDone.")
    print(f"Created: {created}")
    print(f"Skipped duplicates: {len(skipped_duplicates)}")
    if skipped_duplicates:
        print("Duplicate names found (skipped):")
        for nm, locs in skipped_duplicates:
            print(f"  - {nm}")
            for loc in locs:
                print(f"      {loc}")

    print(f"Skipped bad rows: {len(skipped_bad_rows)}")
    if skipped_bad_rows:
        print("Bad rows (skipped):")
        for idx, reason in skipped_bad_rows:
            print(f"  - Row {idx + 2}: {reason}")  # +2 accounts for header and 0-index

    print(f"Errors: {len(errors)}")
    if errors:
        print("Errors:")
        for nm, msg in errors:
            print(f"  - {nm}: {msg}")

if __name__ == "__main__":
    main()
