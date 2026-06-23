#!/usr/bin/env python3
"""Merge a phone GPS track into a MARWIS log, joining on UTC timestamp.

The desktop MARWIS tools can't read GPS (the laptop has none), so position is
logged separately on a phone and the two are joined afterward on their UTC
timestamps. The GPS track is sparser than MARWIS polling, so GPS fixes are
linearly interpolated onto each MARWIS poll time. Output is a single wide,
GIS-ready CSV: one row per poll, position + motion columns, then one column per
MARWIS channel.

Usage:
    python merge_gps.py --marwis run.csv --gps "Location GPS ....zip"

    --out PATH        output CSV (default: <marwis>_geo.csv next to the input)
    --clock-offset S  seconds added to every GPS time, to correct a constant
                      phone-vs-laptop clock skew (default 0)
    --max-gap S       don't interpolate across GPS sampling gaps longer than
                      this many seconds; such polls get blank position (default 5)

--gps accepts a Sensor Logger ".zip" export (read in-memory) or an already
extracted "Raw Data.csv" (a sibling meta/time.csv must supply the START epoch).

Caveats:
  - Join accuracy depends on the phone and laptop clocks being in sync;
    --clock-offset is the lever for a constant skew.
  - Linear interpolation of lat/lon is fine at these scales/speeds. Direction
    is angular (wraps 0/360) so it uses the nearest sample, not interpolation.
"""

import argparse
import bisect
import csv
import math
import os
import sys
import zipfile
from datetime import datetime

# GPS fields carried onto each MARWIS row: output column -> source header in
# Sensor Logger's "Raw Data.csv". Direction is handled specially (angular).
GPS_FIELDS = [
    ("lat", "Latitude (°)"),
    ("lon", "Longitude (°)"),
    ("altitude_m", "Altitude (m)"),
    ("speed_mps", "Speed (m/s)"),
    ("direction_deg", "Direction (°)"),
    ("horiz_acc_m", "Horizontal Accuracy (m)"),
    ("satellites", "Satellites"),
]
ANGULAR = {"direction_deg"}  # nearest-sample, not linear


def _to_float(s):
    """Parse a possibly-scientific-notation cell; '' / 'nan' -> NaN."""
    s = (s or "").strip()
    if not s:
        return math.nan
    try:
        return float(s)
    except ValueError:
        return math.nan


def load_gps(path, clock_offset):
    """Return (times, series): absolute epoch seconds + {field: [values]}.

    Reads a Sensor Logger .zip or a bare Raw Data.csv (with sibling meta/time.csv).
    Absolute UTC = START system epoch + relative Time(s) + clock_offset.
    """
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            raw = z.read("Raw Data.csv").decode("utf-8")
            try:
                meta = z.read("meta/time.csv").decode("utf-8")
            except KeyError:
                sys.exit("GPS zip is missing meta/time.csv (the START time anchor)")
    else:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        meta_path = os.path.join(os.path.dirname(path), "meta", "time.csv")
        if not os.path.exists(meta_path):
            sys.exit(f"need {meta_path} for the START time anchor "
                     "(or pass the original Sensor Logger .zip)")
        with open(meta_path, encoding="utf-8") as f:
            meta = f.read()

    start_epoch = _read_start_epoch(meta)

    rdr = csv.DictReader(raw.splitlines())
    missing = [h for _, h in GPS_FIELDS + [("t", "Time (s)")] if h not in rdr.fieldnames]
    if missing:
        sys.exit(f"GPS Raw Data.csv missing expected column(s): {missing}")

    times = []
    series = {name: [] for name, _ in GPS_FIELDS}
    for row in rdr:
        times.append(start_epoch + float(row["Time (s)"]) + clock_offset)
        for name, header in GPS_FIELDS:
            series[name].append(_to_float(row[header]))
    if not times:
        sys.exit("GPS track has no data rows")
    return times, series


def _read_start_epoch(meta_text):
    """Unix epoch (UTC) of the START event from meta/time.csv."""
    for row in csv.DictReader(meta_text.splitlines()):
        if row.get("event") == "START":
            return float(row["system time"])
    sys.exit("meta/time.csv has no START event")


def _valid_series(times, values):
    """Time/value pairs with NaN values dropped, as parallel sorted lists."""
    vt, vv = [], []
    for t, v in zip(times, values):
        if not math.isnan(v):
            vt.append(t)
            vv.append(v)
    return vt, vv


def interpolate(query_t, times, values, max_gap, angular=False):
    """Value at query_t from (times, values); NaN if uncovered or gap too big."""
    if not times or query_t < times[0] or query_t > times[-1]:
        return math.nan
    i = bisect.bisect_left(times, query_t)
    if i < len(times) and times[i] == query_t:
        return values[i]
    lo, hi = i - 1, i  # times[lo] < query_t < times[hi]
    if times[hi] - times[lo] > max_gap:
        return math.nan
    if angular:
        # Pick the temporally nearer sample; angles wrap so linear is wrong.
        return values[lo] if (query_t - times[lo]) <= (times[hi] - query_t) else values[hi]
    frac = (query_t - times[lo]) / (times[hi] - times[lo])
    return values[lo] + frac * (values[hi] - values[lo])


def load_marwis(path):
    """Return (polls, channel_cols).

    polls: list of dicts {ts, epoch, values:{name->value}} in first-seen order.
    channel_cols: channel column names ordered by channel number.
    """
    by_ts = {}
    order = []
    chan_name = {}  # channel number -> column name
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = row["ts_utc"]
            if ts not in by_ts:
                epoch = datetime.fromisoformat(ts).timestamp()
                by_ts[ts] = {"ts": ts, "epoch": epoch, "values": {}}
                order.append(ts)
            ch = int(row["channel"])
            name = row["name"] or f"channel_{ch}"
            chan_name[ch] = name
            # Blank when the channel reported a fault or a NaN reading.
            v = (row["value"] or "").strip()
            if int(row["status"] or 0) != 0 or v.lower() == "nan":
                v = ""
            by_ts[ts]["values"][name] = v
    channel_cols = [chan_name[ch] for ch in sorted(chan_name)]
    return [by_ts[ts] for ts in order], channel_cols


def _fmt(v):
    return "" if (isinstance(v, float) and math.isnan(v)) else v


def main():
    ap = argparse.ArgumentParser(description="Merge a phone GPS track into a MARWIS log by UTC timestamp.")
    ap.add_argument("--marwis", required=True, help="MARWIS long-format CSV")
    ap.add_argument("--gps", required=True, help="Sensor Logger .zip or Raw Data.csv")
    ap.add_argument("--out", help="output wide CSV (default: <marwis>_geo.csv)")
    ap.add_argument("--clock-offset", type=float, default=0.0,
                    help="seconds added to GPS times (correct phone/laptop skew)")
    ap.add_argument("--max-gap", type=float, default=5.0,
                    help="max GPS gap (s) to interpolate across; else blank position")
    args = ap.parse_args()

    out_path = args.out or os.path.splitext(args.marwis)[0] + "_geo.csv"

    times, series = load_gps(args.gps, args.clock_offset)
    polls, channel_cols = load_marwis(args.marwis)

    # Pre-clean each GPS field once (drop NaNs) so interpolation skips gaps.
    clean = {name: _valid_series(times, series[name]) for name, _ in GPS_FIELDS}

    pos_cols = [name for name, _ in GPS_FIELDS]
    header = ["ts_utc"] + pos_cols + channel_cols

    georef = 0
    lats, lons = [], []
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for poll in polls:
            t = poll["epoch"]
            pos = {}
            for name in pos_cols:
                vt, vv = clean[name]
                pos[name] = interpolate(t, vt, vv, args.max_gap, angular=name in ANGULAR)
            if not math.isnan(pos["lat"]) and not math.isnan(pos["lon"]):
                georef += 1
                lats.append(pos["lat"])
                lons.append(pos["lon"])
            row = [poll["ts"]] + [_fmt(pos[name]) for name in pos_cols]
            row += [poll["values"].get(name, "") for name in channel_cols]
            w.writerow(row)

    print(f"wrote {out_path}")
    print(f"polls: {len(polls)}  georeferenced: {georef}  blank position: {len(polls) - georef}")
    if lats:
        print(f"lat {min(lats):.6f}..{max(lats):.6f}  lon {min(lons):.6f}..{max(lons):.6f}")
    print(f"columns: {len(pos_cols)} position + {len(channel_cols)} channels")


if __name__ == "__main__":
    main()
