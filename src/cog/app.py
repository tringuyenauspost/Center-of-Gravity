"""CustomTkinter GUI application for the Centre of Gravity solver."""

import math
import multiprocessing
import os
import queue
import re
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, filedialog

import customtkinter as ctk

from .dataio import load_fixed_depots
from .report import display_map_in_browser, render_dashboard
from .worker import add_depot_names, execute_job, run_job_in_child

# Short tags used in report filenames (keep filesystem-safe and stable).
_MODE_FILE_TAG = {
    "Single Depot": "single",
    "Multi Depot": "multi",
    "Fixed + Variable Depots": "fixed",
}

# --- Appearance: flip this ONE word to switch the whole UI light/dark. ---
APPEARANCE = "light"
ctk.set_appearance_mode(APPEARANCE)
ctk.set_default_color_theme("blue")  # base theme only; visible accents overridden below

# Layout rhythm.
PAD = 14                    # outer spacing between cards
GAP = 8                     # inner spacing between widgets
CARD_RADIUS = 12
WIDGET_RADIUS = 8

# How often the window checks the solver process for progress / results.
WORKER_POLL_MS = 100

# Fixed-depot table: rows shown before it scrolls, and the height of one row.
FIXED_TABLE_VISIBLE_ROWS = 4
FIXED_TABLE_ROW_H = 34

# --- Colour palette: PRIMARY = chrome (Browse buttons, mode dropdown, active
#     tab), ACCENT = the Run call-to-action. One place to retune the whole look. ---
PRIMARY = "#475569"          # slate
PRIMARY_HOVER = "#334155"
PRIMARY_TEXT = "white"
ACCENT = "#059669"           # emerald
ACCENT_HOVER = "#047857"
ACCENT_TEXT = "white"
STATUS_GREEN = "#047857"     # "file loaded" (emerald, darkened for light-theme contrast)
STATUS_RED = "#dc2626"       # "no file selected"


# --- Show/hide helpers ---------------------------------------------------
# customtkinter's CTkBaseClass records the last grid() call and REPLAYS it on
# every DPI-scaling change (ctk_base_class._set_scaling). grid_remove() does not
# clear that record, so anything hidden with it springs back into view the
# moment the window meets a monitor with different scaling — the whole form
# unfolds at once. grid_forget() does clear the record, so hide with forget and
# re-show by replaying the options we stored ourselves.
_GRID_OPTS = {}


def grid_hideable(widget, **opts):
    """Grid a widget and remember its options so show_widget can restore them."""
    _GRID_OPTS[widget] = opts
    widget.grid(**opts)


def show_widget(widget):
    """Re-grid a hidden widget at its original position."""
    widget.grid(**_GRID_OPTS[widget])


def hide_widget(widget):
    """Hide a widget without leaving a scaling-replayable grid() record."""
    widget.grid_forget()


def default_output_dir():
    """Project-local outputs/ under the process working directory."""
    return os.path.join(os.getcwd(), "outputs")


def build_report_path(out_dir, csv_file, solver_type):
    """
    Unique report path per run so successive runs do not overwrite each other.

    Example: outputs/clusters_map_malaga_pdc_multi_20260721_153045.html
    """
    os.makedirs(out_dir, exist_ok=True)
    stem = Path(csv_file).stem
    stem = re.sub(r"[^\w\-]+", "_", stem).strip("_") or "dataset"
    tag = _MODE_FILE_TAG.get(solver_type, "run")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(out_dir, f"clusters_map_{stem}_{tag}_{stamp}.html")


def format_objective(objective, total_demand):
    """One-line objective summary: total item-km and average km per demand item."""
    total_km = objective / 1000.0
    avg_km = total_km / total_demand if total_demand else 0.0
    return (f"Objective: {total_km:,.1f} item·km total, "
            f"average {avg_km:.2f} km per demand item")


def parse_fixed_count(raw):
    """Number of fixed depots typed by the user: a whole number, at least 1."""
    try:
        count = int((raw or "").strip())
    except ValueError:
        count = 0
    if count < 1:
        raise ValueError("Number of fixed depots must be a whole number, 1 or more.")
    return count


def _parse_coord(raw, name, low, high):
    """One latitude/longitude string -> (value or None, error or None)."""
    raw = (raw or "").strip()
    if not raw:
        return None, f"{name} is required"
    try:
        value = float(raw)
    except ValueError:
        return None, f"{name} must be a number"
    if not math.isfinite(value):
        return None, f"{name} must be a number"
    if not low <= value <= high:
        return value, f"{name} must be between {low} and {high}"
    return value, None


def parse_fixed_depots(rows):
    """Validate the fixed-depot popup rows.

    rows is a sequence of (latitude, longitude, max_items) strings, one per
    fixed depot. Returns (fixed_coords, max_points) in the shapes
    solve_fixed_plus_variable_depots takes: a list of (lat, lon) floats, and
    a list of positive int | None (blank = no limit), or None when no depot
    is capped. Raises ValueError listing every problem, one line per depot.
    """
    coords, caps, errors = [], [], []
    for i, (lat_raw, lon_raw, cap_raw) in enumerate(rows, start=1):
        problems = []
        lat, lat_err = _parse_coord(lat_raw, "Latitude", -90, 90)
        lon, lon_err = _parse_coord(lon_raw, "Longitude", -180, 180)
        if lat_err:
            # A latitude past ±90 next to a longitude that would be a valid
            # latitude is the classic swapped-columns paste.
            if lat is not None and lon is not None and abs(lon) <= 90:
                lat_err += " (latitude and longitude may be swapped)"
            problems.append(lat_err)
        if lon_err:
            problems.append(lon_err)

        cap_text = (cap_raw or "").strip().replace(",", "").replace(" ", "")
        cap = None
        if cap_text:
            try:
                cap = int(cap_text)
            except ValueError:
                problems.append("Max items must be a whole number, or blank for no limit")
            else:
                if cap < 1:
                    problems.append("Max items must be at least 1, or blank for no limit")

        if problems:
            errors.append(f"Depot {i}: " + "; ".join(problems))
        else:
            coords.append((lat, lon))
            caps.append(cap)

    if errors:
        raise ValueError("\n".join(errors))
    return coords, (None if all(c is None for c in caps) else caps)


def describe_fixed_depots(rows):
    """One-line summary of the fixed-depot rows for the main window."""
    n = len(rows)
    noun = "depot" if n == 1 else "depots"
    try:
        _, caps = parse_fixed_depots(rows)
    except ValueError:
        return f"Details needed for {n} fixed {noun}"
    capped = [c for c in (caps or []) if c is not None]
    if not capped:
        return f"{n} fixed {noun} set, no capacity limits"
    caps_text = " / ".join(f"{c:,}" for c in capped)
    uncapped = n - len(capped)
    tail = f", {uncapped} no limit" if uncapped else ""
    return f"{n} fixed {noun} set: {len(capped)} capped ({caps_text} items){tail}"


def format_cap_note(depots_df):
    """One-line note when any fixed-depot demand cap was applied; else None."""
    if "max_points" not in depots_df.columns:
        return None
    if depots_df["max_points"].dropna().empty:
        return None
    parts = []
    for _, row in depots_df.iterrows():
        if not row.get("is_fixed", False):
            continue
        cap = row.get("max_points")
        if cap == cap:  # not NaN
            name = row.get("depot_name") or f"depot {int(row['depot_id'])}"
            parts.append(f"{name} ≤ {int(cap)} items")
    if not parts:
        return None
    return (
        "Demand cap applied: "
        + "; ".join(parts)
        + " (uncapped fixed/free unlimited)"
    )


def format_unserved_note(assignments_df):
    """One-line note when capacity left demand unserved (cluster_id -1); else None."""
    unserved = assignments_df["cluster_id"] == -1
    if not unserved.any():
        return None
    demand = float(assignments_df.loc[unserved, "demand"].sum())
    total = float(assignments_df["demand"].sum())
    pct = demand / total * 100.0 if total else 0.0
    return (f"Unserved: {int(unserved.sum()):,} points, {demand:,.0f} items "
            f"({pct:.1f}% of demand) exceed total depot capacity")


# ============================================================
# GUI Application
# ============================================================

class CogApp(ctk.CTk):
    """CustomTkinter application window for the Centre of Gravity solver."""

    def __init__(self):
        super().__init__()
        self.title("Centre of Gravity - Depot Location Solver")
        self.geometry("900x840")
        self.minsize(820, 680)

        self._solving = False

        # One monospace font for both textboxes so the depot table's
        # space-aligned columns line up (proportional fonts would scatter them).
        families = set(tkfont.families())
        mono_family = "Consolas" if "Consolas" in families else "Courier New"
        self.mono_font = ctk.CTkFont(family=mono_family, size=12)
        title_font = ctk.CTkFont(size=20, weight="bold")
        sub_font = ctk.CTkFont(size=13)

        # Root layout: everything stretches wide; the tabview row grows on resize.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        # ---------- Header: title left, file indicator right ----------
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 6))
        ctk.CTkLabel(header, text="Centre of Gravity", font=title_font).pack(side="left")
        ctk.CTkLabel(header, text="  Depot Location Solver", font=sub_font,
                     text_color="gray60").pack(side="left")
        self.status_label = ctk.CTkLabel(header, text="No file selected",
                                          text_color=STATUS_RED)
        self.status_label.pack(side="right")

        # ---------- Input card ----------
        card = ctk.CTkFrame(self, corner_radius=CARD_RADIUS)
        card.grid(row=1, column=0, sticky="ew", padx=PAD, pady=6)
        card.grid_columnconfigure(1, weight=1)

        # CSV file selection
        ctk.CTkLabel(card, text="CSV File:").grid(
            row=0, column=0, padx=(PAD, GAP), pady=(PAD, GAP), sticky="w")
        self.csv_path_var = ctk.StringVar()
        ctk.CTkEntry(card, textvariable=self.csv_path_var, corner_radius=WIDGET_RADIUS).grid(
            row=0, column=1, padx=GAP, pady=(PAD, GAP), sticky="ew")
        ctk.CTkButton(card, text="Browse", width=90, corner_radius=WIDGET_RADIUS,
                      fg_color=PRIMARY, hover_color=PRIMARY_HOVER, text_color=PRIMARY_TEXT,
                      command=self.select_csv_file).grid(
            row=0, column=2, padx=(GAP, PAD), pady=(PAD, GAP))

        # Solver mode selector (CTkSegmentedButton — three fully-themed pills,
        # all visible at once, no native dropdown popup to break the theme).
        # Unselected pills = slate, active pill = emerald; white text reads on both.
        ctk.CTkLabel(card, text="Solver Mode:").grid(
            row=1, column=0, padx=(PAD, GAP), pady=GAP, sticky="w")
        self.mode_var = ctk.StringVar()
        ctk.CTkSegmentedButton(
            card, variable=self.mode_var,
            values=["Single Depot", "Multi Depot", "Fixed + Variable Depots"],
            corner_radius=WIDGET_RADIUS, fg_color=PRIMARY_HOVER,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=PRIMARY, unselected_hover_color=PRIMARY_HOVER,
            text_color=PRIMARY_TEXT,
        ).grid(row=1, column=1, columnspan=2, padx=GAP, pady=GAP, sticky="ew")

        # ---------- Per-mode parameter frames (only the selected one shows) ----------
        # Transparent frames blend into the card; each holds exactly the fields
        # its mode's solver reads. Switching hides all, shows the active.
        self._mode_frames = {}

        # Single Depot: no extra parameters.
        frame_single = ctk.CTkFrame(card, fg_color="transparent")
        ctk.CTkLabel(frame_single, text="Single Depot uses no extra parameters.",
                     text_color="gray60").grid(row=0, column=0, sticky="w")
        self._mode_frames["Single Depot"] = frame_single

        # Multi Depot: number of depots k (no point cap — caps apply to fixed only).
        frame_multi = ctk.CTkFrame(card, fg_color="transparent")
        frame_multi.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame_multi, text="Number of depots (k):").grid(
            row=0, column=0, padx=(0, GAP), pady=4, sticky="w")
        self.entry_k = ctk.CTkEntry(frame_multi, corner_radius=WIDGET_RADIUS)
        self.entry_k.grid(row=0, column=1, pady=4, sticky="ew")
        self._mode_frames["Multi Depot"] = frame_multi

        # Fixed + Variable: m pinned depots plus n_free optimised ones. Each
        # fixed depot's lat/lon/cap is edited in the table right here, one row
        # per depot, or filled in one go from a CSV ("Load from CSV…"). The
        # typed strings live in self.fixed_depot_rows, so lowering m only
        # hides the extra rows' values, never deletes them.
        frame_fixed = ctk.CTkFrame(card, fg_color="transparent")
        frame_fixed.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame_fixed, text="Number of fixed depots:").grid(
            row=0, column=0, padx=(0, GAP), pady=4, sticky="w")
        self.fixed_count_var = ctk.StringVar(value="1")
        self.entry_fixed_count = ctk.CTkEntry(
            frame_fixed, textvariable=self.fixed_count_var,
            corner_radius=WIDGET_RADIUS, width=80)
        self.entry_fixed_count.grid(row=0, column=1, pady=4, sticky="w")
        ctk.CTkButton(
            frame_fixed, text="Load from CSV…", corner_radius=WIDGET_RADIUS,
            fg_color=PRIMARY, hover_color=PRIMARY_HOVER, text_color=PRIMARY_TEXT,
            command=self.select_fixed_depots_csv,
        ).grid(row=0, column=2, padx=(GAP, 0), pady=4, sticky="e")

        self.fixed_table = ctk.CTkScrollableFrame(frame_fixed, height=FIXED_TABLE_ROW_H)
        self.fixed_table.grid(row=1, column=0, columnspan=3, pady=4, sticky="ew")
        for col, text in enumerate(("Depot", "Name", "Latitude", "Longitude", "Max items")):
            ctk.CTkLabel(self.fixed_table, text=text, text_color="gray40").grid(
                row=0, column=col, padx=(0, GAP), sticky="w")
        # One (number label, name, lat, lon, cap entries) tuple per visible row.
        self._fixed_row_widgets = []
        self.fixed_entries = []         # [(lat_entry, lon_entry, cap_entry), ...]
        self.fixed_name_entries = []    # [name_entry, ...], same order

        self.fixed_summary_label = ctk.CTkLabel(
            frame_fixed, text="", text_color="gray40", anchor="w", justify="left",
            wraplength=780)
        self.fixed_summary_label.grid(
            row=2, column=0, columnspan=3, pady=(0, 4), sticky="w")

        ctk.CTkLabel(frame_fixed, text="Number of free depots:").grid(
            row=3, column=0, padx=(0, GAP), pady=4, sticky="w")
        self.entry_n_free = ctk.CTkEntry(frame_fixed, corner_radius=WIDGET_RADIUS)
        self.entry_n_free.grid(row=3, column=1, columnspan=2, pady=4, sticky="ew")

        self._mode_frames["Fixed + Variable Depots"] = frame_fixed

        self.fixed_depot_rows = []      # [(lat, lon, max_items) strings, ...]
        self.fixed_depot_names = []     # optional name per row ("" = unnamed)
        self.free_depot_names = []      # names of no-location rows from a CSV
        self._suppress_count_trace = False
        self.fixed_count_var.trace_add("write", self._on_fixed_count_change)
        self._rebuild_fixed_table()
        self._refresh_fixed_summary()

        # Record each frame's position once, then hide; _on_mode_change replays it.
        for frame in self._mode_frames.values():
            grid_hideable(frame, row=2, column=0, columnspan=3,
                          padx=PAD, pady=(0, GAP), sticky="ew")
            hide_widget(frame)

        # Output folder
        ctk.CTkLabel(card, text="Output Folder:").grid(
            row=3, column=0, padx=(PAD, GAP), pady=(GAP, PAD), sticky="w")
        self.output_dir_var = ctk.StringVar(value=default_output_dir())
        ctk.CTkEntry(card, textvariable=self.output_dir_var, corner_radius=WIDGET_RADIUS).grid(
            row=3, column=1, padx=GAP, pady=(GAP, PAD), sticky="ew")
        ctk.CTkButton(card, text="Browse", width=90, corner_radius=WIDGET_RADIUS,
                      fg_color=PRIMARY, hover_color=PRIMARY_HOVER, text_color=PRIMARY_TEXT,
                      command=self.select_output_folder).grid(
            row=3, column=2, padx=(GAP, PAD), pady=(GAP, PAD))

        # ---------- Run / Stop buttons + progress (solver runs in a child
        # process, so Stop can end it at once, even mid-solve) ----------
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=PAD, pady=6)
        buttons.grid_columnconfigure(0, weight=1)
        self.run_button = ctk.CTkButton(
            buttons, text="Run Solver", command=self.run_solver,
            corner_radius=WIDGET_RADIUS, height=44, text_color=ACCENT_TEXT,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.run_button.grid(row=0, column=0, sticky="ew")
        self.stop_button = ctk.CTkButton(
            buttons, text="Stop", command=self.stop_solver, width=110,
            corner_radius=WIDGET_RADIUS, height=44, text_color="white",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=STATUS_RED, hover_color="#b91c1c", state="disabled")
        self.stop_button.grid(row=0, column=1, padx=(GAP, 0))
        self._proc = None           # running solver process, if any
        self._queue = None          # its message queue
        self._poll_id = None        # after() id of the next queue check
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        progress_frame.grid(row=3, column=0, sticky="ew", padx=PAD, pady=(0, 4))
        progress_frame.grid_columnconfigure(0, weight=1)
        self.run_status_label = ctk.CTkLabel(
            progress_frame, text="Ready", text_color="gray60", anchor="w")
        self.run_status_label.grid(row=0, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(
            progress_frame, height=12, corner_radius=6,
            progress_color=ACCENT, fg_color="#e2e8f0")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.progress.set(0)

        # ---------- Results / Log tabview ----------
        self.tabview = ctk.CTkTabview(
            self, corner_radius=CARD_RADIUS,
            segmented_button_selected_color=PRIMARY,
            segmented_button_selected_hover_color=PRIMARY_HOVER)
        self.tabview.grid(row=4, column=0, sticky="nsew", padx=PAD, pady=(6, PAD))
        self.tabview.add("Results")
        self.tabview.add("Log")

        # Results tab: the final depot table (the answer). wrap="none" keeps the
        # space-aligned columns intact; a horizontal scrollbar appears if narrow.
        results_tab = self.tabview.tab("Results")
        results_tab.grid_columnconfigure(0, weight=1)
        results_tab.grid_rowconfigure(0, weight=1)
        self.output_box = ctk.CTkTextbox(results_tab, font=self.mono_font, wrap="none")
        self.output_box.grid(row=0, column=0, sticky="nsew")

        # Log tab: the running story of the run (timestamps, saved path, errors).
        log_tab = self.tabview.tab("Log")
        log_tab.grid_columnconfigure(0, weight=1)
        log_tab.grid_rowconfigure(0, weight=1)
        self.log_box = ctk.CTkTextbox(log_tab, font=self.mono_font, wrap="none")
        self.log_box.grid(row=0, column=0, sticky="nsew")

        # ---------- Mode switching + default selection ----------
        # trace_add fires on programmatic .set() too (the smoke test uses that),
        # so field-switching works whether a human clicks or a test sets the mode.
        # CTkOptionMenu's own command= does NOT fire on .set(), so the trace is
        # the load-bearing switch.
        self.mode_var.trace_add("write", self._on_mode_change)
        self.mode_var.set("Single Depot")

    # --------------------------------------------------------
    # Mode switching + logging helpers
    # --------------------------------------------------------

    def _on_mode_change(self, *_):
        """Show only the selected mode's parameter frame."""
        choice = self.mode_var.get()
        for frame in self._mode_frames.values():
            hide_widget(frame)
        frame = self._mode_frames.get(choice)
        if frame is not None:
            show_widget(frame)

    def _fixed_names_for(self, count):
        """The first `count` stored fixed-depot names, padded with blanks."""
        names = list(self.fixed_depot_names[:count])
        return names + [""] * (count - len(names))

    def _fixed_rows_for(self, count):
        """The first `count` stored fixed-depot rows, padded with blanks."""
        rows = list(self.fixed_depot_rows[:count])
        return rows + [("", "", "")] * (count - len(rows))

    def _refresh_fixed_summary(self, *_):
        """Re-describe the fixed depots under the table."""
        try:
            count = parse_fixed_count(self.fixed_count_var.get())
        except ValueError as e:
            self.fixed_summary_label.configure(text=str(e), text_color=STATUS_RED)
            return
        text = describe_fixed_depots(self._fixed_rows_for(count))
        color = STATUS_RED if text.startswith("Details needed") else "gray40"
        self.fixed_summary_label.configure(text=text, text_color=color)

    def _on_fixed_count_change(self, *_):
        """Grow or shrink the table to the typed count (invalid counts leave it)."""
        if self._suppress_count_trace:
            return
        try:
            count = parse_fixed_count(self.fixed_count_var.get())
        except ValueError:
            count = None
        if count is not None and count != len(self.fixed_entries):
            self._rebuild_fixed_table()
        self._refresh_fixed_summary()

    def _rebuild_fixed_table(self):
        """Redraw one editable row per fixed depot from self.fixed_depot_rows."""
        try:
            count = parse_fixed_count(self.fixed_count_var.get())
        except ValueError:
            count = len(self.fixed_entries)
        for widgets in self._fixed_row_widgets:
            for widget in widgets:
                widget.destroy()
        self._fixed_row_widgets = []
        self.fixed_entries = []
        self.fixed_name_entries = []

        names = self._fixed_names_for(count)
        for i, values in enumerate(self._fixed_rows_for(count)):
            number = ctk.CTkLabel(self.fixed_table, text=str(i + 1))
            number.grid(row=i + 1, column=0, padx=(0, GAP), pady=2, sticky="w")
            name_var = ctk.StringVar(value=names[i])
            name_entry = ctk.CTkEntry(self.fixed_table, textvariable=name_var,
                                      corner_radius=WIDGET_RADIUS, width=220,
                                      placeholder_text="optional")
            name_entry.grid(row=i + 1, column=1, padx=(0, GAP), pady=2, sticky="w")
            name_var.trace_add(
                "write", lambda *_a, r=i, v=name_var: self._on_fixed_name_edit(r, v))
            self.fixed_name_entries.append(name_entry)
            row_entries = []
            for col, (placeholder, width) in enumerate(
                    (("Latitude", 130), ("Longitude", 130), ("no limit", 110))):
                var = ctk.StringVar(value=values[col])
                entry = ctk.CTkEntry(self.fixed_table, textvariable=var,
                                     corner_radius=WIDGET_RADIUS, width=width,
                                     placeholder_text=placeholder)
                entry.grid(row=i + 1, column=col + 2, padx=(0, GAP), pady=2, sticky="w")
                var.trace_add(
                    "write", lambda *_a, r=i, c=col, v=var: self._on_fixed_cell_edit(r, c, v))
                row_entries.append(entry)
            self.fixed_entries.append(tuple(row_entries))
            self._fixed_row_widgets.append((number, name_entry, *row_entries))

        visible = min(max(count, 1), FIXED_TABLE_VISIBLE_ROWS)
        self.fixed_table.configure(height=FIXED_TABLE_ROW_H * visible)

    def _on_fixed_cell_edit(self, row, col, var):
        """Store one edited table cell and re-describe the depots."""
        stored = list(self.fixed_depot_rows)
        stored += [("", "", "")] * (row + 1 - len(stored))
        values = list(stored[row])
        values[col] = var.get()
        stored[row] = tuple(values)
        self.fixed_depot_rows = stored
        self._refresh_fixed_summary()

    def _on_fixed_name_edit(self, row, var):
        """Store one edited depot name."""
        names = list(self.fixed_depot_names)
        names += [""] * (row + 1 - len(names))
        names[row] = var.get()
        self.fixed_depot_names = names

    def set_fixed_depot_rows(self, rows, names=None):
        """Replace every fixed depot (e.g. from a loaded CSV) and show them all."""
        self.fixed_depot_rows = [tuple(r) for r in rows]
        self.fixed_depot_names = list(names) if names is not None else [""] * len(rows)
        self.free_depot_names = []
        self._suppress_count_trace = True
        try:
            self.fixed_count_var.set(str(len(rows)))
        finally:
            self._suppress_count_trace = False
        self._rebuild_fixed_table()
        self._refresh_fixed_summary()

    def _show_fixed_problem(self, message):
        """Report a fixed-depot problem in the main window (no popup)."""
        self.log(f"ERROR: {message}")
        self.fixed_summary_label.configure(text=message, text_color=STATUS_RED)

    def load_fixed_depots_from(self, file_path):
        """Fill the fixed-depot table from a CSV. Returns True if it loaded."""
        name = os.path.basename(file_path)
        try:
            rows, names, free_names = load_fixed_depots(file_path)
        except (OSError, ValueError) as e:
            self._show_fixed_problem(f"Could not load {name}: {e}")
            return False
        self.set_fixed_depot_rows(rows, names)
        # Rows without Lat/Long are sites still to be placed: free depots.
        self.free_depot_names = free_names
        self.entry_n_free.delete(0, "end")
        self.entry_n_free.insert(0, str(len(free_names)))
        n = len(rows)
        self.log(f"Loaded {n} fixed depot{'s' if n != 1 else ''} from {name}")
        free_note = ""
        if free_names:
            labels = ", ".join(n or "unnamed" for n in free_names)
            free_note = (f"\n+ {len(free_names)} free "
                         f"depot{'s' if len(free_names) != 1 else ''} "
                         f"(no Lat/Long, placed by the solver): {labels}")
            self.log(free_note.strip())
        try:
            parse_fixed_depots(rows)
        except ValueError as e:
            self._show_fixed_problem(f"{name} needs attention:\n{e}{free_note}")
            return True
        self.fixed_summary_label.configure(
            text=f"{describe_fixed_depots(rows)} (loaded from {name}){free_note}",
            text_color=STATUS_GREEN)
        return True

    def log(self, message):
        """Append one timestamped line to the Log tab."""
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{stamp}] {message}\n")
        self.log_box.see("end")

    def _show_results(self, assignments_df, depots_df, map_file, solver_type,
                       objective=None, csv_file=None):
        """Depot table (+ objective / cap note) -> Results; report path -> Log; open map."""
        self.output_box.delete("1.0", "end")
        self.output_box.insert("end", depots_df.to_string())
        cap_note = format_cap_note(depots_df)
        if cap_note is not None:
            self.output_box.insert("end", f"\n\n{cap_note}")
            self.log(cap_note)
        unserved_note = format_unserved_note(assignments_df)
        if unserved_note is not None:
            self.output_box.insert("end", f"\n\n{unserved_note}")
            self.log(unserved_note)
        if objective is not None:
            # Unserved points add nothing to the objective, so average over served demand.
            served = assignments_df["cluster_id"] != -1
            summary = format_objective(
                objective, float(assignments_df.loc[served, "demand"].sum()))
            self.output_box.insert("end", f"\n\n{summary}")
            self.log(summary)
        run_meta = {
            "dataset": Path(csv_file).stem if csv_file else None,
            "mode_tag": _MODE_FILE_TAG.get(solver_type, "run"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        html_path = render_dashboard(
            assignments_df, depots_df, map_file, solver_type,
            objective=objective, run_meta=run_meta)
        self.log(f"Report saved to: {html_path}")
        display_map_in_browser(html_path)

    def _fail(self, message):
        """Log the error and surface it in a dialog."""
        self.log(f"ERROR: {message}")
        messagebox.showerror("Error", message)

    def _set_busy(self, busy, status_text=None):
        """Enable/disable Run and show progress state. Must run on the UI thread."""
        self._solving = busy
        if busy:
            self.run_button.configure(state="disabled", text="Running…")
            self.stop_button.configure(state="normal")
            self.progress.set(0)
            self.run_status_label.configure(
                text=status_text or "Starting…", text_color=PRIMARY)
        else:
            self.run_button.configure(state="normal", text="Run Solver")
            self.stop_button.configure(state="disabled")
            if status_text is not None:
                self.run_status_label.configure(text=status_text, text_color="gray60")

    def _on_progress(self, done, total, message=None):
        """Update the progress bar from a solver seed tick (UI thread only)."""
        total = max(int(total), 1)
        done = max(0, min(int(done), total))
        self.progress.set(done / total)
        label = message or f"Running… {done}/{total}"
        self.run_status_label.configure(text=label, text_color=PRIMARY)

    def _progress_from_worker(self, done, total, message=None):
        """Thread-safe progress: schedule the bar update on the UI thread."""
        self.after(
            0,
            lambda d=done, t=total, m=message: self._on_progress(d, t, m),
        )

    # --------------------------------------------------------
    # File pickers
    # --------------------------------------------------------

    def select_csv_file(self):
        """Open file dialog to select CSV file."""
        file_path = filedialog.askopenfilename(
            title="Select CSV File",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if file_path:
            self.csv_path_var.set(file_path)
            self.status_label.configure(
                text=f"File: {os.path.basename(file_path)}", text_color=STATUS_GREEN)

    def select_fixed_depots_csv(self):
        """Pick a CSV of fixed depots (latitude, longitude, optional capacity)."""
        file_path = filedialog.askopenfilename(
            title="Select Fixed Depots CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if file_path:
            self.load_fixed_depots_from(file_path)

    def select_output_folder(self):
        """Open folder dialog to select HTML output directory."""
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.output_dir_var.set(folder)

    # --------------------------------------------------------
    # Run (solve off the UI thread so the window stays responsive)
    # --------------------------------------------------------

    def _prepare_job(self):
        """Validate UI inputs on the main thread. Returns a job dict or None."""
        choice = self.mode_var.get()
        csv_file = self.csv_path_var.get()

        if not choice:
            messagebox.showerror("Error", "Please select a solver mode.")
            return None
        if not csv_file:
            messagebox.showerror("Error", "Please select a CSV file.")
            return None

        out_dir = self.output_dir_var.get().strip() or default_output_dir()
        map_file = build_report_path(out_dir, csv_file, choice)
        job = {
            "choice": choice,
            "csv_file": csv_file,
            "map_file": map_file,
        }

        if choice == "Multi Depot":
            try:
                job["k"] = int(self.entry_k.get())
            except ValueError:
                self._fail("Please enter valid k value.")
                return None

        elif choice == "Fixed + Variable Depots":
            try:
                m = parse_fixed_count(self.fixed_count_var.get())
            except ValueError as e:
                self._show_fixed_problem(str(e))
                return None
            try:
                fixed_coords, max_points = parse_fixed_depots(self._fixed_rows_for(m))
            except ValueError as e:
                self._show_fixed_problem(f"Fixed depot details need attention:\n{e}")
                return None
            try:
                n_free = int(self.entry_n_free.get())
            except ValueError:
                self._show_fixed_problem(
                    "Please enter a whole number of free depots (0 or more).")
                return None
            if n_free < 0:
                self._show_fixed_problem("Number of free depots cannot be negative.")
                return None
            job["n_free"] = n_free
            job["fixed_coords"] = fixed_coords
            job["max_points"] = max_points
            job["fixed_names"] = [n.strip() for n in self._fixed_names_for(m)]
            job["free_names"] = [n.strip() for n in self.free_depot_names[:n_free]]

        return job

    def _execute_job(self, job):
        """Run the selected solver on this process. Returns a result dict."""
        return execute_job(job, self._progress_from_worker)

    def _finish_ok(self, result):
        """UI-thread success path after the worker returns."""
        try:
            self.progress.set(1)
            self.run_status_label.configure(text="Building report…", text_color=PRIMARY)
            cap_stats = result.get("cap_stats")
            if cap_stats is not None:
                self.log(
                    f"MCF split-repair: last={cap_stats.get('split_points_last', 0)} "
                    f"max={cap_stats.get('split_points_max', 0)} "
                    f"calls={cap_stats.get('mcf_calls', 0)}"
                )
            self._show_results(
                result["assignments_df"],
                result["depots_df"],
                result["map_file"],
                result["solver_type"],
                result["objective"],
                result["csv_file"],
            )
            self._set_busy(False, status_text="Done")
            self.progress.set(1)
        except Exception as e:
            self._set_busy(False, status_text="Error")
            self._fail(f"An error occurred: {str(e)}")

    def _finish_err(self, exc):
        """UI-thread error path after the worker fails."""
        self._set_busy(False, status_text="Error")
        if isinstance(exc, FileNotFoundError):
            self._fail(f"File not found: {exc}")
        elif isinstance(exc, KeyError):
            self._fail(f"CSV file missing required column: {str(exc)}")
        else:
            self._fail(f"An error occurred: {str(exc)}")

    def run_solver(self, background=True):
        """
        Execute the selected solver and display results.

        By default the heavy solve runs on a background thread so the window
        stays responsive and the progress bar can update. Pass background=False
        for tests that need the results immediately on the calling thread.
        """
        if self._solving:
            return

        job = self._prepare_job()
        if job is None:
            return

        choice = job["choice"]
        self.log(f"Running {choice} on {os.path.basename(job['csv_file'])}")
        if choice == "Fixed + Variable Depots":
            caps = job["max_points"] or [None] * len(job["fixed_coords"])
            for i, ((lat, lon), cap, name) in enumerate(
                    zip(job["fixed_coords"], caps, job["fixed_names"]), start=1):
                limit = f"max {cap:,} items" if cap is not None else "no limit"
                label = f"Fixed depot {i} ({name})" if name else f"Fixed depot {i}"
                self.log(f"{label}: {lat}, {lon}, {limit}")
        if choice == "Fixed + Variable Depots" and job.get("max_points") is not None:
            self.log(
                f"Max demand (items) per fixed depot: {job['max_points']} "
                f"(None = unconstrained; free depots unlimited)"
            )

        self._set_busy(True, status_text="Loading data…")

        if not background:
            try:
                result = self._execute_job(job)
                self._finish_ok(result)
            except Exception as e:
                self._finish_err(e)
            return

        # "spawn" on every platform: a forked copy of a process running Tk
        # is not safe, and spawn is all Windows has anyway.
        ctx = multiprocessing.get_context("spawn")
        self._queue = ctx.Queue()
        self._proc = ctx.Process(
            target=run_job_in_child, args=(job, self._queue), daemon=True)
        self._proc.start()
        self.run_status_label.configure(text="Starting solver…", text_color=PRIMARY)
        self._poll_id = self.after(WORKER_POLL_MS, self._poll_worker)

    def _poll_worker(self):
        """Apply the solver process's progress; finish when it reports back."""
        self._poll_id = None
        if self._proc is None:
            return
        # Read liveness BEFORE draining: a child that has exited has already
        # flushed everything it sent, so an empty queue then means it died.
        alive = self._proc.is_alive()
        try:
            while True:
                kind, *payload = self._queue.get_nowait()
                if kind == "progress":
                    self._on_progress(*payload)
                    continue
                self._end_worker()
                if kind == "ok":
                    self._finish_ok(payload[0])
                else:
                    self._finish_err(payload[0])
                return
        except queue.Empty:
            pass
        if not alive:
            code = self._proc.exitcode
            self._end_worker()
            self._finish_err(RuntimeError(
                f"The solver stopped unexpectedly (exit code {code})."))
            return
        self._poll_id = self.after(WORKER_POLL_MS, self._poll_worker)

    def _end_worker(self):
        """Stop polling, end the solver process if it is still running, tidy up."""
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        proc, q = self._proc, self._queue
        self._proc = self._queue = None
        if proc is not None:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=3)
                if proc.is_alive():
                    proc.kill()
            proc.join(timeout=1)
        if q is not None:
            q.cancel_join_thread()
            q.close()

    def stop_solver(self):
        """Cancel the running solve; nothing is written for a stopped run."""
        if self._proc is None:
            return
        self._end_worker()
        self.log("Run stopped by user.")
        self.progress.set(0)
        self._set_busy(False, status_text="Stopped")

    def _on_close(self):
        """Closing the window also ends any solve still running."""
        self._end_worker()
        self.destroy()


def main():
    """Launch the GUI application."""
    app = CogApp()
    app.mainloop()


if __name__ == "__main__":
    main()
