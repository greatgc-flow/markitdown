import io
import warnings

import pytest

from markitdown import StreamInfo
from markitdown.converters._html_converter import HtmlConverter
from markitdown.converters._xlsx_converter import XlsConverter
from markitdown.ocr_triage import triage


@pytest.mark.parametrize("script", ["run()", " \n", ""])
def test_empty_html_title_and_script_warning(script):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = HtmlConverter().convert_string(
            f"<title> Title </title><body><script>{script}</script></body>"
        )
    assert result.markdown == "# Title"
    assert [str(w.message) for w in caught] == (
        [
            "HTML script content was omitted; no body text was extracted. "
            "JavaScript-generated content requires a separately rendered export."
        ]
        if script.strip()
        else []
    )


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<title>Title</title><body>body<script>run()</script></body>", "body"),
        ("<title> </title><body><style>css</style></body>", ""),
    ],
)
def test_html_body_and_style_unchanged(html, expected):
    with warnings.catch_warnings(record=True) as caught:
        assert HtmlConverter().convert_string(html).markdown == expected
    assert not caught


@pytest.mark.parametrize(
    "data,expected,fmt",
    [
        (b"<html><body>table</body></html>", "table", "HTML"),
        ("hello".encode("utf-16"), "hello", "text"),
        (b"\xef\xbb\xbfhello", "hello", "text"),
        (b"\xfe\xff" + "hello".encode("utf-16-be"), "hello", "text"),
        (b"\xef\xbb\xbf<html><body>table</body></html>", "table", "HTML"),
        ("<html><body>table</body></html>".encode("utf-16"), "table", "HTML"),
    ],
)
def test_xls_mismatch(data, expected, fmt):
    stream = io.BytesIO(b"skip" + data)
    stream.seek(4)
    with pytest.warns(UserWarning, match=f"converted as {fmt}. Verify output."):
        result = XlsConverter().convert(stream, StreamInfo(extension=".xls"))
    assert result.markdown == expected
    assert stream.tell() == 4


@pytest.mark.parametrize(
    "data",
    [
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1bad",
        b"binary\x00\x01",
        b"plain without BOM",
        b"\xef\xbb\xbf\xff",
        b"\xef\xbb\xbfhello\x00",
        b"\xef\xbb\xbf" + b"x" * 8192 + b"\xff",
    ],
)
def test_xls_binary_is_not_recovered(data, monkeypatch):
    from types import SimpleNamespace
    from markitdown.converters import _xlsx_converter as module

    def read_excel(stream, **kwargs):
        assert stream.tell() == 0
        assert kwargs["engine"] == "xlrd"
        raise LookupError("native reader reached")

    monkeypatch.setattr(module, "_xls_dependency_exc_info", None)
    monkeypatch.setattr(
        module, "pd", SimpleNamespace(read_excel=read_excel), raising=False
    )
    with warnings.catch_warnings(record=True) as caught:
        with pytest.raises(LookupError, match="native reader reached"):
            XlsConverter().convert(io.BytesIO(data), StreamInfo(extension=".xls"))
    assert not caught


def test_xls_sniff_is_bounded_and_does_not_route_xlsm(monkeypatch):
    from types import SimpleNamespace
    from markitdown.converters import _xlsx_converter as module

    class PrefixOnly(io.BytesIO):
        def read(self, size=-1):
            assert size == 8192
            return super().read(size)

    def read_excel(stream, **kwargs):
        assert stream.tell() == 4
        raise LookupError("native reader reached")

    monkeypatch.setattr(module, "_xls_dependency_exc_info", None)
    monkeypatch.setattr(
        module, "pd", SimpleNamespace(read_excel=read_excel), raising=False
    )
    for extension, data in [(".xls", b"binary"), (".xlsm", b"<html>text")]:
        stream = PrefixOnly(b"skip" + data)
        stream.seek(4)
        with pytest.raises(LookupError, match="native reader reached"):
            XlsConverter().convert(stream, StreamInfo(extension=extension))
        assert stream.tell() == 4


def test_triage_coverage_is_documented():
    from importlib.resources import files

    guide = files("markitdown").joinpath("GUIDE.md").read_text("utf-8")
    assert "coverage.state: complete, partial, none, or not_applicable" in guide


@pytest.mark.parametrize("suffix", [".txt", ".log", ".md", ".html"])
def test_triage_empty_file_is_corrupt(tmp_path, suffix):
    path = tmp_path / ("input" + suffix)
    path.write_bytes(b"")
    report = triage(path)
    assert report["coverage"]["state"] == "none"
    assert [i["code"] for i in report["issues"]] == ["corrupt"]


@pytest.mark.parametrize("suffix", [".txt", ".log"])
@pytest.mark.parametrize(
    "data",
    [b"plain text\n", "Korean 한글".encode("utf-8"), "plain".encode("utf-16")],
)
def test_triage_plain_text(tmp_path, suffix, data):
    path = tmp_path / ("input" + suffix)
    path.write_bytes(data)
    report = triage(path)
    assert report["decision"] == "no_candidates"
    assert report["coverage"]["state"] == "not_applicable"
    assert report["coverage"]["reason"]
    assert report["rules_version"] == "1"


@pytest.mark.parametrize(
    "suffix", [".md", ".markdown", ".html", ".htm", ".xml", ".rtf"]
)
def test_triage_media_not_inspected(tmp_path, suffix):
    path = tmp_path / ("input" + suffix)
    path.write_bytes(b"text")
    report = triage(path)
    assert report["decision"] == "unknown"
    assert report["coverage"]["state"] == "none"
    assert {
        "code": "media_not_inspected",
        "detail": "Embedded or linked media in this format were not inspected.",
    } in report["issues"]


@pytest.mark.parametrize(
    "data",
    [
        b"bad\x00",
        b"bad\x01",
        b"\xff",
        b" <html>text",
        b"<?xml version='1.0'?>",
        b"{\\rtf1 text}",
    ],
)
def test_triage_txt_rejects_binary_and_markup(tmp_path, data):
    path = tmp_path / "input.txt"
    path.write_bytes(data)
    assert triage(path)["decision"] == "unknown"


def test_external_script_counts_as_omitted_script():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = HtmlConverter().convert_string(
            '<title>T</title><body><script src="app.js"></script></body>'
        )
    assert result.markdown == "# T"
    assert len(caught) == 1


@pytest.mark.parametrize("suffix", [".txt", ".log", ".md", ".html"])
def test_triage_empty_file_is_corrupt(tmp_path, suffix):
    path = tmp_path / ("input" + suffix)
    path.write_bytes(b"")
    report = triage(path)
    assert report["coverage"]["state"] == "none"
    assert [i["code"] for i in report["issues"]] == ["corrupt"]
