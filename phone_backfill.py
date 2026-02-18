# trello_phone_backfill.py
# Uses the single .xlsx in ./input to add/overwrite Phone numbers on EXISTING Trello cards
# across BOTH boards (Men + Women), matching by card name == Excel "Name" (case-insensitive).
#
# Behavior:
# - Finds matching cards across both boards
# - Updates the card description so it contains a "Phone:" line (adds it if missing)
# - If Phone is already correct, it skips (idempotent)
# - If a name matches multiple cards, it reports and skips that name (safe)
#
# Requires env vars:
#   TRELLO_API_KEY
#   TRELLO_TOKEN

import os
import time
import glob
import re
import requests
import pandas as pd

API_BASE = "https://api.trello.com/1"

MEN_BOARD_SHORTLINK = "OLSdLzxK"
WOMEN_BOARD_SHORTLINK = "HdHx0FLI"

INPUT_DIR = "input"

# Excel columns
COL_NAME = "Name"
COL_GENDER = "Guy or Girl?"  # not required for this script, but usually present

# Phone column detection (auto)
PHONE_COL_HINTS = [
    "phone",
    "phone number",
    "phonenumber",
    "mobile",
    "cell",
    "cell phone",
    "telephone",
]

# Safety
DRY_RUN = False  # set to False to apply changes


def trello_get(path, params):
    url = f"{API_BASE}{path}"
    while True:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "2")))
            continue
        r.raise_for_status()
        return r.json()


def trello_put(path, params):
    url = f"{API_BASE}{path}"
    while True:
        r = requests.put(url, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "2")))
            continue
        r.raise_for_status()
        return r.json()


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


def detect_phone_column(df: pd.DataFrame) -> str | None:
    lowered = {c: safe_str(c).lower() for c in df.columns}

    # exact match first
    for hint in PHONE_COL_HINTS:
        for col, col_l in lowered.items():
            if col_l == hint:
                return col

    # contains match
    for hint in PHONE_COL_HINTS:
        for col, col_l in lowered.items():
            if hint in col_l:
                return col

    return None


def get_board_id(shortlink: str, auth: dict) -> str:
    return trello_get(f"/boards/{shortlink}", {**auth, "fields": "id"})["id"]


def fetch_cards_by_name(board_id: str, auth: dict) -> dict:
    """
    Returns mapping: normalized_card_name -> list of dicts {id,name,desc,board_id}
    """
    cards = trello_get(f"/boards/{board_id}/cards", {**auth, "fields": "name,desc"})
    out: dict[str, list[dict]] = {}
    for c in cards:
        name = safe_str(c.get("name"))
        if not name:
            continue
        key = norm_name(name)
        out.setdefault(key, []).append(
            {"id": safe_str(c.get("id")), "name": name, "desc": safe_str(c.get("desc")), "board_id": board_id}
        )
    return out


PHONE_LINE_RE = re.compile(r"^phone\s*:\s*(.*)$", re.IGNORECASE)


def upsert_phone_line(desc: str, phone_value: str) -> tuple[str, bool]:
    """
    Ensure there's a line like: "Phone: <value>"
    - If a Phone line exists, replace its value
    - If none exists, insert it right after the IG line if present, else after Name line if present, else at top
    Returns (new_desc, changed)
    """
    desc = desc or ""
    lines = desc.splitlines()

    # If phone_value empty, we do NOT blank out existing phone lines by default.
    # We simply skip changes.
    if not safe_str(phone_value):
        return desc, False

    desired_line = f"Phone: {safe_str(phone_value)}"

    # 1) Replace existing Phone line if present
    for i, line in enumerate(lines):
        m = PHONE_LINE_RE.match(line.strip())
        if m:
            if line.strip() == desired_line:
                return desc, False
            lines[i] = desired_line
            return "\n".join(lines), True

    # 2) Insert Phone line (prefer after IG, else after Name, else at top)
    insert_at = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("ig:"):
            insert_at = i + 1
            break
    if insert_at is None:
        for i, line in enumerate(lines):
            if line.strip().lower().startswith("name:"):
                insert_at = i + 1
                break
    if insert_at is None:
        insert_at = 0

    lines.insert(insert_at, desired_line)
    return "\n".join(lines), True


def main():
    key = safe_str(os.environ.get("TRELLO_API_KEY"))
    token = safe_str(os.environ.get("TRELLO_TOKEN"))
    if not key or not token:
        raise SystemExit("Missing credentials. Set TRELLO_API_KEY and TRELLO_TOKEN.")

    auth = {"key": key, "token": token}

    excel_files = glob.glob(f"{INPUT_DIR}/*.xlsx")
    if len(excel_files) != 1:
        raise SystemExit(f"Expected exactly 1 Excel file in '{INPUT_DIR}/', found {len(excel_files)}")
    excel_path = excel_files[0]

    df = pd.read_excel(excel_path)

    if COL_NAME not in df.columns:
        raise SystemExit(f"Missing required column: {COL_NAME}")

    phone_col = detect_phone_column(df)
    if not phone_col:
        raise SystemExit(
            "Could not detect a phone column in your Excel sheet. "
            "Rename it to include 'phone' (e.g., 'Phone' or 'Phone Number') or hardcode it in the script."
        )

    print(f"Using Excel: {excel_path}")
    print(f"Detected phone column: {phone_col}")
    print(f"DRY_RUN is set to {DRY_RUN}")

    # Build a name->phone map from Excel (last non-empty wins)
    excel_phone_map: dict[str, str] = {}
    for _, row in df.iterrows():
        name = safe_str(row.get(COL_NAME))
        phone = safe_str(row.get(phone_col))
        if not name or not phone:
            continue
        excel_phone_map[norm_name(name)] = phone

    if not excel_phone_map:
        raise SystemExit("No (Name, Phone) pairs found in Excel (phone values were empty?).")

    # Fetch cards from both boards and merge
    men_board_id = get_board_id(MEN_BOARD_SHORTLINK, auth)
    women_board_id = get_board_id(WOMEN_BOARD_SHORTLINK, auth)

    men_cards = fetch_cards_by_name(men_board_id, auth)
    women_cards = fetch_cards_by_name(women_board_id, auth)

    all_cards: dict[str, list[dict]] = {}
    for d in (men_cards, women_cards):
        for k, v in d.items():
            all_cards.setdefault(k, []).extend(v)

    updated = 0
    skipped_no_match = 0
    skipped_ambiguous = 0
    skipped_no_phone = 0
    unchanged = 0
    errors: list[tuple[str, str]] = []

    for name_key, phone in excel_phone_map.items():
        if name_key not in all_cards:
            skipped_no_match += 1
            continue

        matches = all_cards[name_key]
        if len(matches) != 1:
            # Safety: if multiple cards share the same name across boards/lists, do nothing.
            skipped_ambiguous += 1
            continue

        card = matches[0]
        new_desc, changed = upsert_phone_line(card["desc"], phone)

        if not safe_str(phone):
            skipped_no_phone += 1
            continue

        if not changed:
            unchanged += 1
            continue

        if DRY_RUN:
            print(f"[DRY RUN] Would update phone for: {card['name']}")
            continue

        try:
            trello_put(f"/cards/{card['id']}", {**auth, "desc": new_desc})
            updated += 1
            print(f"Updated phone for: {card['name']}")
        except Exception as e:
            errors.append((card["name"], str(e)))

    print("\nDone.")
    print(f"Updated: {updated}")
    print(f"Unchanged (already correct): {unchanged}")
    print(f"Skipped (no matching card): {skipped_no_match}")
    print(f"Skipped (ambiguous name; multiple cards): {skipped_ambiguous}")
    print(f"Skipped (no phone in Excel): {skipped_no_phone}")
    print(f"Errors: {len(errors)}")
    if errors:
        for nm, msg in errors:
            print(f"  - {nm}: {msg}")

    if DRY_RUN:
        print("\nDry run complete. Set DRY_RUN = False to apply changes.")


if __name__ == "__main__":
    main()
