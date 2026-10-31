"""Regression: HTTP convert_uri owns the response; convert_response does not."""
import pytest
import requests

from markitdown import MarkItDown


class FakeResponse:
    url = "https://example.invalid/fixture.txt"
    headers = {"content-type": "text/plain"}

    def __init__(self, *, status_error=False, read_error=False):
        self.status_error = status_error
        self.read_error = read_error
        self.close_calls = 0

    def raise_for_status(self):
        if self.status_error:
            raise requests.HTTPError("synthetic HTTP 404")

    def iter_content(self, chunk_size=65536):
        yield b"fixture text"
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
    assert session.calls == [(response.url, {"stream": True})]


def test_direct_convert_response_remains_caller_owned():
    response = FakeResponse()
    converter = MarkItDown()
    result = converter.convert_response(response)

    assert "fixture text" in result.markdown
    assert response.close_calls == 0
