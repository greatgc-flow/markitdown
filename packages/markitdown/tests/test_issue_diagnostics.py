import io

import pytest

from markitdown import MarkItDown, UnsupportedFormatException
from markitdown._exceptions import FailedConversionAttempt, FileConversionException


def test_first_cause_on_first_line():
    attempts = [
        FailedConversionAttempt(
            io.BytesIO(), (ValueError, ValueError("bad\r\ninput"), None)
        ),
        FailedConversionAttempt(io.StringIO(), (TypeError, TypeError("second"), None)),
    ]
    message = str(FileConversionException(attempts=attempts))
    assert message.splitlines()[0] == (
        "File conversion failed after 2 attempts: BytesIO: ValueError: bad input"
    )
    assert "StringIO threw TypeError" in message


def test_unsupported_points_to_formats():
    with pytest.raises(
        UnsupportedFormatException, match=r"See markitdown-gc --guide formats\."
    ):
        MarkItDown(enable_builtins=False).convert_stream(io.BytesIO(b"unknown"))


def test_formats_documents_legacy_and_charset():
    from importlib.resources import files

    guide = files("markitdown").joinpath("GUIDE.md").read_text("utf-8")
    assert "Legacy .doc/.ppt/.rtf" in guide
    assert (
        "For known CP949 Korean input use --charset cp949; automatic detection can silently misidentify short legacy-encoded text."
        in guide
    )
