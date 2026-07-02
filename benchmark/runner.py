from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .analysis import analyze
from .rendering import data_url, paginate, render_pages, verify_render_contract
from .scenarios import DEFAULT_LENGTHS, DEFAULT_SEEDS, Probe, build_trajectory
from .scoring import score_answer


ModelCall = Callable[[list[dict[str, Any]]], dict[str, Any]]
JPEG_IMAGE_DETAIL = os.getenv("BENCHMARK_IMAGE_DETAIL", "low").lower()
DENSITY_SWEEP_LENGTHS = (16, 32, 64, 128, 256, 512)
ANSWER_DISCIPLINE = (
    "Read all context in order before answering. Treat log row numbers as references, not answers, "
    "unless the question explicitly asks for a row number. For current-state questions, later updates "
    "override earlier values. For multi-hop questions, resolve each relationship step. For arithmetic, "
    "apply every relevant numeric update. For next-action questions, return the milestone or action name, "
    "not the copied log line."
)


def run_folder(config: dict[str, Any], run_id: str) -> str:
    return str(config.get("run_folder") or run_id)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def counterbalanced_arms(index: int) -> list[str]:
    return ["jpeg", "text"] if index % 2 == 0 else ["text", "jpeg"]


class BenchmarkRunner:
    def __init__(self, db_path: Path, data_root: Path, model_call: ModelCall):
        self.db_path = db_path
        self.data_root = data_root
        self.model_call = model_call
        self._write_lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute(self, query: str, params: tuple = ()) -> None:
        with self._write_lock:
            conn = self._db()
            try:
                conn.execute(query, params)
                conn.commit()
            finally:
                conn.close()

    def _rows(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = self._db()
        try:
            return [dict(row) for row in conn.execute(query, params).fetchall()]
        finally:
            conn.close()

    def cancelled(self, run_id: str) -> bool:
        rows = self._rows("SELECT cancel_requested FROM benchmark_v2_runs WHERE id=?", (run_id,))
        return not rows or bool(rows[0]["cancel_requested"])

    def _set_run(self, run_id: str, **values: Any) -> None:
        values["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in values)
        self._execute(f"UPDATE benchmark_v2_runs SET {assignments} WHERE id=?", tuple(values.values()) + (run_id,))

    def _write_manifest(self, run_id: str, config: dict[str, Any]) -> Path:
        run_dir = self.data_root / run_folder(config, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 2, "run_id": run_id, "created_at": utc_now(),
            "question": "JPEG pages versus identical plain-text context for long-running state fidelity",
            "config": config,
            "limitations": [
                "Pilot effect estimates do not establish broad model-independent claims.",
                "Results are specific to the pinned model and rendering configuration.",
                "Image token accounting may not be comparable with text token accounting.",
                "Synthetic trajectories do not reproduce all properties of software work.",
                "Closed-loop observations conflate context fidelity with error propagation.",
            ],
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return run_dir

    def run(self, run_id: str) -> None:
        run_rows = self._rows("SELECT * FROM benchmark_v2_runs WHERE id=?", (run_id,))
        if not run_rows:
            raise ValueError(f"Unknown benchmark run {run_id}")
        config = json.loads(run_rows[0]["config"])
        run_dir = self._write_manifest(run_id, config)
        try:
            self._set_run(run_id, status="running", phase="preparing", error="")
            work: list[tuple[str, Any, list[str]]] = []
            for length_index, length in enumerate(config["lengths"]):
                for seed_index, seed in enumerate(config["seeds"]):
                    trajectory = build_trajectory(seed, length)
                    arms = counterbalanced_arms(seed_index + length_index)
                    work.append(("primary", trajectory, arms))
            if config.get("closed_loop", True):
                for seed_index, seed in enumerate(config["seeds"][:4]):
                    trajectory = build_trajectory(seed + 90000, 32)
                    arms = counterbalanced_arms(seed_index)
                    work.append(("closed_loop", trajectory, arms))
            if config.get("density_sweep", False):
                density_lengths = config.get("density_lengths") or DENSITY_SWEEP_LENGTHS
                for length_index, length in enumerate(density_lengths):
                    for seed_index, seed in enumerate(config["seeds"][:2]):
                        trajectory = build_trajectory(seed + 700000, int(length))
                        arms = counterbalanced_arms(seed_index + length_index)
                        work.append(("density_sweep", trajectory, arms))
            total = sum(len(trajectory.probes) * 2 for _, trajectory, _ in work)
            self._set_run(run_id, total_observations=total)
            for profile, trajectory, arms in work:
                self._execute(
                    "INSERT OR IGNORE INTO benchmark_v2_trajectories VALUES (?,?,?,?,?,?,?)",
                    (run_id, f"{profile}-{trajectory.trajectory_id}", profile, trajectory.seed, trajectory.length, json.dumps(arms), utc_now()),
                )
                for arm in arms:
                    feedback: list[str] = []
                    for probe in trajectory.probes:
                        if self.cancelled(run_id):
                            self._set_run(run_id, status="cancelled", phase="cancelled")
                            return
                        trajectory_id = f"{profile}-{trajectory.trajectory_id}"
                        existing = self._rows(
                            "SELECT status,answer FROM benchmark_v2_observations WHERE run_id=? AND trajectory_id=? AND arm=? AND checkpoint=?",
                            (run_id, trajectory_id, arm, probe.checkpoint),
                        )
                        if existing and existing[0]["status"] == "complete":
                            if profile == "closed_loop":
                                feedback.append(f"At checkpoint {probe.checkpoint}, the model answered: {existing[0]['answer']}")
                            continue
                        self._set_run(run_id, phase=arm)
                        answer = self._observe(run_id, run_dir, trajectory_id, profile, trajectory.length, arm, probe, feedback)
                        if profile == "closed_loop":
                            feedback.append(f"At checkpoint {probe.checkpoint}, the model answered: {answer or '[no answer]'}")
                        done = self._rows("SELECT COUNT(*) AS count FROM benchmark_v2_observations WHERE run_id=? AND status='complete'", (run_id,))[0]["count"]
                        self._set_run(run_id, completed_observations=done)
            self._set_run(run_id, phase="analysis")
            observations = self._rows("SELECT * FROM benchmark_v2_observations WHERE run_id=? ORDER BY profile,trajectory_id,checkpoint,arm", (run_id,))
            (run_dir / "observations.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in observations), encoding="utf-8")
            summary = analyze(observations, run_dir)
            report = self._report(config, summary)
            (run_dir / "report.md").write_text(report, encoding="utf-8")
            for relative in ["manifest.json", "observations.jsonl", "summary.csv", "summary.json", "report.md", *summary.get("charts", [])]:
                path = run_dir / relative
                if path.exists():
                    self._execute("INSERT OR REPLACE INTO benchmark_v2_artifacts VALUES (?,?,?,?)", (run_id, relative, path.stat().st_size, utc_now()))
            self._set_run(run_id, status="complete", phase="complete", summary=json.dumps(summary), completed_observations=total)
        except Exception as error:
            self._set_run(run_id, status="failed", phase="failed", error=str(error)[:1000])
            raise

    def _observe(self, run_id: str, run_dir: Path, trajectory_id: str, profile: str, length: int, arm: str, probe: Probe, feedback: list[str]) -> str:
        context = probe.context + (("\n\nMODEL FEEDBACK\n" + "\n".join(feedback)) if feedback else "")
        render_profile = "dense" if profile == "density_sweep" else "normal"
        pages = paginate(context, render_profile)
        if not verify_render_contract(context, render_profile):
            raise RuntimeError("Pagination changed canonical context content")
        instruction = (
            "Use the supplied context as the complete task record. Answer the question using JSON only, "
            "with exactly one key named answer. Do not explain.\n\nANSWER DISCIPLINE\n"
            + ANSWER_DISCIPLINE + "\n\nQUESTION\n" + probe.prompt
        )
        image_paths: list[Path] = []
        if arm == "jpeg":
            image_paths = render_pages(pages, run_dir / "pages", f"{trajectory_id}-c{probe.checkpoint}", render_profile)
            content: Any = [{"type": "text", "text": instruction}]
            content.extend({"type": "image_url", "image_url": {"url": data_url(path), "detail": JPEG_IMAGE_DETAIL}} for path in image_paths)
            payload_bytes = len(instruction.encode()) + sum(path.stat().st_size for path in image_paths)
        else:
            page_text = "\n\n".join(f"--- CONTEXT PAGE {index + 1}/{len(pages)} ---\n{page}" for index, page in enumerate(pages))
            content = page_text + "\n\n" + instruction
            payload_bytes = len(content.encode("utf-8"))
        actual_context_hash = hashlib.sha256(context.encode("utf-8")).hexdigest()
        observation_id = uuid.uuid4().hex
        previous = self._rows(
            "SELECT id FROM benchmark_v2_observations WHERE run_id=? AND trajectory_id=? AND arm=? AND checkpoint=?",
            (run_id, trajectory_id, arm, probe.checkpoint),
        )
        if previous:
            observation_id = previous[0]["id"]
        else:
            self._execute(
                "INSERT INTO benchmark_v2_observations (id,run_id,trajectory_id,profile,arm,trajectory_length,checkpoint,probe_type,prompt,expected,status,context_hash,payload_bytes,page_count,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (observation_id, run_id, trajectory_id, profile, arm, length, probe.checkpoint, probe.probe_type, probe.prompt, json.dumps(probe.expected), "running", actual_context_hash, payload_bytes, len(pages), utc_now()),
            )
        answer = ""; result: dict[str, Any] = {}; error_type = ""; error_text = ""; started = time.perf_counter(); attempts = 0
        for attempt in range(1, 4):
            attempts = attempt
            attempt_started = time.perf_counter()
            try:
                result = self.model_call([{"role": "user", "content": content}])
                answer = str(result.get("content") or "")
                self._execute("INSERT INTO benchmark_v2_attempts VALUES (?,?,?,?,?,?,?)", (uuid.uuid4().hex, observation_id, attempt, "complete", round((time.perf_counter() - attempt_started) * 1000), "", utc_now()))
                break
            except Exception as error:
                error_text = str(error)[:1000]
                error_type = "transport" if any(token in error_text.lower() for token in ("connection", "timeout", "429", "502", "503", "504")) else "provider"
                self._execute("INSERT INTO benchmark_v2_attempts VALUES (?,?,?,?,?,?,?)", (uuid.uuid4().hex, observation_id, attempt, "failed", round((time.perf_counter() - attempt_started) * 1000), error_text, utc_now()))
                if attempt < 3 and error_type == "transport":
                    time.sleep(attempt)
                    continue
                break
        scored = score_answer(answer, probe.expected)
        if scored["parse_error"] and not error_type:
            error_type = scored["parse_error"]
        usage = result.get("usage") or {}
        latency = round((time.perf_counter() - started) * 1000)
        self._execute(
            """UPDATE benchmark_v2_observations SET answer=?,correct=?,fields_correct=?,fields_total=?,status='complete',latency_ms=?,input_tokens=?,output_tokens=?,cost=?,resolved_model=?,provider=?,attempt_count=?,error_type=?,error=?,completed_at=? WHERE id=?""",
            (answer, int(scored["correct"]), scored["fields_correct"], scored["fields_total"], latency,
             int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0), int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
             float(usage.get("cost") or result.get("cost") or 0), str(result.get("model") or ""), str(result.get("provider") or ""), attempts,
             error_type, error_text, utc_now(), observation_id),
        )
        return answer

    @staticmethod
    def _report(config: dict[str, Any], summary: dict[str, Any]) -> str:
        return "\n".join([
            "# JPEG Context Benchmark Report", "", "## Configuration", "",
            f"- Model: `{config['model']}`", f"- Lengths: {config['lengths']}", f"- Seeds: {config['seeds']}",
            f"- Rendering: 750×1000 grayscale JPEG, 16 px text, quality 75, image detail `{JPEG_IMAGE_DETAIL}`", "",
            "## Results", "", "```json", json.dumps(summary, indent=2), "```", "",
            "## Interpretation", "", "This pilot estimates paired effects and variance. It does not establish model-independent superiority of either representation.",
        ]) + "\n"
