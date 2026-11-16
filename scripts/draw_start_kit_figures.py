# /// script
# dependencies = ["numpy", "matplotlib"]
# ///
"""Draw the figures `start_kit/wtf.md` embeds into `start_kit/img/`.

Run from the repo root:

    uv run scripts/draw_start_kit_figures.py

The track figure reads `start_kit/centerline.csv`, so rerun this after
`write_centerline_csv.py`.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Arc, Circle, FancyArrowPatch, Polygon

KIT = Path(__file__).resolve().parents[1] / "start_kit"
OUT = KIT / "img"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
MUTED = "#52514e"
FAINT = "#c3c2b7"
ACCENT = "#2a78d6"  # the quantity a figure is about
HIGHLIGHT = "#eb6834"  # beam 0

plt.rcParams.update(
    {
        "font.size": 10,
        "font.family": "DejaVu Sans",
        "mathtext.fontset": "dejavusans",
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "text.color": INK,
    }
)


def bare_axes(ax):
    ax.set_aspect("equal")
    ax.set_axis_off()


def arrow(ax, start, end, color=INK, lw=1.2, style="-|>", ls="-", z=2):
    ax.add_patch(
        FancyArrowPatch(
            start, end, arrowstyle=style, mutation_scale=10,
            color=color, lw=lw, linestyle=ls, shrinkA=0, shrinkB=0, zorder=z,
        )
    )


def car(ax, x, y, yaw, length=1.6, width=0.8, color=INK):
    """A car as an arrowhead-shaped polygon pointing along yaw."""
    body = np.array(
        [[-length / 2, -width / 2], [length / 4, -width / 2], [length / 2, 0],
         [length / 4, width / 2], [-length / 2, width / 2]]
    )
    c, s = np.cos(yaw), np.sin(yaw)
    pts = body @ np.array([[c, s], [-s, c]]) + (x, y)
    ax.add_patch(Polygon(pts, closed=True, color=color, zorder=5))


def pose_figure():
    x, y, yaw = 4.0, 2.6, np.radians(35)
    fig, ax = plt.subplots(figsize=(3.0, 2.1))
    bare_axes(ax)

    arrow(ax, (0, 0), (7, 0))
    arrow(ax, (0, 0), (0, 4.6))
    ax.text(7.15, 0, "x", va="center")
    ax.text(0, 4.75, "y", ha="center")

    # Projections onto the axes give x and y.
    ax.plot([x, x], [0, y], ls=":", color=MUTED, lw=1)
    ax.plot([0, x], [y, y], ls=":", color=MUTED, lw=1)
    ax.text(x, -0.35, "x", ha="center", va="top", color=ACCENT, fontweight="bold")
    ax.text(-0.3, y, "y", ha="right", va="center", color=ACCENT, fontweight="bold")

    # yaw: from a +x reference to the heading, counter-clockwise.
    ref = 2.3
    ax.plot([x, x + ref], [y, y], ls="--", color=MUTED, lw=1)
    arrow(ax, (x, y), (x + ref * np.cos(yaw), y + ref * np.sin(yaw)), color=INK)
    r = 1.5
    ax.add_patch(Arc((x, y), 2 * r, 2 * r, theta1=0, theta2=np.degrees(yaw),
                     color=ACCENT, lw=1.6))
    tip = (x + r * np.cos(yaw), y + r * np.sin(yaw))
    before = (x + r * np.cos(yaw - 0.15), y + r * np.sin(yaw - 0.15))
    arrow(ax, before, tip, color=ACCENT, lw=1.6)
    mid = yaw / 2
    ax.text(x + (r + 0.25) * np.cos(mid), y + (r + 0.25) * np.sin(mid), "yaw",
            color=ACCENT, fontweight="bold", va="center")

    car(ax, x, y, yaw)
    ax.text(x + 0.3, y - 0.5, "char", va="top")
    ax.set_xlim(-0.8, 7.6)
    ax.set_ylim(-0.8, 5.1)
    fig.savefig(OUT / "pose.png", dpi=200, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


def ray_hit(origin, angle, half_width, obstacle, radius):
    """Distance along a beam to the first corridor wall or the obstacle."""
    d = np.array([np.cos(angle), np.sin(angle)])
    hits = []
    if abs(d[1]) > 1e-9:
        for wall in (half_width, -half_width):
            t = (wall - origin[1]) / d[1]
            if t > 0:
                hits.append(t)
    # Circle: |o + t d - c|^2 = r^2
    oc = origin - obstacle
    b = oc @ d
    disc = b * b - (oc @ oc - radius**2)
    if disc >= 0:
        t = -b - np.sqrt(disc)
        if t > 0:
            hits.append(t)
    return min(hits) if hits else np.inf


def scan_figure():
    origin = np.array([0.0, 0.0])
    half_width = 3.0
    obstacle, radius = np.array([6.0, 0.4]), 1.0
    x_min, x_max = -9.0, 12.0
    n = 72
    angles = np.radians(np.arange(n) * 360 / n)
    dists = [ray_hit(origin, a, half_width, obstacle, radius) for a in angles]

    fig, ax = plt.subplots(figsize=(5.2, 2.35))
    bare_axes(ax)

    for wall in (half_width, -half_width):
        ax.plot([x_min, x_max], [wall, wall], color=INK, lw=1.8)
    ax.add_patch(Circle(obstacle, radius, color=INK, zorder=2))

    view = (x_min, x_max)
    for i, (a, d) in enumerate(zip(angles, dists)):
        if i == 0:
            continue
        end = origin + min(d, 30) * np.array([np.cos(a), np.sin(a)])
        if np.isinf(d):
            ax.plot(*zip(origin, end), color=FAINT, lw=1, ls="--", zorder=1)
            ax.text(x_min + 0.2, 0.25, "np.inf", color=MUTED, fontsize=9)
            continue
        ax.plot(*zip(origin, end), color=FAINT, lw=0.6, zorder=1)
        if view[0] <= end[0] <= view[1]:
            ax.plot(*end, "o", color=ACCENT, ms=2, zorder=3)

    # Beam 0: straight ahead, and its distance.
    hit0 = origin + dists[0] * np.array([1.0, 0.0])
    ax.plot(*zip(origin, hit0), color=HIGHLIGHT, lw=1.8, zorder=3)
    ax.plot(*hit0, "o", color=HIGHLIGHT, ms=4, zorder=6)
    ax.annotate(
        f"laser_scan[0] = {dists[0]:.2f} m", xy=(dists[0] / 2, 0),
        xytext=(dists[0] / 2 - 1.5, -1.6), color=HIGHLIGHT, fontweight="bold",
        fontsize=10, arrowprops=dict(arrowstyle="-", color=HIGHLIGHT, lw=0.8),
    )

    # The increment between beam 0 and beam 1, and their indices.
    hit1 = origin + dists[1] * np.array([np.cos(angles[1]), np.sin(angles[1])])
    ax.plot(*zip(origin, hit1), color=MUTED, lw=1, zorder=2)
    r = 3.0
    ax.add_patch(Arc(origin, 2 * r, 2 * r, theta1=0, theta2=5, color=INK, lw=1.2))
    ax.text(r + 0.1, 0.12, "5°", fontsize=10, va="bottom")
    ax.text(1.6, -0.15, "0", color=HIGHLIGHT, fontsize=10, fontweight="bold",
            ha="center", va="top")
    ax.text(1.6, 0.3, "1", color=MUTED, fontsize=10, ha="center", va="bottom")

    car(ax, *origin, 0.0, length=1.2, width=0.6)
    ax.text(-0.6, -0.7, "char", ha="center", va="top", zorder=6,
            bbox=dict(boxstyle="square,pad=0.1", fc=SURFACE, ec="none"))

    # The array a controller gets: the first five beams, as drawn.
    # Beam 0 is drawn over the rest in its colour; the font is monospace,
    # so blanking each part out of the other keeps them aligned.
    head = "laser_scan = ["
    first = f"{dists[0]:.2f}"
    rest = "".join(f", {d:.2f}" for d in dists[1:5]) + ", …]"
    for text, color in (
        (head + " " * len(first) + rest, INK),
        (" " * len(head) + first + " " * len(rest), HIGHLIGHT),
    ):
        ax.text(x_max, -half_width - 0.5, text, color=color, ha="right",
                va="top", family="monospace", fontsize=9)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-half_width - 1.4, half_width + 0.3)
    fig.savefig(OUT / "laser_scan.png", dpi=200, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


def track_figure():
    x, y, left, right = np.loadtxt(
        KIT / "centerline.csv", delimiter=",", skiprows=1, unpack=True
    )
    # The CSV repeats its first point at the end; drop it and wrap the
    # differences around the loop. Each wall is a closed polygon, so it has
    # no seam at the start.
    x, y, left, right = x[:-1], y[:-1], left[:-1], right[:-1]
    tx, ty = np.roll(x, -1) - np.roll(x, 1), np.roll(y, -1) - np.roll(y, 1)
    norm = np.hypot(tx, ty)
    nx, ny = -ty / norm, tx / norm  # unit normal pointing left

    fig, ax = plt.subplots(figsize=(3.0, 3.0))
    ax.set_aspect("equal")
    for side, width in ((1, left), (-1, right)):
        wall = np.column_stack((x + side * nx * width, y + side * ny * width))
        ax.add_patch(Polygon(wall, closed=True, fill=False, edgecolor=INK, lw=1.4))
    ax.plot(np.append(x, x[0]), np.append(y, y[0]), color=ACCENT, lw=0.9, ls="--")
    ax.autoscale_view()
    ax.plot(x[0], y[0], "o", color=HIGHLIGHT, ms=5, zorder=5)
    step = len(x) // 40
    arrow(ax, (x[0], y[0]), (x[step], y[step]), color=HIGHLIGHT, lw=1.6, z=5)

    for spine in ax.spines.values():
        spine.set_color(FAINT)
    ax.tick_params(colors=MUTED, labelsize=10, length=0)
    ax.set_xlabel("x [m]", color=MUTED, fontsize=11)
    ax.set_ylabel("y [m]", color=MUTED, fontsize=11)
    fig.savefig(OUT / "track.png", dpi=200, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


def grip_figure():
    fig, ax = plt.subplots(figsize=(5.4, 3.3))
    bare_axes(ax)

    # Lateral on x, longitudinal on y; the disc is all the tyres' grip.
    ax.plot([-1.32, 1.32], [0, 0], color=FAINT, lw=0.8, zorder=1)
    ax.plot([0, 0], [-1.18, 1.18], color=FAINT, lw=0.8, zorder=1)
    ax.add_patch(Circle((0, 0), 1, fc=ACCENT, alpha=0.18, ec="none", zorder=0))
    ax.add_patch(Circle((0, 0), 1, fill=False, ec=INK, lw=1.6, zorder=2))

    # Asking for more than the circle: the tyres slide.
    arrow(ax, (-1, 0), (-1.3, 0), color=HIGHLIGHT, lw=1.8, z=4)
    ax.text(-1.38, 0, "En dehors\ndu cercle,\non glisse",
            color=HIGHLIGHT, fontweight="bold", ha="right", va="center")

    ax.annotate("adhérence maximale", xy=(np.cos(0.8), np.sin(0.8)),
                xytext=(1.0, 1.05), ha="left", va="bottom",
                arrowprops=dict(arrowstyle="-", color=INK, lw=0.8))
    ax.annotate("Zone d'adhérence", xy=(0.45, -0.45), xytext=(1.1, -0.8),
                color=ACCENT, fontweight="bold", ha="left", va="center",
                arrowprops=dict(arrowstyle="-", color=ACCENT, lw=0.8))

    for text, xy, align in (
        ("accélérer", (0, 1.22), dict(ha="center", va="bottom")),
        ("freiner", (0, -1.22), dict(ha="center", va="top")),
        ("tourner", (1.36, 0), dict(ha="left", va="center")),
    ):
        ax.text(*xy, text, color=MUTED, **align)

    # A car turning right while it accelerates sits up and to the right.
    turning = (0.5, 0.45)
    ax.plot(*turning, "o", color=INK, ms=7, zorder=5)
    ax.annotate("Char qui tourne à droite\nen accélérant",
                xy=(turning[0] + 0.05, turning[1]), xytext=(1.15, 0.5),
                ha="left", va="center",
                arrowprops=dict(arrowstyle="-", color=INK, lw=0.8))
    ax.set_xlim(-2.2, 2.3)
    ax.set_ylim(-1.4, 1.45)
    fig.savefig(OUT / "grip.png", dpi=200, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


def main():
    OUT.mkdir(exist_ok=True)
    pose_figure()
    scan_figure()
    track_figure()
    grip_figure()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
