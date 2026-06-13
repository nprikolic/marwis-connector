# MARWIS Android app

Phone app that talks UMB to the Lufft MARWIS over Bluetooth Classic SPP, shows
live connection health, toggles data recording on demand, and logs GPS
alongside the readings. The phone counterpart of `desktop/marwis_monitor.py`.

**Status:** not yet scaffolded. Build it with the orchestration prompt in
[`../docs/ANDROID_BUILD_PROMPT.md`](../docs/ANDROID_BUILD_PROMPT.md), following
the architecture and test strategy in
[`../docs/ANDROID_PLAN.md`](../docs/ANDROID_PLAN.md).

The protocol is already proven in the Python reference implementation under
`desktop/`; the Kotlin port reuses the exact frame vectors from
`desktop/test_marwis_logger.py` as JVM unit tests.
