"""The HTTP layer: error messages, identification and pacing."""

import io
import time
import urllib.error

import pytest

from bryton_route import web


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr(web, "MIN_INTERVAL_S", 0.0)


def answer(body, seen=None):
    def urlopen(request, timeout):
        if seen is not None:
            seen.append(request)
        return io.BytesIO(body)

    return urlopen


def test_a_refusal_carries_the_server_message(monkeypatch):
    def refuse(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b"target island detected for section 25"),
        )

    monkeypatch.setattr(web.urllib.request, "urlopen", refuse)
    with pytest.raises(web.OnlineError, match="brouter.de a répondu HTTP 400 : target island"):
        web.get("https://brouter.de/brouter?lonlats=4.8,45.7|4.9,45.8")


def test_no_network_points_to_offline_mode(monkeypatch):
    def unreachable(request, timeout):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(web.urllib.request, "urlopen", unreachable)
    with pytest.raises(web.OnlineError, match="--offline"):
        web.post_json("https://valhalla1.openstreetmap.de/trace_route", {})


def test_requests_identify_the_application(monkeypatch):
    seen = []
    monkeypatch.setattr(web.urllib.request, "urlopen", answer(b'{"ok": true}', seen))
    assert web.post_json("https://valhalla1.openstreetmap.de/status", {"a": 1}) == {"ok": True}
    assert seen[0].get_header("User-agent").startswith("bryton-route/")
    assert seen[0].get_header("Content-type") == "application/json"


def test_an_unreadable_reply_is_an_error(monkeypatch):
    monkeypatch.setattr(web.urllib.request, "urlopen", answer(b"<html>busy</html>"))
    with pytest.raises(web.OnlineError, match="illisible"):
        web.post_json("https://valhalla1.openstreetmap.de/trace_route", {})


def test_requests_to_one_host_are_spaced(monkeypatch):
    monkeypatch.setattr(web, "MIN_INTERVAL_S", 0.2)
    monkeypatch.setattr(web.urllib.request, "urlopen", answer(b"ok"))
    web.get("https://pacing.example/a")
    started = time.monotonic()
    web.get("https://pacing.example/b")
    assert time.monotonic() - started >= 0.18
