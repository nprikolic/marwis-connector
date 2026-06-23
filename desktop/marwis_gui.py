#!/usr/bin/env python3
"""MARWIS desktop GUI logger — Tkinter version of marwis_monitor.py.

One window: connect to the MARWIS over its Bluetooth COM port, watch live link
health and readings, and toggle data saving on/off without dropping the link.
All UMB protocol + storage logic is reused from marwis_logger.py — nothing is
reimplemented here.

Architecture:
  - PollWorker (daemon thread) owns the serial port and runs the poll loop at the
    chosen interval, pushing ("data", results, latency) or ("error", msg) onto a
    queue.Queue. On error it closes the port and retries (reconnect).
  - The Tk main thread drains the queue via root.after(...) and updates widgets,
    and — when saving — does the SQLite insert. Serial I/O and sqlite never touch
    the worker thread; widgets/sqlite live only on the Tk thread.

Run:
    python desktop/marwis_gui.py
"""

import datetime
import os
import queue
import sqlite3
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import serial  # pyserial

from marwis_logger import (
    CHANNELS, CLASS_MARWIS, DEFAULT_CHANNELS, ROAD_CONDITION, SCHEMA,
    UmbError, is_overrange, poll_channels,
)

# Output lands in the repo's data/ folder regardless of the working directory it's
# launched from (script-relative, like marwis_monitor.DEFAULT_DB). Each recording
# session gets its own UTC-timestamped file so runs never overwrite or mix.
DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))


def session_db_path():
    """A fresh, timestamped SQLite path for one recording session (UTC, like the rows)."""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    return os.path.join(DATA_DIR, f"marwis_{stamp}Z.sqlite")

CHANNELS_TO_POLL = DEFAULT_CHANNELS  # already includes 600, 601, 4040, 4041

# Readings grid: (channel, title, unit, value-format-or-None). 600/601 and 900
# are formatted specially (over-range sentinel / road-condition badge).
CARDS = [
    (100, "Road temp", "°C", "{:.1f}"),
    (110, "Air temp", "°C", "{:.1f}"),
    (120, "Dew point", "°C", "{:.1f}"),
    (200, "Humidity", "%", "{:.0f}"),
    (600, "Water film", "µm", None),
    (601, "Film on surface", "µm", None),
    (820, "Friction", "", "{:.2f}"),
    (800, "Ice", "%", "{:.0f}"),
    (900, "Road condition", "", None),
]

COND_COLOR = {"dry": "#2e7d32", "damp": "#827717", "wet": "#1565c0",
              "ice": "#00838f", "snow/ice": "#00838f", "snow": "#455a64",
              "water+ice": "#1565c0", "chemically wet": "#6a1b9a"}

REMINDER = ("Location is logged separately on your phone (laptop has no GPS) — "
            "rows are timestamped in UTC; match by time afterward.")


def fmt_dur(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def set_keep_awake(active: bool):
    """Prevent (active) or allow (not active) system sleep. Windows-only; no-op elsewhere.

    If the laptop sleeps mid-recording the poll thread is suspended and the run
    gets a multi-minute hole. ES_SYSTEM_REQUIRED stops the OS sleeping while a
    recording is active but still lets the display turn off; clearing it (passing
    ES_CONTINUOUS alone) restores normal idle-sleep behaviour.
    """
    if sys.platform != "win32":
        return
    import ctypes
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if active else 0)
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except (AttributeError, OSError):
        pass


class PollWorker(threading.Thread):
    """Owns the serial port; polls at `interval` and reports via `out_q`."""

    def __init__(self, port, baud, device_addr, channels, interval, out_q):
        super().__init__(daemon=True)
        self.port, self.baud = port, baud
        self.device_addr, self.channels = device_addr, channels
        self.interval, self.out_q = interval, out_q
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        ser = None
        errors = 0
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                if ser is None:
                    ser = serial.Serial(self.port, self.baud, timeout=0.5)
                results = poll_channels(ser, self.device_addr, self.channels)
                errors = 0
                self.out_q.put(("data", results, (time.monotonic() - t0) * 1000.0))
            except (serial.SerialException, UmbError, OSError) as e:
                errors += 1
                if ser is not None:
                    try:
                        ser.close()
                    except OSError:
                        pass
                    ser = None
                self.out_q.put(("error", str(e)))
                self._stop.wait(min(5.0, errors))  # back off, then reconnect
                continue
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - t0)))
        if ser is not None:
            try:
                ser.close()
            except OSError:
                pass


class MarwisGui:
    def __init__(self, root):
        self.root = root
        root.title("MARWIS logger")
        root.geometry("760x560")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Runtime state
        self.worker = None
        self.queue = queue.Queue()
        self.db = self.db_path = None
        self.csv_file = self.csv_writer = None
        self.saving = False
        self.total_polls = self.ok_polls = self.consecutive_errors = 0
        self.saved_rows = 0
        self.save_started = None
        self.vals = {}

        self._build_ui()
        self.root.after(100, self._drain)

    # ---------------------------------------------------------------- UI build
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # Connection row
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(top, text="Port").pack(side="left")
        self.port_var = tk.StringVar(value="COM4")
        ttk.Entry(top, textvariable=self.port_var, width=8).pack(side="left", **pad)
        ttk.Label(top, text="Interval (s)").pack(side="left")
        self.interval_var = tk.StringVar(value="1.0")
        ttk.Entry(top, textvariable=self.interval_var, width=6).pack(side="left", **pad)
        self.connect_btn = ttk.Button(top, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side="left", **pad)
        self.status_pill = tk.Label(top, text="Disconnected", fg="white", bg="#9e9e9e",
                                    padx=10, pady=2)
        self.status_pill.pack(side="right", **pad)

        # Health strip
        health = ttk.LabelFrame(self.root, text="Link health")
        health.pack(fill="x", padx=10, pady=4)
        self.link_lbl = tk.Label(health, text="link down", fg="white", bg="#c62828",
                                 padx=8, pady=2)
        self.link_lbl.pack(side="left", padx=6, pady=6)
        self.latency_lbl = ttk.Label(health, text="latency —")
        self.latency_lbl.pack(side="left", padx=12)
        self.signal_lbl = ttk.Label(health, text="signal —")
        self.signal_lbl.pack(side="left", padx=12)
        self.polls_lbl = ttk.Label(health, text="polls 0 · ok —")
        self.polls_lbl.pack(side="left", padx=12)

        # Readings grid (cards, 4 per row)
        grid = ttk.LabelFrame(self.root, text="Readings")
        grid.pack(fill="x", padx=10, pady=4)
        for i in range(4):
            grid.columnconfigure(i, weight=1, uniform="card")
        for idx, (ch, title, unit, _fmt) in enumerate(CARDS):
            card = ttk.Frame(grid, relief="groove", borderwidth=1)
            card.grid(row=idx // 4, column=idx % 4, sticky="nsew", padx=4, pady=4, ipady=4)
            ttk.Label(card, text=title, foreground="#555").pack(anchor="w", padx=6, pady=(4, 0))
            val = tk.Label(card, text="—", font=("Segoe UI", 14, "bold"))
            val.pack(anchor="w", padx=6, pady=(0, 4))
            self.vals[ch] = val

        # Record panel
        rec = ttk.LabelFrame(self.root, text="Recording")
        rec.pack(fill="x", padx=10, pady=4)
        self.record_btn = ttk.Button(rec, text="● Record", command=self.toggle_record,
                                     state="disabled")
        self.record_btn.pack(side="left", padx=6, pady=6)
        self.csv_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rec, text="Also write CSV", variable=self.csv_var).pack(side="left", padx=6)
        self.rec_indicator = tk.Label(rec, text="idle (not saving)", fg="#9e9e9e")
        self.rec_indicator.pack(side="left", padx=12)
        self.rec_stats = ttk.Label(rec, text="")
        self.rec_stats.pack(side="right", padx=12)

        # Phone-location reminder
        ttk.Label(self.root, text=REMINDER, foreground="#1565c0", wraplength=720,
                  justify="left").pack(fill="x", padx=12, pady=(2, 4))

        # Status bar
        bar = ttk.Frame(self.root)
        bar.pack(side="bottom", fill="x")
        self.db_lbl = ttk.Label(bar, text=f"DB: {DATA_DIR}", anchor="w", relief="sunken")
        self.db_lbl.pack(side="left", fill="x", expand=True)
        self.timing_lbl = ttk.Label(bar, text="last poll: —", anchor="e", relief="sunken")
        self.timing_lbl.pack(side="right")

    # ---------------------------------------------------------------- connect
    def toggle_connect(self):
        if self.worker is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self):
        port = self.port_var.get().strip()
        try:
            interval = float(self.interval_var.get())
            if interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("MARWIS", "Interval must be a positive number of seconds.")
            return
        device_addr = (CLASS_MARWIS << 12) | 1
        self.total_polls = self.ok_polls = self.consecutive_errors = 0
        self.worker = PollWorker(port, 115200, device_addr, CHANNELS_TO_POLL,
                                 interval, self.queue)
        self.worker.start()
        self.connect_btn.config(text="Disconnect")
        self.status_pill.config(text="Connected", bg="#2e7d32")
        self.record_btn.config(state="normal")

    def disconnect(self):
        if self.saving:
            self._stop_saving()
        if self.worker is not None:
            self.worker.stop()
            self.worker.join(timeout=3.0)  # wait for the serial port to be released
            self.worker = None
        self.connect_btn.config(text="Connect")
        self.status_pill.config(text="Disconnected", bg="#9e9e9e")
        self.record_btn.config(state="disabled")
        self.link_lbl.config(text="link down", bg="#c62828")

    # ---------------------------------------------------------------- record
    def toggle_record(self):
        if not self.saving:
            if not messagebox.askyesno(
                    "Start location logging",
                    "Start location logging on your phone now. Rows are timestamped "
                    "in UTC — you'll match by time afterward.\n\nBegin recording?"):
                return
            self._open_storage()
            set_keep_awake(True)  # don't let the laptop sleep mid-recording
            self.saving = True
            self.saved_rows = 0
            self.save_started = time.monotonic()
            self.record_btn.config(text="■ Stop")
            self.rec_indicator.config(text="REC ●", fg="#c62828")
            self.db_lbl.config(text=f"DB: {self.db_path}")
        else:
            self._stop_saving()

    def _stop_saving(self):
        self.saving = False
        set_keep_awake(False)  # allow normal idle-sleep again
        self._close_storage()
        self.record_btn.config(text="● Record")
        self.rec_indicator.config(text=f"idle — {self.saved_rows} rows saved", fg="#9e9e9e")
        self.db_lbl.config(text=f"DB: {DATA_DIR}")

    def _open_storage(self):
        # A fresh timestamped file per session — never overwrites a previous run.
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db_path = session_db_path()
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript(SCHEMA)
        self.csv_writer = self.csv_file = None
        if self.csv_var.get():
            import csv
            from marwis_logger import CSV_COLS
            self.csv_file = open(os.path.splitext(self.db_path)[0] + ".csv",
                                 "w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(CSV_COLS)

    def _close_storage(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if getattr(self, "csv_file", None):
            self.csv_file.close()
            self.csv_file = self.csv_writer = None

    # ---------------------------------------------------------------- queue
    def _drain(self):
        try:
            while True:
                self._handle(self.queue.get_nowait())
        except queue.Empty:
            pass
        if self.saving:  # keep elapsed clock ticking even between polls
            self.rec_stats.config(
                text=f"{self.saved_rows} rows · {fmt_dur(time.monotonic() - self.save_started)}")
        self.root.after(100, self._drain)

    def _handle(self, msg):
        self.total_polls += 1
        if msg[0] == "error":
            self.consecutive_errors += 1
            self.link_lbl.config(text="link down", bg="#c62828")
            self.timing_lbl.config(text=f"last poll: error — {msg[1][:50]}")
        else:
            _, results, latency = msg
            self.ok_polls += 1
            self.consecutive_errors = 0
            shown = {c: v for c, _s, v in results}
            self.link_lbl.config(text="link up", bg="#2e7d32")
            self.latency_lbl.config(text=f"latency {latency:.0f} ms")
            rssi, qual = shown.get(4040), shown.get(4041)
            sig = "signal —"
            if rssi is not None:
                sig = f"signal {rssi:.0f} dBm" + (f" / {qual:.0f}%" if qual is not None else "")
            self.signal_lbl.config(text=sig)
            self._update_cards(shown)
            now = datetime.datetime.now().strftime("%H:%M:%S")
            self.timing_lbl.config(text=f"last poll: {now} ({latency:.0f} ms)")
            if self.saving:
                self._save(results)
        rate = (self.ok_polls / self.total_polls * 100.0) if self.total_polls else 0.0
        self.polls_lbl.config(text=f"polls {self.total_polls} · ok {rate:.0f}%")

    def _update_cards(self, shown):
        for ch, _title, unit, fmt in CARDS:
            v = shown.get(ch)
            if v is None:
                text, color = "—", "black"
            elif ch in (600, 601):
                text = "OVER-RANGE" if is_overrange(ch, v) else f"{v:.0f} {unit}"
                color = "#c62828" if is_overrange(ch, v) else "black"
            elif ch == 900:
                text = ROAD_CONDITION.get(int(v), "?")
                color = COND_COLOR.get(text, "black")
            else:
                text = fmt.format(v) + (f" {unit}" if unit else "")
                color = "black"
            self.vals[ch].config(text=text, fg=color)

    def _save(self, results):
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")
        rows = []
        for channel, status, value in results:
            name, unit = CHANNELS.get(channel, (f"channel_{channel}", ""))
            rows.append((ts, None, None, channel, name, unit, value, status))
        self.db.executemany(
            "INSERT INTO measurements (ts_utc, lat, lon, channel, name, unit, value, status)"
            " VALUES (?,?,?,?,?,?,?,?)", rows)
        self.db.commit()
        if getattr(self, "csv_writer", None):
            self.csv_writer.writerows(rows)
            self.csv_file.flush()
        self.saved_rows += len(rows)

    # ---------------------------------------------------------------- shutdown
    def on_close(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker.join(timeout=3.0)
        set_keep_awake(False)
        self._close_storage()
        self.root.destroy()


def main():
    root = tk.Tk()
    MarwisGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
