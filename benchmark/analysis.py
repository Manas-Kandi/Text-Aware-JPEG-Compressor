from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def analyze(observations: list[dict[str, Any]], run_dir: Path) -> dict[str, Any]:
    cache = Path(tempfile.gettempdir()) / "piper-matplotlib-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns
    from scipy import stats

    complete = [row for row in observations if row.get("status") == "complete"]
    frame = pd.DataFrame(complete)
    charts = run_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    if frame.empty:
        summary = {"observations": 0, "profiles": {}, "charts": []}
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
    frame["field_accuracy"] = frame["fields_correct"] / frame["fields_total"].clip(lower=1)
    profiles: dict[str, Any] = {}
    for profile, profile_group in frame.groupby("profile"):
        arms: dict[str, Any] = {}
        for arm, group in profile_group.groupby("arm"):
            by_trajectory = group.groupby("trajectory_id")["correct"].mean()
            boots = [by_trajectory.sample(len(by_trajectory), replace=True, random_state=index).mean() for index in range(500)]
            length_accuracy = group.groupby("trajectory_length")["field_accuracy"].mean()
            slope = stats.linregress(length_accuracy.index, length_accuracy.values).slope if len(length_accuracy) > 1 else 0.0
            arms[arm] = {
                "field_accuracy": round(float(group["field_accuracy"].mean()) * 100, 2),
                "probe_accuracy": round(float(group["correct"].mean()) * 100, 2),
                "trajectory_success": round(float(group.groupby("trajectory_id")["correct"].all().mean()) * 100, 2),
                "degradation_per_transition": round(float(slope) * 100, 4),
                "median_latency_ms": round(float(group["latency_ms"].median())),
                "input_tokens": int(group["input_tokens"].sum()),
                "output_tokens": int(group["output_tokens"].sum()),
                "payload_bytes": int(group["payload_bytes"].sum()),
                "cost": round(float(group["cost"].sum()), 6),
                "failures": int((group["error_type"] != "").sum()),
                "resolved_models": sorted(value for value in group["resolved_model"].unique() if value),
                "ci95": [round(float(pd.Series(boots).quantile(.025)) * 100, 2), round(float(pd.Series(boots).quantile(.975)) * 100, 2)],
            }
        resolved = {model for values in arms.values() for model in values["resolved_models"]}
        profiles[profile] = {"observations": len(profile_group), "arms": arms, "comparable_model": len(resolved) == 1}
    primary = frame[frame["profile"] == "primary"].copy()
    plot_specs = []
    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    trajectory_accuracy = primary.groupby(["trajectory_id", "trajectory_length", "arm"], as_index=False)["field_accuracy"].mean()
    sns.lineplot(trajectory_accuracy, x="trajectory_length", y="field_accuracy", hue="arm", marker="o", errorbar=("ci", 95), ax=ax)
    ax.set(title="State fidelity by trajectory length", ylabel="Field accuracy", xlabel="State transitions", ylim=(0, 1.03))
    plot_specs.append((fig, "accuracy-by-length.png"))
    fig, ax = plt.subplots(figsize=(9, 4.5))
    sns.barplot(primary, x="probe_type", y="field_accuracy", hue="arm", errorbar=None, ax=ax)
    ax.tick_params(axis="x", rotation=25); ax.set(title="Accuracy by probe type", ylabel="Field accuracy", xlabel="")
    plot_specs.append((fig, "accuracy-by-probe.png"))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ordered = primary.sort_values(["arm", "trajectory_id", "checkpoint"]).copy()
    ordered["survived"] = ordered.groupby(["arm", "trajectory_id"])["correct"].cummin()
    survival = ordered.groupby(["arm", "checkpoint"], as_index=False)["survived"].mean()
    sns.lineplot(survival, x="checkpoint", y="survived", hue="arm", marker="o", ax=ax)
    ax.set(title="Complete-trajectory survival over depth", ylabel="Proportion with no prior error", ylim=(0, 1.03))
    plot_specs.append((fig, "survival-by-depth.png"))
    fig, axes = plt.subplots(2, 2, figsize=(10, 7))
    sns.ecdfplot(primary, x="latency_ms", hue="arm", ax=axes[0, 0]); axes[0, 0].set(title="Inference latency distribution")
    sns.ecdfplot(primary, x="input_tokens", hue="arm", ax=axes[0, 1]); axes[0, 1].set(title="Reported input-token distribution")
    sns.ecdfplot(primary, x="payload_bytes", hue="arm", ax=axes[1, 0]); axes[1, 0].set(title="Request-payload distribution")
    sns.ecdfplot(primary, x="cost", hue="arm", ax=axes[1, 1]); axes[1, 1].set(title="Reported request-cost distribution")
    plot_specs.append((fig, "efficiency.png"))
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    heat = primary.pivot_table(index=["arm", "page_count"], columns="checkpoint", values="correct", aggfunc="mean")
    sns.heatmap(heat, vmin=0, vmax=1, annot=True, fmt=".2f", ax=axes[0]); axes[0].set(title="Accuracy by page count and depth")
    totals = primary.groupby("arm", as_index=False).agg(cost=("cost", "sum"), field_accuracy=("field_accuracy", "mean"), latency_ms=("latency_ms", "median"))
    sns.scatterplot(totals, x="cost", y="field_accuracy", hue="arm", s=120, ax=axes[1]); axes[1].set(title="Accuracy-cost Pareto view")
    sns.scatterplot(totals, x="latency_ms", y="field_accuracy", hue="arm", s=120, ax=axes[2]); axes[2].set(title="Accuracy-latency Pareto view")
    plot_specs.append((fig, "capacity-and-cost.png"))
    paths = []
    for fig, name in plot_specs:
        fig.tight_layout(); fig.savefig(charts / name, dpi=160); plt.close(fig); paths.append(f"charts/{name}")
    frame.to_csv(run_dir / "summary.csv", index=False)
    summary = {"observations": len(frame), "profiles": profiles, "charts": paths}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
