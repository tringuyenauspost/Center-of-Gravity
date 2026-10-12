"""Regenerate the golden snapshot files in tests/goldens/ from the CURRENT code.

Run from the repository root:

    uv run python tests/generate_goldens.py

Only regenerate goldens deliberately, when a behaviour change is intended and
has been reviewed. The whole point of these files is to prove that the
refactor does NOT change behaviour, so they must stay untouched while code
is only being moved around.
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cog  # noqa: E402

FIXTURE = ROOT / "inputs" / "B2C_Articles_2040_Malaga.csv"
GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"

MODES = ("single", "multi", "fixed")
K_MULTI = 3
FIXED_LAT = -31.95
FIXED_LON = 115.80
N_FREE_FIXED = 2                          # optimised depots; total = 1 + N_FREE_FIXED
FIXED_COORDS = [(FIXED_LAT, FIXED_LON)]   # one pinned depot for the fixture


def load_fixture():
    """Load the fixture CSV exactly the way the GUI's run_solver does."""
    raw = pd.read_csv(FIXTURE)
    return pd.DataFrame(
        {
            "lat": raw["latitude"],
            "lon": raw["longitude"],
            "demand": raw["demand_items"],
        }
    )


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres, independent of the code under test."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (
        np.sin((lat2 - lat1) / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371000.0 * np.arcsin(np.sqrt(a))


def objective_m(assignments_df, depots_df):
    """Objective Σ demand_i * distance(point_i, assigned depot) in metre-items."""
    depots = depots_df.set_index("depot_id")
    dep_lat = assignments_df["cluster_id"].map(depots["depot_lat"]).to_numpy(float)
    dep_lon = assignments_df["cluster_id"].map(depots["depot_lon"]).to_numpy(float)
    dist = haversine_m(
        assignments_df["lat"].to_numpy(float),
        assignments_df["lon"].to_numpy(float),
        dep_lat,
        dep_lon,
    )
    return float(np.sum(assignments_df["demand"].to_numpy(float) * dist))


def run_mode(mode):
    """Run one solver mode on the fixture.

    Returns (solver_type, depots_df, assignments_df, objective) for all three
    modes -- single-depot is exact (no multi-start), multi/fixed are the best
    of a 10-seed multi-start.
    """
    df = load_fixture()
    if mode == "single":
        depots_df, assignments_df, objective = cog.solve_single_depot_weighted_geometric_median(df)
        return "Single Depot", depots_df, assignments_df, objective
    if mode == "multi":
        depots_df, assignments_df, objective = cog.solve_multiple_depots(df, K_MULTI)
        return "Multi Depot", depots_df, assignments_df, objective
    if mode == "fixed":
        depots_df, assignments_df, objective = cog.solve_fixed_plus_variable_depots(
            df, N_FREE_FIXED, FIXED_COORDS
        )
        return "Fixed + Variable Depots", depots_df, assignments_df, objective
    raise ValueError(mode)


def normalized_report_html(solver_type, assignments_df, depots_df):
    """Render the full dashboard HTML report (deterministic for the same data)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clusters_map.html"
        cog.render_dashboard(assignments_df, depots_df, str(out), solver_type)
        html = out.read_text(encoding="utf-8")
    return html


def result_snapshot(solver_type, depots_df, assignments_df):
    return {
        "solver_type": solver_type,
        "depots": json.loads(depots_df.to_json(orient="records")),
        "cluster_ids": [int(c) for c in assignments_df["cluster_id"]],
        "n_points": int(len(assignments_df)),
        "objective_m": objective_m(assignments_df, depots_df),
    }


def main():
    GOLDEN_DIR.mkdir(exist_ok=True)
    for mode in MODES:
        solver_type, depots_df, assignments_df, _objective = run_mode(mode)

        snapshot = result_snapshot(solver_type, depots_df, assignments_df)
        (GOLDEN_DIR / f"results_{mode}.json").write_text(
            json.dumps(snapshot, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

        html_a = normalized_report_html(solver_type, assignments_df, depots_df)
        html_b = normalized_report_html(solver_type, assignments_df, depots_df)
        if html_a != html_b:
            raise RuntimeError(
                f"{mode}: report HTML differs between two renders even after id "
                "normalisation — the HTML golden approach needs adjusting"
            )
        (GOLDEN_DIR / f"report_{mode}.html").write_text(
            html_a, encoding="utf-8", newline="\n"
        )

        print(
            f"{mode:6s}  depots={len(snapshot['depots'])}  "
            f"objective={snapshot['objective_m']:,.1f} m*items  "
            f"html={len(html_a):,} chars"
        )
    print(f"goldens written to {GOLDEN_DIR}")


if __name__ == "__main__":
    main()
