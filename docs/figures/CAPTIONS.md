# Figure captions

All figures share one reconstruction method: demand points are loaded once, projected to a common local plane around the demand-weighted mean of the dataset, and assigned to their nearest depot in that plane -- exactly COG's own assignment rule. Depot *positions* are each tool's own reported coordinates, parsed verbatim from the validation workbook. Depot *colours* are matched across tools by optimal bipartite pairing (minimum total haversine distance to COG's depots for the same k), so the same colour means the same COG depot in every panel.

## displaced_k2.png

At k=2, points shown in light grey are assigned to the same COG-paired depot by both tools; coloured points are assigned to a different depot under ArcGIS (left) or QGIS (right) than under COG, coloured by the destination depot's paired COG index, with counts in the legend. This isolates exactly which points move, rather than requiring a visual diff between full maps. Depot positions as reported by each tool. Territories derived by straight-line nearest-depot under COG's metric; ArcGIS allocated by road-network travel time, so its implied point counts differ slightly from its own export.

## displaced_k4.png

At k=4, points shown in light grey are assigned to the same COG-paired depot by both tools; coloured points are assigned to a different depot under ArcGIS (left) or QGIS (right) than under COG, coloured by the destination depot's paired COG index, with counts in the legend. This isolates exactly which points move, rather than requiring a visual diff between full maps. Depot positions as reported by each tool. Territories derived by straight-line nearest-depot under COG's metric; ArcGIS allocated by road-network travel time, so its implied point counts differ slightly from its own export.

## displaced_k5.png

At k=5, points shown in light grey are assigned to the same COG-paired depot by both tools; coloured points are assigned to a different depot under ArcGIS (left) or QGIS (right) than under COG, coloured by the destination depot's paired COG index, with counts in the legend. This isolates exactly which points move, rather than requiring a visual diff between full maps. Depot positions as reported by each tool. Territories derived by straight-line nearest-depot under COG's metric; ArcGIS allocated by road-network travel time, so its implied point counts differ slightly from its own export.

## displaced_k8.png

At k=8, points shown in light grey are assigned to the same COG-paired depot by both tools; coloured points are assigned to a different depot under ArcGIS (left) or QGIS (right) than under COG, coloured by the destination depot's paired COG index, with counts in the legend. This isolates exactly which points move, rather than requiring a visual diff between full maps. Depot positions as reported by each tool. Territories derived by straight-line nearest-depot under COG's metric; ArcGIS allocated by road-network travel time, so its implied point counts differ slightly from its own export.

## figure_data.csv

Every number plotted on any figure: per depot, per tool, per k -- reconstructed point count and item·km from this script's pipeline, the sheet's own point count and weight for that depot, the sheet's transcribed Δ Points (tool vs its paired COG depot, where applicable), and the haversine pairing distance to COG. The report's tables are built from this file, not by reading values off the images.
