"""Regression: HTTP convert_uri owns the response; convert_response does not."""

import pytest
import requests

from markitdown import MarkItDown
from markitdown._exceptions import ResourceLimitException


class FakeResponse:
    url = "https://example.invalid/fixture.txt"
    headers = {"content-type": "text/plain"}

    def __init__(self, *, status_error=False, read_error=False, chunks=None):
        self.status_error = status_error
        self.read_error = read_error
        self.chunks = [b"fixture text"] if chunks is None else chunks
        self.close_calls = 0

    def raise_for_status(self):
        if self.status_error:
            raise requests.HTTPError("synthetic HTTP 404")

    def iter_content(self, chunk_size=65536):
        yield from self.chunks
        if self.read_error:
            raise requests.ConnectionError("synthetic broken stream")

    def close(self):
        self.close_calls += 1


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


@pytest.mark.parametrize(
    "status_error,read_error,expected_error",
    [
        (False, False, None),
        (True, False, requests.HTTPError),
        (False, True, requests.ConnectionError),
    ],
)
def test_convert_uri_always_closes_owned_http_response(
    status_error, read_error, expected_error
):
    response = FakeResponse(status_error=status_error, read_error=read_error)
    session = FakeSession(response)
    converter = MarkItDown(requests_session=session)

    if expected_error is None:
        result = converter.convert_uri(response.url)
        assert "fixture text" in result.markdown
    else:
        with pytest.raises(expected_error):
            converter.convert_uri(response.url)

    assert response.close_calls == 1
    assert session.calls == [(response.url, {"stream": True, "timeout": (10.0, 30.0)})]


def test_direct_convert_response_remains_caller_owned():
    response = FakeResponse()
    converter = MarkItDown()
    result = converter.convert_response(response)

    assert "fixture text" in result.markdown
    assert response.close_calls == 0


class NonSeekableBytes:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def seekable(self):
        return False

    def read(self, size=-1):
        if size < 0:
            size = len(self.data) - self.pos
        chunk = self.data[self.pos : self.pos + size]
        self.pos += len(chunk)
        return chunk


def test_http_response_budget_rejects_oversize_and_closes_owned_response():
    response = FakeResponse(chunks=[b"first", b"abcdefghij"])
    session = FakeSession(response)
    with pytest.raises(ResourceLimitException, match="HTTP response byte"):
        MarkItDown(requests_session=session).convert_uri(response.url, http_max_bytes=8)
    assert response.close_calls == 1


def test_direct_response_budget_preserves_caller_ownership():
    response = FakeResponse(chunks=[b"abcdefghijk"])
    with pytest.raises(ResourceLimitException, match="HTTP response byte"):
        MarkItDown().convert_response(response, http_max_bytes=8)
    assert response.close_calls == 0


@pytest.mark.parametrize("bad", [0, -1, True, 2.5, "10"])
def test_http_rejects_invalid_byte_budget(bad):
    response = FakeResponse()
    with pytest.raises(ResourceLimitException, match="http_max_bytes"):
        MarkItDown().convert_response(response, http_max_bytes=bad)
    assert response.close_calls == 0


@pytest.mark.parametrize("bad", [0, -2, True, "10", (1,), (0, 2), (1, 0), (1, 2, 3)])
def test_http_rejects_invalid_timeout_before_network(bad):
    response = FakeResponse()
    session = FakeSession(response)
    with pytest.raises(ValueError, match="http_timeout"):
        MarkItDown(requests_session=session).convert_uri(response.url, http_timeout=bad)
    assert session.calls == []


def test_http_explicit_timeout_and_accepted_small_body():
    response = FakeResponse(chunks=[b"hello"])
    session = FakeSession(response)
    result = MarkItDown(requests_session=session).convert_uri(
        response.url, http_max_bytes=5, http_timeout=(2, 5)
    )
    assert "hello" in result.markdown
    assert response.close_calls == 1
    assert session.calls == [(response.url, {"stream": True, "timeout": (2, 5)})]


def test_non_seekable_stream_budget_succeeds_and_preserves_text():
    result = MarkItDown().convert_stream(
        NonSeekableBytes(b"small data"), file_extension=".txt", stream_max_bytes=10
    )
    assert "small data" in result.markdown


def test_non_seekable_stream_budget_rejects_before_conversion():
    with pytest.raises(ResourceLimitException, match="non-seekable stream"):
        MarkItDown().convert_stream(
            NonSeekableBytes(b"more than ten bytes"),
            file_extension=".txt",
            stream_max_bytes=10,
        )


@pytest.mark.parametrize("bad", [0, -1, True, "10"])
def test_non_seekable_stream_budget_rejects_invalid_options(bad):
    with pytest.raises(ResourceLimitException, match="stream_max_bytes"):
        MarkItDown().convert_stream(
            NonSeekableBytes(b"hi"), file_extension=".txt", stream_max_bytes=bad
        )



def _zip_http_response(names):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"fixture hello")
    response = FakeResponse(chunks=[buffer.getvalue()])
    response.url = "https://example.invalid/archive.zip"
    response.headers = {"content-type": "application/zip"}
    return response


def test_http_to_zip_path_accepts_both_budgets_and_closes_response():
    response = _zip_http_response(["note.txt"])
    result = MarkItDown(requests_session=FakeSession(response)).convert_uri(
        response.url,
        http_max_bytes=4096,
        zip_max_entries=1,
        zip_max_directory_bytes=256,
    )
    assert "fixture hello" in result.markdown
    assert response.close_calls == 1


def test_http_to_zip_preflight_rejects_entries_and_closes_response():
    response = _zip_http_response(["note.txt", "other.txt"])
    converter = MarkItDown(requests_session=FakeSession(response))
    with pytest.raises(ResourceLimitException, match="ZIP entries"):
        converter.convert_uri(
            response.url,
            http_max_bytes=4096,
            zip_max_entries=1,
            zip_max_directory_bytes=256,
        )
    assert response.close_calls == 1
