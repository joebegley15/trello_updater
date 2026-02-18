import os
import time
import requests
import pandas as pd
import glob

API_BASE = "https://api.trello.com/1"

BOARD_SHORTLINK = "OLSdLzxK"
TARGET_LIST_NAME = "Applicants"

INPUT_DIR = "input"
excel_files = glob.glob(f"{INPUT_DIR}/*.xlsx")

if len(excel_files) != 1:
    raise SystemExit(
        f"Expected exactly 1 Excel file in '{INPUT_DIR}/', found {len(excel_files)}"
    )

EXCEL_PATH = excel_files[0]


# Excel column names (confirmed)
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


def main():
    key = os.environ.get("TRELLO_API_KEY", "").strip()
    token = os.environ.get("TRELLO_TOKEN", "").strip()

    if not key or not token:
        raise SystemExit(
            "Missing credentials. Set environment variables TRELLO_API_KEY and TRELLO_TOKEN."
        )

    auth = {"key": key, "token": token}

    # 1) Load Excel and filter to Guy
    df = pd.read_excel(EXCEL_PATH)
    if COL_GENDER not in df.columns:
        raise SystemExit(f"Could not find column: {COL_GENDER}")

    df_guy = df[df[COL_GENDER].astype(str).str.strip().str.lower() == "guy"].copy()

    # 2) Resolve board id from shortlink
    board = trello_get(f"/boards/{BOARD_SHORTLINK}", {**auth, "fields": "name"})
    board_id = trello_get(f"/boards/{BOARD_SHORTLINK}", {**auth, "fields": "id"})["id"]

    # 3) Find Applicants list id by name
    lists = trello_get(f"/boards/{board_id}/lists", {**auth, "fields": "name"})
    target_list = None
    for lst in lists:
        if lst.get("name", "").strip().lower() == TARGET_LIST_NAME.strip().lower():
            target_list = lst
            break
    if not target_list:
        raise SystemExit(f'Could not find a list named "{TARGET_LIST_NAME}" on the board.')

    list_id = target_list["id"]

    # 4) Build a set of existing card names on the board (for duplicate skipping)
    cards = trello_get(f"/boards/{board_id}/cards", {**auth, "fields": "name"})
    existing_names = {c["name"].strip().lower() for c in cards if c.get("name")}

    created = 0
    skipped = []
    errors = []

    # 5) Create cards
    for _, row in df_guy.iterrows():
        name = str(row.get(COL_NAME, "")).strip()
        if not name:
            continue

        key_name = name.lower()
        if key_name in existing_names:
            skipped.append(name)
            continue

        desc = build_desc(row)
        try:
            trello_post(
                "/cards",
                {
                    **auth,
                    "idList": list_id,
                    "name": name,
                    "desc": desc,
                },
            )
            created += 1
            existing_names.add(key_name)
        except Exception as e:
            errors.append((name, str(e)))

    print("\nDone.")
    print(f"Created: {created}")
    print(f"Skipped (already existed): {len(skipped)}")
    if skipped:
        print("Skipped names:")
        for s in skipped:
            print(f"  - {s}")

    print(f"Errors: {len(errors)}")
    if errors:
        print("Errors:")
        for n, msg in errors:
            print(f"  - {n}: {msg}")


if __name__ == "__main__":
    main()
