#!/usr/bin/env python3
"""MARWIS live monitor — watch connection health, toggle data saving on demand.

Polls the sensor continuously and shows a live one-line health readout (link
up/down, round-trip latency, poll success rate, device/measurement status, and
the key readings). Saving to SQLite/CSV is OFF at start; press a key to toggle
it on and off without dropping the connection.

Keys (press in the console window):
    s   start / stop saving       q   quit       space   same as 's'

Usage:
    python marwis_monitor.py --port COM5
    python marwis_monitor.py --port COM5 --interval 0.5 --csv run1.csv
"""

import argparse
import csv
import datetime
import os
import sqlite3
import sys
import time

import serial  # pyserial

from marwis_logger import (
    CHANNELS, ROAD_CONDITION, CLASS_MARWIS, DEFAULT_CHANNELS,
    UmbError, SCHEMA, CSV_COLS, poll_channels, is_overrange,
)

try:
    import msvcrt  # Windows console keypress
except ImportError:
    msvcrt = None


# All six water-film channels: 600/605/610 = water film height (um/mil/mm),
# 601/606/611 = WFH on surface (um/mil/mm). Polled together to compare them live.
WFH_CHANNELS = [600, 605, 610, 601, 606, 611]
MONITOR_CHANNELS = list(dict.fromkeys(DEFAULT_CHANNELS + WFH_CHANNELS))

# Default output lands in the repo's data/ folder regardless of the working
# directory it's launched from (computed relative to this script, not the cwd).
DEFAULT_DB = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "marwis.sqlite"))


def fmt_dur(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def main():
    ap = argparse.ArgumentParser(description="Live MARWIS monitor with on-demand data saving.")
    ap.add_argument("--port", required=True, help="COM port of the MARWIS Bluetooth link, e.g. COM5")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate (ignored by BT SPP)")
    ap.add_argument("--device-id", type=int, default=1, help="UMB device ID (default 1 -> 0xA001)")
    ap.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds (default 1.0)")
    ap.add_argument("--channels", default=",".join(map(str, MONITOR_CHANNELS)),
                    help="comma-separated UMB channels (max 20)")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite output file (default {DEFAULT_DB})")
    ap.add_argument("--csv", default=None, help="optional CSV output file (appended)")
    ap.add_argument("--save-on-start", action="store_true", help="begin with saving already enabled")
    args = ap.parse_args()

    channels = [int(c) for c in args.channels.split(",") if c.strip()]
    if len(channels) > 20:
        sys.exit("UMB command 0x2F allows at most 20 channels per request")
    device_addr = (CLASS_MARWIS << 12) | args.device_id

    # Storage is opened lazily the first time saving is enabled.
    db = None
    csv_file = csv_writer = None

    def open_storage():
        nonlocal db, csv_file, csv_writer
        if db is None:
            parent = os.path.dirname(args.db)
            if parent:
                os.makedirs(parent, exist_ok=True)
            db = sqlite3.connect(args.db)
            db.executescript(SCHEMA)
        if args.csv and csv_file is None:
            new = not (os.path.exists(args.csv) and os.path.getsize(args.csv) > 0)
            csv_file = open(args.csv, "a", newline="", encoding="utf-8")
            csv_writer = csv.writer(csv_file)
            if new:
                csv_writer.writerow(CSV_COLS)

    # Health / session state
    prev_len = 0
    saving = False
    total_polls = ok_polls = 0
    consecutive_errors = 0
    last_latency_ms = None
    saved_rows = 0
    save_started = None
    session_start = time.monotonic()

    def event(msg: str):
        # Print a full-width line above the live status line.
        sys.stdout.write("\r" + " " * 100 + "\r" + msg + "\n")
        sys.stdout.flush()

    print(f"Monitoring MARWIS on {args.port} (addr {device_addr:#06x}), "
          f"{len(channels)} channels every {args.interval}s.")
    print(f"Saving target: {args.db}" + (f" + {args.csv}" if args.csv else ""))
    print("Keys:  s = start/stop saving   q = quit\n")

    if args.save_on_start:
        open_storage()
        saving = True
        save_started = time.monotonic()
        event("[saving ON]")

    ser = None
    try:
        while True:
            t0 = time.monotonic()
            results = None
            try:
                if ser is None:
                    ser = serial.Serial(args.port, args.baud, timeout=0.5)
                results = poll_channels(ser, device_addr, channels)
                last_latency_ms = (time.monotonic() - t0) * 1000.0
                if consecutive_errors:
                    event(f"[link restored after {consecutive_errors} error(s)]")
                consecutive_errors = 0
                ok_polls += 1
            except (serial.SerialException, UmbError, OSError) as e:
                consecutive_errors += 1
                if consecutive_errors == 1:
                    event(f"[link DOWN: {e}]")
                if ser is not None:
                    try:
                        ser.close()
                    except OSError:
                        pass
                    ser = None
            total_polls += 1

            shown = {}
            if results is not None:
                shown = {c: v for c, _s, v in results}
                if saving:
                    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
                    rows = []
                    for channel, status, value in results:
                        name, unit = CHANNELS.get(channel, (f"channel_{channel}", ""))
                        rows.append((ts, None, None, channel, name, unit, value, status))
                    db.executemany(
                        "INSERT INTO measurements (ts_utc, lat, lon, channel, name, unit, value, status)"
                        " VALUES (?,?,?,?,?,?,?,?)", rows)
                    db.commit()
                    if csv_writer:
                        csv_writer.writerows(rows)
                        csv_file.flush()
                    saved_rows += len(rows)

            # ---- build the live status line ----
            now = datetime.datetime.now().strftime("%H:%M:%S")
            rate = (ok_polls / total_polls * 100.0) if total_polls else 0.0
            if consecutive_errors == 0 and results is not None:
                link = f"LINK UP  {last_latency_ms:4.0f}ms"
                # device-reported Bluetooth link metrics (channels 4040/4041)
                rssi, quality = shown.get(4040), shown.get(4041)
                if rssi is not None:
                    link += f"  {rssi:.0f}dBm"
                if quality is not None:
                    link += f"/{quality:.0f}%"
            else:
                link = f"LINK DOWN (err {consecutive_errors})"
            health = f"polls {total_polls} ok {rate:3.0f}%"

            if saving:
                rec = f"REC ● {saved_rows} rows ({fmt_dur(time.monotonic() - save_started)})"
            else:
                rec = "idle (not saving)"

            if results is not None:
                dev = shown.get(4000)
                meas = shown.get(4001)
                dev_s = "OK" if dev == 0 else (f"{int(dev):#06x}" if dev is not None else "?")
                meas_s = "OK" if meas == 0 else (f"{int(meas):#06x}" if meas is not None else "?")
                cond = ROAD_CONDITION.get(int(shown[900]), "?") if shown.get(900) is not None else "-"
                nan = float('nan')

                def wfh(um_ch, mm_ch):  # show OVER-RANGE for the 6000um sentinel
                    if is_overrange(um_ch, shown.get(um_ch)):
                        return "OVER-RANGE"
                    return f"{shown.get(um_ch, nan):.0f}um ({shown.get(mm_ch, nan):.3f}mm)"

                readings = (
                    f"WFH={wfh(600, 610)}  surf={wfh(601, 611)}  "
                    f"cond={cond} fric={shown.get(820, nan):.2f}  dev={dev_s} meas={meas_s}")
            else:
                readings = "(no data)"

            line = f"[{now}] {link}  {health}  | {rec}  | {readings}"
            sys.stdout.write("\r" + line.ljust(prev_len))  # pad to clear any longer previous line
            sys.stdout.flush()
            prev_len = len(line)

            # ---- keyboard + interval wait ----
            while time.monotonic() - t0 < args.interval:
                key = None
                try:
                    if msvcrt and msvcrt.kbhit():
                        key = msvcrt.getch().lower()
                except OSError:
                    key = None  # no real console (e.g. redirected stdin)
                if key in (b"q", b"\x1b"):  # q or Esc
                    raise KeyboardInterrupt
                if key in (b"s", b" "):
                    saving = not saving
                    if saving:
                        open_storage()
                        save_started = time.monotonic()
                        event("[saving ON]")
                    else:
                        event(f"[saving OFF — {saved_rows} rows total]")
                time.sleep(0.02)
    except KeyboardInterrupt:
        event("\nStopped.")
    finally:
        if db is not None:
            db.close()
        if csv_file:
            csv_file.close()
        if ser is not None:
            ser.close()


if __name__ == "__main__":
    main()
