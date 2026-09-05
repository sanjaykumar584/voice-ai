#!/usr/bin/env python3
"""Batch caller: read a customer CSV, place a Vobiz call per row, write results back.

Flow per row (1 call at a time):
  map row -> collections body -> POST /start -> poll GET /calls until ended
  -> write outcome / recording URL back into the same CSV (resume-safe).

Usage:
  python batch_caller.py --dry-run          # print what would be called, no calls
  python batch_caller.py --limit 2          # place 2 calls then stop
  python batch_caller.py --force            # allow calls outside 8AM-7PM IST

Needs server.py running and a Vobiz number (VOBIZ_PHONE_NUMBER in .env).
"""

import argparse
import csv
import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, time as dtime, timedelta, timezone

import requests
from dotenv import load_dotenv

from app.batch.mapper import (
    DEFAULT_CSV,
    DEFAULT_SERVER,
    RESULT_COLUMNS,
    CALLING_START,
    CALLING_END,
    default_csv,
    ist_now,
    within_calling_hours,
    to_int,
    to_iso,
    normalize_phone,
    row_to_body,
    derive_result,
)

# -------------------------------- CSV helpers ------------------------------- #


def load_csv(path: str) -> tuple[list[dict], list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def write_csv(path: str, fieldnames: list[str], rows: list[dict]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


# -------------------------------- call helpers ------------------------------ #


def place_call(server: str, phone: str, body: dict, from_number: str) -> str:
    resp = requests.post(
        f"{server}/start",
        json={"phone_number": phone, "body": body, "from_number": from_number},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("call_uuid", "")


def wait_for_call(server: str, call_uuid: str, timeout: float, poll_interval: float) -> dict:
    """Poll GET /calls until this call ends. Returns the call record or {status: timeout}."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{server}/calls", timeout=15)
            resp.raise_for_status()
            for c in resp.json().get("calls", []):
                if c.get("call_uuid") == call_uuid and c.get("status") in ("ended", "failed"):
                    return c
        except requests.RequestException:
            pass
        time.sleep(poll_interval)
    return {"call_uuid": call_uuid, "status": "timeout"}


# ----------------------------------- main ----------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=None, help="Input CSV (default: BATCH_INPUT_CSV env, else the Downloads path)")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Bot server base URL")
    parser.add_argument(
        "--from-number",
        default=os.getenv("VOBIZ_PHONE_NUMBER", ""),
        help="Caller-ID number (defaults to VOBIZ_PHONE_NUMBER from .env)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print mapped calls, place none")
    parser.add_argument("--limit", type=int, default=None, help="Max calls to place")
    parser.add_argument("--from", dest="from_index", type=int, default=0, help="Start at this 0-based row index")
    parser.add_argument("--force", action="store_true", help="Allow calls outside 8AM-7PM IST")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between calls")
    parser.add_argument("--timeout", type=float, default=600.0, help="Max seconds to wait for a call to finish")
    parser.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between status polls")
    args = parser.parse_args()
    args.csv = args.csv or default_csv()

    if not os.path.exists(args.csv):
        sys.exit(f"CSV not found: {args.csv}")

    rows, fieldnames = load_csv(args.csv)
    print(f"Loaded {len(rows)} rows from {args.csv}")

    for col in RESULT_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    if not args.dry_run:
        backup = f"{args.csv}.backup_{ist_now():%Y%m%d_%H%M%S}"
        shutil.copy2(args.csv, backup)
        print(f"Backup written to {backup}")

    if not args.force and not within_calling_hours(ist_now()):
        print(f"WARNING: outside calling hours ({CALLING_START:%H:%M}-{CALLING_END:%H:%M} IST). Use --force.")

    summary: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    placed = 0

    for idx, row in enumerate(rows):
        if idx < args.from_index:
            continue
        if args.limit is not None and placed >= args.limit:
            break

        phone = normalize_phone(row.get("phoneNo"))
        if not phone:
            skipped["no phone"] += 1
            print(f"[{idx}] SKIP: no phone number")
            continue
        if (row.get("outcome") or "").strip():
            skipped["already processed"] += 1
            continue
        if not args.force and not within_calling_hours(ist_now()):
            skipped["outside calling hours"] += 1
            print(f"[{idx}] SKIP: outside calling hours")
            continue

        body = row_to_body(row)
        if args.dry_run:
            print(f"[{idx}] {phone} -> {json.dumps(body, ensure_ascii=False)}")
            placed += 1
            continue

        print(f"[{idx}] Calling {phone} ({body.get('customer_name')})...")
        try:
            call_uuid = place_call(args.server, phone, body, args.from_number)
        except requests.RequestException as e:
            row.update(call_status="FAILED", outcome="", outcome_note=f"start error: {e}", recording="", called_at=ist_now().isoformat(timespec="seconds"), call_uuid="")
            summary["FAILED"] += 1
            print(f"[{idx}] FAILED to start call: {e}")
            write_csv(args.csv, fieldnames, rows)
            placed += 1
            time.sleep(args.delay)
            continue

        if not call_uuid or call_uuid == "unknown":
            row.update(call_status="FAILED", outcome="", outcome_note="no call_uuid from Vobiz", recording="", called_at=ist_now().isoformat(timespec="seconds"), call_uuid="")
            summary["FAILED"] += 1
            print(f"[{idx}] FAILED: no call_uuid from Vobiz")
            write_csv(args.csv, fieldnames, rows)
            placed += 1
            time.sleep(args.delay)
            continue

        rec = wait_for_call(args.server, call_uuid, args.timeout, args.poll_interval)
        call_status, outcome, note, recording = derive_result(rec)
        row.update(
            outcome=outcome,
            outcome_note=note,
            recording=recording,
            call_status=call_status,
            called_at=ist_now().isoformat(timespec="seconds"),
            call_uuid=call_uuid,
        )
        summary[f"{call_status}:{outcome}" if outcome else call_status] += 1
        print(f"[{idx}] {call_status} outcome={outcome!r} note={note!r} recording={recording}")
        write_csv(args.csv, fieldnames, rows)
        placed += 1
        time.sleep(args.delay)

    print("\n=== SUMMARY ===")
    if args.dry_run:
        print(f"Dry run: {placed} calls would be placed")
    else:
        for k, v in sorted(summary.items()):
            print(f"  {k}: {v}")
        for k, v in skipped.items():
            print(f"  skipped ({k}): {v}")
        print(f"Results written back to {args.csv}")


if __name__ == "__main__":
    main()
