#!/usr/bin/env python3
"""Build publication figures from the frozen 20260811 one-shot campaign.

The script reads CSV tables only. It refuses to place outputs inside the
campaign directory and verifies that every input hash is unchanged after
rendering.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap


BLUE = "#4878D0"
ORANGE = "#EE854A"
GREEN = "#6ACC64"
RED = "#D65F5F"
TEAL = "#3D6B68"
NAVY = "#2F4858"
GRAY = "#7D8790"
LIGHT_GRAY = "#D9DEE5"
INK = "#20262D"

PATH_COLORS = {
    "traversal": ORANGE,
    "indexed": BLUE,
    "batch": GREEN,
    "scalar_v3": ORANGE,
    "sq_cq": BLUE,
}


def configure_matplotlib() -> str:
    available = {font.name for font in mpl.font_manager.fontManager.ttflist}
    candidates = [
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    selected = next((name for name in candidates if name in available), "DejaVu Sans")
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [selected, "DejaVu Sans"],
            "font.size": 9.5,
            "axes.titlesize": 11.5,
            "axes.titleweight": "bold",
            "axes.labelsize": 9.5,
            "axes.edgecolor": "#6F7780",
            "axes.linewidth": 0.8,
            "axes.unicode_minus": False,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.2,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "grid.color": LIGHT_GRAY,
            "grid.linewidth": 0.65,
            "grid.alpha": 0.85,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "savefig.facecolor": "white",
            "savefig.edgecolor": "none",
        }
    )
    return selected


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def require_columns(frame: pd.DataFrame, columns: list[str], table: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{table}: missing columns {', '.join(missing)}")


def read_table(tables: Path, name: str, columns: list[str]) -> pd.DataFrame:
    path = tables / f"{name}.csv"
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path)
    require_columns(frame, columns, path.name)
    return frame


def style_axis(ax: Any, *, grid_axis: str = "both") -> None:
    ax.grid(True, axis=grid_axis)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)


def panel_label(ax: Any, label: str, *, x: float = -0.10, y: float = 1.06) -> None:
    if hasattr(ax, "text2D"):
        ax.text2D(x, y, label, transform=ax.transAxes, fontsize=12, weight="bold")
    else:
        ax.text(x, y, label, transform=ax.transAxes, fontsize=12, weight="bold")


def figure_note(fig: Any, text: str) -> None:
    fig.text(0.01, 0.012, text, ha="left", va="bottom", fontsize=7.4, color="#59636D")


def ecdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ordered = np.sort(np.asarray(values, dtype=float))
    if not len(ordered):
        return ordered, ordered
    x = np.r_[ordered[0], ordered]
    y = np.r_[0.0, np.arange(1, len(ordered) + 1) / len(ordered)]
    return x, y


def draw_half_raincloud(
    ax: Any,
    groups: list[np.ndarray],
    labels: list[str],
    colors: list[str],
    *,
    seed: int,
    point_size: float = 18,
) -> None:
    positions = np.arange(1, len(groups) + 1, dtype=float)
    violins = ax.violinplot(
        groups,
        positions=positions,
        widths=0.72,
        showmeans=False,
        showmedians=False,
        showextrema=False,
    )
    for body, position, color in zip(violins["bodies"], positions, colors):
        vertices = body.get_paths()[0].vertices
        vertices[:, 0] = np.maximum(vertices[:, 0], position)
        body.set_facecolor(color)
        body.set_edgecolor(color)
        body.set_linewidth(1.1)
        body.set_alpha(0.30)

    boxes = ax.boxplot(
        groups,
        positions=positions,
        widths=0.15,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": INK, "linewidth": 1.4},
        whiskerprops={"color": GRAY, "linewidth": 0.9},
        capprops={"color": GRAY, "linewidth": 0.9},
    )
    for patch, color in zip(boxes["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_edgecolor(color)
        patch.set_alpha(0.46)

    rng = np.random.default_rng(seed)
    for position, values, color in zip(positions, groups, colors):
        jitter = rng.normal(-0.16, 0.025, len(values))
        ax.scatter(
            position + jitter,
            values,
            s=point_size,
            facecolor=color,
            edgecolor="white",
            linewidth=0.35,
            alpha=0.68,
            zorder=3,
        )
        median = float(np.median(values))
        ax.scatter(position, median, marker="D", s=28, color=INK, zorder=5)
    ax.set_xticks(positions, labels)


def export_figure(
    fig: Any,
    output_dir: Path,
    stem: str,
    formats: list[str],
) -> dict[str, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: dict[str, dict[str, Any]] = {}
    for suffix in formats:
        path = output_dir / f"{stem}.{suffix}"
        kwargs: dict[str, Any] = {"bbox_inches": "tight", "pad_inches": 0.08}
        if suffix == "png":
            kwargs["dpi"] = 300
        fig.savefig(path, **kwargs)
        exported[suffix] = {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    plt.close(fig)
    return exported


def make_paired_core(tables: Path) -> tuple[Any, dict[str, Any]]:
    columns = [
        "sample_id",
        "order",
        "traversal_core_duration_us",
        "indexed_core_duration_us",
        "indexed_minus_traversal_core_us",
        "traversal_over_indexed_core_ratio",
    ]
    frame = read_table(tables, "contest_paired", columns).copy()
    for column in columns[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["traversal_ms"] = frame["traversal_core_duration_us"] / 1000.0
    frame["indexed_ms"] = frame["indexed_core_duration_us"] / 1000.0
    frame["delta_ms"] = frame["indexed_minus_traversal_core_us"] / 1000.0
    frame = frame.sort_values("traversal_ms").reset_index(drop=True)

    fig = plt.figure(figsize=(15.2, 5.25))
    grid = fig.add_gridspec(1, 3, width_ratios=[1.25, 0.86, 1.02], wspace=0.30)
    ax0 = fig.add_subplot(grid[0, 0])
    ax1 = fig.add_subplot(grid[0, 1])
    ax2 = fig.add_subplot(grid[0, 2])

    y = np.arange(len(frame))
    for index, row in frame.iterrows():
        ax0.plot(
            [row["indexed_ms"], row["traversal_ms"]],
            [index, index],
            color="#AAB2BA",
            linewidth=1.0,
            zorder=1,
        )
    ax0.scatter(frame["traversal_ms"], y, s=28, color=ORANGE, label="遍历", zorder=3)
    ax0.scatter(frame["indexed_ms"], y, s=28, color=BLUE, label="索引", zorder=3)
    ax0.set_yticks(y, [f"S{int(value):02d}" for value in frame["sample_id"]])
    ax0.set_xlabel("核心路径延迟（ms）")
    ax0.set_ylabel("配对启动样本")
    ax0.set_title("逐启动配对哑铃图")
    ax0.legend(frameon=False, ncols=2, loc="lower right")
    style_axis(ax0, grid_axis="x")
    panel_label(ax0, "a")

    groups = [frame["traversal_ms"].to_numpy(), frame["indexed_ms"].to_numpy()]
    for left, right in zip(*groups):
        ax1.plot([1, 2], [left, right], color="#AAB2BA", alpha=0.28, linewidth=0.8)
    draw_half_raincloud(
        ax1,
        groups,
        [f"遍历\nn={len(frame)}", f"索引\nn={len(frame)}"],
        [ORANGE, BLUE],
        seed=6101,
    )
    ax1.set_ylabel("核心路径延迟（ms）")
    ax1.set_title("配对雨云分布")
    style_axis(ax1, grid_axis="y")
    panel_label(ax1, "b")

    x, prob = ecdf(frame["delta_ms"].to_numpy())
    ax2.step(x, prob, where="post", color=BLUE, linewidth=2.25)
    ax2.fill_between(x, 0, prob, step="post", color=BLUE, alpha=0.10)
    median = float(frame["delta_ms"].median())
    ax2.axvline(0, color=INK, linestyle="--", linewidth=0.9)
    ax2.axvline(median, color=ORANGE, linestyle=":", linewidth=1.6)
    ax2.text(
        median,
        0.08,
        f"中位差 {median:.2f} ms",
        rotation=90,
        va="bottom",
        ha="right" if median < 0 else "left",
        color=ORANGE,
        fontsize=8.2,
    )
    ax2.set_xlabel("索引 - 遍历（ms；负值表示索引更快）")
    ax2.set_ylabel("累计概率")
    ax2.set_ylim(0, 1.03)
    ax2.set_title("配对差值 ECDF")
    style_axis(ax2)
    panel_label(ax2, "c")

    faster = int((frame["delta_ms"] < 0).sum())
    speedup = float(frame["traversal_over_indexed_core_ratio"].median())
    order_counts = frame["order"].value_counts().to_dict()
    figure_note(
        fig,
        f"数据：contest_paired.csv；n={len(frame)} 对 fresh boot；索引核心路径更快 {faster}/{len(frame)} 对；"
        f"配对加速比中位数 {speedup:.3f}x。横线连接同一启动中的两个路径。",
    )
    fig.subplots_adjust(left=0.055, right=0.992, top=0.91, bottom=0.16)
    return fig, {
        "sources": ["contest_paired.csv"],
        "paired_boots": int(len(frame)),
        "indexed_faster_pairs": faster,
        "median_indexed_minus_traversal_ms": median,
        "median_speedup": speedup,
        "order_counts": {str(key): int(value) for key, value in order_counts.items()},
    }


def make_catalog_landscape(tables: Path) -> tuple[Any, dict[str, Any]]:
    columns = [
        "experiment",
        "dataset_size",
        "operations",
        "speedup_baseline_over_treatment",
    ]
    frame = read_table(tables, "agenteval_pairs", columns)
    frame = frame[frame["experiment"] == "file_query_table_ablation"].copy()
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    grouped = (
        frame.groupby(["dataset_size", "operations"])["speedup_baseline_over_treatment"]
        .agg(median="median", q1=lambda values: values.quantile(0.25), q3=lambda values: values.quantile(0.75), n="size")
        .reset_index()
    )
    catalogs = sorted(grouped["dataset_size"].astype(int).unique())
    hits = sorted(grouped["operations"].astype(int).unique())
    if len(grouped) != len(catalogs) * len(hits):
        raise ValueError("AgentEval speedup grid is incomplete")
    median = grouped.pivot(index="dataset_size", columns="operations", values="median").reindex(index=catalogs, columns=hits)
    counts = grouped.pivot(index="dataset_size", columns="operations", values="n").reindex(index=catalogs, columns=hits)

    cmap = LinearSegmentedColormap.from_list("agentos_speedup", ["#F7FAFC", "#A9CFCA", TEAL])
    fig = plt.figure(figsize=(13.8, 5.55))
    grid = fig.add_gridspec(1, 2, width_ratios=[0.92, 1.25], wspace=0.06)
    ax0 = fig.add_subplot(grid[0, 0])
    ax1 = fig.add_subplot(grid[0, 1], projection="3d")

    values = median.to_numpy(dtype=float)
    image = ax0.imshow(values, aspect="auto", cmap=cmap, vmin=values.min(), vmax=values.max())
    for row in range(values.shape[0]):
        for column in range(values.shape[1]):
            relative = (values[row, column] - values.min()) / max(values.max() - values.min(), 1e-12)
            color = "white" if relative > 0.60 else INK
            ax0.text(
                column,
                row,
                f"{values[row, column]:.2f}x\nn={int(counts.iloc[row, column])}",
                ha="center",
                va="center",
                color=color,
                fontsize=8.4,
                weight="bold" if relative > 0.86 else "normal",
            )
    ax0.set_xticks(range(len(hits)), [str(value) for value in hits])
    ax0.set_yticks(range(len(catalogs)), [str(value) for value in catalogs])
    ax0.set_xlabel("每轮命中次数")
    ax0.set_ylabel("目录规模（条目）")
    ax0.set_title("实测配对加速比热力图")
    for edge in np.arange(-0.5, len(hits), 1):
        ax0.axvline(edge, color="white", linewidth=1.2)
    for edge in np.arange(-0.5, len(catalogs), 1):
        ax0.axhline(edge, color="white", linewidth=1.2)
    colorbar = fig.colorbar(image, ax=ax0, fraction=0.047, pad=0.035)
    colorbar.set_label("遍历 / 索引（倍）")
    panel_label(ax0, "a", x=-0.14)

    x_values = np.asarray(hits, dtype=float)
    y_values = np.asarray(catalogs, dtype=float)
    x_grid, y_grid = np.meshgrid(x_values, y_values)
    surface = ax1.plot_surface(
        x_grid,
        y_grid,
        values,
        cmap=cmap,
        vmin=values.min(),
        vmax=values.max(),
        edgecolor="white",
        linewidth=0.7,
        alpha=0.94,
        antialiased=True,
    )
    ax1.scatter(x_grid, y_grid, values, color=INK, s=24, depthshade=False, label="实测单元中位数")
    ax1.set_xticks(x_values, [str(value) for value in hits])
    ax1.set_yticks(y_values, [str(value) for value in catalogs])
    ax1.set_xlabel("命中次数", labelpad=8)
    ax1.set_ylabel("目录规模", labelpad=8)
    ax1.set_zlabel("遍历 / 索引（倍）", labelpad=8)
    ax1.set_title("同一实测网格的三维性能曲面", pad=12)
    ax1.view_init(elev=25, azim=-57)
    ax1.legend(loc="upper left", frameon=False)
    ax1.xaxis.pane.set_facecolor((1, 1, 1, 0))
    ax1.yaxis.pane.set_facecolor((1, 1, 1, 0))
    ax1.zaxis.pane.set_facecolor((1, 1, 1, 0))
    panel_label(ax1, "b", x=-0.02, y=1.02)

    best = grouped.loc[grouped["median"].idxmax()]
    figure_note(
        fig,
        f"数据：agenteval_pairs.csv；完整 {len(catalogs)}x{len(hits)} 网格，每格 n={int(counts.min().min())} 对。"
        "每个 hit count 由 1 次 fresh boot 产生，15 对是该 boot 内的重复配对，不作为 15 次独立启动。"
        "曲面仅连接相邻实测中位数，不补缺失单元、不向网格外外推。",
    )
    fig.subplots_adjust(left=0.065, right=0.985, top=0.91, bottom=0.15)
    return fig, {
        "sources": ["agenteval_pairs.csv"],
        "measured_cells": int(len(grouped)),
        "pairs_per_cell": sorted(int(value) for value in grouped["n"].unique()),
        "catalog_sizes": catalogs,
        "hit_counts": hits,
        "median_speedup_range": [float(values.min()), float(values.max())],
        "best_cell": {
            "catalog_size": int(best["dataset_size"]),
            "hit_count": int(best["operations"]),
            "median_speedup": float(best["median"]),
        },
    }


def make_scan_states(tables: Path) -> tuple[Any, dict[str, Any]]:
    columns = ["experiment", "load", "pair", "variant", "operations", "duration_us"]
    frame = read_table(tables, "agenteval_samples", columns)
    frame = frame[
        (frame["experiment"] == "file_query_table_ablation")
        & frame["variant"].isin(["scan", "index"])
    ].copy()
    for column in ["load", "pair", "operations", "duration_us"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["category"] = ""
    frame.loc[(frame["variant"] == "scan") & (frame["pair"] == 1), "category"] = "首次计时 scan"
    frame.loc[(frame["variant"] == "scan") & (frame["pair"] >= 2), "category"] = "后续 scan"
    frame.loc[frame["variant"] == "index", "category"] = "ready index"
    frame["us_per_query"] = frame["duration_us"] / frame["operations"]
    categories = ["首次计时 scan", "后续 scan", "ready index"]
    colors = [GRAY, ORANGE, BLUE]
    catalogs = sorted(int(value) for value in frame["load"].unique())
    grouped = (
        frame.groupby(["load", "category"])["us_per_query"]
        .agg(median="median", q1=lambda values: values.quantile(0.25), q3=lambda values: values.quantile(0.75), n="size")
    )

    fig, ax = plt.subplots(figsize=(8.7, 5.15))
    base = np.arange(len(catalogs), dtype=float)
    width = 0.23
    for offset, (category, color) in enumerate(zip(categories, colors)):
        medians = np.array([grouped.loc[(catalog, category), "median"] for catalog in catalogs], dtype=float)
        q1 = np.array([grouped.loc[(catalog, category), "q1"] for catalog in catalogs], dtype=float)
        q3 = np.array([grouped.loc[(catalog, category), "q3"] for catalog in catalogs], dtype=float)
        counts = [int(grouped.loc[(catalog, category), "n"]) for catalog in catalogs]
        x = base + (offset - 1) * width
        bars = ax.bar(
            x,
            medians,
            width=width,
            color=color,
            alpha=0.86,
            label=f"{category}（每个规模 n={min(counts)}）",
        )
        ax.errorbar(x, medians, yerr=[medians - q1, q3 - medians], fmt="none", color=INK, capsize=2.5, linewidth=0.85)
        for bar, value in zip(bars, medians):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:.0f}", ha="center", va="bottom", fontsize=7.8)

    ax.set_xticks(base, [str(value) for value in catalogs])
    ax.set_xlabel("目录规模（条目）")
    ax.set_ylabel("单次查询延迟（us）")
    ax.set_title("首次计时扫描、后续扫描与就绪索引")
    ax.legend(frameon=False, ncols=3, loc="upper left")
    style_axis(ax, grid_axis="y")
    figure_note(
        fig,
        "数据：agenteval_samples.csv；柱为中位数，误差线为 IQR。首次指显式 warmup 后的首个计时 scan 块；数值按每轮命中次数归一到单次查询。"
        "每个 hit count 来自 1 次 fresh boot，共 4 次；图例 n=56/60 包含 boot 内重复测量。",
    )
    fig.subplots_adjust(left=0.10, right=0.985, top=0.89, bottom=0.17)
    return fig, {
        "sources": ["agenteval_samples.csv"],
        "catalog_sizes": catalogs,
        "rows": int(len(frame)),
        "fresh_boots": 4,
        "fresh_boots_per_hit_count": 1,
        "hit_counts": sorted(int(value) for value in frame["operations"].unique()),
        "category_counts": {category: int((frame["category"] == category).sum()) for category in categories},
        "normalization": "duration_us / operations",
        "nested_repeated_samples": True,
    }


def make_task_latency(tables: Path) -> tuple[Any, dict[str, Any]]:
    columns = ["source_file", "boot_round", "path", "operations", "duration_us"]
    frame = read_table(tables, "task_sequences", columns).copy()
    frame["duration_us"] = pd.to_numeric(frame["duration_us"], errors="raise")
    frame["duration_ms"] = frame["duration_us"] / 1000.0
    paths = ["batch", "scalar_v3", "sq_cq"]
    labels = ["Batch", "Scalar V3", "SQ-CQ"]
    colors = [PATH_COLORS[path] for path in paths]
    groups = [frame.loc[frame["path"] == path, "duration_ms"].to_numpy() for path in paths]

    fig, axes = plt.subplots(1, 2, figsize=(11.8, 5.05), gridspec_kw={"width_ratios": [0.88, 1.12]})
    draw_half_raincloud(
        axes[0],
        groups,
        [f"{label}\nn={len(values)}" for label, values in zip(labels, groups)],
        colors,
        seed=6104,
        point_size=17,
    )
    axes[0].set_ylabel("16 操作完整序列延迟（ms）")
    axes[0].set_title("逐序列雨云分布")
    style_axis(axes[0], grid_axis="y")
    panel_label(axes[0], "a")
    for position, values, color in zip(range(1, 4), groups, colors):
        axes[0].text(position, max(values) + 0.13, f"P50 {np.median(values):.2f}", ha="center", color=color, fontsize=8.1)

    for path, label, color, values in zip(paths, labels, colors, groups):
        x, y = ecdf(values)
        axes[1].step(x, y, where="post", linewidth=2.1, color=color, label=f"{label}（n={len(values)}）")
        p95 = float(np.quantile(values, 0.95))
        axes[1].scatter([p95], [0.95], color=color, s=26, edgecolor="white", linewidth=0.4, zorder=4)
    axes[1].set_xscale("log")
    axes[1].set_xlabel("16 操作完整序列延迟（ms，对数轴）")
    axes[1].set_ylabel("累计概率")
    axes[1].set_ylim(0, 1.03)
    axes[1].set_title("尾延迟 ECDF（圆点为 P95）")
    axes[1].legend(frameon=False, loc="lower right")
    style_axis(axes[1])
    panel_label(axes[1], "b")

    medians = {path: float(np.median(values)) for path, values in zip(paths, groups)}
    figure_note(
        fig,
        "数据：task_sequences.csv；4 次 fresh boot x 8 轮，每条路径 n=32 个完整序列；每个序列固定 16 个操作。雨云点和 ECDF 均使用逐序列原始值。",
    )
    fig.subplots_adjust(left=0.075, right=0.985, top=0.89, bottom=0.17, wspace=0.25)
    return fig, {
        "sources": ["task_sequences.csv"],
        "samples_per_path": {path: int(len(values)) for path, values in zip(paths, groups)},
        "operations_per_sequence": sorted(int(value) for value in frame["operations"].unique()),
        "median_duration_ms": medians,
    }


def make_eevdf_combo(tables: Path) -> tuple[Any, dict[str, Any]]:
    wake_columns = ["scenario", "wakeup_latency_ticks", "right_censored"]
    wake = read_table(tables, "eevdf_wakeups", wake_columns).copy()
    for column in wake_columns:
        wake[column] = pd.to_numeric(wake[column], errors="raise")
    if int(wake["right_censored"].sum()) != 0:
        raise ValueError("This document figure expects uncensored exact EEVDF probes")

    sample_columns = ["source_file", "scenario", "index", "service"]
    samples = read_table(tables, "eevdf_samples", sample_columns).copy()
    for column in ["scenario", "index", "service"]:
        samples[column] = pd.to_numeric(samples[column], errors="raise")
    samples = samples[samples["scenario"].isin([1, 2, 3, 4])]
    fairness_rows: list[dict[str, Any]] = []
    for (source, scenario), lane in samples.groupby(["source_file", "scenario"]):
        values = lane["service"].to_numpy(dtype=float)
        if len(values) != int(scenario) or lane["index"].duplicated().any():
            raise ValueError(f"Malformed EEVDF workflow set: {source}, scenario {scenario}")
        fairness = float(values.sum() ** 2 / (len(values) * np.square(values).sum()))
        fairness_rows.append({"source_file": source, "scenario": int(scenario), "fairness": fairness})
    fairness = pd.DataFrame(fairness_rows)

    fig, axes = plt.subplots(1, 2, figsize=(12.4, 5.05), gridspec_kw={"width_ratios": [1.05, 0.95]})
    scenario_colors = {2: TEAL, 3: BLUE, 4: ORANGE, 16: RED, 44: NAVY}
    scenario_labels = {2: "并发 2", 3: "并发 3", 4: "并发 4", 16: "16 到达 / 上限 4", 44: "4 路放大"}
    wake_summary: dict[str, dict[str, float | int]] = {}
    for scenario in [2, 3, 4, 16, 44]:
        values = wake.loc[wake["scenario"] == scenario, "wakeup_latency_ticks"].to_numpy(dtype=float)
        x, y = ecdf(values)
        axes[0].step(
            x,
            y,
            where="post",
            linewidth=2.0,
            color=scenario_colors[scenario],
            label=f"{scenario_labels[scenario]}（n={len(values)}）",
        )
        wake_summary[str(scenario)] = {
            "n": int(len(values)),
            "zero_tick_fraction": float(np.mean(values == 0)),
            "max_ticks": int(values.max()),
        }
    axes[0].set_xlim(-0.05, 1.05)
    axes[0].set_xticks([0, 1])
    axes[0].set_xlabel("wakeup latency（scheduler tick）")
    axes[0].set_ylabel("累计概率")
    axes[0].set_ylim(0, 1.03)
    axes[0].set_title("EEVDF 精确 wakeup latency ECDF")
    axes[0].legend(frameon=False, loc="lower right")
    style_axis(axes[0])
    panel_label(axes[0], "a")

    summary = fairness.groupby("scenario")["fairness"].agg(
        median="median", q1=lambda values: values.quantile(0.25), q3=lambda values: values.quantile(0.75), n="size"
    )
    rng = np.random.default_rng(6105)
    for scenario, lane in fairness.groupby("scenario"):
        jitter = rng.normal(0, 0.035, len(lane))
        axes[1].scatter(
            scenario + jitter,
            lane["fairness"],
            color=BLUE,
            s=28,
            alpha=0.58,
            edgecolor="white",
            linewidth=0.35,
            zorder=3,
        )
    x = summary.index.to_numpy(dtype=float)
    axes[1].plot(x, summary["median"], color=ORANGE, marker="o", linewidth=2.1, label="中位数")
    axes[1].fill_between(x, summary["q1"].to_numpy(), summary["q3"].to_numpy(), color=ORANGE, alpha=0.18, label="IQR")
    lower = float(fairness["fairness"].min())
    pad = max((1.0 - lower) * 0.18, 0.000002)
    axes[1].set_ylim(lower - pad, 1.0 + pad * 0.15)
    axes[1].axhline(1.0, color=INK, linestyle="--", linewidth=0.9)
    axes[1].set_xticks([1, 2, 3, 4])
    axes[1].set_xlabel("并发 EEVDF 工作流数")
    axes[1].set_ylabel("Jain fairness（1 为理想值）")
    axes[1].set_title("基于原始 service cycles 的公平性")
    axes[1].ticklabel_format(axis="y", style="plain", useOffset=False)
    axes[1].yaxis.set_major_formatter(mpl.ticker.FormatStrFormatter("%.6f"))
    axes[1].legend(frameon=False, loc="lower left")
    style_axis(axes[1])
    panel_label(axes[1], "b")

    figure_note(
        fig,
        f"数据：eevdf_wakeups.csv（6 次 fresh boot，n={len(wake)} 个逐 probe 精确值，无右删失）与 eevdf_samples.csv（每个并发度 n={int(summary['n'].min())} 次 fresh boot）。"
        "公平性纵轴放大了距 1.0 的实测差异。",
    )
    fig.subplots_adjust(left=0.075, right=0.985, top=0.89, bottom=0.17, wspace=0.25)
    return fig, {
        "sources": ["eevdf_wakeups.csv", "eevdf_samples.csv"],
        "exact_wakeup_probes": int(len(wake)),
        "right_censored": int(wake["right_censored"].sum()),
        "wakeup_by_scenario": wake_summary,
        "fairness_boots_per_concurrency": {str(int(key)): int(value) for key, value in summary["n"].items()},
        "minimum_jain_fairness": lower,
    }


def heatmap_panel(
    ax: Any,
    pivot: pd.DataFrame,
    counts: pd.DataFrame,
    labels: dict[str, str],
    title: str,
    cmap: Any,
) -> Any:
    maxima = pivot.abs().max(axis=0).replace(0, 1)
    relative = pivot.divide(maxima, axis=1).fillna(0)
    image = ax.imshow(relative.to_numpy(dtype=float), aspect="auto", cmap=cmap, vmin=0, vmax=1)
    for row in range(pivot.shape[0]):
        for column in range(pivot.shape[1]):
            raw = float(pivot.iloc[row, column])
            count = int(counts.iloc[row, column])
            color = "white" if relative.iloc[row, column] > 0.64 else INK
            value = "NA" if not math.isfinite(raw) else f"{raw:.2g}"
            ax.text(column, row, f"{value}\nn={count}", ha="center", va="center", color=color, fontsize=7.2)
    ax.set_xticks(
        range(len(pivot.columns)),
        [labels.get(str(value), str(value)) for value in pivot.columns],
        rotation=25,
        ha="right",
        rotation_mode="anchor",
    )
    ax.set_yticks(
        range(len(pivot.index)),
        [str(value).replace("scalar_v3", "Scalar V3").replace("sq_cq", "SQ-CQ").replace("batch", "Batch").replace("traversal", "遍历").replace("indexed", "索引") for value in pivot.index],
    )
    ax.set_title(title)
    for edge in np.arange(-0.5, pivot.shape[1], 1):
        ax.axvline(edge, color="white", linewidth=1.0)
    for edge in np.arange(-0.5, pivot.shape[0], 1):
        ax.axhline(edge, color="white", linewidth=1.0)
    return image


def make_io_heatmaps(tables: Path) -> tuple[Any, dict[str, Any]]:
    contest_columns = ["path", "metric", "per_workload_syscall", "counter_scope", "counter_window"]
    task_columns = ["path", "metric", "per_operation"]
    contest = read_table(tables, "contest_io_normalized", contest_columns).copy()
    task = read_table(tables, "task_perf_normalized", task_columns).copy()
    contest["per_workload_syscall"] = pd.to_numeric(contest["per_workload_syscall"], errors="coerce")
    task["per_operation"] = pd.to_numeric(task["per_operation"], errors="coerce")

    contest_metrics = [
        "bytes_read",
        "directory_block_probes",
        "directory_entries_examined",
        "physical_reads",
        "physical_writes",
        "durable_flushes",
        "virtio_notifications",
        "virtio_submitted_requests",
    ]
    task_metrics = [
        "syscalls",
        "abi_descriptor_bytes",
        "copied_descriptor_bytes",
        "dispatch_header_bytes",
        "control_abi_bytes",
        "control_copied_bytes",
        "sched_dispatch_delta",
        "sequence_elapsed_ticks",
    ]
    contest = contest[contest["metric"].isin(contest_metrics)].dropna(subset=["per_workload_syscall"])
    task = task[task["metric"].isin(task_metrics)].dropna(subset=["per_operation"])
    contest_pivot = contest.pivot_table(index="path", columns="metric", values="per_workload_syscall", aggfunc="median").reindex(index=["traversal", "indexed"], columns=contest_metrics)
    task_pivot = task.pivot_table(index="path", columns="metric", values="per_operation", aggfunc="median").reindex(index=["batch", "scalar_v3", "sq_cq"], columns=task_metrics)
    contest_counts = contest.pivot_table(index="path", columns="metric", values="per_workload_syscall", aggfunc="count").reindex_like(contest_pivot)
    task_counts = task.pivot_table(index="path", columns="metric", values="per_operation", aggfunc="count").reindex_like(task_pivot)

    labels = {
        "bytes_read": "读取字节\n[B/syscall]",
        "directory_block_probes": "目录块探测\n[count/syscall]",
        "directory_entries_examined": "目录项检查\n[count/syscall]",
        "physical_reads": "物理读\n[count/syscall]",
        "physical_writes": "物理写\n[count/syscall]",
        "durable_flushes": "持久化 flush\n[count/syscall]",
        "virtio_notifications": "virtio 通知\n[count/syscall]",
        "virtio_submitted_requests": "virtio 请求\n[count/syscall]",
        "syscalls": "系统调用\n[count/op]",
        "abi_descriptor_bytes": "ABI 描述符\n[B/op]",
        "copied_descriptor_bytes": "复制描述符\n[B/op]",
        "dispatch_header_bytes": "dispatch 头\n[B/op]",
        "control_abi_bytes": "控制 ABI\n[B/op]",
        "control_copied_bytes": "控制复制\n[B/op]",
        "sched_dispatch_delta": "调度差值\n[tick/op]",
        "sequence_elapsed_ticks": "序列时间\n[tick/op]",
    }
    cmap = LinearSegmentedColormap.from_list("agentos_io", ["#F8FBFC", "#A9CFCA", "#4C84A6", NAVY])
    fig, axes = plt.subplots(2, 1, figsize=(14.2, 6.8), gridspec_kw={"height_ratios": [0.9, 1.1]})
    image = heatmap_panel(
        axes[0],
        contest_pivot,
        contest_counts,
        labels,
        "内核 I/O / observer workload syscall（逐路径中位数）",
        cmap,
    )
    heatmap_panel(
        axes[1],
        task_pivot,
        task_counts,
        labels,
        "Task ABI 工作量 / 完成操作（逐路径中位数）",
        cmap,
    )
    panel_label(axes[0], "a", x=-0.055)
    panel_label(axes[1], "b", x=-0.055)
    colorbar_axis = fig.add_axes([0.935, 0.30, 0.014, 0.39])
    colorbar = fig.colorbar(image, cax=colorbar_axis)
    colorbar.set_label("同一列内相对强度")
    figure_note(
        fig,
        "数据：contest_io_normalized.csv 与 task_perf_normalized.csv。颜色逐列按最大绝对值归一化，只比较同一指标内的路径；格内为原始中位数和样本数。"
        "上图 bytes_read 为 workflow lane/core 窗口，其余计数为 global/end-to-end 窗口。",
    )
    fig.subplots_adjust(left=0.095, right=0.89, top=0.92, bottom=0.19, hspace=0.67)
    return fig, {
        "sources": ["contest_io_normalized.csv", "task_perf_normalized.csv"],
        "contest_metrics": contest_metrics,
        "task_metrics": task_metrics,
        "contest_samples_per_cell": sorted(int(value) for value in contest_counts.stack().unique()),
        "task_samples_per_cell": sorted(int(value) for value in task_counts.stack().unique()),
        "color_normalization": "within_metric_column_max_abs",
    }


def make_scope_comparison(tables: Path) -> tuple[Any, dict[str, Any]]:
    columns = [
        "order",
        "traversal_core_duration_us",
        "indexed_core_duration_us",
        "traversal_end_to_end_duration_us",
        "indexed_end_to_end_duration_us",
        "traversal_over_indexed_core_ratio",
    ]
    frame = read_table(tables, "contest_paired", columns).copy()
    for column in columns[1:5]:
        frame[column] = pd.to_numeric(frame[column], errors="raise") / 1000.0
    frame["traversal_over_indexed_core_ratio"] = pd.to_numeric(
        frame["traversal_over_indexed_core_ratio"], errors="raise"
    )
    frame["core_delta"] = frame["indexed_core_duration_us"] - frame["traversal_core_duration_us"]
    frame["e2e_delta"] = frame["indexed_end_to_end_duration_us"] - frame["traversal_end_to_end_duration_us"]

    fig = plt.figure(figsize=(11.8, 7.15))
    grid = fig.add_gridspec(2, 2, height_ratios=[1.0, 0.78], hspace=0.42, wspace=0.24)
    ax0 = fig.add_subplot(grid[0, 0])
    ax1 = fig.add_subplot(grid[0, 1])
    ax2 = fig.add_subplot(grid[1, :])

    top_specs = [
        (ax0, "traversal_core_duration_us", "indexed_core_duration_us", "核心路径", "延迟（ms）"),
        (ax1, "traversal_end_to_end_duration_us", "indexed_end_to_end_duration_us", "端到端", "延迟（ms）"),
    ]
    for ax, left_column, right_column, title, ylabel in top_specs:
        left = frame[left_column].to_numpy()
        right = frame[right_column].to_numpy()
        for a, b in zip(left, right):
            ax.plot([1, 2], [a, b], color="#AAB2BA", alpha=0.48, linewidth=0.9)
        ax.scatter(np.ones(len(left)), left, color=ORANGE, s=26, edgecolor="white", linewidth=0.35, zorder=3)
        ax.scatter(np.full(len(right), 2), right, color=BLUE, s=26, edgecolor="white", linewidth=0.35, zorder=3)
        ax.plot([1, 2], [np.median(left), np.median(right)], color=INK, marker="D", markersize=5, linewidth=1.6, label="配对中位数")
        ax.set_xticks([1, 2], [f"遍历\nn={len(left)}", f"索引\nn={len(right)}"])
        ax.set_ylabel(ylabel)
        ax.set_title(f"{title}逐启动配对")
        style_axis(ax, grid_axis="y")
    ax0.legend(frameon=False, loc="upper right")
    panel_label(ax0, "a")
    panel_label(ax1, "b")

    deltas = [frame["core_delta"].to_numpy(), frame["e2e_delta"].to_numpy()]
    positions = [1, 2]
    boxes = ax2.boxplot(
        deltas,
        positions=positions,
        vert=False,
        widths=0.36,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": INK, "linewidth": 1.5},
        whiskerprops={"color": GRAY},
        capprops={"color": GRAY},
    )
    for patch, color in zip(boxes["boxes"], [BLUE, ORANGE]):
        patch.set_facecolor(color)
        patch.set_edgecolor(color)
        patch.set_alpha(0.38)
    rng = np.random.default_rng(6107)
    for position, values, color in zip(positions, deltas, [BLUE, ORANGE]):
        jitter = rng.normal(0, 0.045, len(values))
        ax2.scatter(values, position + jitter, color=color, s=22, alpha=0.68, edgecolor="white", linewidth=0.35)
        median = float(np.median(values))
        ax2.text(median, position + 0.25, f"中位差 {median:.2f} ms", ha="center", color=color, fontsize=8.2)
    ax2.axvline(0, color=INK, linestyle="--", linewidth=1.0)
    ax2.set_yticks(positions, ["核心路径", "端到端"])
    ax2.set_xlabel("索引 - 遍历（ms；负值表示索引更快）")
    ax2.set_title("同一批配对启动在两个计时边界上的差值")
    style_axis(ax2, grid_axis="x")
    panel_label(ax2, "c", x=-0.045)

    core_median = float(frame["core_delta"].median())
    e2e_median = float(frame["e2e_delta"].median())
    speedup_median = float(frame["traversal_over_indexed_core_ratio"].median())
    order_speedups = (
        frame.groupby("order")["traversal_over_indexed_core_ratio"].median().to_dict()
    )
    core_wins = int((frame["core_delta"] < 0).sum())
    e2e_wins = int((frame["e2e_delta"] < 0).sum())
    figure_note(
        fig,
        f"数据：contest_paired.csv，n={len(frame)} 对 fresh boot。核心：索引胜 {core_wins}/{len(frame)}，中位差 {core_median:.4f} ms，"
        f"中位加速 {speedup_median:.4f}x；端到端：索引胜 {e2e_wins}/{len(frame)}，中位差 +{e2e_median:.3f} ms。"
        f"按顺序分层的核心加速中位数：索引先跑 {order_speedups['indexed_then_traversal']:.4f}x，遍历先跑 {order_speedups['traversal_then_indexed']:.4f}x。",
    )
    fig.subplots_adjust(left=0.085, right=0.985, top=0.91, bottom=0.13)
    return fig, {
        "sources": ["contest_paired.csv"],
        "paired_boots": int(len(frame)),
        "median_core_delta_ms": core_median,
        "median_end_to_end_delta_ms": e2e_median,
        "median_core_speedup": speedup_median,
        "median_core_speedup_by_order": {
            str(key): float(value) for key, value in order_speedups.items()
        },
        "indexed_faster_core_pairs": core_wins,
        "indexed_faster_end_to_end_pairs": e2e_wins,
    }


FIGURES: list[tuple[str, Callable[[Path], tuple[Any, dict[str, Any]]]]] = [
    ("01_paired_core_performance", make_paired_core),
    ("02_catalog_speedup_landscape", make_catalog_landscape),
    ("03_scan_state_groups", make_scan_states),
    ("04_task_latency_distributions", make_task_latency),
    ("05_eevdf_latency_fairness", make_eevdf_combo),
    ("06_normalized_io_heatmaps", make_io_heatmaps),
    ("07_core_end_to_end_scope", make_scope_comparison),
]


def parse_args() -> argparse.Namespace:
    script = Path(__file__).resolve()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=script.parent,
        help="Figure output directory; must be outside the frozen campaign.",
    )
    parser.add_argument(
        "--formats",
        default="png,pdf,svg",
        help="Comma-separated output formats (default: png,pdf,svg).",
    )
    parser.add_argument(
        "--only",
        action="append",
        help="Render only the named stem to a non-canonical --output-dir; may be repeated.",
    )
    return parser.parse_args()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def main() -> int:
    args = parse_args()
    script = Path(__file__).resolve()
    tables = (script.parents[3] / "one_shot_metrics" / "data" / "20260811" / "tables").resolve()
    output_dir = args.output_dir.expanduser().resolve()
    campaign_root = tables.parent
    if is_within(output_dir, campaign_root):
        raise SystemExit(f"Refusing to write inside frozen campaign: {output_dir}")
    formats = [value.strip().lower() for value in args.formats.split(",") if value.strip()]
    invalid_formats = sorted(set(formats) - {"png", "pdf", "svg"})
    if invalid_formats:
        raise SystemExit(f"Unsupported formats: {', '.join(invalid_formats)}")
    selected = set(args.only or [stem for stem, _ in FIGURES])
    if args.only and output_dir == script.parent:
        raise SystemExit("--only requires an explicit non-canonical --output-dir; run all figures to refresh the canonical manifest")
    known = {stem for stem, _ in FIGURES}
    unknown = sorted(selected - known)
    if unknown:
        raise SystemExit(f"Unknown figure stems: {', '.join(unknown)}")

    font = configure_matplotlib()
    input_paths = sorted(tables.glob("*.csv"))
    before = {path.name: sha256(path) for path in input_paths}
    manifest: dict[str, Any] = {
        "schema": "agentos-doc-figures-v1",
        "campaign": campaign_root.name,
        "font": font,
        "formats": formats,
        "template_references": ["paired-raincloud", "rf-tpe-surface", "urban-park-cooling-combo"],
        "inputs": before,
        "figures": {},
    }
    for stem, maker in FIGURES:
        if stem not in selected:
            continue
        figure, evidence = maker(tables)
        outputs = export_figure(figure, output_dir, stem, formats)
        manifest["figures"][stem] = {"evidence": evidence, "outputs": outputs}
        print(f"rendered {stem}: {', '.join(path['path'] for path in outputs.values())}")

    after = {path.name: sha256(path) for path in input_paths}
    if before != after:
        raise RuntimeError("Frozen campaign input changed while rendering")
    manifest_path = output_dir / "figure_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
