from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "packaging" / "build_release.py"
SPEC = importlib.util.spec_from_file_location("litegit_build_release", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {MODULE_PATH}")
BUILD_RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_RELEASE)


class LocalBuildPolicyTests(unittest.TestCase):
    def test_blocks_local_windows_executable_build(self) -> None:
        policy = {"block_windows_executable_build": True}
        self.assertTrue(
            BUILD_RELEASE.local_windows_build_is_blocked(
                policy,
                environment={},
                system="Windows",
            )
        )

    def test_allows_github_actions_windows_build(self) -> None:
        policy = {"block_windows_executable_build": True}
        self.assertFalse(
            BUILD_RELEASE.local_windows_build_is_blocked(
                policy,
                environment={"GITHUB_ACTIONS": "true"},
                system="Windows",
            )
        )

    def test_allows_non_windows_build(self) -> None:
        policy = {"block_windows_executable_build": True}
        self.assertFalse(
            BUILD_RELEASE.local_windows_build_is_blocked(
                policy,
                environment={},
                system="Linux",
            )
        )


if __name__ == "__main__":
    unittest.main()
