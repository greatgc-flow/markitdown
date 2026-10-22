"""`markitdown --guide` prints a compact usage guide meant for AI agents."""

import subprocess
import sys
from importlib import resources


def _run_guide() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "markitdown", "--guide"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_guide_prints_and_exits_zero_without_filename() -> None:
    result = _run_guide()
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("# MarkItDown guide")


def test_guide_matches_packaged_file() -> None:
    packaged = resources.files("markitdown").joinpath("GUIDE.md").read_text("utf-8")
    assert _run_guide().stdout.strip() == packaged.strip()


def test_guide_is_compact_and_covers_key_topics() -> None:
    text = _run_guide().stdout
    assert len(text.splitlines()) <= 120
    for topic in (
        "Silent empty output",
        "OCR",
        "HWP",
        "--keep-data-uris",
        "UnsupportedFormatException",
        "Python 3.15",
    ):
        assert topic in text, f"guide is missing: {topic}"
