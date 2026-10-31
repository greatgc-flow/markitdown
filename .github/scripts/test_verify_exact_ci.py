"""Offline contract tests for release's exact-commit CI evidence gate."""

from __future__ import annotations

import unittest
from pathlib import Path

from verify_exact_ci import expected_jobs, validate_run


class TestExactReleaseCI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.jobs = expected_jobs(
            Path(__file__).resolve().parents[1] / "workflows" / "tests.yml"
        )

    def _run(self) -> dict:
        return {
            "head_sha": "a" * 40,
            "head_branch": "main",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }

    def _jobs(self) -> list[dict]:
        return [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in sorted(self.jobs)
        ]

    def test_matrix_includes_all_core_ocr_and_mcp_lanes(self) -> None:
        self.assertEqual(len(self.jobs), 24)
        self.assertTrue(
            any(n.startswith("tests (windows-11-vs2026-arm,") for n in self.jobs)
        )
        self.assertTrue(
            any(n.startswith("ocr-tests (windows-11-vs2026-arm,") for n in self.jobs)
        )
        self.assertTrue(
            any(n.startswith("mcp-tests (windows-11-vs2026-arm,") for n in self.jobs)
        )

    def test_exact_successful_main_push_is_accepted(self) -> None:
        validate_run(self._run(), self._jobs(), "a" * 40, self.jobs)

    def test_wrong_source_or_event_or_branch_is_rejected(self) -> None:
        for key, wrong in (
            ("head_sha", "b" * 40),
            ("event", "pull_request"),
            ("head_branch", "feature"),
            ("status", "in_progress"),
            ("conclusion", "cancelled"),
        ):
            with self.subTest(field=key):
                source = self._run()
                source[key] = wrong
                with self.assertRaises(ValueError):
                    validate_run(source, self._jobs(), "a" * 40, self.jobs)

    def test_missing_or_extra_job_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            validate_run(self._run(), self._jobs()[:-1], "a" * 40, self.jobs)
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            validate_run(
                self._run(),
                self._jobs()
                + [
                    {"name": "unlisted", "status": "completed", "conclusion": "success"}
                ],
                "a" * 40,
                self.jobs,
            )

    def test_duplicate_and_skipped_or_failed_jobs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_run(
                self._run(), self._jobs() + self._jobs()[:1], "a" * 40, self.jobs
            )
        for bad in ("failure", "cancelled", "skipped", None):
            with self.subTest(result=bad):
                jobs = self._jobs()
                jobs[0]["conclusion"] = bad
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    validate_run(self._run(), jobs, "a" * 40, self.jobs)


if __name__ == "__main__":
    unittest.main()
