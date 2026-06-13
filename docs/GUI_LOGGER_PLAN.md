# MARWIS desktop GUI logger — plan

> **Status: implemented** in [`desktop/marwis_gui.py`](../desktop/marwis_gui.py).
> One change from this plan: each Record session writes its own UTC-timestamped
> file `data/marwis_YYYYMMDD_HHMMSSZ.sqlite` (runs never overwrite or mix),
> rather than appending to a single fixed `data/marwis.sqlite`.

A lean **Tkinter** single-window app: the GUI version of `desktop/marwis_monitor.py`.
Connect to the MARWIS over the Bluetooth COM port, watch live connection health
and readings, and toggle data saving on/off. This is the active build path; the
Android app (`android/`, `docs/ANDROID_*`) is parked (phone pairing wouldn't
connect).

## Requirements

- **Tkinter only** (Python stdlib — no extra dependencies). One window, one file
  `desktop/marwis_gui.py`. Keep it **very lean** (~300–400 lines).
- **Reuse `desktop/marwis_logger.py` for all protocol + storage logic** —
  `poll_channels`, `build_frame`, `CHANNELS`, `ROAD_CONDITION`, `is_overrange`,
  `WFH_RANGE_MAX`, `SCHEMA`, `CSV_COLS`, `CLASS_MARWIS`, `DEFAULT_CHANNELS`. Do
  **not** reimplement the UMB protocol.
- **Monitor and save both water-film channels 600 and 601** (already in
  `DEFAULT_CHANNELS`); show them side by side and store both every poll.
- **Save/not-save toggle.** Starts in monitor-only mode (no DB writes); a Record
  button starts/stops saving without dropping the connection — same model as
  `marwis_monitor.py`.
- **Connection health**: link up/down, poll latency, device-reported signal
  (`4040` dBm / `4041` %), poll count + success rate.
- **Over-range aware**: water film at the 6000 µm sentinel shows `OVER-RANGE`
  (via `is_overrange`), raw value still stored.
- **No laptop GPS**: `lat`/`lon` stay NULL. Location comes from the phone (below).

## Phone-location workflow (no laptop GPS)

The laptop has no GPS, so location is logged **separately on the phone** and
matched by time afterward.

- Every row is timestamped **ISO-8601 UTC, millisecond precision** (already the
  schema) — that's the join key against the phone's track.
- **Reminder on record start:** when the user toggles saving ON, a dialog says
  *"Start location logging on your phone now. Rows are timestamped in UTC — match
  by time afterward."* with confirm/cancel.
- A persistent reminder line sits near the record controls so it's never missed.
- Analysis later: export the phone GPS track (GPX/CSV) and join on timestamp.

## Architecture (single file)

```
marwis_gui.py
  - imports protocol/storage from marwis_logger
  - PollWorker (threading.Thread, daemon): owns the serial port, runs the poll
    loop at the chosen interval, pushes (results | error) onto a queue.Queue
  - Tk main thread: root.after(...) drains the queue and updates widgets, and
    when saving is on, writes rows via the same INSERT as marwis_logger.
    NEVER touch Tk widgets from the worker thread.
  - App state: connected, saving, health counters, db handle (lazy-opened)
```

Threading rule: serial I/O and the poll loop live on the worker thread; all
widget updates happen on the Tk thread through the queue + `after`.

## UI layout (matches the approved mockup)

- **Connection row** — Port dropdown/entry, Interval, Connect/Disconnect button,
  Connected/Disconnected status pill.
- **Health strip** — Link up/down, latency, `-67 dBm / 92%`, polls · ok %.
- **Readings grid** — road temp, air temp, dew point, humidity, **water film 600**,
  **water film on surface 601**, friction, ice, road-condition badge.
- **Record panel** — Record toggle (Recording indicator + rows + elapsed), Stop.
- **Phone-location reminder** line.
- **Status bar** — output DB path, last-poll timing.

## Storage

- Same `measurements` long-format schema as `marwis_logger` (one row per channel
  per poll); 600 and 601 are just two of those rows. No schema change.
- Default DB resolves to the repo's `data/` folder regardless of the working
  directory (script-relative, like `marwis_monitor.py`). Lazy-opened on first
  record. Optional CSV via a checkbox/menu.
- Over-range 6000 µm stored raw (display-only flag) — see
  `docs/reference/captures/README.md`.

## Testing

1. **No sensor:** app launches, Connect surfaces link-down gracefully (no crash),
   UI stays responsive (worker thread, not the Tk thread, blocks on serial).
2. **With sensor:** connect → readings update live; toggle Record → reminder
   dialog → rows land in `data/marwis.sqlite`; confirm both 600 and 601 are
   stored each poll; force a thick film to see `OVER-RANGE`.
3. Cross-check a few values against `marwis_monitor.py` (one client at a time —
   UMB is single-master).

## Out of scope (to stay lean)

No live plots, no channel-picker UI (fixed `DEFAULT_CHANNELS`), no settings
persistence, no `.exe` packaging — run with `python desktop/marwis_gui.py`.
