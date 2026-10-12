"""Centre of Gravity (COG) depot-location tool.

Public API re-exports. The GUI lives in cog.app and is deliberately NOT
imported here, so importing cog (or the solvers) never touches tkinter.
"""

from .dataio import load_demand
from .geo import EARTH_RADIUS_M, latlon_to_xy, pairwise_sq_dists, xy_to_latlon
from .report import (
    display_map_in_browser,
    render_dashboard,
)
from .solvers import (
    solve_fixed_plus_variable_depots,
    solve_multiple_depots,
    solve_single_depot_weighted_geometric_median,
    weighted_geometric_median,
)

__all__ = [
    "load_demand",
    "EARTH_RADIUS_M",
    "latlon_to_xy",
    "pairwise_sq_dists",
    "xy_to_latlon",
    "display_map_in_browser",
    "render_dashboard",
    "solve_fixed_plus_variable_depots",
    "solve_multiple_depots",
    "solve_single_depot_weighted_geometric_median",
    "weighted_geometric_median",
]
