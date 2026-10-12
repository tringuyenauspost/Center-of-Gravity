"""GUI smoke test: drive the Tkinter app end-to-end for each solver mode.

Runs the real run_solver path (load CSV -> solve -> write HTML report) with
the browser popup and error dialogs patched out. One CogApp instance is
shared across the modes, mirroring real usage (one window, the user switches
mode) and avoiding repeated Tcl/Tk re-initialisation in a single process.
"""

import tkinter as tk

import customtkinter as ctk
import pytest

import cog.app as cog_app
import generate_goldens as gg

MODE_SETUPS = {
    "Single Depot": {},
    "Multi Depot": {"entry_k": "3"},
    "Fixed + Variable Depots": {"entry_n_free": "2"},
}


@pytest.fixture(scope="module")
def app():
    gui = cog_app.CogApp()
    yield gui
    gui.destroy()


def set_fixed_depots(app, rows):
    """Put fixed-depot rows straight into app state and the table."""
    app.set_fixed_depot_rows(rows)


@pytest.mark.parametrize("mode", list(MODE_SETUPS))
def test_gui_runs_each_mode_end_to_end(mode, app, tmp_path, monkeypatch):
    monkeypatch.setattr(cog_app, "display_map_in_browser", lambda html_path: None)
    dialog_errors = []
    monkeypatch.setattr(
        cog_app.messagebox, "showerror", lambda title, msg: dialog_errors.append(msg)
    )

    app.csv_path_var.set(str(gg.FIXTURE))
    app.output_dir_var.set(str(tmp_path))
    app.mode_var.set(mode)
    for entry_name, value in MODE_SETUPS[mode].items():
        entry = getattr(app, entry_name)
        entry.delete(0, tk.END)
        entry.insert(0, value)
    if mode == "Fixed + Variable Depots":
        set_fixed_depots(app, [(str(gg.FIXED_LAT), str(gg.FIXED_LON), "")])

    # Sync path: tests need results before asserting (no background thread).
    app.run_solver(background=False)

    assert dialog_errors == []
    reports = list(tmp_path.glob("clusters_map_*.html"))
    assert len(reports) == 1, f"expected one unique report, found {reports}"
    results_text = app.output_box.get("1.0", tk.END)
    assert "depot_lat" in results_text
    # "Report saved to:" now lives in the Log tab, not the Results table.
    log_text = app.log_box.get("1.0", tk.END)
    assert "Report saved to:" in log_text
    assert not app._solving
    assert str(app.run_button.cget("state")) == "normal"


def test_gui_fixed_with_cap_end_to_end(app, tmp_path, monkeypatch):
    """Fixed mode with demand cap set: solve, log the cap, report written."""
    monkeypatch.setattr(cog_app, "display_map_in_browser", lambda html_path: None)
    dialog_errors = []
    monkeypatch.setattr(
        cog_app.messagebox, "showerror", lambda title, msg: dialog_errors.append(msg)
    )

    # Capacity runs require integer demand; fixture is fractional — use unit demand.
    import pandas as pd
    from pathlib import Path

    df = pd.read_csv(gg.FIXTURE)
    # load_demand expects latitude/longitude/demand_items
    if "demand_items" in df.columns:
        df["demand_items"] = 1
    int_csv = tmp_path / "fixture_unit_demand.csv"
    df.to_csv(int_csv, index=False)

    app.csv_path_var.set(str(int_csv))
    app.output_dir_var.set(str(tmp_path))
    app.mode_var.set("Fixed + Variable Depots")
    app.entry_n_free.delete(0, tk.END)
    app.entry_n_free.insert(0, "2")
    set_fixed_depots(app, [(str(gg.FIXED_LAT), str(gg.FIXED_LON), "5")])

    app.run_solver(background=False)

    assert dialog_errors == []
    results_text = app.output_box.get("1.0", tk.END)
    log_text = app.log_box.get("1.0", tk.END)
    assert "max_points" in results_text or "Demand cap applied" in results_text or "Cap applied" in results_text
    assert "Max demand (items) per fixed depot: [5]" in log_text
    assert f"Fixed depot 1: {gg.FIXED_LAT}, {gg.FIXED_LON}, max 5 items" in log_text
    assert "Demand cap applied" in log_text or "Cap applied" in log_text
    assert "split-repair" in log_text or "MCF split" in log_text
    assert list(tmp_path.glob("clusters_map_*.html"))
    html = next(tmp_path.glob("clusters_map_*.html")).read_text(encoding="utf-8")
    assert "5 items" in html or "/ 5" in html  # demand cap label on fixed depot
    assert "min-cost-flow" in html or "max-demand" in html or "demand" in html


def test_log_persists_across_runs(app, tmp_path, monkeypatch):
    """The Log tab accumulates across runs; it is never cleared between solves.

    Two solves in one session, each writing to a distinct output dir. Both
    runs' "Report saved to:" lines must remain in the Log afterwards -- that
    durable surface is the whole reason for the Log tab. Results, by contrast,
    is rewritten each run (correct and intended).
    """
    monkeypatch.setattr(cog_app, "display_map_in_browser", lambda html_path: None)
    monkeypatch.setattr(cog_app.messagebox, "showerror", lambda title, msg: None)

    app.csv_path_var.set(str(gg.FIXTURE))
    app.mode_var.set("Single Depot")

    # First run -> report lands under run1/
    run1_dir = tmp_path / "run1"
    app.output_dir_var.set(str(run1_dir))
    app.run_solver(background=False)
    assert len(list(run1_dir.glob("clusters_map_*.html"))) == 1

    # Second run -> report lands under run2/ (unique dir, unique filename)
    run2_dir = tmp_path / "run2"
    app.output_dir_var.set(str(run2_dir))
    app.run_solver(background=False)
    assert len(list(run2_dir.glob("clusters_map_*.html"))) == 1

    log_text = app.log_box.get("1.0", tk.END)
    assert "run1" in log_text, "first run's log line was lost -- the Log got cleared"
    assert "run2" in log_text

    # Sanity: the saved-path line belongs to the Log, never the Results table.
    assert "Report saved to:" not in app.output_box.get("1.0", tk.END)


def test_hidden_fields_survive_a_dpi_scaling_change(app):
    """A DPI change must not unfold every hidden field at once.

    customtkinter replays each widget's last grid() call on a scaling change,
    and grid_remove() leaves that record intact -- so hiding with it made every
    mode panel appear at once the moment the window met a monitor with
    different scaling. Real symptom on a second monitor; reproduced here by
    driving the scaling directly.
    """
    app.mode_var.set("Fixed + Variable Depots")
    app.update()

    def visible():
        return [k for k, f in app._mode_frames.items() if f.winfo_ismapped()]

    before = visible()
    assert before == ["Fixed + Variable Depots"]

    try:
        for scaling in (1.25, 0.8):
            ctk.set_widget_scaling(scaling)
            app.update()
            assert visible() == before, f"widgets reappeared at scaling {scaling}"
    finally:
        ctk.set_widget_scaling(1.0)
        app.update()


# ---- Fixed-depot table (in the main window, no popup) -----------------------

def fill(app, i, lat, lon, cap):
    """Type into table row i (0-based) the way a user would."""
    for entry, value in zip(app.fixed_entries[i], (lat, lon, cap)):
        entry.delete(0, tk.END)
        if value:
            entry.insert(0, value)


def table_rows(app):
    return [tuple(e.get() for e in row) for row in app.fixed_entries]


def test_table_has_one_row_per_typed_depot(app):
    set_fixed_depots(app, [])
    app.fixed_count_var.set("12")
    assert len(app.fixed_entries) == 12
    assert table_rows(app) == [("", "", "")] * 12


def test_typing_in_table_stores_rows_and_updates_summary(app):
    set_fixed_depots(app, [])
    app.fixed_count_var.set("3")
    fill(app, 0, "-31.9", "115.8", "20000")
    fill(app, 1, "-31.8", "115.7", "15,000")
    fill(app, 2, "-31.7", "115.6", "")

    assert app.fixed_depot_rows == [
        ("-31.9", "115.8", "20000"), ("-31.8", "115.7", "15,000"), ("-31.7", "115.6", ""),
    ]
    assert app.fixed_summary_label.cget("text") == (
        "3 fixed depots set: 2 capped (20,000 / 15,000 items), 1 no limit")


def test_lowering_then_raising_count_keeps_entered_rows(app):
    rows = [("-31.9", "115.8", ""), ("-31.8", "115.7", "7"), ("-31.7", "115.6", "")]
    set_fixed_depots(app, rows)

    app.fixed_count_var.set("1")
    assert table_rows(app) == rows[:1]
    assert app.fixed_summary_label.cget("text") == "1 fixed depot set, no capacity limits"

    app.fixed_count_var.set("3")
    assert table_rows(app) == rows


def test_count_change_never_opens_a_window(app):
    set_fixed_depots(app, [])
    app.fixed_count_var.set("4")
    app.update()
    assert not [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]


def test_run_with_missing_details_reports_inline(app, tmp_path, monkeypatch):
    monkeypatch.setattr(cog_app, "display_map_in_browser", lambda html_path: None)
    dialog_errors = []
    monkeypatch.setattr(
        cog_app.messagebox, "showerror", lambda title, msg: dialog_errors.append(msg)
    )
    app.csv_path_var.set(str(gg.FIXTURE))
    app.output_dir_var.set(str(tmp_path))
    app.mode_var.set("Fixed + Variable Depots")
    app.entry_n_free.delete(0, tk.END)
    app.entry_n_free.insert(0, "2")
    set_fixed_depots(app, [(str(gg.FIXED_LAT), str(gg.FIXED_LON), "")])
    app.fixed_count_var.set("2")               # depot 2 never entered

    app.run_solver(background=False)
    assert dialog_errors == []
    assert "Depot 2: Latitude is required" in app.fixed_summary_label.cget("text")
    assert "Depot 2: Latitude is required" in app.log_box.get("1.0", tk.END)
    assert not list(tmp_path.glob("clusters_map_*.html"))
    assert not app._solving


def test_invalid_fixed_count_is_reported_inline(app):
    app.fixed_count_var.set("0")
    assert "Number of fixed depots" in app.fixed_summary_label.cget("text")


# ---- Loading fixed depots from a CSV ------------------------------------------

EXAMPLE_FIXED_CSV = gg.ROOT / "inputs" / "fixed_depots_example.csv"


def test_load_example_csv_fills_the_table(app):
    assert app.load_fixed_depots_from(str(EXAMPLE_FIXED_CSV)) is True
    assert app.fixed_count_var.get() == "3"
    assert table_rows(app) == [
        ("-31.858", "115.892", "20,000"),
        ("-31.990", "115.950", "15,000"),
        ("-31.905", "115.810", ""),
    ]
    assert app.fixed_summary_label.cget("text") == (
        "3 fixed depots set: 2 capped (20,000 / 15,000 items), 1 no limit "
        "(loaded from fixed_depots_example.csv)")


def test_load_csv_without_capacity_column(app, tmp_path):
    path = tmp_path / "depots.csv"
    path.write_text("Lat,Long\n-31.9,115.8\n-31.8,115.7\n", encoding="utf-8")
    assert app.load_fixed_depots_from(str(path)) is True
    assert table_rows(app) == [("-31.9", "115.8", ""), ("-31.8", "115.7", "")]
    assert app.fixed_summary_label.cget("text").startswith(
        "2 fixed depots set, no capacity limits")


def test_load_csv_missing_columns_is_reported_inline(app, tmp_path, monkeypatch):
    dialog_errors = []
    monkeypatch.setattr(
        cog_app.messagebox, "showerror", lambda title, msg: dialog_errors.append(msg)
    )
    set_fixed_depots(app, [("-31.9", "115.8", "")])
    path = tmp_path / "bad.csv"
    path.write_text("x,y\n1,2\n", encoding="utf-8")
    assert app.load_fixed_depots_from(str(path)) is False
    assert dialog_errors == []
    assert "'Lat' and 'Long'" in app.fixed_summary_label.cget("text")
    assert app.fixed_depot_rows == [("-31.9", "115.8", "")]  # unchanged


# Same layout as the facility export (padded headers, quoted "28,793",
# "Flexible" = no limit, and a site with no Lat/Long yet = a free depot).
FACILITY_EXPORT = (
    "\ufeffWCC code,WCC name,WCC Type,Region, Delivery volume capacity , Lat , Long \r\n"
    '304020,DAREBIN PDC,PDEP,Melbourne Metro," 28,793 ",-37.743 , 145.029 \r\n'
    "305808,OAKLEIGH SOUTH PDC,PDEP,Melbourne Metro, Flexible ,-37.908 , 145.156 \r\n"
    ",NEW WESTERN HUB,,, Flexible ,,\r\n"
    ',PORT MELBOURNE DELIVERY,,,"16,502",-37.8317868,144.9403631'
)


def test_load_facility_export(app, tmp_path):
    path = tmp_path / "fixed_facilities.csv"
    path.write_text(FACILITY_EXPORT, encoding="utf-8")
    assert app.load_fixed_depots_from(str(path)) is True
    assert app.fixed_count_var.get() == "3"
    assert table_rows(app) == [
        ("-37.743", "145.029", "28,793"),
        ("-37.908", "145.156", ""),                 # Flexible -> no limit
        ("-37.8317868", "144.9403631", "16,502"),
    ]
    assert [e.get() for e in app.fixed_name_entries] == [
        "DAREBIN PDC", "OAKLEIGH SOUTH PDC", "PORT MELBOURNE DELIVERY"]
    # The row without Lat/Long becomes a free depot, named for the results.
    assert app.entry_n_free.get() == "1"
    assert app.free_depot_names == ["NEW WESTERN HUB"]
    text = app.fixed_summary_label.cget("text")
    assert text.startswith("3 fixed depots set: 2 capped (28,793 / 16,502 items), 1 no limit")
    assert "+ 1 free depot (no Lat/Long, placed by the solver): NEW WESTERN HUB" in text


def test_free_depot_from_csv_is_named_in_results(app, tmp_path, monkeypatch):
    monkeypatch.setattr(cog_app, "display_map_in_browser", lambda html_path: None)
    path = tmp_path / "depots.csv"
    path.write_text("WCC name,Lat,Long\nMALAGA PDC,-31.858,115.892\nNEW HUB,,\n",
                    encoding="utf-8")
    app.csv_path_var.set(str(gg.FIXTURE))
    app.output_dir_var.set(str(tmp_path))
    app.mode_var.set("Fixed + Variable Depots")
    app.load_fixed_depots_from(str(path))

    app.run_solver(background=False)
    results_text = app.output_box.get("1.0", tk.END)
    assert "MALAGA PDC" in results_text and "NEW HUB" in results_text


def test_load_csv_with_bad_row_lists_it(app, tmp_path):
    path = tmp_path / "depots.csv"
    path.write_text("latitude,longitude,capacity\n-31.9,115.8,\n115.7,-31.8,\n",
                    encoding="utf-8")
    assert app.load_fixed_depots_from(str(path)) is True
    text = app.fixed_summary_label.cget("text")
    assert "Depot 2:" in text and "may be swapped" in text


def test_gui_fixed_from_example_csv_end_to_end(app, tmp_path, monkeypatch):
    monkeypatch.setattr(cog_app, "display_map_in_browser", lambda html_path: None)
    dialog_errors = []
    monkeypatch.setattr(
        cog_app.messagebox, "showerror", lambda title, msg: dialog_errors.append(msg)
    )
    app.csv_path_var.set(str(gg.FIXTURE))
    app.output_dir_var.set(str(tmp_path))
    app.mode_var.set("Fixed + Variable Depots")
    app.entry_n_free.delete(0, tk.END)
    app.entry_n_free.insert(0, "1")
    # Uncapped copy: the fixture's demand is fractional, which caps reject.
    path = tmp_path / "depots.csv"
    text = EXAMPLE_FIXED_CSV.read_text()
    path.write_text(text.replace('" 20,000 "', "").replace('" 15,000 "', ""))
    app.load_fixed_depots_from(str(path))

    app.run_solver(background=False)
    assert dialog_errors == []
    results_text = app.output_box.get("1.0", tk.END)
    assert results_text.count("True") == 3     # three pinned depots
    assert list(tmp_path.glob("clusters_map_*.html"))
    # WCC names from the CSV label the depots in Results, Log and the report.
    assert "MALAGA PDC" in results_text and "depot_name" in results_text
    assert "Fixed depot 1 (MALAGA PDC): -31.858, 115.892" in app.log_box.get("1.0", tk.END)
    html = next(tmp_path.glob("clusters_map_*.html")).read_text(encoding="utf-8")
    assert "WELSHPOOL PDC <em>(fixed)</em>" in html


def test_renaming_a_depot_in_the_table(app):
    set_fixed_depots(app, [("-31.9", "115.8", "")])
    app.fixed_name_entries[0].insert(0, "MY DEPOT")
    assert app.fixed_depot_names == ["MY DEPOT"]
    app.fixed_count_var.set("2")
    assert [e.get() for e in app.fixed_name_entries] == ["MY DEPOT", ""]



# ---- Stop button (solver runs in a child process) ----------------------------

def wait_until_idle(app, timeout):
    import time
    end = time.monotonic() + timeout
    while app._solving and time.monotonic() < end:
        app.update()
        time.sleep(0.02)


def test_background_run_in_child_process_finishes(app, tmp_path, monkeypatch):
    monkeypatch.setattr(cog_app, "display_map_in_browser", lambda html_path: None)
    app.csv_path_var.set(str(gg.FIXTURE))
    app.output_dir_var.set(str(tmp_path))
    app.mode_var.set("Multi Depot")
    app.entry_k.delete(0, tk.END)
    app.entry_k.insert(0, "3")

    app.run_solver()
    assert str(app.stop_button.cget("state")) == "normal"
    wait_until_idle(app, 120)

    assert app.run_status_label.cget("text") == "Done"
    assert app._proc is None
    assert str(app.stop_button.cget("state")) == "disabled"
    assert list(tmp_path.glob("clusters_map_*.html"))


def test_stop_cancels_a_running_solve(app, tmp_path, monkeypatch):
    monkeypatch.setattr(cog_app, "display_map_in_browser", lambda html_path: None)
    app.csv_path_var.set(str(gg.FIXTURE))
    app.output_dir_var.set(str(tmp_path))
    app.mode_var.set("Single Depot")

    app.run_solver()
    proc = app._proc
    assert proc is not None and proc.is_alive()
    app.stop_solver()

    assert not proc.is_alive()
    assert app._proc is None and not app._solving
    assert app.run_status_label.cget("text") == "Stopped"
    assert str(app.run_button.cget("state")) == "normal"
    assert "Run stopped by user." in app.log_box.get("1.0", tk.END)
    app.update()
    assert not list(tmp_path.glob("clusters_map_*.html"))
