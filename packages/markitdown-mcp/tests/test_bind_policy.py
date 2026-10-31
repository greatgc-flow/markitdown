"""Security regression for the HTTP/SSE binding policy.

These tests do not open a socket, make a request or access local documents.
"""

import sys
from unittest.mock import Mock

import pytest

from markitdown_mcp import __main__ as server


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "192.168.1.5",
        "10.0.0.2",
        "example.com",
        "localhost.example.com",
        "127.0.0.1.evil.example",
    ],
)
def test_remote_bind_rejected_before_server_creation(monkeypatch, capsys, host):
    run = Mock()
    build = Mock()
    monkeypatch.setattr(server.uvicorn, "run", run)
    monkeypatch.setattr(server, "create_starlette_app", build)
    monkeypatch.setattr(sys, "argv", ["markitdown-mcp", "--http", "--host", host])

    with pytest.raises(SystemExit) as error:
        server.main()

    assert error.value.code == 2
    assert "loopback" in capsys.readouterr().err
    run.assert_not_called()
    build.assert_not_called()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_http_uses_production_safe_debug(monkeypatch, host):
    run = Mock()
    build = Mock(return_value=object())
    monkeypatch.setattr(server.uvicorn, "run", run)
    monkeypatch.setattr(server, "create_starlette_app", build)
    monkeypatch.setattr(sys, "argv", ["markitdown-mcp", "--http", "--host", host])

    server.main()

    build.assert_called_once_with(server.mcp, host=host, debug=False)
    assert run.call_args.kwargs["host"] == host


def test_default_http_stays_loopback_and_non_debug(monkeypatch):
    run = Mock()
    build = Mock(return_value=object())
    monkeypatch.setattr(server.uvicorn, "run", run)
    monkeypatch.setattr(server, "create_starlette_app", build)
    monkeypatch.setattr(sys, "argv", ["markitdown-mcp", "--http"])
    server.main()
    build.assert_called_once_with(server.mcp, host="127.0.0.1", debug=False)
    assert run.call_args.kwargs["port"] == 3001
