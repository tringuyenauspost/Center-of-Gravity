"""Depot-location solvers: Weiszfeld weighted geometric median and the three modes."""

import warnings

import numpy as np
import pandas as pd
from ortools.graph.python import min_cost_flow
from sklearn.cluster import KMeans, kmeans_plusplus

from .geo import latlon_to_xy, pairwise_sq_dists, xy_to_latlon


def weighted_geometric_median(X, weights, tol=1e-6, max_iter=500):
    X = np.asarray(X, dtype=float)
    weights = np.asarray(weights, dtype=float)

    if X.shape[0] == 0:
        raise ValueError("X is empty.")
    if np.any(weights < 0):
        raise ValueError("Weights must be nonnegative.")
    if np.all(weights == 0):
        raise ValueError("At least one weight must be positive.")

    y = np.average(X, axis=0, weights=weights)

    for _ in range(max_iter):
        diff = X - y
        dist = np.linalg.norm(diff, axis=1)

        zero_mask = dist < 1e-12
        if np.any(zero_mask):
            return X[zero_mask][0].copy()

        numerator = np.sum((weights[:, None] * X) / dist[:, None], axis=0)
        denominator = np.sum(weights / dist)
        y_new = numerator / denominator

        if np.linalg.norm(y_new - y) < tol:
            return y_new

        y = y_new

    return y


# cluster_id given to a point no depot has capacity left to serve.
UNSERVED = -1

# Stats from the last capacity-constrained solve (for UI/run logs).
_LAST_CAP_STATS = {
    "split_points_last": 0, "split_points_max": 0, "mcf_calls": 0,
    "cap_overflow_last": 0, "cap_overflow_max": 0, "cap_overflow_by_depot": [],
    "unserved_points": 0, "unserved_demand": 0,
}


def get_last_cap_stats():
    """Return a copy of capacity-run MCF stats from the most recent capped solve."""
    return dict(_LAST_CAP_STATS)


def _require_integer_demand(w, context="capacity run"):
    """Raise if any demand is non-integer (fractional demand unsupported when capped)."""
    w = np.asarray(w, dtype=float)
    if not np.all(np.isfinite(w)) or np.any(w < 0):
        raise ValueError(f"Demand must be finite and non-negative for {context}.")
    if not np.allclose(w, np.rint(w), rtol=0, atol=1e-9):
        raise ValueError(
            f"Demand must be integer-valued for {context} "
            f"(capacity limits demand/items per fixed depot). "
            f"Fractional demand is not supported on capacity runs."
        )
    return np.rint(w).astype(np.int64)


def _mcf_assignment(X, centers, w, capacities):
    """Min-cost assignment under per-depot demand (item) capacities.

    Each point supplies int(w_i) units of flow; each depot accepts at most
    capacities[j] demand units. Point→depot unit_cost is rint(distance × 1000)
    only (weighting comes from flow volume). Point→depot arc capacity is
    int(w_i). Uses bulk OR-Tools APIs. After the (splittable) solve, each point
    is repaired wholly onto the depot carrying the largest share of its flow.

    When the caps sum to less than total demand, an extra "unserved" node
    takes the shortfall at a penalty dearer than any rerouting, so as much
    demand as possible is served and the points left over are the ones
    farthest from spare capacity. Those points get cluster_id UNSERVED (-1).
    Returns cluster_id of shape (n,).
    """
    X = np.asarray(X, dtype=float)
    centers = np.asarray(centers, dtype=float)
    w_int = _require_integer_demand(w, context="MCF demand-capacity assignment")
    capacities = np.asarray(capacities, dtype=np.int64)
    n, k = X.shape[0], centers.shape[0]
    if len(capacities) != k:
        raise ValueError(
            f"capacities length {len(capacities)} does not match {k} depots"
        )
    total_demand = int(w_int.sum())

    dist = np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2)
    unit_cost = np.maximum(np.rint(dist * 1000.0), 0.0)
    if unit_cost.max() > np.iinfo(np.int64).max:
        raise OverflowError(
            f"unit_cost max {unit_cost.max()} exceeds int64 range"
        )
    unit_cost = unit_cost.astype(np.int64)

    # Shortfall only possible when every depot is capped and the caps fall
    # short; otherwise the network is unchanged (no extra node or arcs).
    short = int(capacities.sum()) < total_demand
    if short:
        # Any augmenting path that serves one more unit crosses at most 2k
        # point-depot arcs, so this penalty always prefers serving.
        penalty = (2 * k + 1) * (int(unit_cost.max()) + 1)
        if penalty * total_demand > np.iinfo(np.int64).max:
            raise OverflowError("unserved penalty exceeds int64 range")

    mcf = min_cost_flow.SimpleMinCostFlow()
    sink = n + k
    unserved = n + k + 1

    # Supplies: points = demand, depots = 0, sink = -total
    mcf.set_nodes_supplies(np.arange(n, dtype=np.int32), w_int.astype(np.int64))
    mcf.set_nodes_supplies(
        np.arange(n, n + k, dtype=np.int32), np.zeros(k, dtype=np.int64)
    )
    mcf.set_nodes_supplies(
        np.array([sink], dtype=np.int32), np.array([-total_demand], dtype=np.int64)
    )
    if short:
        mcf.set_nodes_supplies(
            np.array([unserved], dtype=np.int32), np.array([0], dtype=np.int64)
        )

    # Depot → sink (capacity = demand cap)
    mcf.add_arcs_with_capacity_and_unit_cost(
        np.arange(n, n + k, dtype=np.int32),
        np.full(k, sink, dtype=np.int32),
        capacities.astype(np.int64, copy=False),
        np.zeros(k, dtype=np.int64),
    )

    # Point → depot arcs (n×k), point-major order
    i_idx = np.repeat(np.arange(n, dtype=np.int32), k)
    j_idx = np.tile(np.arange(k, dtype=np.int32), n)
    mcf.add_arcs_with_capacity_and_unit_cost(
        i_idx,
        (n + j_idx).astype(np.int32),
        np.repeat(w_int, k).astype(np.int64),
        unit_cost[i_idx, j_idx],
    )

    # Point → unserved (penalised) and unserved → sink, after the n×k block
    # so the point→depot arc indices below stay the same.
    if short:
        mcf.add_arcs_with_capacity_and_unit_cost(
            np.arange(n, dtype=np.int32),
            np.full(n, unserved, dtype=np.int32),
            w_int.astype(np.int64),
            np.full(n, penalty, dtype=np.int64),
        )
        mcf.add_arcs_with_capacity_and_unit_cost(
            np.array([unserved], dtype=np.int32),
            np.array([sink], dtype=np.int32),
            np.array([total_demand], dtype=np.int64),
            np.array([0], dtype=np.int64),
        )

    status = mcf.solve()
    if status != mcf.OPTIMAL:
        raise RuntimeError(f"MCF assignment failed with status {status}")

    # Point→depot arcs are indices [k, k + n*k)
    flow_mat = np.asarray(
        mcf.flows(np.arange(k, k + n * k, dtype=np.int32)), dtype=np.int64
    ).reshape(n, k)
    if short:
        # Unserved flow becomes column k, so argmax can pick it.
        unserved_flow = np.asarray(
            mcf.flows(np.arange(k + n * k, k + n * k + n, dtype=np.int32)),
            dtype=np.int64,
        )
        flow_mat = np.column_stack([flow_mat, unserved_flow])
    if np.any(flow_mat.sum(axis=1) <= 0):
        raise RuntimeError("MCF left some points unassigned")

    n_split = int(np.sum((flow_mat > 0).sum(axis=1) > 1))
    # Repair: whole point to depot (or unserved) with largest flow share
    cluster_id = np.argmax(flow_mat, axis=1).astype(int)
    cluster_id[cluster_id == k] = UNSERVED

    _LAST_CAP_STATS["split_points_last"] = n_split
    _LAST_CAP_STATS["split_points_max"] = max(
        _LAST_CAP_STATS.get("split_points_max", 0), n_split
    )
    _LAST_CAP_STATS["mcf_calls"] = _LAST_CAP_STATS.get("mcf_calls", 0) + 1

    # Whole-point repair can push a depot over its cap even though the
    # (splittable) MCF solve respected every cap exactly: this check is
    # reporting-only, it does not alter cluster_id or re-solve.
    served = cluster_id != UNSERVED
    realised = np.bincount(
        cluster_id[served], weights=w_int[served], minlength=k
    ).astype(np.int64)
    overflow = np.maximum(realised - capacities, 0)
    _LAST_CAP_STATS["cap_overflow_last"] = int(overflow.sum())
    _LAST_CAP_STATS["cap_overflow_max"] = max(
        _LAST_CAP_STATS.get("cap_overflow_max", 0), int(overflow.sum())
    )
    _LAST_CAP_STATS["cap_overflow_by_depot"] = overflow.tolist()
    if overflow.sum() > 0:
        offending = ", ".join(
            f"depot {j} realised {int(realised[j])} > cap {int(capacities[j])}"
            for j in np.nonzero(overflow)[0]
        )
        warnings.warn(
            "Capacity split-point repair pushed one or more depots over their "
            f"cap ({offending}). The min-cost-flow solve respected every cap "
            "exactly; the overshoot comes from repairing split demand onto a "
            "single depot without re-checking capacity.",
            stacklevel=2,
        )
    return cluster_id


def _assign_points(X, centers, w, capacities):
    """Nearest-depot assignment; MCF only when a hard demand capacity is exceeded."""
    if capacities is None:
        return np.argmin(pairwise_sq_dists(X, centers), axis=1)

    sq = pairwise_sq_dists(X, centers)
    cluster_id = np.argmin(sq, axis=1)
    caps = np.asarray(capacities, dtype=np.int64)
    demand_by = np.bincount(
        cluster_id, weights=np.asarray(w, dtype=float), minlength=len(centers)
    )
    if np.all(demand_by <= caps + 1e-9):
        return cluster_id
    return _mcf_assignment(X, centers, w, caps)


def _weighted_distance(X, w, centers, cluster_id):
    """Sum of demand x distance to the assigned depot; unserved points add 0."""
    served = cluster_id != UNSERVED
    if not np.all(served):
        X, w, cluster_id = X[served], w[served], cluster_id[served]
    return np.sum(w * np.linalg.norm(X - centers[cluster_id], axis=1))


def _alternating_location_allocation(X, w, centers, fixed_mask=None, capacities=None):
    """One assign-relocate run from the given starting depots.

    Alternates: assign every point to a depot, then relocate each free depot to
    its cluster's weighted geometric median. Depots whose fixed_mask entry is
    True stay put every pass. When capacities is None, assignment is nearest-
    depot (the production path). When capacities is set (demand units per depot),
    nearest-depot is used only if it respects every demand cap; otherwise a
    min-cost-flow assignment enforces the limits. Stops when the objective
    change drops below 1e-4 (max 50 iterations). Returns
    (centers, cluster_id, objective) where objective = sum of demand x distance
    under the final assignment (unserved points, cluster_id UNSERVED, add 0).
    """
    centers = centers.astype(float).copy()
    prev_obj = np.inf

    for _ in range(50):
        cluster_id = _assign_points(X, centers, w, capacities)

        for cid in range(len(centers)):
            if fixed_mask is not None and fixed_mask[cid]:
                continue
            mask = cluster_id == cid
            if np.any(mask):
                centers[cid] = weighted_geometric_median(X[mask], w[mask])

        obj = _weighted_distance(X, w, centers, cluster_id)
        if abs(prev_obj - obj) < 1e-4:
            break
        prev_obj = obj

    cluster_id = _assign_points(X, centers, w, capacities)
    obj = float(_weighted_distance(X, w, centers, cluster_id))
    return centers, cluster_id, obj


def _normalize_max_points(max_points, m):
    """Normalize max_points to None or a length-m list of positive int | None.

    None → None (no caps).
    Scalar int → [v] * m (same cap on every fixed depot; backward compatible).
    list/tuple of length m → each element a positive int or None (None = uncapped
    for that fixed depot). All-None list collapses to None.
    """
    if max_points is None:
        return None

    if isinstance(max_points, (list, tuple)):
        if len(max_points) != m:
            raise ValueError(
                f"max_points list length {len(max_points)} does not match "
                f"{m} fixed depot(s)."
            )
        out = []
        for i, v in enumerate(max_points):
            if v is None:
                out.append(None)
                continue
            try:
                iv = int(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"max_points[{i}] must be a positive integer or None."
                ) from exc
            if iv < 1:
                raise ValueError(
                    f"max_points[{i}] must be a positive integer when set."
                )
            out.append(iv)
        if all(c is None for c in out):
            return None
        return out

    # Scalar: apply the same cap to every fixed depot.
    try:
        v = int(max_points)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "max_points must be a positive integer, a list of int|None, or None."
        ) from exc
    if v < 1:
        raise ValueError("max_points must be a positive integer when set.")
    return [v] * m


# ============================================================
# Solver Functions
# ============================================================

def solve_single_depot_weighted_geometric_median(df):
    """
    Find optimal single depot location using weighted geometric median.

    Returns (depots_df, assignments_df, objective), matching the other two
    solvers. objective is item*metres (X is in local metres), the same
    convention as solve_multiple_depots / solve_fixed_plus_variable_depots;
    it is exact here (no seed/multi-start variance -- one geometric median).
    """
    data = df[df["demand"] > 0].copy()

    lat0 = np.average(data["lat"], weights=data["demand"])
    lon0 = np.average(data["lon"], weights=data["demand"])

    x, y = latlon_to_xy(data["lat"], data["lon"], lat0, lon0)
    X = np.column_stack([x, y])
    w = data["demand"].values

    depot_xy = weighted_geometric_median(X, w)
    depot_lat, depot_lon = xy_to_latlon(depot_xy[0], depot_xy[1], lat0, lon0)
    objective = float(np.sum(w * np.linalg.norm(X - depot_xy, axis=1)))

    depots_df = pd.DataFrame([{
        "depot_id": 0,
        "depot_lat": float(depot_lat),
        "depot_lon": float(depot_lon),
        "assigned_total_demand": float(np.sum(w)),
        "n_nodes": len(data)
    }])

    assignments_df = data.copy()
    assignments_df["cluster_id"] = 0

    return depots_df, assignments_df, objective


def solve_multiple_depots(df, k, max_points=None, progress_callback=None):
    """
    Find k optimal depot locations using demand-weighted clustering.

    10-seed multi-start, keep lowest objective. Each seed initialises the k
    depots with full KMeans (k-means++, one init) and refines them with the
    assign-relocate loop; the run with the smallest sum of demand x distance
    wins. max_points is accepted for API symmetry with Fixed mode but has no
    effect here (Multi has no fixed depots). Optional progress_callback is
    called as progress_callback(done, total, message) after each seed.
    Returns (depots_df, assignments_df, objective).
    """
    # Multi mode has no fixed depots; point caps apply only to fixed depots.
    _ = max_points

    data = df[df["demand"] > 0].copy().reset_index(drop=True)
    lat0 = np.average(data["lat"], weights=data["demand"])
    lon0 = np.average(data["lon"], weights=data["demand"])

    x, y = latlon_to_xy(data["lat"], data["lon"], lat0, lon0)
    X = np.column_stack([x, y])
    w = data["demand"].values.astype(float)

    n_seeds = 10
    best_obj = np.inf
    best_centers = None
    best_cluster_id = None
    for seed in range(n_seeds):
        if progress_callback is not None:
            progress_callback(
                seed, n_seeds, f"Multi-start seed {seed + 1} of {n_seeds}…")
        start = KMeans(n_clusters=k, n_init=1, random_state=seed).fit(
            X, sample_weight=w).cluster_centers_
        centers, cluster_id, obj = _alternating_location_allocation(X, w, start)
        if obj < best_obj:
            best_obj = obj
            best_centers = centers
            best_cluster_id = cluster_id
        if progress_callback is not None:
            progress_callback(
                seed + 1, n_seeds, f"Finished seed {seed + 1} of {n_seeds}")

    data["cluster_id"] = best_cluster_id

    depots = []
    for cid in range(k):
        mask = best_cluster_id == cid
        lat, lon = xy_to_latlon(best_centers[cid, 0], best_centers[cid, 1], lat0, lon0)

        depots.append({
            "depot_id": cid,
            "depot_lat": lat,
            "depot_lon": lon,
            "assigned_demand": float(np.sum(w[mask])),
            "n_nodes": int(np.sum(mask)),
        })

    return pd.DataFrame(depots), data, best_obj


def solve_fixed_plus_variable_depots(
    df, n_free, fixed_coords, max_points=None, progress_callback=None
):
    """
    Find depot locations with m pinned depots and n_free optimised ones.

    fixed_coords is a list of (lat, lon) pairs, one per pinned depot (m of
    them); total depots = m + n_free. The first m depots are pinned at those
    coordinates and held in place; the n_free free depots are seeded from
    demand points via demand-weighted k-means++ across 10 seeded starts and
    refined by the assign-relocate loop, keeping the lowest-objective run.
    When n_free is 0 every point is simply assigned to a pinned depot.
    Optional max_points is a hard *demand* (items) cap on fixed depots only
    (free depots stay unlimited). Accepts None (no caps), a scalar positive int
    (same cap on every fixed depot), or a length-m list of positive int | None
    (None = that fixed depot is uncapped). Demand must be integer-valued when
    any cap is set. If every depot is capped (no free or uncapped fixed
    depot) and the caps sum to less than total demand, the demand that does
    not fit is left unserved: those points get cluster_id UNSERVED (-1) and
    count toward no depot, showing where the network lacks capacity. The
    objective then covers served demand only. Optional progress_callback is called as
    progress_callback(done, total, message) during multi-start. Returns
    (depots_df, assignments_df, objective) with is_fixed True for the first m
    depots.
    """
    data = df[df["demand"] > 0].copy().reset_index(drop=True)

    m = len(fixed_coords)
    k_total = m + n_free
    n_points = len(data)

    if data.empty:
        raise ValueError("No valid demand points.")
    if k_total < 1:
        raise ValueError("Total depots (fixed + free) must be at least 1.")
    if k_total > n_points:
        raise ValueError("Total depots exceed number of demand nodes.")

    max_points = _normalize_max_points(max_points, m)

    lat0 = np.average(data["lat"], weights=data["demand"])
    lon0 = np.average(data["lon"], weights=data["demand"])

    x, y = latlon_to_xy(data["lat"], data["lon"], lat0, lon0)
    X = np.column_stack([x, y])
    w = data["demand"].values.astype(float)

    # Capacity runs require integer demand (items per point).
    if max_points is not None:
        w_int = _require_integer_demand(w, context="fixed-depot capacity runs")
        total_demand = int(w_int.sum())
    else:
        total_demand = int(np.rint(w.sum())) if np.allclose(w, np.rint(w)) else float(w.sum())

    # The m pinned depots occupy the first m centers, held in place by the mask.
    fixed_lats = np.array([c[0] for c in fixed_coords], dtype=float)
    fixed_lons = np.array([c[1] for c in fixed_coords], dtype=float)
    fx, fy = latlon_to_xy(fixed_lats, fixed_lons, lat0, lon0)
    fixed_xy = np.column_stack([fx, fy])

    fixed_mask = np.zeros(k_total, dtype=bool)
    fixed_mask[:m] = True

    # Cap only fixed depots that have a numeric demand limit; uncapped fixed
    # depots and all free depots get capacity = total_demand (unlimited).
    _LAST_CAP_STATS["split_points_last"] = 0
    _LAST_CAP_STATS["split_points_max"] = 0
    _LAST_CAP_STATS["mcf_calls"] = 0
    _LAST_CAP_STATS["cap_overflow_last"] = 0
    _LAST_CAP_STATS["cap_overflow_max"] = 0
    _LAST_CAP_STATS["cap_overflow_by_depot"] = []
    _LAST_CAP_STATS["unserved_points"] = 0
    _LAST_CAP_STATS["unserved_demand"] = 0
    if max_points is None:
        capacities = None
    else:
        capacities = np.empty(k_total, dtype=np.int64)
        for i in range(m):
            capacities[i] = (
                int(max_points[i]) if max_points[i] is not None else total_demand
            )
        capacities[m:] = total_demand

    if n_free == 0:
        # All depots pinned: one pass assigns every point to a pinned depot
        # (the mask leaves the depots where they are).
        if progress_callback is not None:
            progress_callback(0, 1, "Assigning points to fixed depot(s)…")
        centers, cluster_id, obj = _alternating_location_allocation(
            X, w, fixed_xy, fixed_mask=fixed_mask, capacities=capacities)
        if progress_callback is not None:
            progress_callback(1, 1, "Assignment complete")
    else:
        n_seeds = 10
        best = None
        for seed in range(n_seeds):
            if progress_callback is not None:
                progress_callback(
                    seed, n_seeds, f"Multi-start seed {seed + 1} of {n_seeds}…")
            free_start, _ = kmeans_plusplus(
                X, n_clusters=n_free, sample_weight=w, random_state=seed)
            start = np.vstack([fixed_xy, free_start])
            result = _alternating_location_allocation(
                X, w, start, fixed_mask=fixed_mask, capacities=capacities)
            if best is None or result[2] < best[2]:
                best = result
            if progress_callback is not None:
                progress_callback(
                    seed + 1, n_seeds, f"Finished seed {seed + 1} of {n_seeds}")
        centers, cluster_id, obj = best

    data["cluster_id"] = cluster_id
    unserved = cluster_id == UNSERVED
    if np.any(unserved):
        _LAST_CAP_STATS["unserved_points"] = int(unserved.sum())
        _LAST_CAP_STATS["unserved_demand"] = int(np.rint(w[unserved].sum()))

    depot_rows = []
    for cid in range(k_total):
        mask = cluster_id == cid
        depot_lat, depot_lon = xy_to_latlon(centers[cid, 0], centers[cid, 1], lat0, lon0)

        row = {
            "depot_id": cid,
            "is_fixed": bool(cid < m),
            "depot_lat": float(depot_lat),
            "depot_lon": float(depot_lon),
            "assigned_total_demand": float(np.sum(w[mask])) if np.any(mask) else 0.0,
            "n_nodes": int(np.sum(mask)),
        }
        # Report max_points (demand cap in items) only on fixed depots that have a cap.
        if max_points is not None and cid < m and max_points[cid] is not None:
            row["max_points"] = int(max_points[cid])
        depot_rows.append(row)

    depots_df = pd.DataFrame(depot_rows)
    return depots_df, data, float(obj)
