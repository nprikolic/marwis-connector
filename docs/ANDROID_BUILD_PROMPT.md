# Parallel-agent build prompt — MARWIS Android app

Paste everything in the `=== PROMPT ===` block below into a **fresh Claude Code
session** started at the repo root (`marwis-conntector/`). It tells the lead
session to scaffold the project, freeze shared contracts, then fan out the
implementation across parallel subagents, and finally integrate and verify.

Why it's shaped this way: parallel agents only stay conflict-free if (1) the
**API contracts they code against are fixed before they start**, and (2) **each
agent owns a disjoint set of files**. The prompt enforces both with a wave
structure and an explicit file-ownership map. Don't skip Wave 1.

---

=== PROMPT ===

You are the lead engineer building the **MARWIS Android app**. Work in
`android/`. Build the app by orchestrating parallel subagents (the `Agent`
tool), following the wave plan below exactly.

## Context — read these first
- `docs/ANDROID_PLAN.md` — architecture, phases, and the test strategy. Source of truth.
- `desktop/marwis_logger.py` — the proven protocol: `crc16`, `build_frame`,
  `parse_frame`, `poll_channels`, `read_frame`, the channel table, and addressing.
  Your Kotlin must reproduce this behavior byte-for-byte.
- `desktop/test_marwis_logger.py` — the documented frame vectors. These become
  your Kotlin unit tests verbatim.
- `README.md` — protocol summary (UMB frame, CRC16-MCRF4XX, addresses, channels).

## Locked technical decisions (do not relitigate)
- Native **Kotlin**, **Jetpack Compose**, **Room**, **play-services-location**.
- Package `com.marwis.connector`. `minSdk 31`, `targetSdk 35`, Kotlin 2.x, AGP current.
- Bluetooth **Classic SPP / RFCOMM**, SPP UUID `00001101-0000-1000-8000-00805F9B34FB`.
- MARWIS addr `0xA001`, master `0xF001`, poll cmd `0x2F`, CRC16-MCRF4XX (poly
  `0x8408`, init `0xFFFF`, no final xor).
- Use **bonded devices only** (no scanning). Pairing PIN `1007` is entered in
  Android Settings, not by the app.

## File-ownership map (NO agent writes outside its column)
```
WAVE 1 (you, the lead — sequential):
  Gradle scaffold, AndroidManifest.xml, gradle wrapper, theme stub
  src/main/java/com/marwis/connector/core/Contracts.kt   ← all shared types below
WAVE 2 (parallel subagents — each owns ONLY its listed files):
  A Protocol  core/MarwisProtocol.kt, core/MarwisClient.kt
              test/.../MarwisProtocolTest.kt
  B Transport transport/BtTransport.kt, transport/FakeTransport.kt
  C Storage   data/room/{MeasurementEntity,MeasurementDao,AppDatabase}.kt,
              data/RoomMarwisRepository.kt, data/CsvExporter.kt
              test/.../RoomMarwisRepositoryTest.kt
  D Location  location/FusedLocationProvider.kt, location/LocationPermissions.kt
  E UI        ui/MainScreen.kt, ui/HealthCard.kt, ui/FakePreviewViewModel.kt,
              ui/theme/*.kt
WAVE 3 (you, the lead — sequential):
  service/MarwisService.kt, MarwisViewModelImpl.kt, MainActivity.kt, manifest wiring
```

## Wave 1 — scaffold + freeze contracts (do this yourself, no agents)
1. Create the Gradle project under `android/` (app module, Compose enabled,
   Room + kapt/ksp, play-services-location, JUnit). Confirm `./gradlew help` runs.
2. Write `core/Contracts.kt` with these **frozen** types — every Wave 2 agent
   codes against these signatures, so get them right and do not change them after
   agents launch:
   ```kotlin
   data class Reading(val channel: Int, val status: Int, val value: Double?)
   data class Measurement(val tsUtc: String, val lat: Double?, val lon: Double?,
       val channel: Int, val name: String, val unit: String,
       val value: Double?, val status: Int)
   data class LatLon(val lat: Double, val lon: Double)
   data class HealthState(
       val linkUp: Boolean = false, val lastLatencyMs: Long? = null,
       val totalPolls: Int = 0, val okPolls: Int = 0, val consecutiveErrors: Int = 0,
       val latest: Map<Int, Double?> = emptyMap(),
       val deviceStatus: Int? = null, val measStatus: Int? = null,
       val rssiDbm: Int? = null, val linkQualityPct: Int? = null,  // device-reported BT (ch 4040/4041)
       val fix: LatLon? = null,
       val saving: Boolean = false, val savedRows: Int = 0, val saveStartedMs: Long? = null)
   interface Transport {            // raw byte pipe; framing lives in protocol
       fun write(bytes: ByteArray)
       fun read(buf: ByteArray, timeoutMs: Long): Int   // bytes read, 0 on timeout
       fun close()
   }
   interface MarwisRepository {
       suspend fun insert(rows: List<Measurement>)
       suspend fun count(): Int
       suspend fun exportCsv(): android.net.Uri
   }
   interface LocationProvider { val fix: kotlinx.coroutines.flow.StateFlow<LatLon?>; fun start(); fun stop() }
   interface MarwisViewModel {       // UI binds to this; lead supplies impl in Wave 3
       val state: kotlinx.coroutines.flow.StateFlow<HealthState>
       val bondedDevices: List<Pair<String,String>>   // name, address
       fun connect(address: String); fun disconnect()
       fun toggleSaving(); fun export()
   }
   val CHANNELS: Map<Int, Pair<String,String>>   // channel -> (name, unit), from the Python table
   val ROAD_CONDITION: Map<Int, String>
   val DEFAULT_CHANNELS: List<Int>               // = 100,110,120,200,600,601,612,800,820,900,4000,4001,4040,4041
   // 4040 = BT signal strength (dBm, int16), 4041 = BT link quality (%, uint16) — device-reported link health
   // Water-film over-range sentinel: a water-film channel reading its range max is NOT a depth —
   // it's a saturation flag (confirmed: 600 & 601 both pin to 6000 µm, status 0). Keep the raw value
   // in storage (self-identifying) but DISPLAY "OVER-RANGE", not "6 mm". See captures/README.md.
   val WFH_RANGE_MAX: Map<Int, Double>           // 600/601=6000.0 µm, 610/611=6.0 mm, 605/606=236.22 mil
   fun isOverrange(channel: Int, value: Double?): Boolean   // value != null && value >= max*0.999
   ```
3. Make the skeleton compile (`./gradlew assembleDebug`) with `TODO()` stubs where
   needed. **Gate:** do not launch Wave 2 until the skeleton compiles.

## Wave 2 — fan out (spawn ALL agents in ONE message, parallel)
Issue all five `Agent` calls in a single message. Give each the self-contained
brief below verbatim (agents start cold — they only know what you tell them).
Each brief ends with: "Read `docs/ANDROID_PLAN.md` and `desktop/marwis_logger.py`
first. Implement ONLY your listed files. Code against `core/Contracts.kt`; do not
modify it. Make your code compile and your tests pass before returning."

- **Agent A — Protocol core.** Port `crc16`, `buildFrame(to,cmd,verc,payload)`,
  `parseFrame` → `(from,cmd,status,payload)`, and channel decoding from
  `desktop/marwis_logger.py` into `core/MarwisProtocol.kt`. Add
  `core/MarwisClient.kt`: `class MarwisClient(t: Transport, deviceAddr: Int=0xA001)`
  with `fun poll(channels: List<Int>): List<Reading>` that builds a 0x2F request,
  reads one frame (scan for SOH `0x01`, then read `12 + len` bytes — the
  `read_frame` logic), parses, and returns readings. In
  `MarwisProtocolTest.kt` port the vectors from `desktop/test_marwis_logger.py`:
  `crc16("123456789")==0x6F91`; build single-channel(0x23,ch100)==
  `01 10 01 A0 01 F0 04 02 23 10 64 00 03 BE F8 04`; build multi(0x2F,ch100+900)==
  `01 10 01 A0 01 F0 07 02 2F 10 02 64 00 84 03 03 C1 26 04`; parse the multi
  response so ch100≈20.655 and ch900==1; corrupted byte → error. Use a tiny
  in-test stub `Transport` (don't depend on Agent B). Pure Kotlin, no Android imports.

- **Agent B — Transport.** `transport/BtTransport.kt`: implement `Transport` over
  `BluetoothSocket.createRfcommSocketToServiceRecord(SPP_UUID)`; `cancelDiscovery()`
  before `connect()`; connect off the main thread; on failure fall back to the
  reflective `createRfcommSocket(1)`. `read` pulls from the socket InputStream with
  the given timeout. `transport/FakeTransport.kt`: a `Transport` you preload with
  response frames (for Wave 3 integration tests / link-loss simulation by throwing
  on read). No UI, no storage.

- **Agent C — Storage.** Room `MeasurementEntity` mirroring `Measurement` +
  the schema in `README.md`; `MeasurementDao` (insert list, count, query-all for
  export); `AppDatabase`; `RoomMarwisRepository : MarwisRepository`; `CsvExporter`
  writing the 8 columns and returning a shareable `Uri` (FileProvider, authority
  `${applicationId}.fileprovider`). Add `RoomMarwisRepositoryTest` (Robolectric or
  instrumented) covering insert+count. Touch only `data/`.

- **Agent D — Location.** `location/FusedLocationProvider.kt` implementing
  `LocationProvider` with `FusedLocationProviderClient` (interval ~1s, balanced
  power), exposing the last fix as `StateFlow<LatLon?>`.
  `location/LocationPermissions.kt`: a Compose-friendly `ACCESS_FINE_LOCATION`
  request helper. Touch only `location/`.

- **Agent E — UI.** `ui/MainScreen.kt` + `ui/HealthCard.kt`: one screen binding a
  `MarwisViewModel` — bonded-device dropdown + Connect/Disconnect; a health card
  showing LINK UP/DOWN, latency, `okPolls/totalPolls` %, device-reported BT signal
  (`rssiDbm`/`linkQualityPct`), Troad(100)/RH(200)/fric(820)/road-condition(900)
  via `CHANNELS`+`ROAD_CONDITION` (show water film as **"OVER-RANGE"** when
  `isOverrange(600, …)`, not as 6 mm), dev/meas status,
  GPS fix; a large REC toggle (rows + elapsed); an Export button. Provide
  `ui/FakePreviewViewModel.kt` emitting sample `HealthState` for `@Preview` so the
  UI builds with zero hardware. Material 3 theme in `ui/theme/`. Touch only `ui/`.

When agents return, run `./gradlew assembleDebug testDebugUnitTest`. If an agent's
files don't compile or tests fail, send that agent back with the exact error —
do not fix another agent's files yourself.

## Wave 3 — integrate + verify (you, the lead)
1. `service/MarwisService.kt`: foreground service owning a coroutine poll loop
   that uses `MarwisClient` over `BtTransport`, updates a `MutableStateFlow<HealthState>`
   (latency, ok/total, link up/down, latest, dev/meas status, plus `rssiDbm`/
   `linkQualityPct` from channels 4040/4041), pulls the GPS fix
   from `LocationProvider`, and when `saving` writes `Measurement` rows via
   `MarwisRepository`. Auto-reconnect with backoff on link loss. Foreground types
   `location|connectedDevice`.
2. `MarwisViewModelImpl : MarwisViewModel` bridging the service to the UI;
   `MainActivity` requests BLUETOOTH_CONNECT + ACCESS_FINE_LOCATION, hosts
   `MainScreen`, binds the service. Register service + FileProvider in the manifest.
3. Verify:
   - `./gradlew testDebugUnitTest` — protocol vectors + repository tests green.
   - `./gradlew assembleDebug` — APK builds.
   - Print an **on-device checklist** (emulator can't do BT Classic): install on a
     phone already paired with the MARWIS (PIN 1007), connect, watch latency/
     success, toggle REC, move for GPS variation, export, inspect the DB/CSV; and
     cross-check a few readings against `desktop/marwis_logger.py` run **one at a
     time** (UMB is single-master).

## Definition of done
Skeleton + all Wave 2 modules compile; unit tests pass; debug APK builds; the
on-device checklist is printed. Report what was built per agent and any contract
deviations.

## Guardrails
- Freeze `core/Contracts.kt` before Wave 2; if a real need to change it appears,
  stop, change it yourself, and re-brief affected agents — never let two agents
  edit shared types.
- Strict file ownership: an agent that needs something outside its column asks
  you; it does not reach across.
- Compile gate between every wave. Keep each agent's scope to its files so their
  work merges cleanly.

=== END PROMPT ===

---

## Notes for the human (not part of the prompt)

- **Parallelism realized:** 5 agents run concurrently in Wave 2 (protocol,
  transport, storage, location, UI). Waves 1 and 3 are inherently sequential —
  contracts must precede implementation, and integration must follow it.
- **Why not more agents:** finer splits (e.g. theme vs screen) start sharing
  files and the coordination cost outweighs the speedup. If you want more
  fan-out, have the lead split Agent C into DAO/entity vs CSV-export, or Agent E
  into screen vs health-card — but only if their files stay disjoint.
- **The one thing that can't be parallelized away:** real-Bluetooth testing needs
  the physical sensor and a phone. Everything up to that is covered by the JVM
  vector tests and the FakeTransport integration path, which need no hardware.
- Start the fresh session at the repo root so the agents can read `desktop/` and
  `docs/`.
