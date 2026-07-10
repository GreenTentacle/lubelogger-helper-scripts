#!/usr/bin/env python3
"""Backfill a native LubeLogger fuel-record field from an extra field.

Values are copied, not moved: the extra field is left in place, so you can
delete it from the LubeLogger UI once you're happy with the result. Nothing
is written unless you pass --apply; the default run is a preview.

Needs Python 3.7+, standard library only.

Environment variables:
  LUBELOGGER_BASE_URL  base URL of your instance, e.g. https://lubelogger.example.com
  LUBELOGGER_API_KEY   API key, sent as the x-api-key header

Example (backfilling state of charge on an EV):
  export LUBELOGGER_BASE_URL=https://lubelogger.example.com
  export LUBELOGGER_API_KEY=...
  ./backfill_fuel_record_from_extrafield.py --vehicle-id 3 \
      --source-field "SOC Before Charging" --target-field startingSoc \
      --min 0 --max 100
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime

# PUT /api/vehicle/gasrecords/update replaces the whole record, not just the
# fields you send, so every field from the GET response has to be echoed back
# or the server blanks it (losing attachments, notes, etc.). BASE_PAYLOAD_FIELDS
# is the documented payload; NATIVE_PASSTHROUGH_FIELDS covers native fields
# added since (extend it as LubeLogger promotes more extra fields).
BASE_PAYLOAD_FIELDS = [
    "id", "date", "odometer", "fuelConsumed", "cost", "isFillToFull",
    "missedFuelUp", "notes", "tags", "extraFields", "files",
]
NATIVE_PASSTHROUGH_FIELDS = ["startingSoc", "endingSoc"]

NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


class ApiError(Exception):
    """An API call failed. Fatal unless the caller catches it."""


def api_request(base_url, api_key, method, endpoint, body=None):
    url = base_url.rstrip("/") + endpoint
    data = None
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise ApiError(f"{method} {endpoint} -> HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise ApiError(f"{method} {endpoint} -> {e.reason}")
    if not payload:
        return None
    return json.loads(payload)


def vehicle_label(v):
    parts = [str(v.get(k, "")).strip() for k in ("year", "make", "model")]
    label = " ".join(p for p in parts if p)
    return label or f"vehicle {v.get('id')}"


def resolve_vehicle(base_url, api_key, vehicle_id):
    vehicles = api_request(base_url, api_key, "GET", "/api/vehicles") or []
    for v in vehicles:
        if v.get("id") == vehicle_id:
            return v
    listing = "\n".join(f"  {v.get('id')}: {vehicle_label(v)}" for v in vehicles)
    sys.exit(f"error: no vehicle with id {vehicle_id}. Vehicles:\n{listing}")


def parse_record_date(raw):
    """Parse a record date from the API.

    The API formats dates using the server's culture setting, so the same
    endpoint can return ISO 8601, M/D/YYYY, or D.M.YYYY depending on the
    instance. Returns None for anything unrecognized.
    """
    if not raw:
        return None
    text = str(raw).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text.split(" ")[0], fmt).date()
        except ValueError:
            continue
    return None


def parse_cli_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {text!r}")


def extract_number(raw, lo, hi):
    """Pull the first number out of a field value ("45%" -> 45), or None.

    Extra-field values are free text, so tolerate units and other cruft.
    Values outside the optional [lo, hi] range also come back as None.
    """
    match = NUMBER_RE.search(str(raw))
    if not match:
        return None
    value = float(match.group(0))
    if lo is not None and value < lo:
        return None
    if hi is not None and value > hi:
        return None
    return int(value) if value == int(value) else value


def find_extra_field(record, name):
    for field in record.get("extraFields") or []:
        if str(field.get("name", "")).strip().lower() == name.strip().lower():
            return field.get("value")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Copy a LubeLogger extra field into a native fuel-record field.",
    )
    parser.add_argument("--source-field", required=True,
                        help="extra-field name to read (case-insensitive)")
    parser.add_argument("--target-field", required=True,
                        help="native fuel-record field to write, e.g. startingSoc")
    parser.add_argument("--vehicle-id", type=int, required=True,
                        help="LubeLogger vehicle id")
    parser.add_argument("--year", type=int, default=date.today().year,
                        help="year to backfill (default: current year)")
    parser.add_argument("--start-date", type=parse_cli_date,
                        help="inclusive lower bound, YYYY-MM-DD (default: Jan 1 of --year)")
    parser.add_argument("--end-date", type=parse_cli_date,
                        help="inclusive upper bound, YYYY-MM-DD (default: Dec 31 of --year)")
    parser.add_argument("--min", type=float, dest="lo",
                        help="reject parsed values below this")
    parser.add_argument("--max", type=float, dest="hi",
                        help="reject parsed values above this")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace a differing existing value in the target field")
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default is dry run)")
    args = parser.parse_args()

    base_url = os.environ.get("LUBELOGGER_BASE_URL")
    api_key = os.environ.get("LUBELOGGER_API_KEY")
    if not base_url:
        sys.exit("error: set LUBELOGGER_BASE_URL (e.g. https://lubelogger.example.com)")
    if not api_key:
        sys.exit("error: set LUBELOGGER_API_KEY")

    start = args.start_date or date(args.year, 1, 1)
    end = args.end_date or date(args.year, 12, 31)
    if start > end:
        sys.exit(f"error: start date {start} is after end date {end}")

    vehicle = resolve_vehicle(base_url, api_key, args.vehicle_id)
    vid = vehicle["id"]
    print(f"Vehicle: {vehicle_label(vehicle)} (id {vid})")
    print(f"Copying extra field {args.source_field!r} -> {args.target_field!r}, "
          f"{start} to {end}, {'APPLY' if args.apply else 'dry run'}")

    records = api_request(
        base_url, api_key, "GET",
        f"/api/vehicle/gasrecords?vehicleId={vid}",
    ) or []

    updated = skipped = warned = 0
    for record in records:
        rid = record.get("id")
        when = parse_record_date(record.get("date"))
        if when is None:
            print(f"  warn  #{rid}: unparseable date {record.get('date')!r}, skipping")
            warned += 1
            continue
        if not (start <= when <= end):
            continue

        raw = find_extra_field(record, args.source_field)
        if raw is None:
            skipped += 1
            continue
        value = extract_number(raw, args.lo, args.hi)
        if value is None:
            print(f"  warn  #{rid} {when}: can't parse {args.source_field!r} "
                  f"value {raw!r}, skipping")
            warned += 1
            continue

        existing = record.get(args.target_field)
        existing_num = extract_number(existing, None, None) if existing is not None else None
        if existing_num == value:
            print(f"  skip  #{rid} {when}: {args.target_field} already {value}")
            skipped += 1
            continue
        # The API reports unset numeric fields as 0, so 0 counts as empty here.
        if existing_num not in (None, 0) and not args.overwrite:
            print(f"  warn  #{rid} {when}: {args.target_field} is {existing!r}, "
                  f"parsed {value}; pass --overwrite to replace")
            warned += 1
            continue

        payload = {k: record.get(k) for k in BASE_PAYLOAD_FIELDS}
        for field in NATIVE_PASSTHROUGH_FIELDS:
            if field in record:
                payload[field] = record[field]
        payload[args.target_field] = value

        verb = "update" if args.apply else "would update"
        print(f"  {verb}  #{rid} {when}: {args.target_field} "
              f"{existing!r} -> {value} (from {raw!r})")
        if args.apply:
            try:
                api_request(base_url, api_key, "PUT",
                            "/api/vehicle/gasrecords/update", body=payload)
            except ApiError as e:
                print(f"  warn  #{rid} {when}: update failed: {e}")
                warned += 1
                continue
        updated += 1

    print(f"\n{'Updated' if args.apply else 'Would update'} {updated}, "
          f"skipped {skipped}, warnings {warned} "
          f"({len(records)} records fetched)")
    if updated and not args.apply:
        print("This was a dry run. Re-run with --apply to write.")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        sys.exit(f"error: {e}")
