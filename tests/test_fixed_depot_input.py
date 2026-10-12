"""Fixed-depot popup input: parsing and validation, no window needed."""

import pytest

from cog.app import describe_fixed_depots, parse_fixed_count, parse_fixed_depots


def test_valid_rows_return_solver_shapes():
    coords, caps = parse_fixed_depots([
        ("-31.898003", "115.860751", "20000"),
        (" -31.79 ", "115.79", ""),
    ])
    assert coords == [(-31.898003, 115.860751), (-31.79, 115.79)]
    assert caps == [20000, None]


def test_all_blank_caps_collapse_to_none():
    """No capped depot must reach the solver as max_points=None (the cap-off path)."""
    _, caps = parse_fixed_depots([("-31.9", "115.8", ""), ("-31.8", "115.7", "  ")])
    assert caps is None


def test_cap_accepts_thousands_separators():
    _, caps = parse_fixed_depots([("-31.9", "115.8", "20,000")])
    assert caps == [20000]


@pytest.mark.parametrize("row, message", [
    (("", "115.8", ""), "Latitude is required"),
    (("-31.9", "", ""), "Longitude is required"),
    (("abc", "115.8", ""), "Latitude must be a number"),
    (("nan", "115.8", ""), "Latitude must be a number"),
    (("-91", "115.8", ""), "Latitude must be between -90 and 90"),
    (("-31.9", "181", ""), "Longitude must be between -180 and 180"),
    (("-31.9", "115.8", "12.5"), "Max items must be a whole number"),
    (("-31.9", "115.8", "0"), "Max items must be at least 1"),
    (("-31.9", "115.8", "-5"), "Max items must be at least 1"),
])
def test_invalid_row_names_the_problem(row, message):
    with pytest.raises(ValueError, match=message):
        parse_fixed_depots([row])


def test_swapped_lat_lon_gets_a_hint():
    with pytest.raises(ValueError, match="may be swapped"):
        parse_fixed_depots([("115.86", "-31.89", "")])


def test_every_bad_row_is_reported_with_its_number():
    with pytest.raises(ValueError) as exc:
        parse_fixed_depots([
            ("-31.9", "115.8", ""),
            ("", "115.8", ""),
            ("-31.9", "115.8", "x"),
        ])
    lines = str(exc.value).splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("Depot 2:")
    assert lines[1].startswith("Depot 3:")


@pytest.mark.parametrize("raw", ["", "0", "-1", "two", "1.5"])
def test_fixed_count_rejects_non_positive_or_non_integer(raw):
    with pytest.raises(ValueError, match="Number of fixed depots"):
        parse_fixed_count(raw)


def test_fixed_count_accepts_large_numbers():
    assert parse_fixed_count(" 25 ") == 25


def test_summary_lines():
    assert describe_fixed_depots([("", "", "")]) == "Details needed for 1 fixed depot"
    assert describe_fixed_depots([("-31.9", "115.8", "")]) == (
        "1 fixed depot set, no capacity limits")
    assert describe_fixed_depots([
        ("-31.9", "115.8", "20000"), ("-31.8", "115.7", "15000"), ("-31.7", "115.6", ""),
    ]) == "3 fixed depots set: 2 capped (20,000 / 15,000 items), 1 no limit"
