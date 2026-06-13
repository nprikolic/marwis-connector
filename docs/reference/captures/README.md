# Real device captures

Hardware-derived facts and recorded frames from the physical MARWIS unit. These
let the Android build and tests proceed **without** the sensor.

## Device identity (this unit)

| | |
|---|---|
| Bluetooth name | `MARWIS-UMB_0017.1121` (serial sections 0017.1121) |
| Bluetooth MAC | `00:12:F3:43:87:86` |
| USB/BT VID:PID | `0x0071 : 0x0106` |
| UMB device ID | 1 -> address `0xA001` |
| Pairing PIN | `1007` |

## Polling characterization (Bluetooth SPP, 12 channels)

- 40/40 polls OK (100%). Latency min 116 ms / median 149 ms / max 252 ms.
- Sustained ~6.7 polls/s. **1 s default has wide headroom; 0.2 s is feasible.**

## Finding: 6000 um is an over-range *sentinel*, not a 6 mm depth

The water-film channel (600/601) has **two regimes**, proven directly from a
recorded run (`data/marwis.sqlite`, burst at 09:44 UTC 2026-06-13):

1. **Real measurement:** graded values 0 -> **~1145 um**, surface-dependent.
2. **Over-range sentinel:** when water is too deep for the optics on that
   surface, the device outputs **exactly 6000 um** (= 6 mm = the declared range
   max) -- *not* a true depth.

Evidence it's a sentinel, not a measurement:
- On all 23 saturated polls, **channel 600 AND 601 both read exactly 6000** --
  impossible for real data, where 601 ("on surface") is always a fraction of 600.
- A clean gap: **zero values between 1200 and 5999 um**. Real readings top at
  1145 um, then jump straight to 6000.
- **Status byte stays 0** (and 4001 = 0) on the 6000 rows -- the device does not
  flag it; the only way to detect over-range is by value (`== 6000`, with
  `600 == 601`).

This reconciles every observation on this unit (171121 / serial 0017.1121):

| Source | Surface / conditions | What it showed |
|---|---|---|
| 2026 deep tabletop film, our UMB reader | poor surface, dunked deep | real <=1145 um, then **6000 sentinel** |
| 2024-05-24 field run (server -> xlsx) | asphalt road, thin films | real, max 1.145 mm (never tripped sentinel) |
| Heavy downpour years ago (SmartView) | asphalt road | real, "~1.15 mm" |
| Lab test vs volumetric ground truth (app) | concrete / reference | accurate 0-6 mm (real range extends on concrete) |

So both remembered numbers are real device outputs: **~1.15 mm** is the genuine
measurement ceiling on asphalt/non-ideal surfaces (per the datasheet, full range
needs concrete -- [Lufft_MARWIS datasheet:109](../extracted/Lufft_MARWIS_-_Mobile_Advanced_Road_Weather_Information_Sensor.txt)),
and **6 mm** is the over-range sentinel (6000 um) -- which the MARWIS app appears
to display literally as "6 mm". Our reader and the manufacturer server both
decode the bytes faithfully; the difference is *interpretation* of 6000.

(Earlier notes in this repo called 1.15 mm an intrinsic optical ceiling, then a
purely surface-dependent one -- both were guesses ahead of the data. This version
is grounded in the recorded bytes.)

**Use:** treat **value == 6000 um (or 600 == 601 at the range max) as
"over-range / saturated", not a 6 mm depth.** Real water film is valid 0 -> the
surface-dependent ceiling. Still to confirm: that the app renders 6000 as "6 mm"
(compare app display vs our 6000 read on the same deep film).

## Fixtures

Each `*.json` holds one real request+response: `request_hex`, `response_hex`,
`latency_ms`, and decoded per-channel sub-frames (`raw_hex`, type, value,
status). Feed these to the Android `FakeTransport` and as extra parse tests.

- `dry-baseline.json` -- indoor dry surface, all 20 documented channels.

### Capture more scenarios (do while you have the sensor -- irreproducible later)

```
python desktop/capture.py --port COM5 --label <scenario>
```

Priority scenarios (the values can't be reproduced without the device + water):

1. **wet-surface** -- lightly wet the measuring surface; expect water film height
   (600/601) > 0, road condition (900) -> wet/damp, friction (820) drop.
2. **damp-surface** -- a thin film / a few breaths of moisture; condition -> damp.
3. **drying** -- capture a couple of points as it dries back toward dry.
4. (optional) **cold** -- if you can chill the surface, capture changing Troad/dew.

Re-run after each physical change; one JSON per scenario. Aim to capture at least
one clearly non-dry state so the app's parsing of realistic water-film/condition
values is proven against real bytes.
