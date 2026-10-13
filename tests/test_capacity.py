"""Fixed-depot demand capacity: cap-off identity and demand limits."""

import json
import warnings

import numpy as np
import pandas as pd
import pytest

import generate_goldens as gg
from cog.solvers import (
    _mcf_assignment,
    get_last_cap_stats,
    solve_fixed_plus_variable_depots,
    solve_multiple_depots,
)


def _integer_demand_fixture():
    """Fixture with unit demand per point (capacity tests need integer items)."""
    df = gg.load_fixture().copy()
    pos = df["demand"] > 0
    df.loc[pos, "demand"] = 1.0
    return df


def test_multi_cap_off_matches_golden_twice():
    """Multi stays unlimited and byte-identical to the multi golden."""
    golden = json.loads(
        (gg.GOLDEN_DIR / "results_multi.json").read_text(encoding="utf-8")
    )
    snapshots = []
    for _ in range(2):
        depots_df, assignments_df, _ = solve_multiple_depots(gg.load_fixture(), gg.K_MULTI)
        snapshots.append(gg.result_snapshot("Multi Depot", depots_df, assignments_df))

    assert snapshots[0] == snapshots[1]
    actual = snapshots[0]
    assert actual["cluster_ids"] == golden["cluster_ids"]
    assert actual["objective_m"] == pytest.approx(golden["objective_m"], rel=1e-6)
    assert len(actual["depots"]) == len(golden["depots"])
    for actual_depot, expected_depot in zip(actual["depots"], golden["depots"]):
        for key, expected_value in expected_depot.items():
            actual_value = actual_depot[key]
            if isinstance(expected_value, float):
                assert actual_value == pytest.approx(
                    expected_value, rel=1e-6, abs=1e-6
                )
            else:
                assert actual_value == expected_value


def test_multi_max_points_argument_has_no_effect():
    """max_points is ignored in Multi mode (no fixed depots)."""
    df = gg.load_fixture()
    a = gg.result_snapshot(
        "Multi Depot", *solve_multiple_depots(df, gg.K_MULTI)[:2]
    )
    b = gg.result_snapshot(
        "Multi Depot", *solve_multiple_depots(df, gg.K_MULTI, max_points=1)[:2]
    )
    assert a == b


def test_fixed_cap_off_matches_golden_twice():
    """Default max_points=None must stay byte-identical to the fixed golden."""
    golden = json.loads(
        (gg.GOLDEN_DIR / "results_fixed.json").read_text(encoding="utf-8")
    )
    snapshots = []
    for _ in range(2):
        depots_df, assignments_df, _ = solve_fixed_plus_variable_depots(
            gg.load_fixture(), gg.N_FREE_FIXED, gg.FIXED_COORDS
        )
        snapshots.append(
            gg.result_snapshot("Fixed + Variable Depots", depots_df, assignments_df)
        )

    assert snapshots[0] == snapshots[1]
    actual = snapshots[0]
    assert actual["cluster_ids"] == golden["cluster_ids"]
    assert actual["objective_m"] == pytest.approx(golden["objective_m"], rel=1e-6)


def test_fixed_demand_cap_binds_only_fixed_depots():
    """Fixed depot assigned_demand <= cap (items); free depots may exceed it.

    Changed from points-cap: capacity now limits demand (items), not n_nodes.
    Fixture demand is unit-scaled so 1 point = 1 item.
    """
    df = _integer_demand_fixture()
    n = int((df["demand"] > 0).sum())
    max_demand = 5  # tight on the fixed depot; free depots absorb the rest

    depots_df, assignments_df, _ = solve_fixed_plus_variable_depots(
        df, gg.N_FREE_FIXED, gg.FIXED_COORDS, max_points=max_demand
    )

    fixed = depots_df[depots_df["is_fixed"]]
    free = depots_df[~depots_df["is_fixed"]]
    max_pt_demand = float(df.loc[df["demand"] > 0, "demand"].max())

    # After split-repair, demand may overshoot by at most one point's demand.
    assert (fixed["assigned_total_demand"] <= max_demand + max_pt_demand + 1e-9).all()
    assert (fixed["max_points"] == max_demand).all()
    assert "max_points" not in free.columns or free["max_points"].isna().all()
    assert float(free["assigned_total_demand"].max()) > max_demand
    assert int(depots_df["n_nodes"].sum()) == n


def test_all_fixed_short_capacity_leaves_rest_unserved():
    """No free depots and caps below total demand: the rest is unserved (-1)."""
    df = _integer_demand_fixture()
    total = int(df.loc[df["demand"] > 0, "demand"].sum())
    depots_df, assignments_df, _ = solve_fixed_plus_variable_depots(
        df, n_free=0, fixed_coords=gg.FIXED_COORDS, max_points=1
    )
    unserved = assignments_df["cluster_id"] == -1
    stats = get_last_cap_stats()
    assert int(depots_df["assigned_total_demand"].sum()) == 1
    assert int(unserved.sum()) == total - 1
    assert stats["unserved_points"] == total - 1
    assert stats["unserved_demand"] == total - 1
    assert int(depots_df["n_nodes"].sum()) + int(unserved.sum()) == len(assignments_df)


def test_fixed_plus_free_small_demand_cap_is_feasible():
    """Free depots absorb overflow when fixed demand cap is tiny."""
    df = _integer_demand_fixture()
    depots_df, _, _ = solve_fixed_plus_variable_depots(
        df, gg.N_FREE_FIXED, gg.FIXED_COORDS, max_points=1
    )
    fixed = depots_df[depots_df["is_fixed"]]
    max_pt_demand = float(df.loc[df["demand"] > 0, "demand"].max())
    assert (fixed["assigned_total_demand"] <= 1 + max_pt_demand + 1e-9).all()


def test_per_depot_demand_cap_list_binds_only_capped_fixed():
    """[cap, None] with 2 fixed + free: only first fixed is demand-capped."""
    df = _integer_demand_fixture()
    n = int((df["demand"] > 0).sum())
    fixed_coords = list(gg.FIXED_COORDS) + [(gg.FIXED_LAT + 0.01, gg.FIXED_LON + 0.01)]
    depots_df, _, _ = solve_fixed_plus_variable_depots(
        df, n_free=1, fixed_coords=fixed_coords, max_points=[10, None]
    )
    fixed = depots_df[depots_df["is_fixed"]].sort_values("depot_id")
    free = depots_df[~depots_df["is_fixed"]]
    max_pt_demand = float(df.loc[df["demand"] > 0, "demand"].max())
    assert float(fixed.iloc[0]["assigned_total_demand"]) <= 10 + max_pt_demand + 1e-9
    assert int(fixed.iloc[0]["max_points"]) == 10
    assert fixed.iloc[1]["max_points"] != fixed.iloc[1]["max_points"]  # NaN
    assert free["max_points"].isna().all() if "max_points" in free.columns else True
    assert int(depots_df["n_nodes"].sum()) == n


def test_all_fixed_short_cap_sum_serves_nearest_points():
    """Every depot capped, caps < demand: caps fill and the far points go unserved."""
    df = _integer_demand_fixture()
    total = int(df.loc[df["demand"] > 0, "demand"].sum())
    coords = list(gg.FIXED_COORDS) + [(gg.FIXED_LAT + 0.02, gg.FIXED_LON)]
    caps = [3, 4]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        depots_df, assignments_df, objective = solve_fixed_plus_variable_depots(
            df, n_free=0, fixed_coords=coords, max_points=caps
        )
    served = assignments_df[assignments_df["cluster_id"] >= 0]
    unserved = assignments_df[assignments_df["cluster_id"] == -1]
    # Unit demand, so no split points: every cap is used exactly.
    assert depots_df["assigned_total_demand"].tolist() == [3.0, 4.0]
    assert len(unserved) == total - sum(caps)
    assert len(served) == sum(caps)
    assert objective > 0

    # Served points are no farther from their depot than any unserved point
    # is from its nearest depot (MCF fills caps with the closest demand).
    lat = np.array([c[0] for c in coords]); lon = np.array([c[1] for c in coords])

    def nearest_deg(rows):
        d = np.hypot(rows["lat"].to_numpy()[:, None] - lat[None, :],
                     rows["lon"].to_numpy()[:, None] - lon[None, :])
        return d.min(axis=1)
    assert nearest_deg(served).max() <= nearest_deg(unserved).min() + 1e-3


def test_short_capacity_report_shows_unserved(tmp_path):
    """The dashboard reconciles served + unserved and lists the unserved row."""
    from cog.report import UNSERVED_HEX, render_dashboard

    df = _integer_demand_fixture()
    depots_df, assignments_df, objective = solve_fixed_plus_variable_depots(
        df, n_free=0, fixed_coords=gg.FIXED_COORDS, max_points=5
    )
    n_unserved = int((assignments_df["cluster_id"] == -1).sum())
    out = render_dashboard(assignments_df, depots_df, str(tmp_path / "r.html"),
                           "Fixed + Variable Depots", objective=objective)
    html = open(out, encoding="utf-8").read()
    assert 'data-cluster="-1"' in html
    assert "Unserved volume" in html
    assert f'"-1": "{UNSERVED_HEX}"' in html
    assert f'"unserved_points": {n_unserved}' in html


def test_all_fixed_with_one_none_cap_does_not_raise():
    """All fixed but one uncapped fixed can absorb demand overflow."""
    df = _integer_demand_fixture()
    n = int((df["demand"] > 0).sum())
    coords = list(gg.FIXED_COORDS) + [(gg.FIXED_LAT + 0.02, gg.FIXED_LON)]
    depots_df, _, _ = solve_fixed_plus_variable_depots(
        df, n_free=0, fixed_coords=coords, max_points=[1, None]
    )
    fixed = depots_df[depots_df["is_fixed"]].sort_values("depot_id")
    max_pt_demand = float(df.loc[df["demand"] > 0, "demand"].max())
    assert float(fixed.iloc[0]["assigned_total_demand"]) <= 1 + max_pt_demand + 1e-9
    assert int(depots_df["n_nodes"].sum()) == n


def test_max_points_list_validation_errors():
    """Wrong length, zero, and negative caps are rejected."""
    df = _integer_demand_fixture()
    coords = list(gg.FIXED_COORDS)
    m = len(coords)
    with pytest.raises(ValueError, match="length"):
        solve_fixed_plus_variable_depots(
            df, n_free=1, fixed_coords=coords, max_points=[5] * (m + 1)
        )
    with pytest.raises(ValueError, match="positive"):
        solve_fixed_plus_variable_depots(
            df, n_free=1, fixed_coords=coords, max_points=[0] * m
        )
    with pytest.raises(ValueError, match="positive"):
        solve_fixed_plus_variable_depots(
            df, n_free=1, fixed_coords=coords, max_points=[-3] * m
        )


def test_non_integer_demand_raises_on_capacity_run():
    """Fractional demand must fail loudly on capacity runs (B2C out of scope)."""
    df = gg.load_fixture().copy()
    df = df[df["demand"] > 0].reset_index(drop=True)
    # fixture already fractional — capacity run must raise
    with pytest.raises(ValueError, match="integer-valued"):
        solve_fixed_plus_variable_depots(
            df, gg.N_FREE_FIXED, gg.FIXED_COORDS, max_points=10
        )


def test_mcf_bulk_deterministic_on_small_fixture():
    """Two MCF solves on the same centres yield identical cluster_id."""
    df = _integer_demand_fixture()
    data = df[df["demand"] > 0].copy().reset_index(drop=True)
    data = data.iloc[:40].copy()
    from cog.geo import latlon_to_xy

    lat0 = float(np.average(data["lat"], weights=data["demand"]))
    lon0 = float(np.average(data["lon"], weights=data["demand"]))
    x, y = latlon_to_xy(data["lat"], data["lon"], lat0, lon0)
    X = np.column_stack([x, y])
    w = data["demand"].to_numpy(dtype=float)
    centers = np.array([X[0], X[10]], dtype=float)
    total = int(np.rint(w.sum()))
    caps = np.array([max(5, total // 3), total], dtype=np.int64)
    a = _mcf_assignment(X, centers, w, caps)
    b = _mcf_assignment(X, centers, w, caps)
    assert np.array_equal(a, b)
    dem0 = float(w[a == 0].sum())
    max_pt = float(w.max())
    assert dem0 <= caps[0] + max_pt + 1e-9


def test_split_repair_overflow_is_detected():
    """2 depots, 3 points of demand 2, cap 3 on depot 0 -> depot 0 overflows.

    Reproduction from the defect report: whole-point repair of a split point
    pushes depot 0 to 4 realised items against a cap of 3.
    """
    X = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    centers = np.array([[0.0, 0.0], [100.0, 0.0]])
    w = np.array([2.0, 2.0, 2.0])
    caps = np.array([3, 6], dtype=np.int64)

    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        _mcf_assignment(X, centers, w, caps)
        assert any("cap" in str(w.message).lower() for w in rec)

    stats = get_last_cap_stats()
    assert stats["cap_overflow_last"] > 0
    assert stats["cap_overflow_by_depot"][0] > 0


def test_split_repair_no_overflow_when_no_split_occurs():
    """Ample capacity everywhere -> no split, no overflow, no warning."""
    X = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    centers = np.array([[0.0, 0.0], [100.0, 0.0]])
    w = np.array([2.0, 2.0, 2.0])
    caps = np.array([6, 6], dtype=np.int64)

    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        _mcf_assignment(X, centers, w, caps)
        assert len(rec) == 0

    stats = get_last_cap_stats()
    assert stats["cap_overflow_last"] == 0


def test_split_repair_assignment_is_unchanged():
    """The overflow check is reporting-only: cluster_id must not move.

    Expected values captured from the same capped scenario as
    test_split_repair_overflow_is_detected, run before this change (the fix
    adds no code path that can alter np.argmax(flow_mat, axis=1)).
    """
    X = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    centers = np.array([[0.0, 0.0], [100.0, 0.0]])
    w = np.array([2.0, 2.0, 2.0])
    caps = np.array([3, 6], dtype=np.int64)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cluster_id = _mcf_assignment(X, centers, w, caps)

    assert np.array_equal(cluster_id, np.array([0, 0, 1]))
