"""Report generation: Leaflet dashboard HTML + browser display."""

import json
import os
import pathlib
import webbrowser
from pathlib import Path

import numpy as np
from jinja2 import Environment, FileSystemLoader

try:
    import webview
except ImportError:
    webview = None

_TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates")
)

# Dashboard palette: 12 categorical colours picked by farthest-point search in
# OKLab and validated ALL-pairs on a white surface (every pair, not just
# neighbours in the list, since any two catchments can meet on the map):
# worst normal-vision dE 16.4 (>= 15), worst protan/deutan dE 8.2 (>= 8),
# all inside OKLCH L 0.43-0.77 with chroma >= 0.10. Three lighter slots sit
# under 3:1 contrast on white, so depot pins carry their number and the
# sidebar lists every depot - identity is never colour alone.
CLUSTER_HEX = (
    "#242beb",  # blue
    "#b26f06",  # amber
    "#0d957e",  # teal
    "#fc146f",  # rose
    "#8962c5",  # violet
    "#4ed227",  # lime
    "#9e0163",  # maroon
    "#35c2ee",  # sky
    "#32670c",  # forest
    "#dd47fc",  # magenta
    "#225a93",  # steel
    "#e793b8",  # pink
)


def cluster_hex(cluster_id):
    """Hex colour for cluster/depot id when the run has at most 12 depots."""
    return CLUSTER_HEX[int(cluster_id) % len(CLUSTER_HEX)]


def _luminance(hex_color):
    """WCAG relative luminance of a #rrggbb colour."""
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ink_on(hex_color):
    """Dark or white text, whichever contrasts more with the given fill."""
    lum = _luminance(hex_color)
    on_dark = 1.05 / (lum + 0.05)                     # white text
    on_light = (lum + 0.05) / (_luminance("#111827") + 0.05)
    return "#ffffff" if on_dark >= on_light else "#111827"


def _oklab(hex_color):
    """OKLab coordinates (x100) of a #rrggbb colour, for perceptual distance."""
    rgb = np.array([int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)])
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                   [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]])
    m2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                   [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]])
    return 100 * (m2 @ np.cbrt(m1 @ lin))


_PALETTE_LAB = {c: _oklab(c) for c in CLUSTER_HEX}
_NEIGHBOURS = 3     # nearby depots whose colours a new depot should differ from


def cluster_colors(depots_df):
    """{depot_id: hex}, chosen so neighbouring catchments look most different.

    Depots are coloured in id order. Each takes the unused palette colour
    farthest (in OKLab) from the colours of its nearest already-coloured
    depots, so look-alike pairs (e.g. two blues) end up apart on the map.
    Up to 12 depots every colour is distinct; past that a colour has to
    repeat, and it goes to the depot farthest from that colour's users.
    Deterministic for the same depots, so a report always looks the same.
    """
    ids = [int(i) for i in depots_df["depot_id"]]
    lat = np.radians(depots_df["depot_lat"].to_numpy(dtype=float))
    lon = np.radians(depots_df["depot_lon"].to_numpy(dtype=float))
    # Equirectangular distance is plenty for ranking depots within a region.
    pos = np.column_stack([lon * np.cos(lat.mean()), lat])

    assigned = []                 # (index, colour) in assignment order
    colors = {}
    for i, did in enumerate(ids):
        used = {c for _, c in assigned}
        free = [c for c in CLUSTER_HEX if c not in used]
        if free:
            near = sorted(assigned, key=lambda jc: np.hypot(*(pos[jc[0]] - pos[i])))
            near = near[:_NEIGHBOURS]

            def separation(c):
                if not near:
                    return 0.0
                return min(np.linalg.norm(_PALETTE_LAB[c] - _PALETTE_LAB[nc])
                           for _, nc in near)
            # max() keeps the first of equal scores: palette order breaks ties.
            choice = max(free, key=separation)
        else:
            def nearest_user(c):
                return min(np.hypot(*(pos[j] - pos[i])) for j, jc in assigned if jc == c)
            choice = max(CLUSTER_HEX, key=nearest_user)
        assigned.append((i, choice))
        colors[did] = choice
    return colors


def _dashboard_context(assignments_df, depots_df, solver_type, objective=None, run_meta=None):
    """
    Build the template variables for report.html.j2 from solver output.

    Single source of truth: everything the page shows (stat cards, sidebar list,
    map) is derived here from the SAME two DataFrames the solver returned and the
    log summarises. The assertions guarantee the per-depot breakdown reconciles
    with the totals, so the report can never silently disagree with the log.

    objective is in item*metres (see _alternating_location_allocation); None for
    Single Depot. run_meta is {"dataset", "mode_tag", "generated_at"} or None
    (e.g. golden generation), in which case those RUN_JSON fields are null.
    """
    total_demand = float(assignments_df["demand"].sum())
    n_points = int(len(assignments_df))
    demand_col = (
        "assigned_demand"
        if "assigned_demand" in depots_df.columns
        else "assigned_total_demand"
    )
    has_fixed = "is_fixed" in depots_df.columns

    has_cap = "max_points" in depots_df.columns
    has_name = "depot_name" in depots_df.columns
    palette = cluster_colors(depots_df)
    depots = []
    max_share = 0.0
    sum_points = 0
    sum_demand = 0.0
    for _, row in depots_df.iterrows():
        did = int(row["depot_id"])
        dem = float(row[demand_col])
        pts = int(row["n_nodes"])
        share = (dem / total_demand * 100.0) if total_demand else 0.0
        max_share = max(max_share, share)
        sum_points += pts
        sum_demand += dem
        # Cap applies only to fixed depots; free rows leave max_points as NaN.
        if has_cap and row["max_points"] == row["max_points"]:  # True iff not NaN
            cap = int(row["max_points"])
        else:
            cap = None
        # Rounded to the same 6dp the sidebar already displays — the CSV
        # export inherits this precision rather than carrying full float64.
        # Fixed depots may carry a name (e.g. WCC name); free ones never do.
        name = str(row["depot_name"]).strip() if has_name else ""
        if name.lower() == "nan":
            name = ""
        lat_raw = round(float(row["depot_lat"]), 6)
        lon_raw = round(float(row["depot_lon"]), 6)
        depots.append({
            "id": did,
            "name": name or None,
            "label": name or f"Depot {did}",
            "color": palette[did],
            "ink": ink_on(palette[did]),        # number drawn on pin / chip
            "is_fixed": bool(row["is_fixed"]) if has_fixed else False,
            "lat": f"{lat_raw:.6f}",
            "lon": f"{lon_raw:.6f}",
            # points stays unformatted for map JS; labels are for sidebar text.
            "points": pts,
            "points_label": f"{pts:,}",
            "cap": cap,
            # Cap is max demand (items) on fixed depots when capacity is enabled.
            "cap_label": f"{cap:,} items" if cap is not None else None,
            "demand": f"{dem:,.0f}",
            "pct": f"{share:.1f}",
            # Raw (unformatted) numbers for the CSV export — never feed the
            # comma/6dp display strings above into a CSV cell.
            "demand_raw": dem,
            "lat_raw": lat_raw,
            "lon_raw": lon_raw,
            "cap_raw": cap,
            "pct_raw": share,
        })

    # Single-source-of-truth guard: the per-depot breakdown must reconcile with
    # the totals, or the report would disagree with the log about the counts.
    if sum_points != n_points:
        raise ValueError(
            f"per-depot points {sum_points} != total demand points {n_points}")
    if abs(sum_demand - total_demand) > 1e-6 * max(1.0, abs(total_demand)):
        raise ValueError(
            f"per-depot demand {sum_demand} != total demand {total_demand}")

    # One point = [lat, lon, demand, cluster_id]; coords rounded to 5 dp (~1 m).
    # Compact array, no per-point JS objects — the radius is scaled in the
    # browser from demand_min/demand_max.
    points = [
        [round(float(la), 5), round(float(lo), 5), float(de), int(ci)]
        for la, lo, de, ci in zip(
            assignments_df["lat"], assignments_df["lon"],
            assignments_df["demand"], assignments_df["cluster_id"])
    ]
    demands = assignments_df["demand"].to_numpy(dtype=float)
    cluster_ids = sorted({int(c) for c in assignments_df["cluster_id"]})
    colors = {cid: palette.get(cid, cluster_hex(cid)) for cid in cluster_ids}
    center = [float(assignments_df["lat"].mean()), float(assignments_df["lon"].mean())]

    # True only when at least one depot carried a numeric max_points (cap-on
    # Fixed runs). Cap-off reports leave this false so golden HTML stays stable.
    has_fixed_cap = any(d["cap"] is not None for d in depots)

    # objective is item*METRES; expose both units explicitly so nothing
    # downstream (CSV, JS) can mistake one for the other. None for Single Depot.
    objective_item_m = float(objective) if objective is not None else None
    objective_item_km = (
        objective_item_m / 1000.0 if objective_item_m is not None else None
    )

    run_meta = run_meta or {}
    run_json = {
        "solver_mode": solver_type,
        "dataset": run_meta.get("dataset"),
        "mode_tag": run_meta.get("mode_tag"),
        "generated_at": run_meta.get("generated_at"),
        "depots": len(depots),
        "delivery_points": n_points,
        "total_demand_items": total_demand,
        "objective_item_km": objective_item_km,
        "objective_item_m": objective_item_m,
        "largest_depot_demand_share_pct": max_share,
    }

    return {
        "solver_type": solver_type,
        "show_single": solver_type == "Single Depot",
        "show_multi": solver_type == "Multi Depot",
        "show_fixed": solver_type == "Fixed + Variable Depots",
        "has_fixed_cap": has_fixed_cap,
        "stat_depots": len(depots),
        "stat_points": f"{n_points:,}",
        "stat_demand": f"{total_demand:,.0f}",
        "stat_share": f"{max_share:.1f}",
        # Share is always of total demand volume, never of point counts.
        "stat_share_label": (
            "Largest volume share" if has_fixed_cap else "Largest depot share"
        ),
        "depots": depots,
        "points_json": json.dumps(points, separators=(",", ":")),
        "depots_json": json.dumps(
            [{"id": d["id"], "label": d["label"], "name": d["name"],
              "lat": float(d["lat"]), "lon": float(d["lon"]),
              "color": d["color"], "ink": d["ink"],
              "demand": d["demand"], "points": d["points"],
              "is_fixed": d["is_fixed"], "demand_raw": d["demand_raw"],
              "lat_raw": d["lat_raw"], "lon_raw": d["lon_raw"],
              "cap_raw": d["cap_raw"], "pct_raw": d["pct_raw"]}
             for d in depots]),
        "colors_json": json.dumps(colors),
        "center_json": json.dumps(center),
        "demand_min": float(demands.min()) if n_points else 0.0,
        "demand_max": float(demands.max()) if n_points else 1.0,
        "run_json": json.dumps(run_json),
    }


def render_dashboard(assignments_df, depots_df, out_file, solver_type="Single Depot",
                      objective=None, run_meta=None):
    """
    Render the Leaflet dashboard report and return its absolute path.

    Map, stat cards and sidebar all come from _dashboard_context — one source
    of truth.
    """
    ctx = _dashboard_context(assignments_df, depots_df, solver_type, objective, run_meta)
    html = _TEMPLATE_ENV.get_template("report.html.j2").render(**ctx)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.abspath(out_file)


def display_map_in_browser(html_path):
    """
    Display the generated HTML map.
    Prefer pywebview when available, otherwise open in system browser.
    """
    file_url = pathlib.Path(html_path).resolve().as_uri()

    if webview is not None:
        try:
            webview.create_window("Centre of Gravity - Depot Location", file_url)
            webview.start()
            return
        except Exception:
            # Fall back to system browser if pywebview backend is unavailable.
            pass

    webbrowser.open_new_tab(file_url)
