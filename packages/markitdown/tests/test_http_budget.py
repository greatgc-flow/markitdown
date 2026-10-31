"""Bounded HTTP conversion: deterministic no-network resource-policy tests."""

import pytest

from markitdown import (
    HTTPResponseLimitExceeded,
    MarkItDown,
    ZipLimitExceeded,
    ZipLimits,
)


class _FakeResponse:
    def __init__(self, chunks, *, headers=None):
        self.url = "https://example.test/doc.txt"
        self.headers = headers or {"content-type": "text/plain"}
        self.chunks = list(chunks)
        self.closed = False
        self.read_calls = 0

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        for chunk in self.chunks:
            self.read_calls += 1
            yield chunk

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, uri, **kwargs):
        self.calls.append((uri, dict(kwargs)))
        return self.response


def _converter(chunks, headers=None):
    response = _FakeResponse(chunks, headers=headers)
    session = _FakeSession(response)
    converter = MarkItDown(requests_session=session)
    return converter, session, response


def test_http_budget_accepts_exact_size_and_forwards_timeout():
    conv, session, response = _converter([b"hello", b" world"])
    result = conv.convert_uri(response.url, http_max_bytes=11, http_timeout_seconds=3.5)
    assert "hello world" in result.markdown
    assert session.calls == [(response.url, {"stream": True, "timeout": 3.5})]
    assert response.read_calls == 2


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/plain"},
        {"content-type": "text/plain", "content-length": "1"},
        {"content-type": "text/plain", "content-length": "untrusted"},
    ],
)
def test_http_budget_fails_closed_on_real_bytes_and_closes_response(headers):
    conv, _, response = _converter([b"abcd", b"efgh"], headers=headers)
    with pytest.raises(HTTPResponseLimitExceeded, match="byte limit"):
        conv.convert_uri(response.url, http_max_bytes=5)
    assert response.closed
    assert response.read_calls == 2


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "100", [], {}])
def test_http_budget_invalid_limit_rejected_before_fetch(limit):
    conv, session, response = _converter([b"hello"])
    with pytest.raises(ValueError, match="http_max_bytes"):
        conv.convert_uri(response.url, http_max_bytes=limit)
    assert session.calls == []
    assert response.read_calls == 0


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "30"])
def test_http_timeout_invalid_value_rejected_before_fetch(value):
    conv, session, _ = _converter([b"hello"])
    with pytest.raises(ValueError, match="http_timeout_seconds"):
        conv.convert_uri("https://example.test/doc.txt", http_timeout_seconds=value)
    assert session.calls == []


@pytest.mark.parametrize("uri", ["file:///tmp/a.txt", "data:text/plain,hi"])
def test_http_limits_not_silently_ignored_for_other_uri_schemes(uri):
    conv, session, _ = _converter([b"ignored"])
    with pytest.raises(ValueError, match="http: or https:"):
        conv.convert_uri(uri, http_max_bytes=4)
    assert session.calls == []


def test_direct_response_api_enforces_same_bytes_gate():
    conv, session, response = _converter([b"abcdef"])
    with pytest.raises(HTTPResponseLimitExceeded):
        conv.convert_response(response, http_max_bytes=5)
    assert session.calls == []


def test_legacy_http_call_does_not_force_new_options():
    conv, session, response = _converter([b"hello"])
    assert "hello" in conv.convert_uri(response.url).markdown
    assert session.calls == [(response.url, {"stream": True})]


@pytest.mark.parametrize("method", ["convert_local", "convert_stream"])
def test_http_budget_never_claimed_for_local_content(method, tmp_path):
    conv, session, _ = _converter([b"ignored"])
    path = tmp_path / "local.txt"
    path.write_text("hello", encoding="utf-8")
    if method == "convert_local":
        call = lambda: conv.convert_local(path, http_max_bytes=5)
    else:
        import io

        call = lambda: conv.convert_stream(io.BytesIO(b"hello"), http_max_bytes=5)
    with pytest.raises(ValueError, match="HTTP resource limits"):
        call()
    assert session.calls == []


def test_http_timeout_is_not_silently_ignored_for_existing_response():
    conv, session, response = _converter([b"hello"])
    with pytest.raises(ValueError, match="retroactively"):
        conv.convert_response(response, http_timeout_seconds=10)
    assert session.calls == []
    assert response.read_calls == 0


def _zip_over_http():
    """Generate one ZIP response without performing any external request."""
    import io
    import zipfile

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as writer:
        writer.writestr("notes.txt", b"hello")
    payload = archive.getvalue()
    conv, session, response = _converter(
        [payload], headers={"content-type": "application/zip"}
    )
    response.url = "https://example.test/notes.zip"
    return conv, session, response, payload


def test_http_then_zip_rejects_http_budget_before_archive_parsing():
    conv, session, response, payload = _zip_over_http()
    with pytest.raises(HTTPResponseLimitExceeded):
        conv.convert_uri(
            response.url,
            http_max_bytes=len(payload) - 1,
            zip_limits=ZipLimits(max_member_bytes=4),
        )
    assert response.closed
    assert session.calls == [(response.url, {"stream": True})]


def test_http_then_zip_rejects_zip_budget_after_accepted_download():
    conv, _, response, payload = _zip_over_http()
    with pytest.raises(ZipLimitExceeded, match="member"):
        conv.convert_uri(
            response.url,
            http_max_bytes=len(payload),
            zip_limits=ZipLimits(max_member_bytes=4),
        )
    assert response.closed


def test_http_then_zip_accepts_both_budgets():
    conv, _, response, payload = _zip_over_http()
    result = conv.convert_uri(
        response.url, http_max_bytes=len(payload), zip_limits=ZipLimits()
    )
    assert "## File: notes.txt" in result.markdown
    assert "hello" in result.markdown
