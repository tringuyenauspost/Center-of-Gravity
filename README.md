# Centre of Gravity (COG)

Finds depot locations that minimise demand-weighted straight-line distance for a
set of delivery points. Give it a CSV of latitude, longitude and parcel volume;
it places depots and opens an interactive map report in your browser.

The objective is **item·km** — Σ (demand × distance), reported as a total and as
average km per demand item. Lower is better. It is the only number to compare
runs by.

## Running it

**End users** double-click `Start COG.bat`. It bootstraps its own environment at
`%LOCALAPPDATA%\COG\.venv` and launches the GUI with no terminal window. The
environment deliberately lives outside the repo —
this checkout sits in a OneDrive tree, and a 400-500MB `.venv` inside it gets
synced and file-locked mid-install.

**Developers** use uv:

```
uv sync
uv run cog
```

`requirements.txt` exists for non-uv installs and is generated, not hand-edited:

```
uv export --no-hashes --no-dev --no-emit-project -o requirements.txt
```

## Input format

CSV with `latitude`, `longitude`, `demand_items` (decimal degrees; extra columns
ignored). Samples in `inputs/`; `malaga_pdc.csv` is the 69,743-point Malaga PDC
dataset used throughout the validation.

## Solver modes

- **Single Depot** — weighted geometric median of all points (Weiszfeld).
- **Multi Depot** — choose k. Ten starts, each seeded by demand-weighted
  k-means++ and refined by alternating location-allocation; lowest objective
  wins.
- **Fixed + Variable** — pin any number of depots at known coordinates
  (typed into a table in the main window, or loaded from a CSV with
  `Lat`, `Long` and optional `Delivery volume capacity` columns — see
  `inputs/fixed_depots_example.csv`), add free ones.
  Supports per-depot demand caps, enforced by min-cost flow (ortools).

`docs/COG_User_Guide.md` covers operation in detail.

## Layout

```
src/cog/       solvers.py, geo.py, dataio.py, report.py, app.py, templates/
main.py        entry point — this is what Start COG.bat runs
tests/         golden-snapshot tests pinning solver behaviour
docs/          validation report, user guide, figures
inputs/        sample datasets
```

## Tests

```
uv run pytest
```

Golden-snapshot tests pin solver output byte-for-byte. Regression gate: k=2 on
`malaga_pdc.csv` must give **627,996.592167 item·km**. If that moves, solver
behaviour changed — intentionally or otherwise.

## Documents

`docs/COG_Validation_Report.md` is the **single source of truth**. The PDF and
docx are rendered from it.

Never edit the PDF or docx directly — corrections applied there are silently
lost on the next render. The renderers are not part of this repo — ask the
tool owner to re-render after any change to the markdown.

## Known limitations

- **Distances are straight-line**, on an equirectangular projection centred on
  the data. Suits metro-scale datasets; degrades past roughly 500 km.
- **Capacity caps can be exceeded by a small margin.** Min-cost flow may split
  one point's demand across depots to hit a cap exactly; the repair step assigns
  the whole point to whichever depot took more, without re-checking the cap. A
  depot can finish marginally over. Instrumented with a warning, not fixed —
  see the validation report's limitations section.
- **`get_last_cap_stats()` is not a compliance check.** The stats are written
  per min-cost-flow call inside `_mcf_assignment`, which runs once per seed, so
  they describe the last internal call rather than the winning solution. For
  real compliance compare `depots_df.assigned_total_demand` against
  `max_points`.
- The report needs internet to display — Leaflet and OpenStreetMap tiles load
  from a CDN. Depot numbers are self-contained; only the basemap needs network.

## Gotchas

- `.gitattributes` forces CRLF for `*.bat` and `*.cmd`.
  The repo-wide `eol=lf` rule would otherwise break the launcher on clone. Do
  not remove it.
- Raw QGIS and ArcGIS comparator exports are gitignored and kept local; the
  report's figures are the shipped form of that evidence.
