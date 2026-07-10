# LubeLogger Helper Scripts

Small, dependency-free Python scripts for [LubeLogger](https://lubelogger.com). Each script talks to the LubeLogger API and previews its changes by default — nothing is written until you pass `--apply`.

---

## Requirements

- Python 3.7+ (standard library only)
- A LubeLogger instance and API key

Every script reads the same two environment variables:

```sh
export LUBELOGGER_BASE_URL=https://lubelogger.example.com
export LUBELOGGER_API_KEY=your-api-key
```

## Scripts

| Script | What it does |
| --- | --- |
| [`backfill_fuel_record_from_extrafield.py`](#backfill_fuel_record_from_extrafieldpy) | Copy a fuel-record extra field into a native field |

---

### `backfill_fuel_record_from_extrafield.py`

Copies a value from a fuel-record **extra field** into a **native field**. Useful when LubeLogger promotes something you'd been tracking manually — like state of charge on an EV — into a first-class field.

```sh
# Preview (dry run)
python3 backfill_fuel_record_from_extrafield.py \
    --vehicle-id 3 \
    --source-field "SOC Before Charging" \
    --target-field startingSoc \
    --min 0 --max 100

# Write the changes
python3 backfill_fuel_record_from_extrafield.py ... --apply
```

| Flag | Description |
| --- | --- |
| `--vehicle-id` | LubeLogger vehicle ID (a bad ID prints the vehicle list) |
| `--source-field` | Extra-field name to read, case-insensitive |
| `--target-field` | Native fuel-record field to write, e.g. `startingSoc` |
| `--year` / `--start-date` / `--end-date` | Date range to backfill (default: current year) |
| `--min` / `--max` | Reject parsed values outside this range |
| `--overwrite` | Replace existing values in the target field |
| `--apply` | Actually write — without it, the run is a preview |

Good to know:

- **Values are copied, not moved.** The extra field stays put; delete it from the LubeLogger UI once you're happy.
- **Free-text friendly.** `45%`, ` 45 `, and `45.0` all parse as `45`; unparseable values are skipped with a warning.
- **Safe by default.** Existing non-zero target values are never overwritten unless you pass `--overwrite`.
