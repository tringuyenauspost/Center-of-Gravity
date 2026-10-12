"""Coordinate utilities: lat/lon <-> local planar coordinates."""

import numpy as np

# ============================================================
# Utilities: lat/lon <-> local planar coordinates
# ============================================================
EARTH_RADIUS_M = 6371000.0  # meters

def latlon_to_xy(lat_deg, lon_deg, lat0_deg, lon0_deg):
    """
    Convert latitude/longitude to local x,y coordinates (meters)
    using an equirectangular approximation around reference point (lat0, lon0).
    Good practical approximation for regional-scale problems.
    """
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    lat0 = np.radians(lat0_deg)
    lon0 = np.radians(lon0_deg)

    x = EARTH_RADIUS_M * (lon - lon0) * np.cos(lat0)
    y = EARTH_RADIUS_M * (lat - lat0)
    return x, y


def xy_to_latlon(x, y, lat0_deg, lon0_deg):
    """
    Convert local x,y coordinates (meters) back to latitude/longitude.
    """
    lat0 = np.radians(lat0_deg)
    lon0 = np.radians(lon0_deg)

    lat = y / EARTH_RADIUS_M + lat0
    lon = x / (EARTH_RADIUS_M * np.cos(lat0)) + lon0

    return np.degrees(lat), np.degrees(lon)


def pairwise_sq_dists(A, B):
    """
    Efficient squared Euclidean distance between rows of A and B.
    A: (n,2), B: (m,2)
    returns: (n,m)
    """
    A2 = np.sum(A * A, axis=1)[:, None]
    B2 = np.sum(B * B, axis=1)[None, :]
    return A2 + B2 - 2 * A @ B.T
