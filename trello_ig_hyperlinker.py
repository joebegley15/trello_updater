# trello_ig_hyperlinker.py
# Converts "IG:" lines in Trello card descriptions (both boards) into Instagram hyperlinks.
# Supports multiple IGs separated by commas and/or whitespace.
# Idempotent: cards already in canonical format are left unchanged.

import os
import re
import time
import requests

API_BASE = "https://api.trello.com/1"

MEN_BOARD_SHORTLINK = "OLSdLzxK"
WOMEN_BOARD_SHORTLINK = "HdHx0FLI"

# Set to True to preview changes without writing to Trello
DRY_RUN = False

def load_env_file(path=".env"):
    """
    Load KEY=VALUE pairs from a .env file into os.environ
    without overwriting variables already set in the shell.
    """
    if not os.path.exists(path):
        return

    with open(path, "r") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key not in os.environ:
                os.environ[key] = value

load_env_file()

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


def trello_put(path, params):
    url = f"{API_BASE}{path}"
    while True:
        r = requests.put(url, params=params, timeout=30)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "2"))
            time.sleep(retry_after)
            continue
        r.raise_for_status()
        return r.json()


def norm_handle(raw: str) -> str:
    """
    Extract a single Instagram handle from:
      @handle
      handle
      instagram.com/handle
      https://www.instagram.com/handle/
    Returns "" if not a plausible handle.
    """
    if raw is None:
        return ""

    s = str(raw).strip()
    if not s:
        return ""

    # Pull handle from URL if present
    m = re.search(
        r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]+)",
        s,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(" @/")

    # Strip leading @
    if s.startswith("@"):
        s = s[1:].strip()

    # Trim common trailing junk
    s = s.strip().strip("/").strip()

    # IG handle rules: 1–30 chars, letters/numbers/._ only
    if re.fullmatch(r"[A-Za-z0-9._]{1,30}", s):
        return s

    return ""


def norm_handles(raw_value: str) -> list[str]:
    """
    Extract multiple Instagram handles separated by commas OR whitespace.
    Ignores duplicates and invalid tokens.
    """
    if raw_value is None:
        return []

    s = str(raw_value).strip()
    if not s:
        return []

    # Split on commas OR whitespace
    parts = re.split(r"[,\s]+", s)

    handles: list[str] = []
    seen = set()

    for p in parts:
        if not p:
            continue

        h = norm_handle(p)
        if not h:
            continue

        key = h.lower()
        if key in seen:
            continue

        seen.add(key)
        handles.append(h)

    return handles


def convert_ig_line(line: str) -> str:
    """
    Convert an IG line to canonical Trello markdown:
      IG: [@handle](https://www.instagram.com/handle), [@handle2](...)
    Returns original line if no change needed or no valid handles.
    """
    if not line.strip().lower().startswith("ig:"):
        return line

    raw_value = line.split(":", 1)[1].strip() if ":" in line else ""
    handles = norm_handles(raw_value)

    if not handles:
        # Keep as "IG:" if blank; otherwise leave weird values untouched
        if raw_value == "":
            return "IG:"
        return line

    links = ", ".join([f"[@{h}](https://www.instagram.com/{h})" for h in handles])
    desired = f"IG: {links}"

    # Idempotency: if already canonical, don't change
    if line.strip() == desired:
        return line

    return desired


def update_desc(desc: str) -> tuple[str, bool]:
    """
    Finds the first line that begins with "IG:" (case-insensitive),
    replaces it with the canonical hyperlink format, and returns (new_desc, changed).
    """
    if desc is None:
        desc = ""

    lines = desc.splitlines()
    changed = False

    for i, line in enumerate(lines):
        if line.strip().lower().startswith("ig:"):
            new_line = convert_ig_line(line)
            if new_line != line:
                lines[i] = new_line
                changed = True
            # Only update the first IG line
            break

    return ("\n".join(lines), changed)


def get_board_id(shortlink: str, auth: dict) -> str:
    return trello_get(f"/boards/{shortlink}", {**auth, "fields": "id"})["id"]


def process_board(board_shortlink: str, auth: dict) -> dict:
    board_id = get_board_id(board_shortlink, auth)
    cards = trello_get(f"/boards/{board_id}/cards", {**auth, "fields": "name,desc"})

    changed_cards = 0
    skipped_cards = 0
    updated_cards = 0
    errors = []

    for c in cards:
        card_id = c.get("id")
        name = c.get("name", "(no name)")
        desc = c.get("desc", "")

        new_desc, changed = update_desc(desc)

        if not changed:
            skipped_cards += 1
            continue

        changed_cards += 1

        if DRY_RUN:
            print(f"[DRY RUN] Would update: {name}")
            continue

        try:
            trello_put(f"/cards/{card_id}", {**auth, "desc": new_desc})
            updated_cards += 1
            print(f"Updated: {name}")
        except Exception as e:
            errors.append((name, str(e)))

    return {
        "board_shortlink": board_shortlink,
        "changed_cards": changed_cards,
        "skipped_cards": skipped_cards,
        "updated_cards": updated_cards,
        "errors": errors,
    }


def main():
    key = os.environ.get("TRELLO_API_KEY", "").strip()
    token = os.environ.get("TRELLO_TOKEN", "").strip()
    if not key or not token:
        raise SystemExit("Missing credentials. Set environment variables TRELLO_API_KEY and TRELLO_TOKEN.")

    auth = {"key": key, "token": token}

    print("Starting IG hyperlink conversion.")
    print(f"DRY_RUN is set to {DRY_RUN}")

    men_result = process_board(MEN_BOARD_SHORTLINK, auth)
    women_result = process_board(WOMEN_BOARD_SHORTLINK, auth)

    for r in [men_result, women_result]:
        print("")
        print(f"Board {r['board_shortlink']}")
        print(f"Cards needing change: {r['changed_cards']}")
        print(f"Cards skipped: {r['skipped_cards']}")
        print(f"Cards updated: {r['updated_cards']}")
        print(f"Errors: {len(r['errors'])}")
        if r["errors"]:
            for nm, msg in r["errors"]:
                print(f"  - {nm}: {msg}")

    if DRY_RUN:
        print("")
        print("Dry run complete. Set DRY_RUN = False to apply changes.")


if __name__ == "__main__":
    main()
