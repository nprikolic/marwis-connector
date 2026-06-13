# MARWIS Android app — development plan

Goal: a minimal Android app that connects to the MARWIS over Bluetooth, shows
connection health, lets you toggle data saving on/off, and logs GPS location
alongside the readings. Functionally the phone version of
`desktop/marwis_monitor.py`.

## Recommended stack

- **Native Kotlin + Android Studio.** The sensor uses **Bluetooth Classic SPP
  (RFCOMM)**, not BLE — Android supports this directly via `BluetoothSocket`.
  Cross-platform options (Flutter `flutter_bluetooth_serial`, React Native) wrap
  the same API but are less maintained for Classic SPP; native is the simplest,
  most reliable path here.
- **Jetpack Compose** for the UI (one screen, very little code).
- **Room** (SQLite) for storage, mirroring the existing `measurements` schema.
- **FusedLocationProviderClient** (Google Play Services) for GPS — simplest
  reliable location source.
- **minSdk 31** (Android 12) if your phone is on 12+ — it simplifies the
  Bluetooth permission model. Drop to 26 only if you need older devices.

## Why this maps cleanly from the Python code

The hard part — the protocol — is already proven and tiny.
`desktop/marwis_logger.py` is ~100 lines of real logic: `crc16`, `build_frame`,
`parse_frame`, `poll_channels`, plus the channel table. All of it ports directly
to Kotlin, and the existing `desktop/test_marwis_logger.py` vectors become
Kotlin unit tests that
prove the port byte-for-byte **without any hardware**.

Confirmed facts to carry over:
- SPP UUID `00001101-0000-1000-8000-00805F9B34FB` (seen in the Windows COM-port
  enumeration — standard Serial Port Profile).
- MARWIS address `0xA001`, master `0xF001`, command `0x2F`, CRC16-MCRF4XX.
- Pairing PIN `1007` (handled by Android's pairing dialog, not the app).

## Architecture (keep it to a handful of files)

```
MarwisProtocol.kt   pure functions: crc16, buildFrame, parseFrame, decodeChannels
                    + channel table. No Android imports → unit-testable on JVM.
Transport.kt        interface { write(bytes); read(n, timeout): bytes }.
                    BtTransport (real RFCOMM) + FakeTransport (canned responses).
MarwisService.kt    foreground Service. Owns the socket + poll loop (coroutine).
                    Exposes StateFlow<HealthState> and a saving on/off flag.
                    Reads GPS, writes rows to Room when saving.
Storage (Room)      Measurement entity = existing schema (ts_utc, lat, lon,
                    channel, name, unit, value, status). DAO insert + export.
MainScreen.kt       Compose UI: device picker, Connect, health card, REC toggle.
MainActivity.kt     permissions + hosts the screen, binds the service.
```

Polling loop = the Python loop, transliterated: build 0x2F frame → write → read
frame → parse → update `HealthState` (latency, ok/total, link up/down, latest
readings, device/measurement status) → if saving, grab last GPS fix and insert.

### HealthState (what the UI observes)

`linkUp`, `lastLatencyMs`, `totalPolls`, `okPolls`, `consecutiveErrors`,
`latest` readings map, `deviceStatus`/`measStatus`, `gpsLat`/`gpsLon`,
`saving`, `savedRows`, `saveStartedAt`. One `StateFlow`, the UI just renders it.

## UI (one screen)

- **Bonded-device dropdown** (`BluetoothAdapter.bondedDevices`) + **Connect**.
- **Health card**: LINK UP/DOWN, latency, poll success %, Troad / RH / friction
  / road condition, dev/meas status, GPS fix.
- **Big REC toggle**: OFF by default; shows rows written + elapsed when ON.
- **Export/Share** button: share the SQLite file or a CSV dump.

## Permissions / manifest essentials

- `BLUETOOTH_CONNECT` (runtime, API 31+); `BLUETOOTH` + `BLUETOOTH_ADMIN` for
  ≤30. We use **bonded** devices, so scanning (`BLUETOOTH_SCAN`) is optional.
- `ACCESS_FINE_LOCATION` (runtime) for GPS.
- Foreground service so logging survives screen-off:
  `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_LOCATION`,
  `FOREGROUND_SERVICE_CONNECTED_DEVICE`; service `type` set accordingly (API 34+
  requires it).

## Development phases (de-risk first)

0. **Spike (half a day): prove RFCOMM works on the phone.** Throwaway activity:
   pick the bonded MARWIS, open `createRfcommSocketToServiceRecord(SPP_UUID)` on
   a background thread, send one hard-coded 0x2F frame, log the raw + parsed
   reply. This is the Android equivalent of a one-shot probe. It retires the
   single biggest unknown (does this BT stack/module connect and respond) before
   you build anything.
1. **Protocol core + unit tests.** Port `MarwisProtocol.kt`; port the
   `desktop/test_marwis_logger.py` vectors to JUnit (see below). Green tests = correct
   port, no hardware needed.
2. **Service + health monitoring + UI card.** Real `BtTransport`, poll loop,
   `HealthState`, Compose health card. Connect/disconnect, auto-reconnect.
3. **GPS.** FusedLocation updates → last fix into `HealthState`.
4. **Storage + REC toggle.** Room schema, insert when saving, row counter.
5. **Export + robustness.** Share file, foreground-service notification,
   graceful link-loss/reconnect, lifecycle handling.

## How to test it

Layered, cheapest first — and note the one hard constraint up front:

> **The Android emulator has no Bluetooth Classic.** Anything touching the real
> radio must run on a **physical phone** paired with the sensor.

1. **JVM unit tests (no device, no sensor) — your safety net.** Port the exact
   recorded vectors from `desktop/test_marwis_logger.py`:
   - CRC check: `crc16("123456789") == 0x6F91`.
   - Build single-channel (0x23, ch 100) → `01 10 01 A0 01 F0 04 02 23 10 64 00
     03 BE F8 04`.
   - Build multi-channel (0x2F, ch 100+900) → `01 10 01 A0 01 F0 07 02 2F 10 02
     64 00 84 03 03 C1 26 04`.
   - Parse multi-channel response → ch100 ≈ 20.655 °C float, ch900 = 1 (damp).
   - Corrupted byte → CRC error raised.
   These pin the protocol byte-for-byte and run on every build.

2. **FakeTransport integration test (emulator OK).** Feed canned response frames
   through the `Transport` interface and drive the whole poll loop + Room insert
   + `HealthState` updates. Verifies parsing, saving, and health math with zero
   hardware. Also lets you simulate link-loss (throw on read) to test the
   reconnect/health-down path.

3. **On-device manual test (real sensor).** Spike app first, then the full app:
   connect, watch latency/success, toggle REC, walk around to get GPS variation,
   stop, export, inspect the DB/CSV.

4. **Cross-check against the Python tools.** Point `desktop/marwis_logger.py` and
   the app at the same sensor **one at a time** (UMB is single-master — never both at
   once) and confirm the readings agree. The Python side is your reference
   oracle.

## Known gotchas

- **Single master.** Close the official app / any PC logger before the phone
  connects. Only one RFCOMM master at a time.
- **`createRfcommSocketToServiceRecord` sometimes fails** on quirky stacks; the
  common fallback is the reflective `createRfcommSocket(channel 1)`. Keep both.
- **Call `cancelDiscovery()` before `connect()`**, and always connect off the
  main thread.
- **Stuck radio recovery** (same as desktop): green LED = no BT link, blue =
  connected. If connect hangs, power-cycle the sensor.
- **Read framing:** TCP/RFCOMM gives a byte stream, not packets — reuse the
  Python `read_frame` approach (scan for SOH, then read `12 + len` bytes).
- **Foreground-service types** are mandatory on Android 14+ or the service is
  killed.
- **Water-film over-range sentinel:** a water-film channel reading its range max
  (600/601 = 6000 µm, 610/611 = 6.0 mm) is a saturation flag, not a depth —
  confirmed in recorded data (600 and 601 both pin to 6000, status 0; the app
  likely shows it as "6 mm"). Keep the raw value but display "OVER-RANGE". See
  docs/reference/captures/README.md.

## First concrete step

Build Phase 0 (the spike) on your phone. If it connects and prints a parsed
reply, the rest is straightforward porting of code that already works.
