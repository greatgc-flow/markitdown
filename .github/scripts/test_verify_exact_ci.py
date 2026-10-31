"""Offline contract tests for release's exact-commit CI evidence gate."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

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


class TestPinnedReleaseActions(unittest.TestCase):
    """Release job dependencies must be immutable, including OIDC publisher."""

    @classmethod
    def setUpClass(cls) -> None:
        file = Path(__file__).resolve().parents[1] / "workflows" / "release.yml"
        cls.release = yaml.safe_load(file.read_text(encoding="utf-8"))["jobs"]

    def test_every_external_release_action_has_full_commit_id(self) -> None:
        count = 0
        for job_id, job in self.release.items():
            for step in job.get("steps", []):
                uses = step.get("uses")
                if not uses:
                    continue
                with self.subTest(job=job_id, action=uses):
                    self.assertRegex(
                        uses, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$"
                    )
                count += 1
        self.assertEqual(count, 8)

    def test_publisher_is_known_security_fixed_commit(self) -> None:
        release_job = self.release["publish"]
        self.assertEqual(
            release_job["steps"][-1]["uses"],
            "pypa/gh-action-pypi-publish@" "7245906fb61fa33a13554f9d5fb856fe61758086",
        )
        self.assertEqual(release_job["environment"], "pypi")
        self.assertEqual(release_job["permissions"].get("id-token"), "write")
        self.assertEqual(release_job["needs"], "build")
        self.assertEqual(self.release["build"]["needs"], "verify-source-ci")


if __name__ == "__main__":
    unittest.main()
