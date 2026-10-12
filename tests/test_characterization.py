"""Characterisation tests: pin the exact behaviour of the three solver modes.

Golden files live in tests/goldens/ and are regenerated only deliberately via
`uv run python tests/generate_goldens.py`. While refactoring, these tests must
stay green at every step — that is the proof that no behaviour changed.
"""

import json

import pytest

import generate_goldens as gg


@pytest.mark.parametrize("mode", gg.MODES)
def test_solver_results_match_golden(mode):
    golden = json.loads(
        (gg.GOLDEN_DIR / f"results_{mode}.json").read_text(encoding="utf-8")
    )
    solver_type, depots_df, assignments_df, _objective = gg.run_mode(mode)
    actual = gg.result_snapshot(solver_type, depots_df, assignments_df)

    assert actual["solver_type"] == golden["solver_type"]
    assert actual["n_points"] == golden["n_points"]
    assert actual["cluster_ids"] == golden["cluster_ids"]
    assert actual["objective_m"] == pytest.approx(golden["objective_m"], rel=1e-6)

    assert len(actual["depots"]) == len(golden["depots"])
    for actual_depot, expected_depot in zip(actual["depots"], golden["depots"]):
        assert set(actual_depot) == set(expected_depot), "depot column schema changed"
        for key, expected_value in expected_depot.items():
            actual_value = actual_depot[key]
            if isinstance(expected_value, float):
                assert actual_value == pytest.approx(
                    expected_value, rel=1e-6, abs=1e-6
                ), f"depot field {key!r} changed"
            else:
                assert actual_value == expected_value, f"depot field {key!r} changed"


@pytest.mark.parametrize("mode", gg.MODES)
def test_report_html_matches_golden(mode):
    golden_html = (gg.GOLDEN_DIR / f"report_{mode}.html").read_text(encoding="utf-8")
    solver_type, depots_df, assignments_df, _objective = gg.run_mode(mode)
    actual_html = gg.normalized_report_html(solver_type, assignments_df, depots_df)

    assert len(actual_html) == len(golden_html), (
        f"report size changed: {len(golden_html):,} -> {len(actual_html):,} chars"
    )
    assert actual_html == golden_html


def test_single_depot_objective_exact():
    """Single Depot's returned objective (3rd tuple element) is exact -- one
    weighted geometric median, no seed/multi-start variance -- unlike the
    approx-compared objective_m in test_solver_results_match_golden above,
    which independently recomputes via haversine rather than checking this
    value directly."""
    _solver_type, _depots_df, _assignments_df, objective = gg.run_mode("single")
    assert objective == 762239766.0198251
