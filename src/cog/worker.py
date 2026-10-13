"""Solver jobs, run in-process or in a child process the GUI can stop.

Kept free of any GUI import so a spawned child process starts quickly and
never touches Tk.
"""

import pickle

from .dataio import load_demand
from .solvers import (
    get_last_cap_stats,
    solve_fixed_plus_variable_depots,
    solve_multiple_depots,
    solve_single_depot_weighted_geometric_median,
)


def add_depot_names(depots_df, fixed_names, free_names=()):
    """Add a depot_name column: fixed depots first, then free ones, in order.

    The solver numbers the m fixed depots 0..m-1 and the free ones after
    them, so names line up by position; any depot without one stays blank.
    Left untouched when nothing has a name, so unnamed runs keep the exact
    columns they always had.
    """
    all_names = list(fixed_names) + list(free_names)
    if not any(all_names):
        return depots_df
    depots_df = depots_df.copy()
    names = [all_names[int(i)] if int(i) < len(all_names) else ""
             for i in depots_df["depot_id"]]
    depots_df.insert(1, "depot_name", names)
    return depots_df


def execute_job(job, progress):
    """Run the job's solver. progress(done, total, message) reports seeds."""
    choice = job["choice"]
    csv_file = job["csv_file"]
    result = {"map_file": job["map_file"], "solver_type": choice, "csv_file": csv_file}

    df = load_demand(csv_file)

    if choice == "Single Depot":
        progress(0, 1, "Computing single-depot geometric median…")
        depots_df, assignments_df, objective = solve_single_depot_weighted_geometric_median(df)
        progress(1, 1, "Single-depot solve complete")

    elif choice == "Multi Depot":
        depots_df, assignments_df, objective = solve_multiple_depots(
            df, job["k"], progress_callback=progress)

    elif choice == "Fixed + Variable Depots":
        depots_df, assignments_df, objective = solve_fixed_plus_variable_depots(
            df,
            job["n_free"],
            job["fixed_coords"],
            max_points=job["max_points"],
            progress_callback=progress,
        )
        depots_df = add_depot_names(
            depots_df, job["fixed_names"], job.get("free_names", ()))
        if job.get("max_points") is not None:
            stats = get_last_cap_stats()
            # Surface split-repair diagnostics even when zero.
            print(
                f"[capacity] MCF calls={stats.get('mcf_calls', 0)} "
                f"split_points_last={stats.get('split_points_last', 0)} "
                f"split_points_max={stats.get('split_points_max', 0)} "
                f"unserved_points={stats.get('unserved_points', 0)} "
                f"unserved_demand={stats.get('unserved_demand', 0)}",
                flush=True,
            )
            # Copied here: the stats live in the solver module's globals, which
            # a child process cannot share with the GUI.
            result["cap_stats"] = dict(stats)

    else:
        raise ValueError(f"Unknown solver mode: {choice}")

    result.update(assignments_df=assignments_df, depots_df=depots_df, objective=objective)
    return result


def run_job_in_child(job, queue):
    """Child-process entry point: messages go back to the GUI on `queue`.

    ("progress", done, total, message) per solver tick, then exactly one of
    ("ok", result) or ("err", exception).
    """
    def progress(done, total, message=None):
        queue.put(("progress", done, total, message))

    try:
        queue.put(("ok", execute_job(job, progress)))
    except Exception as e:
        try:
            pickle.dumps(e)
        except Exception:
            e = RuntimeError(str(e))   # unpicklable exceptions would be lost
        queue.put(("err", e))
