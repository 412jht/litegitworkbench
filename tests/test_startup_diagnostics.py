from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import litegit_startup


class StartupDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="litegit-startup-test-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "diagnostics" / "startup.jsonl"
        for replacement in (patch.object(litegit_startup, "_log_file", None), patch.object(litegit_startup, "_stages", set())):
            replacement.start()
            self.addCleanup(replacement.stop)

    def test_source_launches_do_not_create_default_diagnostics(self) -> None:
        with patch.dict(litegit_startup.os.environ, {}, clear=True), patch.object(litegit_startup.sys, "frozen", False, create=True):
            litegit_startup.begin_startup()
        self.assertIsNone(litegit_startup._log_file)

    def test_explicit_diagnostics_record_each_phase_once_across_refresh_workers(self) -> None:
        with patch.dict(litegit_startup.os.environ, {"LITEGIT_STARTUP_LOG": str(self.path), "GIT_ASKPASS": "private-helper"}):
            litegit_startup.begin_startup()
            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(lambda _: litegit_startup.startup_phase("git_process_requested"), range(24)))
        records = [json.loads(line) for line in self.path.read_text(encoding="ascii").splitlines()]
        self.assertEqual(["python_entry", "git_process_requested"], [record["stage"] for record in records])
        self.assertTrue(all(set(record) == {"time_ns", "pid", "stage"} for record in records))
        self.assertNotIn("private-helper", self.path.read_text(encoding="ascii"))

    def test_unavailable_log_storage_does_not_interrupt_startup(self) -> None:
        self.path.parent.mkdir()
        self.path.write_text("Existing diagnostic data\n", encoding="ascii")
        with patch.dict(litegit_startup.os.environ, {"LITEGIT_STARTUP_LOG": str(self.path / "invalid.jsonl")}):
            litegit_startup.begin_startup()
            litegit_startup.startup_phase("tk_created")
        self.assertEqual("Existing diagnostic data\n", self.path.read_text(encoding="ascii"))


if __name__ == "__main__":
    unittest.main()
