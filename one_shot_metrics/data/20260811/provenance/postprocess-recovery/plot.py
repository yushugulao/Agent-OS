#!/usr/bin/env python3
"""Render the ten requested publication figures from extracted one-shot tables."""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Callable, Sequence


SCHEMA_VERSION = 1
KIND = "agentos-one-shot-publication-figures"

COLORS = {
    "traversal": "#D95F02",
    "indexed": "#1B9E77",
    "cold": "#7570B3",
    "scan": "#E6AB02",
    "batch": "#1B9E77",
    "scalar_v3": "#D95F02",
    "sq_cq": "#2C7FB8",
}


class PlotUnavailable(RuntimeError):
    """Raised when a chart cannot be built without inventing observations."""


def _imports() -> tuple[Any, Any, Any, Any]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd
    except ImportError as error:
        raise PlotUnavailable(
            "plot.py requires pandas, numpy, and matplotlib; extraction/validation "
            "remain standard-library only"
        ) from error
    return matplotlib, plt, np, pd


def _read_table(root: Path, name: str, pd: Any) -> Any:
    path = root / f"{name}.csv"
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _numeric(df: Any, columns: Sequence[str], pd: Any) -> Any:
    result = df.copy()
    for column in columns:
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def _require(df: Any, columns: Sequence[str], label: str) -> None:
    if df.empty:
        raise PlotUnavailable(f"{label}: no rows")
    missing = [column for column in columns if column not in df]
    if missing:
        raise PlotUnavailable(f"{label}: missing columns {', '.join(missing)}")


def _configure(matplotlib: Any) -> None:
    matplotlib.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#333333",
            "axes.labelcolor": "#222222",
            "axes.titleweight": "bold",
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "xtick.color": "#333333",
            "ytick.color": "#333333",
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "legend.frameon": False,
            "grid.color": "#D9D9D9",
            "grid.linewidth": 0.7,
            "grid.alpha": 0.7,
            "savefig.facecolor": "white",
            "savefig.bbox": "tight",
        }
    )


def _footnote(fig: Any, text: str) -> None:
    wrap_width = max(72, int(fig.get_figwidth() * 15))
    lines = textwrap.wrap(text, width=wrap_width) or [text]
    footer_height = 0.075 + 0.026 * (len(lines) - 1)
    layout_engine = fig.get_layout_engine()
    if layout_engine is not None:
        layout_engine.set(rect=(0.0, footer_height, 1.0, 1.0))
    fig.text(
        0.01, 0.014, "\n".join(lines), ha="left", va="bottom",
        fontsize=7, color="#555555", linespacing=1.25,
    )


def _save(fig: Any, output_dir: Path, name: str, formats: Sequence[str]) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for extension in formats:
        path = output_dir / f"{name}.{extension}"
        kwargs: dict[str, Any] = {"bbox_inches": "tight"}
        if extension == "png":
            kwargs["dpi"] = 320
        fig.savefig(path, **kwargs)
        files.append(path.name)
    return files


def _ecdf(values: Any, np: Any) -> tuple[Any, Any]:
    x = np.sort(np.asarray(values, dtype=float))
    if x.size == 0:
        raise PlotUnavailable("ECDF needs at least one finite observation")
    y = np.arange(1, x.size + 1, dtype=float) / x.size
    return x, y


def _kaplan_meier_cdf(times: Any, censored: Any, np: Any) -> tuple[Any, Any, Any, Any]:
    """Return event-time CDF steps and censor marks without inventing tail events."""
    observations = sorted(
        (float(time), bool(flag)) for time, flag in zip(times, censored)
    )
    if not observations:
        raise PlotUnavailable("Kaplan-Meier CDF needs at least one observation")
    at_risk = len(observations)
    survival = 1.0
    event_x: list[float] = []
    event_y: list[float] = []
    censor_x: list[float] = []
    censor_y: list[float] = []
    for time in sorted({item[0] for item in observations}):
        at_time = [item for item in observations if item[0] == time]
        events = sum(not flag for _, flag in at_time)
        censors = len(at_time) - events
        if events:
            survival *= 1.0 - events / at_risk
            event_x.append(time)
            event_y.append(1.0 - survival)
        if censors:
            censor_x.extend([time] * censors)
            censor_y.extend([1.0 - survival] * censors)
        at_risk -= events + censors
    return (
        np.asarray(event_x, dtype=float),
        np.asarray(event_y, dtype=float),
        np.asarray(censor_x, dtype=float),
        np.asarray(censor_y, dtype=float),
    )


def plot_dumbbell(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    df = _read_table(root, "contest_paired", pd)
    required = ["sample_id", "traversal_core_duration_us", "indexed_core_duration_us"]
    _require(df, required, "dumbbell")
    df = _numeric(df, required[1:], pd).dropna(subset=required[1:])
    if len(df) < 2:
        raise PlotUnavailable("dumbbell: fewer than two paired boots")
    df = df.sort_values("traversal_core_duration_us", ascending=True).reset_index(drop=True)
    y = np.arange(len(df))
    height = max(4.6, min(9.0, 0.32 * len(df) + 2.0))
    fig, ax = plt.subplots(figsize=(8.2, height), constrained_layout=True)
    for index, row in df.iterrows():
        ax.plot(
            [row["indexed_core_duration_us"], row["traversal_core_duration_us"]],
            [index, index],
            color="#A7A7A7",
            linewidth=1.2,
            zorder=1,
        )
    ax.scatter(
        df["traversal_core_duration_us"], y, s=38, color=COLORS["traversal"],
        label="Traversal", zorder=3,
    )
    ax.scatter(
        df["indexed_core_duration_us"], y, s=38, color=COLORS["indexed"],
        label="Indexed", zorder=3,
    )
    labels = [
        f"Boot {int(value)}" if float(value).is_integer() else f"Boot {value}"
        for value in df["sample_id"]
    ]
    ax.set_yticks(y, labels)
    ax.set_xlabel("Core duration (us, lower is better)")
    ax.set_ylabel("Paired QEMU boot")
    ax.set_title("Traversal vs indexed file query: paired dumbbell")
    ax.grid(axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower right")
    _footnote(fig, "Each connector is one within-boot pair; execution order is balanced AB/BA.")
    return fig, {"paired_boots": int(len(df)), "measurement": "core_duration_us"}


def plot_core_violin(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    df = _read_table(root, "contest_paths", pd)
    _require(df, ["path", "core_duration_us"], "core violin")
    df = _numeric(df, ["core_duration_us"], pd).dropna(subset=["core_duration_us"])
    groups = [
        df.loc[df["path"] == "traversal", "core_duration_us"].to_numpy(),
        df.loc[df["path"] == "indexed", "core_duration_us"].to_numpy(),
    ]
    if min(map(len, groups)) < 2:
        raise PlotUnavailable("core violin: both paths need at least two samples")
    fig, ax = plt.subplots(figsize=(7.3, 5.0), constrained_layout=True)
    parts = ax.violinplot(groups, positions=[1, 2], widths=0.72, showmedians=True, showextrema=False)
    for body, color in zip(parts["bodies"], [COLORS["traversal"], COLORS["indexed"]]):
        body.set_facecolor(color)
        body.set_edgecolor(color)
        body.set_alpha(0.32)
    parts["cmedians"].set_color("#222222")
    parts["cmedians"].set_linewidth(2)
    rng = np.random.default_rng(61)
    for position, values, color in zip([1, 2], groups, [COLORS["traversal"], COLORS["indexed"]]):
        jitter = rng.uniform(-0.10, 0.10, len(values))
        ax.scatter(position + jitter, values, s=20, alpha=0.76, color=color, edgecolor="white", linewidth=0.35)
        ax.text(position, max(values) * 1.035, f"n={len(values)}", ha="center", fontsize=8)
    ax.set_xticks([1, 2], ["Traversal", "Indexed"])
    ax.set_ylabel("Core duration (us)")
    ax.set_title("Core latency distribution: raw boot samples")
    ax.grid(axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    _footnote(fig, "Violin width is a density estimate; dots are the retained measurements and bars are medians.")
    return fig, {"traversal_n": len(groups[0]), "indexed_n": len(groups[1])}


def plot_difference_ecdf(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    df = _read_table(root, "contest_paired", pd)
    _require(df, ["traversal_core_duration_us", "indexed_core_duration_us"], "paired ECDF")
    df = _numeric(df, ["traversal_core_duration_us", "indexed_core_duration_us"], pd)
    delta = (df["indexed_core_duration_us"] - df["traversal_core_duration_us"]).dropna()
    x, y = _ecdf(delta, np)
    fig, ax = plt.subplots(figsize=(7.4, 4.8), constrained_layout=True)
    ax.step(x, y, where="post", color="#2C7FB8", linewidth=2.2)
    ax.scatter(x, y, color="#2C7FB8", s=18, zorder=3)
    ax.axvline(0, color="#444444", linestyle="--", linewidth=1)
    ax.set_xlabel("Indexed minus traversal core duration (us)")
    ax.set_ylabel("Empirical cumulative probability")
    ax.set_ylim(0, 1.03)
    ax.set_title("Paired latency difference ECDF")
    ax.grid()
    ax.spines[["top", "right"]].set_visible(False)
    _footnote(fig, "Negative values favor indexed lookup; differences are computed within the same QEMU boot.")
    return fig, {"paired_differences": int(len(delta)), "median_delta_us": float(delta.median())}


def _speedup_grid(root: Path, pd: Any) -> tuple[Any, Any]:
    df = _read_table(root, "agenteval_pairs", pd)
    required = ["experiment", "load", "operations", "speedup_baseline_over_treatment"]
    _require(df, required, "speedup grid")
    df = df[df["experiment"] == "file_query_table_ablation"].copy()
    df = _numeric(df, ["load", "operations", "speedup_baseline_over_treatment"], pd)
    df = df.dropna(subset=["load", "operations", "speedup_baseline_over_treatment"])
    grouped = (
        df.groupby(["operations", "load"], as_index=False)["speedup_baseline_over_treatment"]
        .median()
        .sort_values(["operations", "load"])
    )
    if grouped.empty:
        raise PlotUnavailable("speedup grid has no scan/index pairs")
    pivot = grouped.pivot(index="operations", columns="load", values="speedup_baseline_over_treatment")
    if pivot.shape[0] < 2 or pivot.shape[1] < 2 or pivot.isna().any().any():
        raise PlotUnavailable("speedup grid is incomplete; refusing to interpolate missing cells")
    return grouped, pivot


def plot_speedup_heatmap(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    grouped, pivot = _speedup_grid(root, pd)
    values = pivot.to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(7.6, 5.0), constrained_layout=True)
    if values.min() < 1 < values.max():
        from matplotlib.colors import TwoSlopeNorm

        norm = TwoSlopeNorm(vmin=values.min(), vcenter=1, vmax=values.max())
    else:
        norm = None
    image = ax.imshow(values, aspect="auto", cmap="RdYlGn", norm=norm)
    for row in range(values.shape[0]):
        for column in range(values.shape[1]):
            color = "white" if values[row, column] > np.nanmedian(values) * 1.2 else "#222222"
            ax.text(column, row, f"{values[row, column]:.2f}x", ha="center", va="center", color=color, fontweight="bold")
    ax.set_xticks(range(len(pivot.columns)), [f"{int(value)}" for value in pivot.columns])
    ax.set_yticks(range(len(pivot.index)), [f"{int(value)}" for value in pivot.index])
    ax.set_xlabel("Catalog size (records)")
    ax.set_ylabel("Hit-producing queries (operations)")
    ax.set_title("Scan/index paired speedup heatmap")
    colorbar = fig.colorbar(image, ax=ax, pad=0.02)
    colorbar.set_label("Median scan / index duration")
    _footnote(fig, "Each cell is a median of raw AB/BA pairs; no cell is interpolated.")
    return fig, {"cells": int(len(grouped)), "loads": int(pivot.shape[1]), "operation_counts": int(pivot.shape[0])}


def plot_performance_surface(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    grouped, pivot = _speedup_grid(root, pd)
    x, y = np.meshgrid(pivot.columns.to_numpy(dtype=float), pivot.index.to_numpy(dtype=float))
    z = pivot.to_numpy(dtype=float)
    fig = plt.figure(figsize=(8.2, 5.9), constrained_layout=True)
    ax = fig.add_subplot(111, projection="3d")
    surface = ax.plot_surface(x, y, z, cmap="Spectral", edgecolor="#555555", linewidth=0.35, antialiased=True, alpha=0.92)
    ax.scatter(x.ravel(), y.ravel(), z.ravel(), color="#202020", s=16, depthshade=False)
    ax.set_xlabel("Catalog size")
    ax.set_ylabel("Hit-producing queries")
    ax.set_zlabel("Median scan/index speedup")
    ax.set_title("Measured file-query performance surface")
    ax.view_init(elev=27, azim=-128)
    fig.colorbar(surface, ax=ax, shrink=0.65, pad=0.08, label="Speedup (x)")
    _footnote(fig, "Surface connects a complete measured grid; black points are measured parameter cells.")
    return fig, {"measured_cells": int(len(grouped)), "interpolation": "none_between_missing_cells"}


def plot_cold_warm_grouped(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    samples = _read_table(root, "agenteval_samples", pd)
    _require(samples, ["experiment", "load", "pair", "variant", "operations", "duration_us"], "cold/warm grouped")
    samples = samples[
        (samples["experiment"] == "file_query_table_ablation")
        & samples["variant"].isin(["scan", "index"])
    ].copy()
    samples = _numeric(samples, ["load", "pair", "operations", "duration_us"], pd)
    samples["category"] = ""
    samples.loc[(samples["variant"] == "scan") & (samples["pair"] == 1), "category"] = "First measured scan"
    samples.loc[(samples["variant"] == "scan") & (samples["pair"] >= 2), "category"] = "Repeat scan"
    samples.loc[samples["variant"] == "index", "category"] = "Ready index"
    samples["us_per_query"] = samples["duration_us"] / samples["operations"]
    data = samples[["load", "category", "us_per_query"]].dropna()
    categories = ["First measured scan", "Repeat scan", "Ready index"]
    loads = sorted(data["load"].unique())
    if len(loads) < 2 or not set(categories).issubset(set(data["category"])):
        raise PlotUnavailable("cold/warm grouped: incomplete category/load coverage")
    grouped = data.groupby(["load", "category"])["us_per_query"].agg(["median", lambda x: x.quantile(0.25), lambda x: x.quantile(0.75), "count"])
    grouped.columns = ["median", "q1", "q3", "count"]
    fig, ax = plt.subplots(figsize=(8.2, 5.1), constrained_layout=True)
    positions = np.arange(len(loads), dtype=float)
    width = 0.24
    palette = [COLORS["cold"], COLORS["scan"], COLORS["indexed"]]
    for offset, (category, color) in enumerate(zip(categories, palette)):
        medians, lower, upper = [], [], []
        for load in loads:
            row = grouped.loc[(load, category)]
            medians.append(row["median"])
            lower.append(row["median"] - row["q1"])
            upper.append(row["q3"] - row["median"])
        x = positions + (offset - 1) * width
        ax.bar(x, medians, width=width, color=color, alpha=0.84, label=category)
        ax.errorbar(x, medians, yerr=[lower, upper], fmt="none", color="#333333", capsize=2, linewidth=0.8)
    ax.set_xticks(positions, [str(int(value)) for value in loads])
    ax.set_xlabel("Catalog size (records)")
    ax.set_ylabel("Duration per query (us)")
    ax.set_title("First/repeat scan vs ready index")
    ax.grid(axis="y")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(ncols=3, loc="upper left")
    _footnote(fig, "First means the first timed scan block, not a proven physical cold cache; values are per query, bars median, whiskers IQR.")
    return fig, {"rows": int(len(data)), "loads": [int(value) for value in loads], "normalization": "duration_us_per_operation"}


def plot_task_distribution(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    sequences = _read_table(root, "task_sequences", pd)
    operations = _read_table(root, "task_operations", pd)
    _require(sequences, ["path", "duration_us", "operations"], "task distribution")
    _require(operations, ["path", "service_start_interval_tick"], "task distribution")
    sequences = _numeric(sequences, ["duration_us", "operations"], pd).dropna(subset=["duration_us"])
    operations = _numeric(operations, ["service_start_interval_tick"], pd).dropna(subset=["service_start_interval_tick"])
    paths = ["batch", "scalar_v3", "sq_cq"]
    if any((sequences["path"] == path).sum() < 2 for path in paths):
        raise PlotUnavailable("task distribution: each path needs at least two sequence samples")
    labels = ["Batch", "Scalar V3", "SQ-CQ"]
    colors = [COLORS[path] for path in paths]
    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.9), constrained_layout=True)
    rng = np.random.default_rng(169)
    for ax, frame, metric, title, ylabel in (
        (axes[0], sequences, "duration_us", "Complete sequence latency", "Sequence duration (us)"),
        (axes[1], operations, "service_start_interval_tick", "Per-operation service-start interval", "Interval (scheduler ticks)"),
    ):
        groups = [frame.loc[frame["path"] == path, metric].to_numpy(dtype=float) for path in paths]
        parts = ax.violinplot(groups, positions=[1, 2, 3], widths=0.72, showmedians=True, showextrema=False)
        for body, color in zip(parts["bodies"], colors):
            body.set_facecolor(color)
            body.set_edgecolor(color)
            body.set_alpha(0.32)
        parts["cmedians"].set_color("#222222")
        for position, values, color in zip([1, 2, 3], groups, colors):
            jitter = rng.uniform(-0.09, 0.09, len(values))
            ax.scatter(position + jitter, values, s=12, color=color, alpha=0.48, edgecolor="none")
        ax.set_xticks([1, 2, 3], labels)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(axis="y")
        ax.spines[["top", "right"]].set_visible(False)
    operation_count = sorted({int(value) for value in sequences["operations"].dropna().unique()})
    _footnote(fig, "Left: repeated complete sequences. Right: every retained service-start interval; zero-tick intervals are valid.")
    return fig, {"sequence_rows": int(len(sequences)), "operation_rows": int(len(operations)), "operations_per_sequence": operation_count}


def plot_eevdf_ecdf(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    exact = _read_table(root, "eevdf_wakeups", pd)
    fig, ax = plt.subplots(figsize=(7.7, 5.0), constrained_layout=True)
    palette = ["#1B9E77", "#2C7FB8", "#D95F02", "#7570B3", "#666666"]
    if not exact.empty and {"scenario", "wakeup_latency_ticks"}.issubset(exact.columns):
        exact = _numeric(
            exact, ["scenario", "wakeup_latency_ticks", "right_censored"], pd
        ).dropna(subset=["scenario", "wakeup_latency_ticks"])
        if "right_censored" not in exact or exact["right_censored"].isna().all():
            exact["right_censored"] = (exact["wakeup_latency_ticks"] >= 200).astype(int)
        else:
            exact["right_censored"] = exact["right_censored"].fillna(
                (exact["wakeup_latency_ticks"] >= 200).astype(int)
            )
        scenarios = sorted(int(value) for value in exact["scenario"].unique())
        preferred = [value for value in scenarios if value in {2, 3, 4, 16, 44}]
        scenarios = preferred or scenarios
        for color, scenario in zip(palette, scenarios):
            values = exact.loc[exact["scenario"] == scenario, "wakeup_latency_ticks"].to_numpy(dtype=float)
            censored = exact.loc[exact["scenario"] == scenario, "right_censored"].to_numpy(dtype=int)
            if not len(values):
                continue
            x, y, censor_x, censor_y = _kaplan_meier_cdf(values, censored, np)
            label = {16: "16 arrivals (cap 4)", 44: "4-way amplified"}.get(scenario, f"Concurrency {scenario}")
            if len(x):
                step_x = np.concatenate(([0.0], x))
                step_y = np.concatenate(([0.0], y))
                ax.step(step_x, step_y, where="post", linewidth=2, color=color, label=f"{label} (n={len(values)})")
            if len(censor_x):
                ax.scatter(censor_x, censor_y, marker="|", s=75, linewidth=1.4, color=color)
        ax.set_xlabel("Wakeup latency (scheduler ticks)")
        censored_count = int(exact["right_censored"].sum())
        title = (
            "EEVDF wakeup latency: Kaplan-Meier CDF"
            if censored_count
            else "EEVDF wakeup latency ECDF: exact timeout probes"
        )
        method = "kaplan_meier_right_censored" if censored_count else "exact_probe_ecdf"
        sample_count = int(len(exact))
        footnote = (
            "Each row is an emitted post-timeout scheduler probe; | marks right-censoring at ready_age=200 ticks."
            if censored_count
            else "Each point is an emitted post-timeout scheduler probe; no percentile reconstruction."
        )
    else:
        histogram = _read_table(root, "eevdf_wake_histogram", pd)
        _require(histogram, ["scenario", "histogram_scope", "bucket_index", "count"], "EEVDF histogram ECDF")
        workflow = histogram[histogram["histogram_scope"] == "workflow"]
        histogram = workflow if not workflow.empty else histogram[histogram["histogram_scope"] == "cohort"]
        histogram = _numeric(histogram, ["scenario", "bucket_index", "count"], pd).dropna(subset=["scenario", "bucket_index", "count"])
        grouped = histogram.groupby(["scenario", "bucket_index"], as_index=False)["count"].sum()
        scenarios = sorted(int(value) for value in grouped["scenario"].unique())
        positions = np.arange(4)
        for color, scenario in zip(palette, scenarios):
            lane = grouped[grouped["scenario"] == scenario].set_index("bucket_index")["count"].reindex(range(4), fill_value=0)
            total = lane.sum()
            if total <= 0:
                continue
            cumulative = lane.cumsum() / total
            ax.step(positions, cumulative, where="post", linewidth=2, color=color, label=f"Scenario {scenario} (n={int(total)})")
            ax.scatter(positions, cumulative, color=color, s=15)
        ax.set_xticks(positions, ["<=1", "<=2", "<=8", ">8"])
        ax.set_xlabel("Wakeup latency bucket (ticks; final bucket is open-ended)")
        title = "EEVDF wakeup latency: histogram-derived step CDF"
        method = "histogram_derived_step_cdf"
        sample_count = int(grouped["count"].sum())
        footnote = "Histogram-derived approximation only: values inside each bucket are not reconstructed; >8 has no invented endpoint."
    ax.set_ylabel("Cumulative probability")
    ax.set_ylim(0, 1.03)
    ax.set_title(title)
    ax.grid()
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower right")
    _footnote(fig, footnote)
    evidence = {"method": method, "samples": sample_count}
    if method.startswith("kaplan"):
        evidence["right_censored"] = censored_count
    return fig, evidence


def plot_jain_fairness(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    samples = _read_table(root, "eevdf_samples", pd)
    _require(
        samples, ["source_file", "scenario", "index", "service"],
        "Jain fairness",
    )
    samples = _numeric(samples, ["scenario", "index", "service"], pd).dropna(
        subset=["scenario", "index", "service"]
    )
    samples = samples[samples["scenario"].isin([1, 2, 3, 4])].copy()
    records: list[dict[str, Any]] = []
    for (source, scenario), lane in samples.groupby(["source_file", "scenario"]):
        if len(lane) != int(scenario) or lane["index"].duplicated().any():
            raise PlotUnavailable(
                f"Jain fairness: malformed workflow set for {source} scenario {scenario}"
            )
        values = lane["service"].to_numpy(dtype=float)
        fairness = float(values.sum() ** 2 / (len(values) * np.square(values).sum()))
        records.append(
            {"source_file": source, "scenario": int(scenario), "fairness": fairness}
        )
    jain = pd.DataFrame.from_records(records)
    if jain.empty or set(jain["scenario"].astype(int)) != {1, 2, 3, 4}:
        raise PlotUnavailable("Jain fairness: EEVDF scenarios 1, 2, 3, and 4 are required")
    summary = jain.groupby("scenario")["fairness"].agg(["median", lambda x: x.quantile(0.25), lambda x: x.quantile(0.75), "count"])
    summary.columns = ["median", "q1", "q3", "count"]
    x = summary.index.to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(7.4, 4.9), constrained_layout=True)
    for scenario, lane in jain.groupby("scenario"):
        offsets = np.linspace(-0.065, 0.065, len(lane)) if len(lane) > 1 else np.asarray([0.0])
        ax.scatter(
            scenario + offsets, lane["fairness"], s=27,
            color="#2C7FB8", alpha=0.55, zorder=2,
        )
    ax.plot(x, summary["median"], marker="o", linewidth=2.2, color="#D95F02", label="Median Jain fairness")
    ax.fill_between(x, summary["q1"].to_numpy(), summary["q3"].to_numpy(), color="#D95F02", alpha=0.16, label="IQR")
    deficit = max(0.0, 1.0 - float(jain["fairness"].min()))
    padding = max(deficit * 0.16, 0.000001)
    ax.set_ylim(max(0.0, 1.0 - deficit - padding), 1.0 + padding * 0.18)
    ax.axhline(1.0, color="#555555", linestyle="--", linewidth=0.9, zorder=1)
    from matplotlib.ticker import FormatStrFormatter

    ax.yaxis.set_major_formatter(FormatStrFormatter("%.6f"))
    ax.set_xticks([1, 2, 3, 4])
    ax.set_xlabel("Concurrent EEVDF workflows")
    ax.set_ylabel("Jain fairness (1 is ideal)")
    ax.set_title("EEVDF service fairness under concurrency")
    ax.grid()
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="lower left")
    _footnote(
        fig,
        "Focused y-axis expands the measured distance from 1.0. J=(sum x)^2/(n sum x^2), "
        "with x equal to each workflow's unscaled emitted service_cycles.",
    )
    return fig, {"concurrency_levels": [1, 2, 3, 4], "rows": int(len(jain)), "basis": "raw_service_cycles"}


def _heatmap_panel(ax: Any, pivot: Any, title: str, cmap: str, np: Any) -> None:
    # Compare paths inside each metric while retaining the raw normalized value as text.
    maxima = pivot.abs().max(axis=0).replace(0, 1)
    relative = pivot.divide(maxima, axis=1).fillna(0)
    image = ax.imshow(relative.to_numpy(dtype=float), aspect="auto", cmap=cmap, vmin=0, vmax=1)
    for row in range(pivot.shape[0]):
        for column in range(pivot.shape[1]):
            raw = pivot.iloc[row, column]
            text = "NA" if not math.isfinite(float(raw)) else f"{raw:.2g}"
            color = "white" if relative.iloc[row, column] > 0.68 else "#222222"
            ax.text(column, row, text, ha="center", va="center", fontsize=7, color=color)
    metric_labels = {
        "bytes_read": "bytes read",
        "directory_block_probes": "dir block probes",
        "directory_entries_examined": "dir entries",
        "physical_reads": "physical reads",
        "physical_writes": "physical writes",
        "durable_flushes": "durable flushes",
        "virtio_notifications": "virtio notify",
        "virtio_submitted_requests": "virtio requests",
        "syscalls": "syscalls",
        "abi_descriptor_bytes": "ABI desc bytes",
        "copied_descriptor_bytes": "copied desc bytes",
        "dispatch_header_bytes": "dispatch hdr bytes",
        "control_abi_bytes": "control ABI bytes",
        "control_copied_bytes": "control copy bytes",
        "sched_dispatch_delta": "sched dispatch",
        "sequence_elapsed_ticks": "elapsed ticks",
    }
    ax.set_xticks(
        range(len(pivot.columns)),
        [metric_labels.get(str(value), str(value).replace("_", " ")) for value in pivot.columns],
        rotation=24,
        ha="right",
        rotation_mode="anchor",
    )
    ax.set_yticks(range(len(pivot.index)), [str(value).replace("_v3", " V3").replace("_", " ") for value in pivot.index])
    ax.set_title(title)
    return image


def plot_io_heatmap(root: Path, plt: Any, np: Any, pd: Any) -> tuple[Any, dict[str, Any]]:
    contest = _read_table(root, "contest_io_normalized", pd)
    task = _read_table(root, "task_perf_normalized", pd)
    _require(
        contest,
        [
            "path", "metric", "per_workload_syscall", "counter_scope",
            "counter_window", "denominator_scope", "denominator_window",
        ],
        "I/O heatmap",
    )
    _require(task, ["path", "metric", "per_operation"], "I/O heatmap")
    contest = _numeric(contest, ["per_workload_syscall"], pd).dropna(subset=["per_workload_syscall"])
    task = _numeric(task, ["per_operation"], pd).dropna(subset=["per_operation"])
    contest_metrics = [
        "bytes_read", "directory_block_probes", "directory_entries_examined",
        "physical_reads", "physical_writes", "durable_flushes",
        "virtio_notifications", "virtio_submitted_requests",
    ]
    task_metrics = [
        "syscalls", "abi_descriptor_bytes", "copied_descriptor_bytes",
        "dispatch_header_bytes", "control_abi_bytes", "control_copied_bytes",
        "sched_dispatch_delta", "sequence_elapsed_ticks",
    ]
    contest = contest[contest["metric"].isin(contest_metrics)]
    task = task[task["metric"].isin(task_metrics)]
    contest_pivot = contest.pivot_table(index="path", columns="metric", values="per_workload_syscall", aggfunc="median")
    task_pivot = task.pivot_table(index="path", columns="metric", values="per_operation", aggfunc="median")
    contest_pivot = contest_pivot.reindex(index=["traversal", "indexed"])
    task_pivot = task_pivot.reindex(index=["batch", "scalar_v3", "sq_cq"])
    contest_pivot = contest_pivot.dropna(axis=1, how="all")
    task_pivot = task_pivot.dropna(axis=1, how="all")
    contest_pivot = contest_pivot.loc[:, (contest_pivot.fillna(0) != 0).any(axis=0)]
    task_pivot = task_pivot.loc[:, (task_pivot.fillna(0) != 0).any(axis=0)]
    if contest_pivot.empty or task_pivot.empty:
        raise PlotUnavailable("I/O heatmap: normalized contest or Task panel is empty")
    fig, axes = plt.subplots(2, 1, figsize=(11.2, 6.4), constrained_layout=True, gridspec_kw={"height_ratios": [1, 1.25]})
    image = _heatmap_panel(
        axes[0], contest_pivot,
        "File I/O / observer workload syscall (mixed scope)", "YlGnBu", np
    )
    _heatmap_panel(axes[1], task_pivot, "Task ABI work per completed operation", "YlGnBu", np)
    colorbar = fig.colorbar(image, ax=axes, shrink=0.65, pad=0.02)
    colorbar.set_label("Within-metric relative intensity")
    _footnote(
        fig,
        "Top: bytes read is workflow-actor lane work from the core window; all other numerators are shared global "
        "kernel deltas from the end-to-end window. Each is divided by the workflow actor's core-window observer "
        "syscall count. Text is the median raw normalized value; color is scaled only within each metric.",
    )
    return fig, {
        "contest_metrics": list(contest_pivot.columns),
        "task_metrics": list(task_pivot.columns),
        "contest_scope": "bytes_read=workflow_lane/core; remaining metrics=global_kernel/end_to_end",
        "contest_denominator": "observer_process/core workload syscalls",
    }


PLOTS: tuple[tuple[str, Callable[..., tuple[Any, dict[str, Any]]]], ...] = (
    ("traversal_indexed_dumbbell", plot_dumbbell),
    ("core_latency_violin", plot_core_violin),
    ("paired_difference_ecdf", plot_difference_ecdf),
    ("catalog_hit_speedup_heatmap", plot_speedup_heatmap),
    ("performance_surface_3d", plot_performance_surface),
    ("cold_warm_indexed_grouped", plot_cold_warm_grouped),
    ("task_latency_distribution", plot_task_distribution),
    ("eevdf_wakeup_ecdf", plot_eevdf_ecdf),
    ("jain_fairness_concurrency", plot_jain_fairness),
    ("kernel_io_normalized_heatmap", plot_io_heatmap),
)


def render_all(
    tables: Path,
    output_dir: Path,
    formats: Sequence[str],
    *,
    allow_missing: bool = False,
) -> dict[str, Any]:
    matplotlib, plt, np, pd = _imports()
    _configure(matplotlib)
    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for name, function in PLOTS:
        try:
            fig, evidence = function(tables, plt, np, pd)
            files = _save(fig, output_dir, name, formats)
            generated.append({"name": name, "files": files, "evidence": evidence})
            plt.close(fig)
        except (PlotUnavailable, KeyError, ValueError) as error:
            skipped.append({"name": name, "reason": str(error)})
            plt.close("all")
    try:
        tables_label = tables.resolve().relative_to(
            output_dir.resolve().parent
        ).as_posix()
    except ValueError:
        tables_label = tables.name
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "tables_dir": tables_label,
        "formats": list(formats),
        "generated": generated,
        "skipped": skipped,
        "complete": len(generated) == len(PLOTS),
        "truthfulness_notes": [
            "Dots and ECDF steps use retained raw observations.",
            "The 3D surface connects only a complete measured parameter grid.",
            "EEVDF uses exact probe rows when available; histogram fallback is labeled approximate.",
            "Heatmap colors are normalized within each metric; annotations retain the raw normalized values.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "plot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if skipped and not allow_missing:
        reasons = "; ".join(f"{item['name']}: {item['reason']}" for item in skipped)
        raise PlotUnavailable(f"not all requested charts could be rendered: {reasons}")
    return manifest


def _self_test() -> None:
    matplotlib, plt, np, _ = _imports()
    _configure(matplotlib)
    x, y = _ecdf([3, 1, 2, 2], np)
    assert x.tolist() == [1.0, 2.0, 2.0, 3.0]
    assert y[-1] == 1.0
    km_x, km_y, censor_x, censor_y = _kaplan_meier_cdf(
        [1, 2, 200], [0, 0, 1], np
    )
    assert km_x.tolist() == [1.0, 2.0]
    assert censor_x.tolist() == [200.0] and censor_y[-1] == km_y[-1]
    with tempfile.TemporaryDirectory() as tmp:
        fig, ax = plt.subplots()
        ax.step(x, y, where="post")
        files = _save(fig, Path(tmp), "probe", ["png", "pdf"])
        plt.close(fig)
        assert files == ["probe.png", "probe.pdf"]
        assert all((Path(tmp) / name).stat().st_size > 0 for name in files)
    print("plot.py self-test: passed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables", type=Path, help="directory written by extract.py")
    parser.add_argument("--output-dir", type=Path, help="figure destination")
    parser.add_argument("--format", default="png,pdf", help="comma-separated: png,pdf")
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    if args.tables is None or args.output_dir is None:
        raise SystemExit("--tables and --output-dir are required")
    formats = [value.strip().lower() for value in args.format.split(",") if value.strip()]
    if not formats or any(value not in {"png", "pdf"} for value in formats):
        raise SystemExit("--format accepts only png and pdf")
    try:
        manifest = render_all(
            args.tables, args.output_dir, formats, allow_missing=args.allow_missing
        )
    except PlotUnavailable as error:
        print(f"plot.py: {error}", file=sys.stderr)
        return 1
    print(
        f"plot.py: rendered {len(manifest['generated'])}/{len(PLOTS)} figures "
        f"in {', '.join(formats)} to {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
