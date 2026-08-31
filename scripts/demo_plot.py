"""
demo_plot.py
------------
Regenerates docs/demo_output.png: a 3-panel figure showing the
interpolated DEM, flow accumulation, and the delineated catchment +
pond site for a given contour map.

Usage:
    python3 scripts/demo_plot.py path/to/contours.kml
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import hydrology  # noqa: E402
from app.dem import build_dem  # noqa: E402
from app.kml_parser import parse_contours  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/demo_plot.py path/to/contours.kml")
        sys.exit(1)

    src = Path(sys.argv[1])
    points = parse_contours(src.read_bytes())
    dem = build_dem(points)
    flow = hydrology.build_flow_model(dem.elevation, dem.cell_size_m)
    outlet = hydrology.find_pour_point(flow, dem.elevation)
    mask = hydrology.delineate_catchment(flow, outlet)

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    im0 = axes[0].imshow(dem.elevation, origin="lower", cmap="terrain")
    axes[0].contour(dem.elevation, levels=15, colors="k", linewidths=0.3, alpha=0.5)
    axes[0].set_title("Interpolated DEM (m)")
    plt.colorbar(im0, ax=axes[0], fraction=0.046)

    im1 = axes[1].imshow(np.log1p(flow.accumulation), origin="lower", cmap="Blues")
    axes[1].set_title("Flow accumulation (log scale)")
    plt.colorbar(im1, ax=axes[1], fraction=0.046)

    axes[2].imshow(dem.elevation, origin="lower", cmap="terrain", alpha=0.55)
    overlay = np.ma.masked_where(~mask, np.ones_like(dem.elevation))
    axes[2].imshow(overlay, origin="lower", cmap="autumn", alpha=0.65)
    axes[2].scatter(
        [outlet[1]], [outlet[0]], c="red", marker="*", s=250,
        edgecolors="black", label="Pond / pour point", zorder=5,
    )
    axes[2].set_title(f"Catchment ({mask.sum()} cells) + pond site")
    axes[2].legend()

    plt.tight_layout()
    out_path = Path(__file__).resolve().parent.parent / "docs" / "demo_output.png"
    out_path.parent.mkdir(exist_ok=True)
    plt.savefig(out_path, dpi=110)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
