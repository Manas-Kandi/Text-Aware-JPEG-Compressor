import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import server
from PIL import Image

from benchmark.rendering import WIDTH, paginate, render_pages, verify_render_contract
from benchmark.runner import BenchmarkRunner, counterbalanced_arms
from benchmark.analysis import analyze
from benchmark.scenarios import DEFAULT_LENGTHS, DEFAULT_SEEDS, PROBE_TYPES, build_trajectory
from benchmark.scoring import score_answer


class ResearchBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.original_db = server.DB_PATH
        self.root = Path(tempfile.mkdtemp(prefix="piper-benchmark-test-"))
        server.DB_PATH = self.root / "test.db"
        server.init_db()

    def tearDown(self):
        server.DB_PATH = self.original_db

    def test_scenarios_are_deterministic_and_cover_probe_families(self):
        first = build_trajectory(DEFAULT_SEEDS[0], 128)
        second = build_trajectory(DEFAULT_SEEDS[0], 128)
        self.assertEqual(first, second)
        self.assertEqual(len(first.probes), 4)
        observed = {
            probe.probe_type
            for length in DEFAULT_LENGTHS
            for seed in DEFAULT_SEEDS
            for probe in build_trajectory(seed, length).probes
        }
        self.assertEqual(observed, set(PROBE_TYPES))

    def test_page_segments_are_shared_without_clipping(self):
        context = build_trajectory(DEFAULT_SEEDS[0], 128).probes[-1].context
        pages = paginate(context)
        self.assertTrue(verify_render_contract(context))
        paths = render_pages(pages, self.root / "pages", "contract")
        self.assertEqual(len(paths), len(pages))
        self.assertTrue(all(path.stat().st_size > 0 for path in paths))

    def test_rendered_jpeg_wraps_before_right_edge(self):
        context = "TASK LOG\n0001 | " + ("Initialize project; " * 10)
        pages = paginate(context)
        path = render_pages(pages, self.root / "pages", "right-edge")[0]
        with Image.open(path) as image:
            right_edge = image.crop((WIDTH - 8, 0, WIDTH, image.height))
            self.assertGreater(min(right_edge.getdata()), 230)

    def test_scoring_exact_numeric_ordered_and_invalid_json(self):
        self.assertTrue(score_answer('{"answer": 42}', 42)["correct"])
        self.assertTrue(score_answer('{"answer": ["Mira", "Oslo"]}', ["Mira", "Oslo"])["correct"])
        self.assertFalse(score_answer('{"answer": ["Oslo", "Mira"]}', ["Mira", "Oslo"])["correct"])
        invalid = score_answer("Mira", "Mira")
        self.assertTrue(invalid["correct"])
        self.assertEqual(invalid["parse_error"], "invalid_json")

    def test_router_aliases_are_rejected(self):
        with self.assertRaises(ValueError):
            server.validate_benchmark_model("openrouter/free")
        server.validate_benchmark_model("openai/gpt-4.1")
        self.assertEqual(counterbalanced_arms(0), ["jpeg", "text"])
        self.assertEqual(counterbalanced_arms(1), ["text", "jpeg"])

    def test_pinned_model_check_ignores_variant_and_snapshot_suffixes(self):
        self.assertEqual(server.base_slug("qwen/qwen2.5-vl-72b-instruct:free"), "qwen/qwen2.5-vl-72b-instruct")
        self.assertTrue(server.same_pinned_model("google/gemma-4-26b-a4b-it:free", "google/gemma-4-26b-a4b-it-20260403:free"))
        self.assertFalse(server.same_pinned_model("qwen/qwen2.5-vl-72b", "qwen/qwen2.5-vl-72b2"))
        with patch.object(server, "model_chat", return_value={"content": "ok", "model": "qwen/qwen2.5-vl-72b-instruct"}):
            result = server.benchmark_model_call("qwen/qwen2.5-vl-72b-instruct:free", [])
        self.assertEqual(result["content"], "ok")
        with patch.object(server, "model_chat", return_value={"content": "ok", "model": "other/model"}):
            with self.assertRaises(RuntimeError):
                server.benchmark_model_call("qwen/qwen2.5-vl-72b-instruct", [])

    def test_default_benchmark_model_picks_biggest_free_vision_model(self):
        catalog = [
            {"id": "a/small:free", "name": "small", "free": True, "context_length": 32000},
            {"id": "b/big:free", "name": "big", "free": True, "context_length": 262000},
            {"id": "d/huge-preview", "name": "huge preview", "free": True, "context_length": 1048576},
            {"id": "c/paid", "name": "paid", "free": False, "context_length": 999000},
        ]
        with patch.object(server, "BENCHMARK_MODEL", "openrouter/free"), \
                patch.object(server, "list_vision_models", return_value=catalog):
            self.assertEqual(server.default_benchmark_model(), "b/big:free")
        with patch.object(server, "BENCHMARK_MODEL", "qwen/qwen2.5-vl-72b-instruct"):
            self.assertEqual(server.default_benchmark_model(), "qwen/qwen2.5-vl-72b-instruct")
        with patch.object(server, "BENCHMARK_MODEL", "openrouter/free"), \
                patch.object(server, "list_vision_models", side_effect=RuntimeError("offline")):
            self.assertEqual(server.default_benchmark_model(), "")

    def test_closed_loop_can_be_skipped(self):
        run_id = uuid.uuid4().hex
        config = {"model": "test/vision", "lengths": [4], "seeds": [1103], "closed_loop": False}
        timestamp = server.now_iso()
        server.execute(
            "INSERT INTO benchmark_v2_runs (id,created_at,updated_at,status,phase,config) VALUES (?,?,?,?,?,?)",
            (run_id, timestamp, timestamp, "queued", "queued", json.dumps(config)),
        )

        def fake_model(messages):
            return {"content": '{"answer":"x"}', "model": "test/vision", "provider": "test", "usage": {}}

        runner = BenchmarkRunner(server.DB_PATH, self.root / "runs", fake_model)
        with patch("benchmark.runner.analyze", return_value={"observations": 2, "profiles": {}, "charts": []}):
            runner.run(run_id)
        observations = server.rows("SELECT profile FROM benchmark_v2_observations WHERE run_id=?", (run_id,))
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(row["profile"] == "primary" for row in observations))

    def test_runner_checkpoints_and_resume_do_not_duplicate(self):
        run_id = uuid.uuid4().hex
        config = {"model": "test/vision", "lengths": [4], "seeds": [1103], "run_folder": "run A"}
        timestamp = server.now_iso()
        server.execute(
            "INSERT INTO benchmark_v2_runs (id,created_at,updated_at,status,phase,config) VALUES (?,?,?,?,?,?)",
            (run_id, timestamp, timestamp, "queued", "queued", json.dumps(config)),
        )
        calls = []

        def fake_model(messages):
            calls.append(messages)
            return {"content": '{"answer":"wrong"}', "model": "test/vision", "provider": "test", "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": .001}}

        runner = BenchmarkRunner(server.DB_PATH, self.root / "runs", fake_model)
        summary = {"observations": 10, "profiles": {}, "charts": []}
        with patch("benchmark.runner.analyze", return_value=summary):
            runner.run(run_id)
            count = server.rows("SELECT COUNT(*) count FROM benchmark_v2_observations WHERE run_id=?", (run_id,))[0]["count"]
            runner.run(run_id)
        self.assertEqual(count, 10)
        self.assertEqual(len(calls), 10)
        final = server.rows("SELECT * FROM benchmark_v2_runs WHERE id=?", (run_id,))[0]
        self.assertEqual(final["status"], "complete")
        self.assertEqual(final["completed_observations"], 10)
        self.assertTrue((self.root / "runs" / "run A" / "manifest.json").exists())
        public = server.benchmark_public(final)
        self.assertEqual(public["run_folder"], "run A")
        self.assertIn("/benchmark-runs/run%20A/manifest.json", [item["url"] for item in public["artifacts"]])
        log = server.benchmark_diagnostic_log(run_id)
        self.assertIn("PIEDPIPER JPEG CONTEXT DIAGNOSTIC LOG", log)
        self.assertIn("- folder: run A", log)
        self.assertIn("PRIMARY SUMMARY", log)
        self.assertIn("JPEG FAILURES", log)
        self.assertIn("prompt=", log)

    def test_transcript_endpoints_rebuild_verified_context(self):
        run_id = uuid.uuid4().hex
        config = {"model": "test/vision", "lengths": [4], "seeds": [1103]}
        timestamp = server.now_iso()
        server.execute(
            "INSERT INTO benchmark_v2_runs (id,created_at,updated_at,status,phase,config) VALUES (?,?,?,?,?,?)",
            (run_id, timestamp, timestamp, "queued", "queued", json.dumps(config)),
        )

        def fake_model(messages):
            return {"content": '{"answer":"wrong"}', "model": "test/vision", "provider": "test", "usage": {}}

        original_runs = server.BENCHMARK_RUNS
        server.BENCHMARK_RUNS = self.root / "runs"
        try:
            runner = BenchmarkRunner(server.DB_PATH, server.BENCHMARK_RUNS, fake_model)
            with patch("benchmark.runner.analyze", return_value={"observations": 10, "profiles": {}, "charts": []}):
                runner.run(run_id)
            listing = server.get_benchmark_observations(run_id)["observations"]
            self.assertEqual(len(listing), 10)
            self.assertTrue(all("seed" in item and "length" in item for item in listing))
            primary = next(item for item in listing if item["profile"] == "primary" and item["arm"] == "jpeg")
            closed = next(item for item in listing if item["profile"] == "closed_loop" and item["checkpoint"] == 32)
            for observation in (primary, closed):
                detail = server.get_benchmark_observation(run_id, observation["id"])
                self.assertTrue(detail["context_verified"], observation["profile"])
                self.assertIn("TASK LOG", detail["context"])
                self.assertEqual(len(detail["attempts"]), 1)
            self.assertTrue(server.get_benchmark_observation(run_id, primary["id"])["pages"])
        finally:
            server.BENCHMARK_RUNS = original_runs

    def test_analysis_generates_profile_metrics_and_python_charts(self):
        observations = []
        for arm in ("jpeg", "text"):
            for length in (16, 32):
                for checkpoint in (length // 4, length // 2, length * 3 // 4, length):
                    correct = not (arm == "jpeg" and checkpoint == length)
                    observations.append({
                        "status": "complete", "profile": "primary", "arm": arm,
                        "trajectory_id": f"{arm}-{length}", "trajectory_length": length,
                        "checkpoint": checkpoint, "probe_type": "current_state",
                        "correct": int(correct), "fields_correct": int(correct), "fields_total": 1,
                        "latency_ms": 100 + checkpoint, "input_tokens": 50, "output_tokens": 3,
                        "payload_bytes": 1000 + length, "cost": .001, "error_type": "",
                        "page_count": 1, "resolved_model": "test/vision",
                    })
        output = self.root / "analysis"
        summary = analyze(observations, output)
        self.assertIn("primary", summary["profiles"])
        self.assertTrue(summary["profiles"]["primary"]["comparable_model"])
        self.assertEqual(summary["profiles"]["primary"]["tradeoff"]["input_tokens_saved"], 0)
        self.assertEqual(summary["profiles"]["primary"]["tradeoff"]["accuracy_delta_points"], -25.0)
        self.assertEqual(len(summary["charts"]), 5)
        self.assertTrue((output / "summary.csv").exists())
        self.assertTrue(all((output / path).exists() for path in summary["charts"]))


if __name__ == "__main__":
    unittest.main()
