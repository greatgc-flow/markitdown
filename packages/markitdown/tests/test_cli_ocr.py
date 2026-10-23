"""CLI contract for --ocr-report and the stderr hints."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

FILES = Path(__file__).parent / "test_files"
SCAN = FILES / "MEDRPT-2024-PAT-3847_medical_report_scan.pdf"
TEXT = FILES / "test.pdf"


def _run(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "markitdown", *args],
        input=stdin,
        capture_output=True,
    )


def _text(data: bytes) -> str:
    return data.decode("utf-8")


def test_report_to_stdout_is_json_only_and_verdict_first() -> None:
    result = _run("--ocr-report", str(SCAN))
    assert result.returncode == 0, result.stderr
    report = json.loads(_text(result.stdout))
    keys = list(report)
    assert keys[:5] == ["decision", "coverage", "summary", "candidates", "issues"]
    assert keys[-1] in ("pages", "rasters")
    assert report["decision"] == "ocr_recommended"


def test_report_with_output_prints_one_summary_line(tmp_path) -> None:
    out = tmp_path / "r.json"
    result = _run("--ocr-report", str(SCAN), "-o", str(out))
    assert result.returncode == 0, result.stderr
    lines = _text(result.stdout).splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("ocr-report: decision=ocr_recommended high=3 low=0")
    assert "coverage=complete" in lines[0] and "issues=0" in lines[0]
    assert lines[0].endswith(f"-> {out}")
    assert json.loads(out.read_text(encoding="utf-8"))["decision"] == "ocr_recommended"


def test_report_is_deterministic_bytes() -> None:
    first = _run("--ocr-report", str(SCAN)).stdout
    second = _run("--ocr-report", str(SCAN)).stdout
    assert first == second


def test_report_from_stdin_and_exit_zero_for_unknown() -> None:
    ok = _run("--ocr-report", stdin=SCAN.read_bytes())
    assert ok.returncode == 0
    assert json.loads(_text(ok.stdout))["decision"] == "ocr_recommended"
    unknown = _run("--ocr-report", stdin=b"\x00\x01 not a document")
    assert unknown.returncode == 0
    assert json.loads(_text(unknown.stdout))["decision"] == "unknown"


def test_samples_only_with_flag() -> None:
    without = json.loads(_text(_run("--ocr-report", str(TEXT)).stdout))
    assert all("text_sample" not in p for p in without["pages"])
    with_samples = _run("--ocr-report", "--ocr-report-samples", str(TEXT))
    report = json.loads(_text(with_samples.stdout))
    assert report["options"]["samples"] is True
    assert 0 < len(report["pages"][0]["text_sample"]) <= 120


def test_utf8_output_survives_a_legacy_console_encoding(tmp_path) -> None:

    pdf = tmp_path / "ko.pdf"
    Image.new("RGB", (50, 50), "white").save(pdf, "PDF")
    result = subprocess.run(
        [sys.executable, "-m", "markitdown", "--ocr-report", str(pdf)],
        capture_output=True,
        env={**__import__("os").environ, "PYTHONIOENCODING": "cp949"},
    )
    assert result.returncode == 0, result.stderr
    json.loads(_text(result.stdout))


@pytest.mark.parametrize(
    "extra",
    [
        ["-d"],
        ["--use-cu"],
        ["-p"],
        ["--keep-data-uris"],
        ["-c", "utf-8"],
    ],
)
def test_report_mode_rejects_conversion_options(extra) -> None:
    result = _run("--ocr-report", str(SCAN), *extra)
    assert result.returncode == 2
    assert result.stdout == b""


def test_samples_flag_requires_report_mode() -> None:
    result = _run("--ocr-report-samples", str(SCAN))
    assert result.returncode == 2


def test_report_mode_never_converts_or_hints(tmp_path) -> None:
    result = _run("--ocr-report", str(SCAN))
    assert b"no text extracted" not in result.stderr


def test_empty_output_prints_hint_on_stderr_only() -> None:
    result = _run(str(SCAN))
    assert result.returncode == 0
    assert _text(result.stdout).strip() == ""
    assert f"no text extracted; try: markitdown {SCAN} --ocr-report" in _text(
        result.stderr
    )


def test_non_empty_output_has_no_hint() -> None:
    result = _run(str(TEXT))
    assert result.returncode == 0
    assert _text(result.stdout).strip() != ""
    assert b"no text extracted" not in result.stderr


def test_error_hint_keeps_failure_status_and_traceback() -> None:
    result = _run(str(FILES / "random.bin"))
    assert result.returncode == 1
    stderr = _text(result.stderr)
    assert "UnsupportedFormatException" in stderr
    assert "hint: markitdown --guide errors" in stderr
