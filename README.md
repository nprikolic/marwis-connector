# MARWIS connector — log Lufft MARWIS data without ViewMondo

Self-hosted logging of measurement data from a Lufft MARWIS-UMB road weather
sensor over Bluetooth, bypassing the official app and the paid ViewMondo/
SmartView server.

All targets share one protocol (UMB over the Bluetooth COM port):

- **`desktop/`** — the working Python tools (Windows COM port). Proven and
  complete: `marwis_logger.py` (unattended) and `marwis_monitor.py` (live monitor
  + on-demand saving).
- **GUI logger** — the **active build path**: a lean Tkinter window wrapping the
  same logic (live monitoring + save toggle + phone-location reminder). Plan in
  [`docs/GUI_LOGGER_PLAN.md`](docs/GUI_LOGGER_PLAN.md), build prompt in
  [`docs/GUI_LOGGER_BUILD_PROMPT.md`](docs/GUI_LOGGER_BUILD_PROMPT.md).
- **`android/`** — *parked.* A phone app was scoped (`docs/ANDROID_*`) but the
  sensor wouldn't pair with the phone, so we pivoted to the desktop GUI. Kept for
  possible revisit.

### Repository layout

```
desktop/                 Python tools
  marwis_logger.py         unattended/always-on logger (UMB protocol + storage)
  marwis_monitor.py        live monitor + on-demand saving (console)
  marwis_gui.py            lean Tkinter GUI logger (to be built — active path)
  capture.py               one-shot labelled fixture capture
  discover_channels.py     enumerate device channels (UMB 0x2D)
  test_marwis_logger.py    offline protocol tests (documented frame vectors)
docs/
  GUI_LOGGER_PLAN.md       active: Tkinter logger plan
  GUI_LOGGER_BUILD_PROMPT.md  active: fresh-session build prompt
  ANDROID_PLAN.md / ANDROID_BUILD_PROMPT.md   parked: Android app
  reference/               manuals (PDF), text dumps, captures/ (device facts)
android/                 parked Android app
data/                    SQLite outputs (marwis.sqlite, …)
```

## Findings from the documentation

### How the sensor communicates

- **Bluetooth Classic SPP** (Serial Port Profile), *not* BLE. After pairing on
  Windows, the sensor appears as a **virtual COM port** (user manual §8.5:
  "select the COM port … that has been assigned to your Bluetooth connection").
  The device advertises itself under the first two sections of its serial number.
- Over that serial link it speaks the standard, fully documented **UMB binary
  protocol** ("UMB telegrams may be tunneled through other communication media,
  e.g. Ethernet, Wi-Fi, or Bluetooth" — UMB spec §3.1.8). Master–slave: we poll,
  the sensor answers.

### UMB frame format (spec §3.1.9)

```
SOH ver to(2,LE) from(2,LE) len STX cmd verc [payload] ETX crc16(2,LE) EOT
01h 10h  ......   ......    ..  02h ..   ..             03h  ......    04h
```

- `len` = number of bytes between STX and ETX; CRC16-MCRF4XX (poly 0x8408,
  init 0xFFFF) over everything from SOH up to and including ETX, little endian.
- Addressing: MARWIS = class 10 → device address **0xA001** (device ID 1);
  master/PC = class 15 → **0xF001**.
- Polling command: **0x2F Multi-Channel Online Data Request** — up to 20
  channels per request; each answer carries per-channel status, type byte
  (0x16 = float32, 0x10 = uint8, 0x12 = uint16 …) and the value.
- Note: the worked request example in UMB spec §3.11 prints its CRC
  byte-swapped ("61D9h"; correct value is D961h). The spec's own response
  example (8690h) and both recorded frames in the MARWIS manual appendix
  confirm the standard MCRF4XX algorithm used here (check value 0x6F91).

### MARWIS channels (manual §6 / appendix 19.1)

| Channel | Variable | Type | Unit |
|---|---|---|---|
| 100 / 105 | road surface temperature | float | °C / °F |
| 110 / 115 | ambient temperature | float | °C / °F |
| 120 / 125 | dew point temperature | float | °C / °F |
| 200 | rel. humidity at road temperature | float | % |
| 210 | relative humidity | float | % |
| 600 / 605 / 610 | water film height | float | µm / mil / mm |
| 601 / 606 / 611 | water film height on smooth surface | float | µm / mil / mm |
| 612 | snow height | float | mm |
| 800 | ice percentage | float | % |
| 820 | friction | float | 0–1 |
| 900 | road condition (0 dry, 1 damp, 2 wet, 3 ice, 4 snow/ice, 5 chem. wet, 6 water+ice, 8 snow, 99 undef.) | uint8 | code |
| 4000 / 4001 | device / measurement status bitfields | uint16 | — |

### What the app sends to a "server"

The iOS app's Server settings (host/IP, TCP port 30100) connect to Lufft
**SmartView3 / ViewMondo** ("the Marwis interface must be configured as
'active' for SmartView/Collector"). The wire format of that TCP stream is
**proprietary and not documented** in any of the manuals — only the
configuration UI is described. Receiving it ourselves would mean
reverse-engineering an undocumented protocol with no spec to validate against.

## Option comparison → recommendation

| | (a) Own UMB reader over Bluetooth COM port | (b) TCP server receiving the app's stream |
|---|---|---|
| Protocol documentation | Complete (UMB spec + channel list, with recorded example frames) | None — proprietary SmartView format |
| Extra hardware | None (laptop Bluetooth) | None, but iPad/iPhone + app stay in the loop |
| Effort | Small — ~300 lines Python, prior art exists ([lufft-python](https://github.com/Tasm-Devil/lufft-python)) | Unbounded — packet-capture reverse engineering, breaks on app updates |
| Robustness | We control polling rate, retries, storage | App standby logic (location/BT timeouts) silently pauses the stream |

**Recommendation: (a)** — talk UMB directly over the Bluetooth COM port.
That's what `desktop/marwis_logger.py` implements. (The `lufft-python` repo confirmed
the approach but targets RS-485/Linux; this implementation is self-contained,
adds the 0x2F multi-channel command, SQLite/CSV storage and Windows COM ports.)

## Setup (Windows)

1. **Pair the sensor:** power the MARWIS, then Windows *Settings → Bluetooth &
   devices → Add device*. It appears named after the start of its serial
   number. The default pairing **PIN is `1007`** (same for every MARWIS-UMB).
   The status LED turns **blue** when a Bluetooth link is active.
2. **Find the COM port:** *Bluetooth & devices → Devices → More Bluetooth
   settings → COM Ports* tab — use the **Outgoing** port (e.g. `COM5`).
   (Or: Device Manager → Ports → "Standard Serial over Bluetooth link".)
3. **Install:** Python 3.x + `pip install pyserial`.
4. **Run** (from the `desktop/` folder, or prefix the path as shown):

   ```
   python desktop/marwis_logger.py --port COM5
   ```

   Useful options:

   ```
   --interval 0.5            poll every 0.5 s (sensor samples up to 100 Hz internally)
   --channels 100,600,900    custom channel set (max 20)
   --db marwis.sqlite        SQLite output (default)
   --csv run1.csv            additionally append CSV
   --gps-port COM7           NMEA GPS receiver -> lat/lon filled in each row
   --device-id 2             if the sensor's UMB ID was changed (default 1)
   ```

   Important: close the official app and the UMB Config Tool first — UMB is
   strictly single-master, and the COM port is exclusive anyway.

5. **Verify offline** (no sensor needed), from `desktop/`:
   `python -m unittest test_marwis_logger` — validates frame building/parsing
   against the recorded examples from the manuals.

   Tip — the status LED tells you the link state (operating manual §5.2.1):
   **green** = device OK but *no* Bluetooth connection; **blue** = OK *with* an
   active Bluetooth connection. If a previous session left the radio stuck
   (port open fails with "semaphore timeout period has expired"), power-cycle
   the sensor and reopen the port.

## Live monitoring (watch health, record on demand)

```
python desktop/marwis_monitor.py --port COM5
```

Starts **monitoring only — not saving**, and prints one self-updating status
line so you can confirm the link is healthy before recording:

```
[09:59:03] LINK UP  295ms  polls 3 ok 100%  | idle (not saving)  | Troad=18.2C RH=57% fric=0.82 cond=dry  dev=OK meas=OK
```

- `LINK UP/DOWN` — live connection state; `DOWN (err N)` counts consecutive
  failures while it auto-reconnects.
- `295ms` — round-trip latency of the last poll (signal-health indicator).
- `polls N ok %` — total polls and success rate this session.
- `dev=/meas=` — device & measurement status bitfields (channels 4000/4001);
  `OK` when zero, hex if a fault appears.

Keys (press in the console window):

- **`s`** (or space) — start/stop saving. Toggling prints a marked line
  (`[saving ON]` / `[saving OFF — N rows total]`) so recordings are bracketed
  in the scrollback.
- **`q`** (or Esc) — quit.

Saving is lazy: `marwis.sqlite` (and `--csv` if given) isn't created until the
first time you press `s`. Other flags: `--interval`, `--channels`, `--csv`,
`--save-on-start` (begin recording immediately). Use `marwis_monitor.py` for
interactive sessions and `marwis_logger.py` for unattended/always-on logging.

## Storage schema (extensible for georeferencing)

```sql
measurements (
    id      INTEGER PRIMARY KEY,
    ts_utc  TEXT,     -- ISO 8601 UTC, ms precision
    lat     REAL,     -- NULL unless --gps-port given (fill/join later otherwise)
    lon     REAL,
    channel INTEGER,  -- UMB channel number
    name    TEXT,     -- e.g. road_surface_temp
    unit    TEXT,     -- e.g. degC
    value   REAL,     -- NULL if channel status != 0
    status  INTEGER   -- UMB per-channel status byte (0 = OK)
)
```

Long format (one row per channel per poll) so new channels or derived
quantities need no schema change; pivot to wide tables in analysis. For later
road-condition work, join on `ts_utc` against any external GPS track, or use
the built-in `--gps-port` NMEA reader.

## Files

See **Repository layout** at the top. In short: Python tools live in
`desktop/`, the Android app in `android/`, manuals and text dumps in
`docs/reference/` (`extracted/` holds the pdftotext dumps), and SQLite output
in `data/`.
