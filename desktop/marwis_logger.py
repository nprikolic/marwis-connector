#!/usr/bin/env python3
"""MARWIS road weather sensor logger — UMB protocol over Bluetooth SPP (virtual COM port).

Polls a Lufft MARWIS-UMB with the Multi-Channel Online Data Request (0x2F) and
logs all values with timestamps to SQLite (and optionally CSV). Bypasses the
official app entirely.

References:
  - UMB-Protokoll_1_0_Version_1_7_e.pdf  (frame format, CRC16-MCRF4XX, cmd 0x2F)
  - Marwis-UserManual_34_en.pdf          (channel list, class ID 10 -> addr 0xA001)

Usage:
  python marwis_logger.py --port COM5
  python marwis_logger.py --port COM5 --interval 0.5 --channels 100,600,900 --csv out.csv
  python marwis_logger.py --port COM5 --gps-port COM7   # NMEA GPS for lat/lon
"""

import argparse
import csv
import datetime
import sqlite3
import struct
import sys
import threading
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is required:  pip install pyserial")

# ---------------------------------------------------------------- UMB protocol

SOH, STX, ETX, EOT = 0x01, 0x02, 0x03, 0x04
VERSION = 0x10          # protocol version 1.0
CMD_ONLINE_MULTI = 0x2F
VERC = 0x10             # command version 1.0
ADDR_MASTER = 0xF001    # class 15 (master/PC), id 1
CLASS_MARWIS = 0xA      # class 10 = mobile road sensor

# UMB <type> byte -> struct format (little endian)
UMB_TYPES = {
    0x10: ("B", 1),   # unsigned char
    0x11: ("b", 1),   # signed char
    0x12: ("<H", 2),  # unsigned short
    0x13: ("<h", 2),  # signed short
    0x14: ("<I", 4),  # unsigned long
    0x15: ("<i", 4),  # signed long
    0x16: ("<f", 4),  # float (IEEE)
    0x17: ("<d", 8),  # double (IEEE)
}

# MARWIS channel list. The measurement channels are from the operating manual
# (appendix 19.1); the 4000-block diagnostics/status channels were enumerated
# live from the device via UMB Device Info 0x2D (see
# docs/reference/captures/device-channels.md).
CHANNELS = {
    100: ("road_surface_temp", "degC"),
    105: ("road_surface_temp", "degF"),
    110: ("ambient_temp", "degC"),
    115: ("ambient_temp", "degF"),
    120: ("dew_point_temp", "degC"),
    125: ("dew_point_temp", "degF"),
    200: ("rel_humidity_at_road_temp", "%"),
    210: ("rel_humidity", "%"),
    600: ("water_film_height", "um"),
    605: ("water_film_height", "mil"),
    610: ("water_film_height", "mm"),
    601: ("water_film_surface", "um"),
    606: ("water_film_surface", "mil"),
    611: ("water_film_surface", "mm"),
    612: ("snow_height", "mm"),
    800: ("ice_percentage", "%"),
    820: ("friction", ""),
    900: ("road_condition", "code"),
    910: ("road_weather_index", "code"),
    4000: ("device_status", "bitfield"),
    4001: ("measurement_status", "bitfield"),
    4002: ("flash_status", "bitfield"),
    4003: ("rs485_status", "bitfield"),
    4004: ("bluetooth_status", "bitfield"),
    4040: ("bt_signal_strength", "dBm"),
    4041: ("bt_link_quality", "%"),
    4005: ("attended_time", "s"),
    4006: ("reset_status", "bitfield"),
    4010: ("heater_on", "bitfield"),
    4011: ("housing_temp", "degC"),
    4012: ("housing_temp", "degF"),
    4020: ("led_heater_on", "bitfield"),
    4021: ("led_temp", "degC"),
    4022: ("led_temp", "degF"),
    4050: ("fw_update_status", "bitfield"),
}

ROAD_CONDITION = {0: "dry", 1: "damp", 2: "wet", 3: "ice", 4: "snow/ice",
                  5: "chemically wet", 6: "water+ice", 8: "snow", 99: "undefined"}

# A water-film channel reading its declared range max is an OVER-RANGE sentinel,
# not a real depth: the optics are saturated (confirmed in recorded data — 600 and
# 601 both pin to 6000 together, status 0). See docs/reference/captures/README.md.
# We keep the raw value in storage (it is self-identifying) but flag it on display.
WFH_RANGE_MAX = {600: 6000.0, 601: 6000.0, 605: 236.22, 606: 236.22, 610: 6.0, 611: 6.0}


def is_overrange(channel, value) -> bool:
    """True if a water-film channel is reporting its range-max over-range sentinel."""
    m = WFH_RANGE_MAX.get(channel)
    return value is not None and m is not None and value >= m * 0.999

# Default poll set: core measurements + device/measurement status + the device's
# own Bluetooth signal strength (4040) and link quality (4041) for link health.
DEFAULT_CHANNELS = [100, 110, 120, 200, 600, 601, 612, 800, 820, 900,
                    4000, 4001, 4040, 4041]


def crc16(data: bytes) -> int:
    """CRC16-MCRF4XX: poly 0x8408 (reflected 0x1021), init 0xFFFF, no final xor."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def build_frame(to_addr: int, cmd: int, verc: int, payload: bytes,
                from_addr: int = ADDR_MASTER) -> bytes:
    body = bytes([SOH, VERSION]) \
        + struct.pack("<H", to_addr) + struct.pack("<H", from_addr) \
        + bytes([2 + len(payload), STX, cmd, verc]) + payload + bytes([ETX])
    return body + struct.pack("<H", crc16(body)) + bytes([EOT])


class UmbError(Exception):
    pass


def parse_frame(frame: bytes):
    """Validate a complete UMB frame, return (from_addr, cmd, status, payload)."""
    if len(frame) < 12 or frame[0] != SOH or frame[-1] != EOT:
        raise UmbError("malformed frame")
    length = frame[6]
    if len(frame) != 12 + length:
        raise UmbError(f"length mismatch: header says {length}, frame is {len(frame)} bytes")
    if struct.unpack("<H", frame[-3:-1])[0] != crc16(frame[:-3]):
        raise UmbError("CRC mismatch")
    if frame[7] != STX or frame[-4] != ETX:
        raise UmbError("missing STX/ETX")
    from_addr = struct.unpack("<H", frame[4:6])[0]
    cmd, status = frame[8], frame[10]
    payload = frame[11:8 + length]   # after <cmd><verc><status>, up to ETX
    return from_addr, cmd, status, payload


def read_frame(ser, deadline: float) -> bytes:
    """Read one complete frame from the serial port; scan for SOH first."""
    buf = b""
    while time.monotonic() < deadline:
        b = ser.read(1)
        if not b:
            continue
        if not buf and b[0] != SOH:
            continue  # skip noise until start of frame
        buf += b
        if len(buf) >= 7:
            total = 12 + buf[6]
            if len(buf) >= total:
                return buf[:total]
    raise UmbError("timeout waiting for response")


def poll_channels(ser, device_addr: int, channels: list):
    """Multi-channel online data request (0x2F). Returns list of (channel, status, value)."""
    payload = bytes([len(channels)]) + b"".join(struct.pack("<H", c) for c in channels)
    ser.reset_input_buffer()
    ser.write(build_frame(device_addr, CMD_ONLINE_MULTI, VERC, payload))
    frame = read_frame(ser, time.monotonic() + 2.0)
    _, cmd, status, data = parse_frame(frame)
    if cmd != CMD_ONLINE_MULTI:
        raise UmbError(f"unexpected command in response: {cmd:#x}")
    if status != 0x00:
        raise UmbError(f"device returned status {status:#04x}")

    results = []
    count, pos = data[0], 1
    for _ in range(count):
        sub_len = data[pos]
        sub = data[pos + 1: pos + 1 + sub_len]
        pos += 1 + sub_len
        ch_status = sub[0]
        channel = struct.unpack("<H", sub[1:3])[0]
        value = None
        if ch_status == 0x00 and len(sub) > 3:
            fmt, size = UMB_TYPES[sub[3]]
            value = struct.unpack(fmt, sub[4:4 + size])[0]
        results.append((channel, ch_status, value))
    return results


# ---------------------------------------------------------------- GPS (NMEA)

class GpsReader(threading.Thread):
    """Background reader of NMEA sentences from a GPS COM port. Keeps last fix."""

    def __init__(self, port: str, baud: int = 9600):
        super().__init__(daemon=True)
        self.port, self.baud = port, baud
        self.lat = self.lon = None
        self._lock = threading.Lock()

    @staticmethod
    def _coord(value: str, hemi: str):
        if not value:
            return None
        deg_len = 2 if hemi in "NS" else 3
        deg = float(value[:deg_len]) + float(value[deg_len:]) / 60.0
        return -deg if hemi in "SW" else deg

    def run(self):
        while True:
            try:
                with serial.Serial(self.port, self.baud, timeout=2) as ser:
                    for raw in ser:
                        line = raw.decode("ascii", "ignore").strip()
                        f = line.split(",")
                        # $xxRMC: fields 3/4 lat, 5/6 lon, status field 2 == 'A'
                        # $xxGGA: fields 2/3 lat, 4/5 lon, fix quality field 6 > 0
                        if line[3:6] == "RMC" and len(f) > 6 and f[2] == "A":
                            lat, lon = self._coord(f[3], f[4]), self._coord(f[5], f[6])
                        elif line[3:6] == "GGA" and len(f) > 6 and f[6] not in ("", "0"):
                            lat, lon = self._coord(f[2], f[3]), self._coord(f[4], f[5])
                        else:
                            continue
                        with self._lock:
                            self.lat, self.lon = lat, lon
            except (serial.SerialException, ValueError):
                time.sleep(5)  # GPS unplugged — retry

    def position(self):
        with self._lock:
            return self.lat, self.lon


# ---------------------------------------------------------------- storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc  TEXT    NOT NULL,           -- ISO 8601, millisecond precision
    lat     REAL,                       -- NULL until georeferenced
    lon     REAL,
    channel INTEGER NOT NULL,
    name    TEXT,
    unit    TEXT,
    value   REAL,                       -- NULL if channel status != 0
    status  INTEGER NOT NULL DEFAULT 0  -- UMB channel status byte
);
CREATE INDEX IF NOT EXISTS idx_meas_ts ON measurements (ts_utc);
CREATE INDEX IF NOT EXISTS idx_meas_ch ON measurements (channel, ts_utc);
"""

CSV_COLS = ["ts_utc", "lat", "lon", "channel", "name", "unit", "value", "status"]


# ---------------------------------------------------------------- main loop

def main():
    ap = argparse.ArgumentParser(description="Log MARWIS UMB data over a Bluetooth COM port.")
    ap.add_argument("--port", required=True, help="COM port of the MARWIS Bluetooth link, e.g. COM5")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate (ignored by BT SPP, default 115200)")
    ap.add_argument("--device-id", type=int, default=1, help="UMB device ID (default 1 -> address 0xA001)")
    ap.add_argument("--interval", type=float, default=1.0, help="polling interval in seconds (default 1.0)")
    ap.add_argument("--channels", default=",".join(map(str, DEFAULT_CHANNELS)),
                    help="comma-separated UMB channel numbers (max 20)")
    ap.add_argument("--db", default="marwis.sqlite", help="SQLite output file (default marwis.sqlite)")
    ap.add_argument("--csv", default=None, help="optional CSV output file (appended)")
    ap.add_argument("--gps-port", default=None, help="optional COM port of an NMEA GPS receiver")
    ap.add_argument("--gps-baud", type=int, default=9600, help="GPS baud rate (default 9600)")
    args = ap.parse_args()

    channels = [int(c) for c in args.channels.split(",") if c.strip()]
    if len(channels) > 20:
        sys.exit("UMB command 0x2F allows at most 20 channels per request")
    device_addr = (CLASS_MARWIS << 12) | args.device_id

    db = sqlite3.connect(args.db)
    db.executescript(SCHEMA)

    csv_file = csv_writer = None
    if args.csv:
        import os
        new = not (os.path.exists(args.csv) and os.path.getsize(args.csv) > 0)
        csv_file = open(args.csv, "a", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        if new:
            csv_writer.writerow(CSV_COLS)

    gps = None
    if args.gps_port:
        gps = GpsReader(args.gps_port, args.gps_baud)
        gps.start()

    print(f"Polling MARWIS at addr {device_addr:#06x} on {args.port}, "
          f"{len(channels)} channels every {args.interval}s -> {args.db}"
          + (f" + {args.csv}" if args.csv else ""))
    print("Ctrl+C to stop.")

    ser = None
    errors = 0
    try:
        while True:
            t0 = time.monotonic()
            try:
                if ser is None:
                    ser = serial.Serial(args.port, args.baud, timeout=0.5)
                results = poll_channels(ser, device_addr, channels)
                errors = 0
            except (serial.SerialException, UmbError, OSError) as e:
                errors += 1
                print(f"[{datetime.datetime.now():%H:%M:%S}] error ({errors}): {e}", file=sys.stderr)
                if ser is not None:
                    try:
                        ser.close()
                    except OSError:
                        pass
                    ser = None
                time.sleep(min(5.0, errors))  # back off, then reconnect
                continue

            ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
            lat, lon = gps.position() if gps else (None, None)
            rows = []
            for channel, status, value in results:
                name, unit = CHANNELS.get(channel, (f"channel_{channel}", ""))
                rows.append((ts, lat, lon, channel, name, unit, value, status))
            db.executemany("INSERT INTO measurements (ts_utc, lat, lon, channel, name, unit, value, status)"
                           " VALUES (?,?,?,?,?,?,?,?)", rows)
            db.commit()
            if csv_writer:
                csv_writer.writerows(rows)
                csv_file.flush()

            # one-line live status
            shown = {c: v for c, _s, v in results}
            cond = ROAD_CONDITION.get(int(shown[900]), "?") if shown.get(900) is not None else "-"
            wfh = ("OVER-RANGE" if is_overrange(600, shown.get(600))
                   else f"{shown.get(600, float('nan')):.0f}um")
            parts = [f"Troad={shown.get(100, float('nan')):.1f}C" if 100 in shown else "",
                     f"WFH={wfh}" if 600 in shown else "",
                     f"fric={shown.get(820, float('nan')):.2f}" if 820 in shown else "",
                     f"cond={cond}",
                     f"gps={lat:.5f},{lon:.5f}" if lat is not None else "gps=-"]
            print(f"[{ts}] " + "  ".join(p for p in parts if p))

            time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        db.close()
        if csv_file:
            csv_file.close()
        if ser is not None:
            ser.close()


if __name__ == "__main__":
    main()
