"""Turn instructions for a line, by re-routing it with BRouter.

This is brouter-web's "Load Track as Route", with waypoints placed densely
enough that BRouter has no room to choose another street: on the Lyon
benchmark the re-routed line stayed within 2 m of the original and yielded all
48 reference manoeuvres, on the right side for 46.

BRouter can still leave the line where its bicycle profile will not use a road
the line takes: on a 112 km Lyon-Grenoble ride it did so twice, over 1.7 and
1.3 km. Those passages are reported so that ``online.py`` can describe them
with Valhalla instead, and keep BRouter's instructions everywhere else. Where
the line goes somewhere a bicycle cannot go at all, BRouter refuses outright.

A recorded track is another matter: every GPS-noisy waypoint pulled BRouter
onto a sidewalk or a parallel lane and back, 772 cues over the same 11 km of
Lyon. ``online.py`` therefore hands BRouter such a track only once Valhalla has
put it back on the roads, with waypoints spaced by NOISY_TOLERANCE_M so that
the few metres by which the two map-matchers disagree at junctions are not
taken for turns.

BRouter gives no street names; ``online.py`` adds them from Valhalla.
"""

import time
from math import cos, floor, hypot, inf, radians

from . import web
from .cues import Hint
from .model import CueType, RoutePoint
from .route import read_gpx, snap
from .simplify import kept_indices

DEFAULT_SERVER = "https://brouter.de"
PROFILE = "trekking"

#: Waypoints are the vertices left after thinning the line to this tolerance.
WAYPOINT_TOLERANCE_M = 2.0
#: ...or to this one, for a recorded track moved onto the roads by Valhalla.
#: Tuning run on the noisy Lyon line: 87 cues at 2 m, 76 at 5 m, 71 at 10 m,
#: for 42, 42 and 40 of the 48 manoeuvres.
NOISY_TOLERANCE_M = 5.0
#: Waypoints per request. brouter.de accepted 556 in a single 10 KiB URL.
MAX_WAYPOINTS = 400
#: Waypoints shared by consecutive requests. A request cannot report a turn at
#: its own first or last waypoint, so each one only keeps the cues between the
#: middles of its overlaps with its neighbours.
OVERLAP = 20

#: brouter.de sheds load by killing requests ("thread-priority-watchdog"), and
#: such refusals clear within seconds -- unlike a line a bicycle cannot follow,
#: which fails the same way every time and is not retried.
RETRY_DELAYS_S = (10.0, 30.0)
_TRANSIENT = ("watchdog", "injoignable", "HTTP 5")

#: A point of the line further than this from BRouter's route is one BRouter
#: did not follow.
STRAY_M = 50.0
#: Grid cell used to find nearby segments. Distances beyond one cell -- at
#: least 175 m anywhere in France -- may come out as infinite: still strayed.
_CELL_DEG = 0.0025


def _url(server, profile, points, chunk):
    lonlats = "|".join(f"{points[i].lon:.6f},{points[i].lat:.6f}" for i in chunk)
    return (
        f"{server.rstrip('/')}/brouter?lonlats={lonlats}&profile={profile}"
        "&alternativeidx=0&format=gpx&timode=3"
    )


def _fetch(url):
    for delay in (*RETRY_DELAYS_S, None):
        try:
            return web.get(url)
        except web.OnlineError as exc:
            if delay is None or not any(marker in str(exc) for marker in _TRANSIENT):
                raise
            time.sleep(delay)


class _Polyline:
    """A polyline bucketed on a coarse grid, for point-to-line distances."""

    def __init__(self, points):
        self.points = points
        self.kx = 111_320.0 * cos(radians(points[0].lat)) if points else 1.0
        self.cells = {}
        for i in range(len(points) - 1):
            a, b = points[i], points[i + 1]
            for cy in range(self._cell(min(a.lat, b.lat)), self._cell(max(a.lat, b.lat)) + 1):
                for cx in range(self._cell(min(a.lon, b.lon)), self._cell(max(a.lon, b.lon)) + 1):
                    self.cells.setdefault((cy, cx), []).append(i)

    @staticmethod
    def _cell(degrees):
        return floor(degrees / _CELL_DEG)

    def _to_segment(self, p, a, b):
        ky = 110_540.0
        px, py = (p.lon - a.lon) * self.kx, (p.lat - a.lat) * ky
        ex, ey = (b.lon - a.lon) * self.kx, (b.lat - a.lat) * ky
        length_sq = ex * ex + ey * ey
        t = 0.0 if length_sq == 0 else max(0.0, min(1.0, (px * ex + py * ey) / length_sq))
        return hypot(px - t * ex, py - t * ey)

    def distance(self, point):
        """Metres from ``point`` to the polyline; inf when nothing is nearby."""
        cy, cx = self._cell(point.lat), self._cell(point.lon)
        candidates = {
            i
            for dy in (-1, 0, 1)
            for dx in (-1, 0, 1)
            for i in self.cells.get((cy + dy, cx + dx), ())
        }
        return min(
            (self._to_segment(point, self.points[i], self.points[i + 1]) for i in candidates),
            default=inf,
        )


def _strays(line, routed, offset):
    """[first, last, worst] line index ranges further than STRAY_M from ``routed``."""
    polyline = _Polyline(routed)
    ranges = []
    for i, point in enumerate(line):
        distance = polyline.distance(point)
        if distance <= STRAY_M:
            continue
        index = offset + i
        if ranges and index - ranges[-1][1] <= 3:
            ranges[-1][1] = index
            ranges[-1][2] = max(ranges[-1][2], distance)
        else:
            ranges.append([index, index, distance])
    return ranges


def _merge(ranges):
    merged = []
    for first, last, worst in sorted(ranges):
        if merged and first <= merged[-1][1] + 3:
            merged[-1][1] = max(merged[-1][1], last)
            merged[-1][2] = max(merged[-1][2], worst)
        else:
            merged.append([first, last, worst])
    return [tuple(r) for r in merged]


def cues_along(points, server=DEFAULT_SERVER, profile=PROFILE, tolerance_m=None):
    """BRouter's instructions for the line, placed on it.

    Returns ``(placed, strays)``. ``placed`` holds ``(index into points, Hint)``
    in route order. ``strays`` lists ``(first, last, worst)``: index ranges of
    the line BRouter's route did not follow, and how far it went in metres
    (inf when further than the search reaches). ``tolerance_m`` overrides
    WAYPOINT_TOLERANCE_M.
    """
    if tolerance_m is None:
        tolerance_m = WAYPOINT_TOLERANCE_M
    # Waypoints follow the line on the ground; its elevation does not matter.
    keep = kept_indices([RoutePoint(p.lat, p.lon) for p in points], tolerance_m)
    starts = list(range(0, max(len(keep) - OVERLAP, 1), MAX_WAYPOINTS - OVERLAP))
    placed, strays = [], []
    for n, start in enumerate(starts):
        chunk = keep[start : start + MAX_WAYPOINTS]
        first_chunk, last_chunk = n == 0, n == len(starts) - 1
        own_from = 0 if first_chunk else keep[start + OVERLAP // 2]
        own_to = len(points) if last_chunk else keep[start + MAX_WAYPOINTS - OVERLAP // 2]

        piece = read_gpx(_fetch(_url(server, profile, points, chunk)), allow_no_cues=True)
        segment = points[chunk[0] : chunk[-1] + 1]
        for first, last, worst in _strays(segment, piece.points, chunk[0]):
            first, last = max(first, own_from), min(last, own_to - 1)
            if first <= last:
                strays.append([first, last, worst])

        cues = [
            cue
            for cue in piece.cues
            if not (cue.index == 0 and not first_chunk)
            and not (cue.type == CueType.END_OF_ROUTE and not last_chunk)
        ]
        indices, _ = snap([piece.points[cue.index] for cue in cues], segment)
        for cue, index in zip(cues, indices):
            if index is None:
                continue
            line_index = chunk[0] + index
            if own_from <= line_index < own_to:
                placed.append((line_index, Hint(cue.type, cue.label)))
    return placed, _merge(strays)
