"""Capture a real MARWIS request/response as a labelled fixture.

Polls every documented channel once and records the raw request frame, the raw
response frame, latency, and the decoded per-channel sub-frames to a JSON file.
These real frames feed the Android FakeTransport tests (no hardware needed later).

Usage:
    python capture.py --port COM5 --label dry-baseline
    python capture.py --port COM5 --label wet-surface   # after wetting the sensor
"""
import argparse
import datetime
import json
import os
import struct
import time

import serial

from marwis_logger import (
    build_frame, read_frame, parse_frame, UMB_TYPES,
    CHANNELS, ROAD_CONDITION, CLASS_MARWIS, CMD_ONLINE_MULTI, VERC,
)

TYPE_NAMES = {0x10: "uint8", 0x11: "int8", 0x12: "uint16", 0x13: "int16",
              0x14: "uint32", 0x15: "int32", 0x16: "float32", 0x17: "float64"}


def main():
    ap = argparse.ArgumentParser(description="Capture a labelled MARWIS fixture.")
    ap.add_argument("--port", required=True)
    ap.add_argument("--label", required=True, help="scenario name, e.g. dry-baseline")
    ap.add_argument("--device-id", type=int, default=1)
    ap.add_argument("--out", default=os.path.join("..", "docs", "reference", "captures"))
    args = ap.parse_args()

    device_addr = (CLASS_MARWIS << 12) | args.device_id
    channels = list(CHANNELS.keys())  # all 20 documented channels fit one 0x2F request
    payload = bytes([len(channels)]) + b"".join(struct.pack("<H", c) for c in channels)
    request = build_frame(device_addr, CMD_ONLINE_MULTI, VERC, payload)

    with serial.Serial(args.port, 115200, timeout=0.5) as ser:
        ser.reset_input_buffer()
        t0 = time.monotonic()
        ser.write(request)
        frame = read_frame(ser, time.monotonic() + 2.0)
        latency_ms = round((time.monotonic() - t0) * 1000.0, 1)

    _, cmd, status, data = parse_frame(frame)
    if cmd != CMD_ONLINE_MULTI or status != 0x00:
        raise SystemExit(f"bad response: cmd={cmd:#x} status={status:#04x}")

    rows = []
    count, pos = data[0], 1
    for _ in range(count):
        sub_len = data[pos]
        sub = data[pos + 1: pos + 1 + sub_len]
        pos += 1 + sub_len
        ch_status, channel = sub[0], struct.unpack("<H", sub[1:3])[0]
        type_byte = sub[3] if len(sub) > 3 else None
        value = None
        if ch_status == 0x00 and type_byte is not None:
            fmt, size = UMB_TYPES[type_byte]
            value = struct.unpack(fmt, sub[4:4 + size])[0]
        name, unit = CHANNELS.get(channel, (f"channel_{channel}", ""))
        rows.append({
            "channel": channel, "name": name, "unit": unit, "status": ch_status,
            "type": TYPE_NAMES.get(type_byte, type_byte), "value": value,
            "raw_hex": sub.hex(),
        })

    fixture = {
        "label": args.label,
        "ts_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "device_addr": f"{device_addr:#06x}",
        "latency_ms": latency_ms,
        "request_hex": request.hex(),
        "response_hex": frame.hex(),
        "channels": rows,
    }

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"{args.label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=2)

    # readable summary
    print(f"\n=== {args.label}  ({latency_ms} ms)  -> {path} ===")
    for r in rows:
        v = "-" if r["value"] is None else (
            f"{r['value']} ({ROAD_CONDITION.get(int(r['value']), '?')})"
            if r["channel"] == 900 and r["value"] is not None else r["value"])
        flag = "" if r["status"] == 0 else f"  [status {r['status']:#04x}]"
        print(f"  {r['channel']:>4}  {r['name']:<28} {str(v):<22} {r['unit']}{flag}")


if __name__ == "__main__":
    main()
