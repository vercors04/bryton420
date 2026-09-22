"""HTTP for the online mode: User-Agent, pacing and errors in one place.

The public services used here ask for an identifying User-Agent and at most
one request per second (FOSSGIS terms for valhalla1.openstreetmap.de; the same
courtesy is extended to brouter.de).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "bryton-route/0.4"
TIMEOUT_S = 120
MIN_INTERVAL_S = 1.1

_last_request = {}

#: Called with the host name before each request, to show progress; a long
#: ride takes a dozen requests or more, a minute or two on a phone.
on_request = None


class OnlineError(ValueError):
    """A remote service could not be reached, or refused the request."""


def _host(url):
    return urllib.parse.urlsplit(url).netloc or url


def _pace(host):
    wait = _last_request.get(host, 0.0) + MIN_INTERVAL_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.monotonic()


def _open(request):
    host = _host(request.full_url)
    if on_request is not None:
        on_request(host)
    _pace(host)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace").strip()
        raise OnlineError(f"{host} a répondu HTTP {exc.code} : {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise OnlineError(
            f"{host} injoignable ({exc}) ; sans connexion, utilise --offline"
        ) from exc


def get(url):
    """GET ``url``; the body as bytes."""
    return _open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}))


def post_json(url, body):
    """POST ``body`` as JSON; the decoded JSON reply."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    raw = _open(request)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OnlineError(f"réponse illisible de {_host(url)}") from exc
