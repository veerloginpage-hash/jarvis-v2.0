import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent.planner import _fast_plan
from agent.task_queue import Task, TaskStatus
from core.diagnostics import format_snapshot, snapshot
from core.visual_diff import change_score, changed
from core.audio_devices import select_device, configured_preference
from core.task_runtime import checkpoint, load_checkpoint, clear_checkpoint
from core.command_normalizer import normalize_command
from core.performance import PerformanceTracker
from actions import automation as automation_module
from actions.local_workflow import _validate


class FastPathTests(unittest.TestCase):
    def test_open_app_fast_path(self):
        plan = _fast_plan("open calculator")
        self.assertEqual(plan["steps"][0]["tool"], "open_app")
        self.assertEqual(plan["steps"][0]["parameters"]["app_name"], "calculator")

    def test_url_fast_path_adds_scheme(self):
        plan = _fast_plan("visit www.example.com")
        self.assertEqual(plan["steps"][0]["tool"], "browser_control")
        self.assertEqual(plan["steps"][0]["parameters"]["url"], "https://www.example.com")

    def test_weather_fast_path(self):
        plan = _fast_plan("mausam in Delhi")
        self.assertEqual(plan["steps"][0]["tool"], "weather_report")
        self.assertEqual(plan["steps"][0]["parameters"]["city"], "Delhi")

    def test_deterministic_command_chain(self):
        plan = _fast_plan("open calculator and then search for quantum computing")
        self.assertEqual([step["tool"] for step in plan["steps"]], ["open_app", "web_search"])

    def test_task_defaults_are_runtime_safe(self):
        task = Task(priority=2, created_at=1.0, task_id="abc", goal="demo")
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertIsNotNone(task.updated_at)
        self.assertIsNone(task.started_at)

    def test_diagnostics_are_safe_without_config(self):
        with TemporaryDirectory() as folder:
            data = snapshot(Path(folder))
            text = format_snapshot(data)
            self.assertIn("Gemini API: MISSING", text)
            self.assertNotIn("key", text.lower())
            self.assertIn("offline_tts", data)
            self.assertIn("Offline voice fallback:", text)
            self.assertIn("Local vision runtime:", format_snapshot({"local_vision_status": "cold"}))

    def test_visual_diff_detects_real_change(self):
        from PIL import Image
        from io import BytesIO

        def png(color):
            buf = BytesIO()
            Image.new("RGB", (40, 40), color).save(buf, format="PNG")
            return buf.getvalue()

        before, after = png("black"), png("white")
        self.assertGreater(change_score(before, after), 0.9)
        self.assertTrue(changed(before, after))

    def test_audio_selection_is_safe_without_devices(self):
        # The helper must never crash on CI/headless machines.
        self.assertTrue(select_device("input") is None or isinstance(select_device("input"), int))
        with TemporaryDirectory() as folder:
            self.assertEqual(configured_preference(folder, "input"), "")

    def test_runtime_checkpoint_roundtrip(self):
        task_id = "test_resume_roundtrip"
        checkpoint(task_id, {"completed_steps": [1, 2], "last_result": "ok"})
        try:
            saved = load_checkpoint(task_id)
            self.assertEqual(saved["completed_steps"], [1, 2])
            self.assertEqual(saved["last_result"], "ok")
        finally:
            clear_checkpoint(task_id)

    def test_hinglish_command_normalization_is_conservative(self):
        self.assertEqual(normalize_command("  calculator kholo  "), "open calculator")
        self.assertEqual(normalize_command("search dhundho cats"), "search for cats")
        self.assertEqual(normalize_command("Open VS Code"), "Open VS Code")

    def test_performance_tracker_aggregates_calls(self):
        tracker = PerformanceTracker()
        tracker.record("demo", 100)
        tracker.record("demo", 300, failed=True)
        stats = tracker.snapshot()["tools"]["demo"]
        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["avg_ms"], 200.0)

    def test_automation_registry_supports_lifecycle_without_os_scheduler(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(automation_module, "DATA_ROOT", root), \
                 patch.object(automation_module, "REGISTRY_PATH", root / "automations.json"), \
                 patch.object(automation_module, "HISTORY_PATH", root / "history.jsonl"), \
                 patch.object(automation_module, "_register"), \
                 patch.object(automation_module, "_unregister"):
                created = automation_module.automation({
                    "action": "create", "approved": True, "name": "Morning brief",
                    "goal": "open calendar", "schedule": "daily", "time": "08:30",
                })
                self.assertIn("active", created)
                records = automation_module._load()["automations"]
                self.assertEqual(len(records), 1)
                automation_id = records[0]["id"]
                self.assertIn("paused", automation_module.automation({"action": "pause", "id": automation_id}))
                self.assertIn("resumed", automation_module.automation({"action": "resume", "id": automation_id}))
                self.assertIn("deleted", automation_module.automation({"action": "delete", "id": automation_id}))

    def test_local_workflow_validation_requires_visible_targets(self):
        steps = _validate([
            {"action": "open_app", "app_name": "notepad"},
            {"action": "click", "target": "File"},
            {"action": "type", "text": "hello"},
        ])
        self.assertEqual(len(steps), 3)
        with self.assertRaises(ValueError):
            _validate([{"action": "click"}])


if __name__ == "__main__":
    unittest.main()
