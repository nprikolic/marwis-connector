# Device channel inventory (unit 171121 / serial 0017.1121)

Enumerated live from the device via UMB Device Info (0x2D) with
`desktop/discover_channels.py`. Every channel the MARWIS actually exposes, with
declared data type, measurement-value type, and min/max range.

| chan | variable | unit | dtype | mv_type | min | max |
|---|---|---|---|---|---|---|
| 100 | Road temperature | °C | float32 | current | -50 | 70 |
| 105 | Road temperature | °F | float32 | current | -58 | 158 |
| 110 | Amb. temperature | °C | float32 | current | -50 | 70 |
| 115 | Amb. temperature | °F | float32 | current | -58 | 158 |
| 120 | Dewpoint temperature | °C | float32 | current | -50 | 60 |
| 125 | Dewpoint temperature | °F | float32 | current | -58 | 140 |
| 200 | Rel. humidity o.r. | % | float32 | current | 0 | 100 |
| 210 | Rel. humidity | % | float32 | current | 0 | 100 |
| 600 | Waterfilm height | µm | float32 | current | 0 | 6000 |
| 605 | Waterfilm height | mil | float32 | current | 0 | 236.22 |
| 610 | Waterfilm height | mm | float32 | current | 0 | 6.0 |
| 601 | WFH on surface | µm | float32 | current | 0 | 6000 |
| 606 | WFH on surface | mil | float32 | current | 0 | 236.22 |
| 611 | WFH on surface | mm | float32 | current | 0 | 6.0 |
| 612 | Snow height | mm | float32 | current | 0 | 50 |
| 800 | Ice percentage | % | float32 | current | 0 | 100 |
| 820 | Friction | n/a | float32 | current | 0 | 1.0 |
| 900 | Road condition | logic | uint8 | current | 0 | 255 |
| 910 | Road weather index | logic | uint8 | current | 0 | 255 |
| 4000 | Device status | digits | uint16 | current | 0 | 65535 |
| 4001 | Measure status | digits | uint16 | current | 0 | 65535 |
| 4002 | Flash status | digits | uint16 | current | 0 | 65535 |
| 4003 | RS485 status | digits | uint16 | current | 0 | 65535 |
| 4004 | Bluetooth status | digits | uint16 | current | 0 | 65535 |
| **4040** | **BT signal strength** | **dBm** | int16 | current | -128 | 128 |
| **4041** | **BT link quality** | **%** | uint16 | current | 0 | 100 |
| 4005 | Attended time | s | uint32 | current | 0 | 4294967295 |
| 4006 | Reset status | digits | uint16 | current | 0 | 65535 |
| 4010 | Heater on/off | digits | uint16 | current | 0 | 65535 |
| 4011 | Housing temperature | °C | float32 | current | -40 | 60 |
| 4012 | Housing temperature | °F | float32 | current | -40 | 140 |
| 4020 | LED heater on/off | digits | uint16 | current | 0 | 65535 |
| 4021 | LED temperature | °C | float32 | current | -40 | 60 |
| 4022 | LED temperature | °F | float32 | current | -40 | 140 |
| 4050 | FW update status | digits | uint16 | current | 0 | 65535 |

## Notes

- **Water film:** `600` is declared full-range (0–6000 µm) and is the only
  water-film-height channel (605/610 are the same value in mil/mm; 601/606/611
  are the "on surface" variant). No alternate full-range channel exists, so the
  app/UMB water-film ceiling discrepancy is *not* a channel-selection issue
  (parked — see captures/README.md).
- **Connection health (new):** `4040` BT signal strength (dBm) and `4041` BT link
  quality (%) come straight from the device — better link-health indicators than
  inferred poll latency. Wire these into `marwis_monitor.py` and the Android
  `HealthState`. `4004` is the Bluetooth status bitfield.
- All channels report `current` (instantaneous) values; no min/max/mean
  aggregate channels are exposed over UMB.
