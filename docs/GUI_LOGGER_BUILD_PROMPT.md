# Build prompt — MARWIS desktop GUI logger

Paste the `=== PROMPT ===` block below into a **fresh Claude Code session** at the
repo root (`marwis-conntector/`). It builds the lean Tkinter logger in one file.
This is a single-session build — no subagents; the app is small and self-contained.

---

=== PROMPT ===

Build a lean **Tkinter** desktop GUI logger for a Lufft MARWIS road-weather
sensor, as one file `desktop/marwis_gui.py`. It is the GUI version of the
existing `desktop/marwis_monitor.py`. Keep it **very lean** (~300–400 lines).

## Read first
- `docs/GUI_LOGGER_PLAN.md` — the spec (source of truth).
- `desktop/marwis_logger.py` — reuse ALL protocol + storage logic from here.
- `desktop/marwis_monitor.py` — the console version; mirror its behaviour.
- `docs/reference/captures/README.md` — the water-film over-range sentinel.

## Hard rules
- **Tkinter only** (Python stdlib). No new dependencies beyond `pyserial`
  (already used by `marwis_logger`).
- **Do NOT reimplement the UMB protocol or storage.** Import and reuse from
  `marwis_logger`: `poll_channels`, `CHANNELS`, `ROAD_CONDITION`, `is_overrange`,
  `WFH_RANGE_MAX`, `SCHEMA`, `CSV_COLS`, `CLASS_MARWIS`, `DEFAULT_CHANNELS`,
  `UmbError`. (`DEFAULT_CHANNELS` already includes 600, 601, 4040, 4041.)
- One window, one file. No live plots, no channel picker, no settings file, no
  packaging.

## What it must do
1. **Connect** to a COM port (dropdown/entry, default COM5) at a chosen interval
   (default 1.0 s); Connect/Disconnect button; Connected/Disconnected status pill.
2. **Monitor live** (always, once connected): a health strip — link up/down,
   poll latency (ms), device signal `4040` dBm / `4041` %, poll count + success %
   — and a readings grid: road temp (100), air temp (110), dew point (120),
   humidity (200), **water film 600**, **water film on surface 601**, friction
   (820), ice (800), and a road-condition (900) badge via `ROAD_CONDITION`.
3. **Water film 600 and 601 both shown and saved.** Display `OVER-RANGE` for a
   channel when `is_overrange(ch, value)` (the 6000 µm sentinel); store the raw
   value regardless.
4. **Save/not-save toggle.** Start in monitor-only mode (no DB writes). A Record
   button toggles saving on/off without dropping the link (mirror
   `marwis_monitor.py`): when on, append every poll's rows to SQLite using the
   same INSERT/`SCHEMA` as `marwis_logger`; show rows-written + elapsed.
5. **Phone-location reminder** (laptop has no GPS — `lat`/`lon` stay NULL):
   - On toggling Record ON, show a confirm dialog: *"Start location logging on
     your phone now. Rows are timestamped in UTC — you'll match by time
     afterward. Begin recording?"* (Begin / Cancel).
   - A persistent reminder line near the record controls.
   - Keep timestamps ISO-8601 **UTC, ms precision** (the join key).
6. **Storage path**: default DB resolves to the repo `data/` folder regardless of
   the working directory (script-relative, like `marwis_monitor.py`'s
   `DEFAULT_DB`); create the folder if missing; lazy-open on first record.
   Optional CSV via a checkbox.

## Architecture (required)
- A daemon **worker thread** owns the serial port and runs the poll loop at the
  interval, pushing `("data", results)` or `("error", msg)` onto a
  `queue.Queue`. On error it closes the port and retries (reconnect), exactly
  like `marwis_monitor.py`.
- The **Tk main thread** drains the queue via `root.after(~100ms, ...)`, updates
  widgets, and (when saving) does the DB insert. **Never touch Tk widgets or
  sqlite from the worker thread** — sqlite connections aren't shareable across
  threads; open/use the DB on the Tk thread.
- Clean shutdown: stop the worker, close serial + DB on window close.

## UI layout (match the approved mockup)
Connection row → health strip → readings grid (cards, ~4 per row) → record panel
(Recording indicator + rows + elapsed + Stop) → phone-location reminder line →
status bar (DB path, last-poll timing). Use `ttk` widgets, a fixed comfortable
window size, sentence-case labels.

## Verify before finishing
- Launches with no sensor and stays responsive; Connect to a bad/empty port
  surfaces "link down" without freezing the UI or crashing.
- `python -c "import ast; ast.parse(open('desktop/marwis_gui.py').read())"` clean;
  app imports `marwis_logger` without duplicating protocol code.
- If a sensor is available: connect, see live readings, toggle Record (reminder
  appears), confirm rows for both 600 and 601 land in `data/marwis.sqlite`, and a
  thick film shows `OVER-RANGE`.
- Print how to run it and a one-line summary of what was built.

## Definition of done
One lean `desktop/marwis_gui.py` that monitors live, toggles saving with the
phone-location reminder, stores both 600 and 601 (over-range-aware) via reused
`marwis_logger` logic, and never blocks the UI thread on serial I/O.

=== END PROMPT ===
