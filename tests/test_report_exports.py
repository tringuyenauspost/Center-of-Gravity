"""Export buttons + CSV/PNG export JS: sidebar placement, the flat one-row-per-point
CSV schema, and the no-formatted-numbers-in-raw-fields guarantee (see the
parseDemand() trap in report.html.j2).
"""

import json
import re
import tempfile
from pathlib import Path

import generate_goldens as gg

from cog.report import render_dashboard

RUN_RE = re.compile(r"const RUN = (\{.*?\});", re.DOTALL)
CSV_COLUMNS = ["point_index", "latitude", "longitude", "demand_items",
               "cluster_id", "depot_name", "depot_lat", "depot_lon",
               "objective_item_km"]
# Matches csvRow(['point_index', 'latitude', ..., 'objective_item_km']),
# tolerant of the actual line-wrapping/whitespace in the template.
CSV_HEADER_RE = re.compile(
    r"csvRow\(\[\s*" + r",\s*".join(f"'{c}'" for c in CSV_COLUMNS) + r"\s*\]\)"
)


def _render(mode, objective, run_meta=None):
    df = gg.load_fixture()
    if mode == "single":
        depots_df, assignments_df, objective = gg.cog.solve_single_depot_weighted_geometric_median(df)
        solver_type = "Single Depot"
    elif mode == "multi":
        depots_df, assignments_df, objective = gg.cog.solve_multiple_depots(df, gg.K_MULTI)
        solver_type = "Multi Depot"
    else:
        depots_df, assignments_df, objective = gg.cog.solve_fixed_plus_variable_depots(
            df, gg.N_FREE_FIXED, gg.FIXED_COORDS
        )
        solver_type = "Fixed + Variable Depots"

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clusters_map.html"
        render_dashboard(
            assignments_df, depots_df, str(out), solver_type,
            objective=objective, run_meta=run_meta,
        )
        return out.read_text(encoding="utf-8")


def test_export_buttons_present_in_sidebar_not_topbar():
    html = _render("multi", objective=None)

    topbar = html[html.index('<div class="topbar">'):html.index('<div class="layout">')]
    assert "export-png-btn" not in topbar
    assert "export-csv-btn" not in topbar

    sidebar_start = html.index('<div class="sidebar">')
    details_end = html.index("</details>", sidebar_start)
    export_actions = html[details_end:html.index('<div class="export-actions">', details_end)
                           + len('<div class="export-actions">') + 400]

    assert sidebar_start < details_end  # buttons are inside the sidebar
    assert 'class="export-actions"' in export_actions
    assert 'id="export-png-btn"' in export_actions
    assert 'id="export-csv-btn"' in export_actions
    assert 'class="show-all-btn" id="export-png-btn"' in html
    assert 'class="show-all-btn" id="export-csv-btn"' in html


def test_export_buttons_present_for_all_modes():
    for mode in ("single", "multi", "fixed"):
        html = _render(mode, objective=None)
        assert 'id="export-png-btn"' in html
        assert 'id="export-csv-btn"' in html


def test_run_json_well_formed_single_depot_now_has_objective():
    """Single Depot used to be the one mode with no objective (a 2-tuple
    solver). solve_single_depot_weighted_geometric_median now returns a real
    (exact, no multi-start) objective like the other two modes, so RUN_JSON
    should carry a real number here too, not None."""
    html = _render("single", objective=None, run_meta={
        "dataset": "B2C_Articles_2040_Malaga", "mode_tag": "single",
        "generated_at": "2026-08-06T12:00:00",
    })
    run = json.loads(RUN_RE.search(html).group(1))
    assert run["solver_mode"] == "Single Depot"
    assert isinstance(run["objective_item_km"], float)
    assert isinstance(run["objective_item_m"], float)
    assert run["objective_item_m"] == run["objective_item_km"] * 1000.0
    assert run["dataset"] == "B2C_Articles_2040_Malaga"
    assert run["mode_tag"] == "single"
    assert run["generated_at"] == "2026-08-06T12:00:00"


def test_run_json_well_formed_all_modes_have_objective():
    for mode, tag in (("single", "single"), ("multi", "multi"), ("fixed", "fixed")):
        html = _render(mode, objective=None, run_meta={
            "dataset": "ds", "mode_tag": tag, "generated_at": "2026-08-06T12:00:00",
        })
        run = json.loads(RUN_RE.search(html).group(1))
        assert isinstance(run["objective_item_km"], float)
        assert isinstance(run["objective_item_m"], float)
        assert run["objective_item_m"] == run["objective_item_km"] * 1000.0


def test_no_comma_formatted_numbers_in_raw_depot_fields():
    """The raw_* fields feed the CSV export directly — they must be numeric,
    never the comma-formatted display strings ("demand"/"lat"/"lon") that the
    sidebar uses (see the parseDemand() trap this mirrors)."""
    for mode in ("single", "multi", "fixed"):
        html = _render(mode, objective=None)
        depots_json = re.search(r"const DEPOTS = (\[.*?\]);", html, re.DOTALL).group(1)
        depots = json.loads(depots_json)
        for d in depots:
            for key in ("demand_raw", "lat_raw", "lon_raw", "pct_raw"):
                value = d[key]
                assert not isinstance(value, str), f"{key} must be numeric, got {value!r}"
            assert d["cap_raw"] is None or not isinstance(d["cap_raw"], str)


def test_csv_builder_is_flat_one_row_per_point():
    """The CSV went from three sections (summary/depots/points) to one flat
    table, and carries NO comment line at all -- the file starts directly
    with the header row. Assert the header is present and that buildPointsCsv
    doesn't concatenate any '#'-prefixed line into the CSV string."""
    html = _render("multi", objective=None)

    assert CSV_HEADER_RE.search(html), "flat CSV header (9 columns, in order) not found"

    fn_start = html.index("function buildPointsCsv()")
    fn_end = html.index("\n        }", fn_start)
    fn_body = html[fn_start:fn_end]

    # No CSV comment line of any kind -- the file starts with the header.
    assert "out += '#" not in fn_body
    first_out_append = re.search(r"out \+= (.+?);", fn_body, re.DOTALL).group(1)
    assert "csvRow(['point_index'" in first_out_append, (
        f"first line appended to the CSV must be the header row, got: {first_out_append!r}")

    # Old three-section structure must be gone, not just superseded. Quoted
    # as JS string literals (not bare substrings) since
    # "largest_depot_demand_share_pct" legitimately remains in RUN_JSON
    # (server-side summary, unrelated to the CSV, PART 0 stays as-is).
    assert "# COG run summary" not in html
    assert "# Depots\\r\\n" not in html
    assert "buildRunCsv" not in html
    assert "'demand_share_pct'" not in html
    assert "'assigned_demand_items'" not in html
    assert "'max_demand_items'" not in html


def test_csv_objective_column_is_number_or_na_literal():
    # All three modes now report a real objective (Single Depot's solver
    # returns one too, exact/no multi-start) -- the "n/a" ternary in the CSV
    # builder still exists in the JS for robustness/API symmetry, but none of
    # the three modes currently trigger it. Assert the ternary is still
    # present, and that every mode's RUN.objective_item_km is a real number.
    for mode in ("single", "multi", "fixed"):
        html = _render(mode, objective=None)
        assert "objVal = (RUN.objective_item_km === null" in html
        run = json.loads(RUN_RE.search(html).group(1))
        assert isinstance(run["objective_item_km"], float)


def test_depot_legend_drawn_for_png_export_only():
    html = _render("multi", objective=None)
    assert "drawDepotLegend" in html
    # Legend uses the display (comma-formatted) lat/lon fields directly, per
    # spec -- not a new raw field, and not shown anywhere in the live sidebar
    # (it's only invoked from inside the PNG export functions).
    assert "d.lat + ', ' + d.lon" in html
    png_click_handler = html.split("export-png-btn").pop()
    assert "drawDepotLegend(ctx)" in png_click_handler


def test_named_fixed_depot_labels_sidebar_tooltip_and_csv():
    """A depot_name column (from the fixed-depot CSV's WCC name) replaces
    "Depot N" in the sidebar and DEPOTS labels; free depots keep "Depot N"."""
    from cog.app import add_depot_names

    df = gg.load_fixture()
    depots_df, assignments_df, objective = gg.cog.solve_fixed_plus_variable_depots(
        df, gg.N_FREE_FIXED, gg.FIXED_COORDS)
    depots_df = add_depot_names(depots_df, ["MALAGA PDC & CO"])
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clusters_map.html"
        render_dashboard(assignments_df, depots_df, str(out),
                         "Fixed + Variable Depots", objective=objective)
        html = out.read_text(encoding="utf-8")

    assert "MALAGA PDC &amp; CO <em>(fixed)</em>" in html
    assert "Depot 0 · " in html
    depots = json.loads(re.search(r"const DEPOTS = (\[.*?\]);", html, re.DOTALL).group(1))
    assert [d["label"] for d in depots] == ["MALAGA PDC & CO", "Depot 1", "Depot 2"]
    assert depots[1]["name"] is None


def test_up_to_twelve_depots_all_get_distinct_colours():
    import pandas as pd
    from cog.report import CLUSTER_HEX, cluster_colors

    depots = pd.DataFrame({"depot_id": range(12), "depot_lat": -37.8, "depot_lon": 145.0})
    colors = cluster_colors(depots)
    assert len(set(colors.values())) == 12
    assert set(colors.values()) == set(CLUSTER_HEX)


def test_repeated_colours_go_to_far_apart_depots():
    """Past 12 depots a colour must repeat; the repeat is the farthest one."""
    import pandas as pd
    from cog.report import cluster_colors

    # 12 depots in a west-east line, then a 13th right next to depot 0.
    lons = [144.5 + 0.1 * i for i in range(12)] + [144.51]
    depots = pd.DataFrame({"depot_id": range(13), "depot_lat": -37.8, "depot_lon": lons})
    colors = cluster_colors(depots)
    assert colors[12] == colors[11]      # shares with the depot at the far end
    assert colors[12] != colors[0]       # never with its neighbour


def test_neighbouring_depots_get_well_separated_colours():
    """Next-door catchments never get the palette's closest look-alike pair."""
    import itertools
    import numpy as np
    import pandas as pd
    from cog.report import CLUSTER_HEX, _oklab, cluster_colors

    lons = [144.5 + 0.1 * i for i in range(11)]
    depots = pd.DataFrame({"depot_id": range(11), "depot_lat": -37.8, "depot_lon": lons})
    colors = cluster_colors(depots)
    gaps = [np.linalg.norm(_oklab(colors[i]) - _oklab(colors[i + 1])) for i in range(10)]
    closest_pair = min(np.linalg.norm(_oklab(a) - _oklab(b))
                       for a, b in itertools.combinations(CLUSTER_HEX, 2))
    assert min(gaps) > closest_pair
