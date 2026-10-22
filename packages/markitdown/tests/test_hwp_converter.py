"""HWP routing, resource cleanup, and owned HWPX integration."""

import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import hwp_hwpx_parser as parser
import pytest

from markitdown import (
    FileConversionException,
    MarkItDown,
    MissingDependencyException,
    StreamInfo,
)
from markitdown.converters import HwpConverter

from ._hwpx_builder import build_hwpx


def _hwp_only():
    converter = MarkItDown(enable_builtins=False)
    converter.register_converter(HwpConverter())
    return converter


@pytest.mark.parametrize("extension", [".hwp", ".hwpx", ".HWP", ".HWPX"])
def test_accepts_extension(extension):
    stream = io.BytesIO(b"not read")
    assert HwpConverter().accepts(stream, StreamInfo(extension=extension))
    assert stream.tell() == 0


@pytest.mark.parametrize(
    "mimetype",
    [
        "application/x-hwp",
        "application/x-hwp-v5",
        "application/haansofthwp",
        "application/vnd.hancom.hwp",
        "application/vnd.hancom.hwpx",
        "application/hwpx",
        "application/x-hwpx",
        "application/hwp+zip",
        "APPLICATION/HWPX; version=1",
    ],
)
def test_accepts_mimetype(mimetype):
    assert HwpConverter().accepts(io.BytesIO(), StreamInfo(mimetype=mimetype))


@pytest.mark.parametrize(
    "info",
    [
        StreamInfo(extension=".zip"),
        StreamInfo(extension=".docx"),
        StreamInfo(mimetype="application/zip"),
        StreamInfo(),
    ],
)
def test_rejects_other_formats(info):
    assert not HwpConverter().accepts(io.BytesIO(), info)


@pytest.mark.parametrize("extension", [".hwp", ".hwpx"])
def test_missing_backend(monkeypatch, extension):
    monkeypatch.setitem(sys.modules, "hwp_hwpx_parser", None)
    info = StreamInfo(extension=extension)
    stream = io.BytesIO(b"unread")
    with pytest.raises(MissingDependencyException, match=r"markitdown\[hwp\]"):
        HwpConverter().convert(stream, info)
    assert stream.tell() == 0
    with pytest.raises(FileConversionException, match=r"markitdown\[hwp\]") as exc:
        _hwp_only().convert_stream(stream, stream_info=info)
    assert any(
        isinstance(attempt.exc_info[1], MissingDependencyException)
        for attempt in exc.value.attempts
        if attempt.exc_info
    )


def test_lazy_import_without_backend_in_fresh_process():
    code = """
import sys, io
sys.modules['hwp_hwpx_parser'] = None
from markitdown import MarkItDown, StreamInfo, MissingDependencyException
from markitdown.converters import HwpConverter
MarkItDown()
try:
    HwpConverter().convert(io.BytesIO(), StreamInfo(extension='.hwp'))
except MissingDependencyException as exc:
    assert 'markitdown[hwp]' in str(exc)
else:
    raise AssertionError('Missing dependency was not reported')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "info, data, suffix",
    [
        (StreamInfo(extension=".HWP"), b"document", ".hwp"),
        (StreamInfo(extension=".hwpx"), b"document", ".hwpx"),
        (StreamInfo(mimetype="application/hwp+zip"), b"document", ".hwpx"),
        (StreamInfo(mimetype="application/vnd.hancom.hwpx"), b"document", ".hwpx"),
        (StreamInfo(mimetype="application/x-hwp"), b"document", ".hwp"),
        (StreamInfo(), b"\xd0\xcf\x11\xe0document", ".hwp"),
        (StreamInfo(), b"PKdocument", ".hwpx"),
    ],
)
def test_path_only_api_suffix_and_stream_position(
    monkeypatch, tmp_path, info, data, suffix
):
    _check_lifecycle(monkeypatch, tmp_path, info, data, suffix)


@pytest.mark.parametrize("failure", ["read", "extract", "close", "write"])
def test_tempfile_cleanup_on_failure(monkeypatch, tmp_path, failure):
    _check_lifecycle(
        monkeypatch,
        tmp_path,
        StreamInfo(extension=".hwp"),
        b"document",
        ".hwp",
        failure,
    )


def _check_lifecycle(monkeypatch, tmp_path, info, data, suffix, failure=None):
    actual_tempfile = tempfile.NamedTemporaryFile
    files = []

    def create_tempfile(*args, **kwargs):
        handle = actual_tempfile(*args, dir=tmp_path, **kwargs)
        files.append(handle)
        if failure == "write":
            handle.write = Mock(side_effect=RuntimeError("failed write"))
        return handle

    reader = Mock()
    reader.extract_text_with_notes.return_value = SimpleNamespace(
        text="document", footnotes=[], endnotes=[]
    )
    if failure in ("extract", "close"):
        method = reader.close if failure == "close" else reader.extract_text_with_notes
        method.side_effect = RuntimeError("failed " + failure)

    def read(path):
        assert files[
            -1
        ].closed, "Temporary file must be closed before reading on Windows"
        assert Path(path).suffix == suffix
        assert Path(path).read_bytes() == data
        if failure == "read":
            raise RuntimeError("failed read")
        return reader

    fake = SimpleNamespace(
        read=Mock(side_effect=read),
        ExtractOptions=parser.ExtractOptions,
        TableStyle=parser.TableStyle,
        ImageMarkerStyle=parser.ImageMarkerStyle,
    )
    monkeypatch.setitem(sys.modules, "hwp_hwpx_parser", fake)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", create_tempfile)
    stream = io.BytesIO(b"prefix " + data)
    stream.seek(7)
    if failure:
        with pytest.raises(RuntimeError, match="failed " + failure):
            HwpConverter().convert(stream, info)
    else:
        assert HwpConverter().convert(stream, info).markdown == "document"
        options = reader.extract_text_with_notes.call_args.args[0]
        assert options.table_style == parser.TableStyle.MARKDOWN
        assert options.image_marker == parser.ImageMarkerStyle.SIMPLE
    if failure not in ("read", "write"):
        reader.close.assert_called_once()
    assert files and all(handle.closed for handle in files)
    assert not list(tmp_path.iterdir())
    assert stream.tell() == len(stream.getvalue())


@pytest.mark.parametrize(
    "info",
    [StreamInfo(extension=".hwpx"), StreamInfo(mimetype="application/hwp+zip")],
)
def test_generated_hwpx_registered_conversion(info):
    result = MarkItDown().convert_stream(io.BytesIO(build_hwpx()), stream_info=info)
    markdown = result.markdown
    assert "| 항목 | 값 |" in markdown
    assert "| 일반왼쪽 | 일반오른쪽 |" in markdown
    assert "<table" not in markdown
    ordered = [
        "첫 번째",
        "항목",
        "값",
        "일반왼쪽",
        "일반오른쪽",
        "두 번째",
        "병합 제목",
        "병합왼쪽",
        "병합오른쪽",
        "마지막",
    ]
    for text in ordered:
        assert markdown.count(text) == 1
    positions = [markdown.index(text) for text in ordered]
    assert positions == sorted(positions)
    assert markdown.count("[IMAGE]") == 1
    assert "bin://" not in markdown
    assert "[^1]" in markdown and "[^1]: 각주 본문입니다." in markdown
    assert "[^e1]" in markdown and "[^e1]: 미주 본문입니다." in markdown
    assert markdown.count("각주 본문입니다.") == 1
    assert markdown.count("미주 본문입니다.") == 1


@pytest.mark.parametrize("extension", [".hwp", ".hwpx"])
@pytest.mark.parametrize(
    "data", [b"garbage", b"PK\x03\x04truncated", b"\xd0\xcf\x11\xe0"]
)
def test_corrupt_documents_fail(extension, data):
    with pytest.raises(FileConversionException):
        _hwp_only().convert_stream(
            io.BytesIO(data), stream_info=StreamInfo(extension=extension)
        )


def test_owner_authored_hwp5_fixture():
    path = Path(__file__).parent / "test_files" / "test_hwp5.hwp"
    if not path.exists():
        pytest.skip("owner-authored HWP5 fixture pending")
    assert MarkItDown().convert(path).markdown.strip()


def test_private_samples_local_smoke():
    directory = os.environ.get("MARKITDOWN_HWP_SMOKE_DIR")
    if not directory or os.environ.get("GITHUB_ACTIONS"):
        pytest.skip("local private sample smoke test is opt-in")
    paths = sorted(
        p for p in Path(directory).iterdir() if p.suffix.lower() in (".hwp", ".hwpx")
    )
    assert paths, "No HWP/HWPX samples found"
    code = (
        "from markitdown import MarkItDown; import sys; "
        "result=MarkItDown().convert(sys.argv[1]); "
        "assert result.markdown.strip(); print(len(result.markdown))"
    )
    for path in paths:
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(path)],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, "Private conversion failed (content suppressed)"
        assert int(result.stdout.strip()) > 0
