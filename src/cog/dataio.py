"""Demand data loading."""

import pandas as pd


def load_demand(csv_file):
    """Load a demand CSV (latitude, longitude, demand_items columns) into the
    lat/lon/demand frame the solvers expect. Extra columns are ignored."""
    df = pd.read_csv(csv_file)
    df = pd.DataFrame({
        "lat": df["latitude"],
        "lon": df["longitude"],
        "demand": df["demand_items"]
    })
    return df


# Accepted header spellings for a fixed-depot CSV (matched case-insensitively,
# ignoring surrounding spaces). The facility export uses "Lat", "Long" and
# "Delivery volume capacity" (optional), and "WCC name" (optional).
_FIXED_LAT_COLS = ("lat", "latitude")
_FIXED_LON_COLS = ("long", "longitude", "lon", "lng")
_FIXED_CAP_COLS = ("delivery volume capacity", "capacity", "max_items",
                   "max_demand", "max_points", "cap")
_FIXED_NAME_COLS = ("wcc name", "name", "depot name", "facility name")
# Capacity cells that mean "no limit" (Excel's accounting format writes a
# blank as "-"; the facility export marks uncapped sites "Flexible").
_NO_LIMIT_WORDS = {"-", "flexible", "unlimited", "no limit", "none", "n/a"}


def _pick_column(columns, names):
    """First of `names` present in `columns` (lower-cased header -> real header)."""
    for name in names:
        if name in columns:
            return columns[name]
    return None


def load_fixed_depots(csv_file):
    """Load a fixed-depot CSV into (rows, names, free_names).

    rows are (latitude, longitude, max_items) strings and names the matching
    depot names ("" when the file has none). Needs Lat and Long columns;
    WCC name and Delivery volume capacity are optional, and a blank,
    "Flexible" or "-" capacity means no limit. A row with neither Lat nor
    Long is a site whose location is not decided yet: it becomes a free
    depot for the solver to place, and its name goes in free_names. Values stay
    strings so they go through the same validation as typed-in rows. Raises
    ValueError when the file has no usable columns or no depot rows.
    """
    df = pd.read_csv(csv_file, dtype=str, keep_default_na=False,
                     encoding="utf-8-sig")
    columns = {str(c).strip().lower(): c for c in df.columns}
    lat_col = _pick_column(columns, _FIXED_LAT_COLS)
    lon_col = _pick_column(columns, _FIXED_LON_COLS)
    cap_col = _pick_column(columns, _FIXED_CAP_COLS)
    name_col = _pick_column(columns, _FIXED_NAME_COLS)
    if lat_col is None or lon_col is None:
        raise ValueError(
            "Fixed depot CSV needs 'Lat' and 'Long' columns "
            "(and optionally 'Delivery volume capacity').")

    rows, names, free_names = [], [], []
    for _, r in df.iterrows():
        lat = r[lat_col].strip()
        lon = r[lon_col].strip()
        cap = r[cap_col].strip() if cap_col is not None else ""
        if cap.lower() in _NO_LIMIT_WORDS:
            cap = ""
        name = r[name_col].strip() if name_col is not None else ""
        if not lat and not lon:
            if name or cap:     # a real row with no location; fully blank lines vanish
                free_names.append(name)
            continue
        rows.append((lat, lon, cap))
        names.append(name)
    if not rows:
        raise ValueError("Fixed depot CSV has no depot rows with Lat and Long.")
    return rows, names, free_names
