"""Default install must convert documents with the same text as the underlying libraries."""

import io
import os
import re

import pytest

from markitdown import MarkItDown, StreamInfo

TEST_FILES = os.path.join(os.path.dirname(__file__), "test_files")


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text)


def _squash(text: str) -> str:
    return "".join(_words(text))


def test_pdf_text_matches_pdfplumber():
    import pdfplumber

    path = os.path.join(TEST_FILES, "test.pdf")
    with pdfplumber.open(path) as pdf:
        direct = " ".join(page.extract_text() or "" for page in pdf.pages)
    result = MarkItDown().convert(path).markdown
    assert _squash(direct)
    assert _squash(direct) in _squash(result)


def test_docx_text_matches_python_docx():
    docx = pytest.importorskip("docx")

    path = os.path.join(TEST_FILES, "test.docx")
    direct = " ".join(p.text for p in docx.Document(path).paragraphs)
    result = MarkItDown().convert(path).markdown
    assert _words(direct)
    assert set(_words(direct)) <= set(_words(result))


def test_hwpx_text_matches_hwp_hwpx_parser(tmp_path):
    import hwp_hwpx_parser as parser

    from ._hwpx_builder import build_hwpx

    data = build_hwpx()
    path = tmp_path / "sample.hwpx"
    path.write_bytes(data)
    reader = parser.read(str(path))
    try:
        direct = reader.extract_text()
    finally:
        reader.close()
    result = (
        MarkItDown()
        .convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=".hwpx"))
        .markdown
    )
    assert _words(direct)
    assert set(_words(direct)) <= set(_words(result))
