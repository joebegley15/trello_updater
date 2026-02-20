import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

API_BASE = "https://api.trello.com/1"

MEN_BOARD_SHORTLINK = "OLSdLzxK"
WOMEN_BOARD_SHORTLINK = "HdHx0FLI"

LOCAL_TZ = ZoneInfo("America/Chicago")

# Safety switch: start True, then set False to apply
DRY_RUN = False

# Field labels in card descriptions
LABEL_SUBMISSION_DATE = "Submission date"
LABEL_ID = "ID"
LABEL_BACKDATED = "Backdated"  # optional, but useful for auditability


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


def get_board_id(shortlink: str, auth: dict) -> str:
    return trello_get(f"/boards/{shortlink}", {**auth, "fields": "id"})["id"]


def fetch_cards(board_id: str, auth: dict):
    # Get id, name, desc for each card on board
    return trello_get(f"/boards/{board_id}/cards", {**auth, "fields": "id,name,desc"})


def card_created_unix_from_card_id(card_id: str) -> int:
    """
    Trello card IDs are Mongo-style ObjectIds. The first 8 hex chars are unix seconds.
    """
    if not card_id or len(card_id) < 8:
        return -1
    try:
        return int(card_id[:8], 16)
    except ValueError:
        return -1


def unix_to_submission_date(unix_seconds: int) -> str:
    dt = datetime.fromtimestamp(unix_seconds, tz=LOCAL_TZ)
    return f"{dt.month}/{dt.day}/{dt.year}"


def has_line(desc: str, label: str) -> bool:
    if not desc:
        return False
    return re.search(rf"^{re.escape(label)}:\s*", desc, flags=re.MULTILINE) is not None


def extract_existing_id(desc: str) -> int:
    if not desc:
        return -1
    m = re.search(r"^ID:\s*(\d+)\s*$", desc, re.MULTILINE)
    return int(m.group(1)) if m else -1


def remove_existing_fields(desc: str) -> str:
    """
    Remove existing Submission date / ID / Backdated lines so we can re-add canonically.
    """
    if desc is None:
        desc = ""
    out = []
    for line in desc.splitlines():
        if re.match(r"^\s*Submission date:\s*", line, re.IGNORECASE):
            continue
        if re.match(r"^\s*ID:\s*\d+\s*$", line, re.IGNORECASE):
            continue
        if re.match(r"^\s*Backdated:\s*(Yes|No)\s*$", line, re.IGNORECASE):
            continue
        out.append(line)
    return "\n".join(out)


def insert_fields(desc: str, submission_date: str, submission_id: int, backdated: str = "Yes") -> str:
    """
    Insert fields right after the IG line if present; otherwise after Name line; otherwise at top.
    """
    base = remove_existing_fields(desc)
    lines = base.splitlines()

    block = [
        f"{LABEL_SUBMISSION_DATE}: {submission_date}" if submission_date else f"{LABEL_SUBMISSION_DATE}:",
        f"{LABEL_ID}: {submission_id}" if submission_id != -1 else f"{LABEL_ID}:",
        f"{LABEL_BACKDATED}: {backdated}",
    ]

    insert_at = 0
    for idx, line in enumerate(lines):
        if line.strip().lower().startswith("ig:"):
            insert_at = idx + 1
            break
    else:
        for idx, line in enumerate(lines):
            if line.strip().lower().startswith("name:"):
                insert_at = idx + 1
                break

    new_lines = lines[:insert_at] + block + lines[insert_at:]
    return "\n".join(new_lines).strip() + "\n"


def process_board(board_shortlink: str, auth: dict):
    board_id = get_board_id(board_shortlink, auth)
    cards = fetch_cards(board_id, auth)

    to_update = 0
    updated = 0
    skipped = 0
    errors = []

    for c in cards:
        card_id = c.get("id", "")
        name = c.get("name", "(no name)")
        desc = c.get("desc", "") or ""

        # If ID already exists, skip (do not overwrite)
        existing_id = extract_existing_id(desc)
        if existing_id != -1 and has_line(desc, LABEL_SUBMISSION_DATE):
            skipped += 1
            continue

        created_unix = card_created_unix_from_card_id(card_id)
        if created_unix == -1:
            skipped += 1
            continue

        submission_id = existing_id if existing_id != -1 else created_unix
        submission_date = unix_to_submission_date(submission_id)

        new_desc = insert_fields(desc, submission_date, submission_id, backdated="Yes")

        if new_desc == (desc.strip() + "\n"):
            skipped += 1
            continue

        to_update += 1

        if DRY_RUN:
            print(f"[DRY RUN] Would update: {name}")
            continue

        try:
            trello_put(f"/cards/{card_id}", {**auth, "desc": new_desc})
            updated += 1
            print(f"Updated: {name}")
        except Exception as e:
            errors.append((name, str(e)))

    return {"board": board_shortlink, "to_update": to_update, "updated": updated, "skipped": skipped, "errors": errors}


def main():
    key = os.environ.get("TRELLO_API_KEY", "").strip()
    token = os.environ.get("TRELLO_TOKEN", "").strip()
    if not key or not token:
        raise SystemExit("Missing credentials. Set environment variables TRELLO_API_KEY and TRELLO_TOKEN.")

    auth = {"key": key, "token": token}

    print("Starting ID + timestamp backfill for existing cards.")
    print(f"DRY_RUN is set to {DRY_RUN}\n")

    men = process_board(MEN_BOARD_SHORTLINK, auth)
    women = process_board(WOMEN_BOARD_SHORTLINK, auth)

    for r in (men, women):
        print("")
        print(f"Board {r['board']}")
        print(f"Cards needing update: {r['to_update']}")
        print(f"Cards updated: {r['updated']}")
        print(f"Cards skipped: {r['skipped']}")
        print(f"Errors: {len(r['errors'])}")
        if r["errors"]:
            for nm, msg in r["errors"]:
                print(f"  - {nm}: {msg}")

    if DRY_RUN:
        print("\nDry run complete. Set DRY_RUN = False to apply changes.")


if __name__ == "__main__":
    main()