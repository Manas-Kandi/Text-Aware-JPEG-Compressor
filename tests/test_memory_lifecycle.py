import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class MemoryLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.original_api_key = server.OPENROUTER_API_KEY
        server.OPENROUTER_API_KEY = ""
        self.root = Path(tempfile.mkdtemp(prefix="piper-test-"))
        server.DB_PATH = self.root / "test.db"
        server.IMAGES = self.root / "images"
        server.ARTIFACTS = self.root / "artifacts"
        server.BENCHMARK_IMAGES = self.root / "benchmark-images"
        server.IMAGES.mkdir()
        server.ARTIFACTS.mkdir()
        server.BENCHMARK_IMAGES.mkdir()
        server.init_db()

    def tearDown(self):
        server.OPENROUTER_API_KEY = self.original_api_key

    def test_task_graph_decay_and_recall(self):
        first = server.create_task(server.TaskRequest(title="Design memory graph", details="Define node relationships"))
        self.assertEqual(len(first["state"]["memories"]), 1)
        memory = first["state"]["memories"][0]
        self.assertTrue((server.IMAGES / Path(memory["image_url"]).name).exists())

        second = server.create_task(server.TaskRequest(title="Render memory image", details="Compress the node"))
        self.assertEqual(len(second["state"]["memories"]), 2)
        self.assertGreaterEqual(len(second["state"]["edges"]), 1)

        memory_id = second["state"]["memories"][0]["id"]
        server.decay()
        decayed = next(item for item in server.current_state()["memories"] if item["id"] == memory_id)
        self.assertEqual(decayed["decay_stage"], 1)

        server.access_memory(memory_id)
        restored = next(item for item in server.current_state()["memories"] if item["id"] == memory_id)
        self.assertEqual(restored["decay_stage"], 0)
        self.assertGreaterEqual(restored["access_count"], 1)

    def test_task_creation_survives_memory_index_failure(self):
        with patch.object(server, "create_memory", side_effect=RuntimeError("image encoder unavailable")):
            result = server.create_task(server.TaskRequest(title="Keep the task", details="Even when memory fails"))

        self.assertEqual(result["task"]["title"], "Keep the task")
        self.assertIn("image encoder unavailable", result["warning"])
        self.assertEqual(len(result["state"]["tasks"]), 1)
        self.assertEqual(result["state"]["memories"], [])

    def test_non_json_planner_falls_back_to_plain_chat(self):
        original_key = server.OPENROUTER_API_KEY
        server.OPENROUTER_API_KEY = "test-key"
        try:
            with patch.object(server, "model_chat", side_effect=["not json", "Hello — how can I help?"]):
                result = server.plan_response("hi", "", [])
            self.assertEqual(result["reply"], "Hello — how can I help?")
            self.assertEqual(result["actions"], [])
        finally:
            server.OPENROUTER_API_KEY = original_key

    def test_entity_tags_drive_retrieval(self):
        aurora = server.create_memory("TASK COMPLETED\nProject Aurora deployed gateway api.py.\nOUTCOME\nAurora health checks passed.")
        server.create_memory("TASK COMPLETED\nProject Borealis drafted a marketing outline.\nOUTCOME\nOutline saved.")
        retrieved = server.retrieve_memories("What happened to the Aurora gateway deployment?")
        self.assertEqual(retrieved[0]["id"], aurora["id"])
        self.assertIn("entities", retrieved[0]["retrieval_meta"])
        self.assertIn("outcomes", retrieved[0]["retrieval_meta"])


if __name__ == "__main__":
    unittest.main()
