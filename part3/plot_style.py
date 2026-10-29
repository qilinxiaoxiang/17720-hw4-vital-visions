"""Shared matplotlib style for Part 3 figures (light surface, fixed categorical order)."""
import matplotlib as mpl

# Categorical slots in fixed order (validated default palette, light mode).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
TEXT = "#0b0b0b"
TEXT2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"
BIT0 = SERIES[0]   # f0 / bit 0
BIT1 = SERIES[1]   # f1 / bit 1
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]


def apply():
    mpl.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": TEXT2,
        "axes.labelcolor": TEXT,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": TEXT2,
        "ytick.color": TEXT2,
        "lines.linewidth": 2,
        "lines.markersize": 7,
        "legend.fontsize": 9,
        "font.size": 10,
        "savefig.facecolor": SURFACE,
    })
