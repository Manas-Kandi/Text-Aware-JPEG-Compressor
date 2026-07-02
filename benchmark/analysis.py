from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _percent_delta(new_value: float, old_value: float) -> float:
    if old_value == 0:
        return 0.0
    return (new_value - old_value) / old_value * 100


def _tradeoff(arms: dict[str, Any]) -> dict[str, Any]:
    jpeg = arms.get("jpeg")
    text = arms.get("text")
    if not jpeg or not text:
        return {}
    token_delta = int(text["input_tokens"]) - int(jpeg["input_tokens"])
    return {
        "input_tokens_saved": token_delta,
        "input_token_savings_percent": round((token_delta / max(1, int(text["input_tokens"]))) * 100, 2),
        "accuracy_delta_points": round(float(jpeg["field_accuracy"]) - float(text["field_accuracy"]), 2),
        "latency_delta_ms": int(jpeg["median_latency_ms"]) - int(text["median_latency_ms"]),
        "payload_bytes_delta_percent": round(_percent_delta(float(jpeg["payload_bytes"]), float(text["payload_bytes"])), 2),
        "cost_delta": round(float(jpeg["cost"]) - float(text["cost"]), 6),
    }


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
    token_crossover: dict[str, Any] = {}
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
        profiles[profile] = {
            "observations": len(profile_group),
            "arms": arms,
            "comparable_model": len(resolved) == 1,
            "tradeoff": _tradeoff(arms),
        }
        token_rows = profile_group.groupby(["trajectory_length", "arm"], as_index=False).agg(
            input_tokens=("input_tokens", "mean"),
            field_accuracy=("field_accuracy", "mean"),
            page_count=("page_count", "mean"),
        )
        token_pivot = token_rows.pivot(index="trajectory_length", columns="arm", values="input_tokens")
        if {"jpeg", "text"}.issubset(token_pivot.columns):
            deltas = token_pivot["text"] - token_pivot["jpeg"]
            first_win = next((int(length) for length, delta in deltas.items() if delta >= 0), None)
            token_crossover[profile] = {
                "first_jpeg_token_win_length": first_win,
                "points": [
                    {
                        "trajectory_length": int(length),
                        "jpeg_input_tokens": round(float(token_pivot.loc[length, "jpeg"]), 2),
                        "text_input_tokens": round(float(token_pivot.loc[length, "text"]), 2),
                        "input_tokens_saved_by_jpeg": round(float(deltas.loc[length]), 2),
                    }
                    for length in token_pivot.index
                ],
            }
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
    density = frame[frame["profile"] == "density_sweep"].copy()
    crossover_source = density if not density.empty else primary
    if not crossover_source.empty:
        fig, ax_tokens = plt.subplots(figsize=(9, 4.8))
        token_curve = crossover_source.groupby(["trajectory_length", "arm"], as_index=False).agg(
            input_tokens=("input_tokens", "mean"),
            field_accuracy=("field_accuracy", "mean"),
            page_count=("page_count", "mean"),
        )
        sns.lineplot(token_curve, x="trajectory_length", y="input_tokens", hue="arm", marker="o", ax=ax_tokens)
        ax_tokens.set(title="Reported input-token crossover by context density", xlabel="State transitions in context", ylabel="Mean reported input tokens")
        ax_tokens.set_xscale("log", base=2)
        ax_accuracy = ax_tokens.twinx()
        sns.lineplot(token_curve, x="trajectory_length", y="field_accuracy", hue="arm", marker="s", linestyle="--", legend=False, ax=ax_accuracy)
        ax_accuracy.set(ylabel="Mean field accuracy", ylim=(0, 1.03))
        source_profile = "density_sweep" if not density.empty else "primary"
        crossing = token_crossover.get(source_profile, {}).get("first_jpeg_token_win_length")
        if crossing:
            ax_tokens.axvline(crossing, color="#555", linestyle=":", linewidth=1)
            ax_tokens.text(crossing, ax_tokens.get_ylim()[1] * 0.92, f"JPEG cheaper at {crossing}", rotation=90, va="top", ha="right", fontsize=8)
        plot_specs.append((fig, "token-crossover-by-density.png"))
    paths = []
    for fig, name in plot_specs:
        fig.tight_layout(); fig.savefig(charts / name, dpi=160); plt.close(fig); paths.append(f"charts/{name}")
    frame.to_csv(run_dir / "summary.csv", index=False)
    summary = {"observations": len(frame), "profiles": profiles, "token_crossover": token_crossover, "charts": paths}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
