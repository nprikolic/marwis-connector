"""Enumerate every channel the MARWIS actually exposes, via UMB Device Info (0x2D).

For each channel it asks the device for the declared variable name, unit, data
type, measurement-value type (mean/min/max/current), and min/max range. This
reveals channels the app may read that aren't in our hand-picked list — e.g. a
full-range (0-6 mm) water-film channel distinct from channel 600.

Usage:  python discover_channels.py --port COM5 [--filter water]
"""
import argparse
import struct
import time

import serial

from marwis_logger import build_frame, read_frame, parse_frame, CLASS_MARWIS, UMB_TYPES

CMD_INFO, VERC = 0x2D, 0x10
DTYPE = {0x10: "uint8", 0x11: "int8", 0x12: "uint16", 0x13: "int16",
         0x14: "uint32", 0x15: "int32", 0x16: "float32", 0x17: "float64"}
# measurement-value type (UMB 0x2D info 0x24) — best-effort labels
MVTYPE = {0x10: "current", 0x11: "min", 0x12: "max", 0x13: "mean", 0x14: "sum",
          0x15: "vector_avg", 0x16: "stddev"}


def req(ser, addr, payload):
    ser.reset_input_buffer()
    ser.write(build_frame(addr, CMD_INFO, VERC, bytes(payload)))
    _, _, status, data = parse_frame(read_frame(ser, time.monotonic() + 2.0))
    return status, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--device-id", type=int, default=1)
    ap.add_argument("--filter", default=None, help="only show channels whose name contains this")
    args = ap.parse_args()
    addr = (CLASS_MARWIS << 12) | args.device_id

    with serial.Serial(args.port, 115200, timeout=0.5) as ser:
        st, data = req(ser, addr, [0x15])               # number of channels
        if st != 0:
            raise SystemExit(f"count query failed: status {st:#04x}")
        nch = struct.unpack("<H", data[1:3])[0]
        blocks = data[3]
        print(f"device reports {nch} channels in {blocks} block(s)\n")

        channels = []
        for blk in range(blocks):
            st, data = req(ser, addr, [0x16, blk])       # channel numbers in block
            if st != 0:
                continue
            cnt = data[2]
            pos = 3
            for _ in range(cnt):
                channels.append(struct.unpack("<H", data[pos:pos + 2])[0])
                pos += 2

        print(f"{'chan':>5}  {'variable':<26} {'unit':<8} {'dtype':<8} {'mv_type':<9} {'min':>10} {'max':>10}")
        print("-" * 86)
        for c in channels:
            st, data = req(ser, addr, [0x30, c & 0xFF, (c >> 8) & 0xFF])  # complete info
            if st != 0:
                continue
            pos = 1
            ch = struct.unpack("<H", data[pos:pos + 2])[0]; pos += 2
            variable = data[pos:pos + 20].decode("latin1").strip(); pos += 20
            unit = data[pos:pos + 15].decode("latin1").strip(); pos += 15
            mv_type = data[pos]; pos += 1
            dtype = data[pos]; pos += 1
            fmt, size = UMB_TYPES.get(dtype, (None, 0))
            if fmt:
                mn = struct.unpack(fmt, data[pos:pos + size])[0]; pos += size
                mx = struct.unpack(fmt, data[pos:pos + size])[0]; pos += size
            else:
                mn = mx = None
            if args.filter and args.filter.lower() not in variable.lower():
                continue
            print(f"{ch:>5}  {variable:<26} {unit:<8} {DTYPE.get(dtype, hex(dtype)):<8} "
                  f"{MVTYPE.get(mv_type, hex(mv_type)):<9} {str(round(mn,2) if mn is not None else '?'):>10} "
                  f"{str(round(mx,2) if mx is not None else '?'):>10}")


if __name__ == "__main__":
    main()
