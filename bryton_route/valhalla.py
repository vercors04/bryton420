"""Valhalla map matching: street names for a line, and turns when BRouter fails.

Valhalla snaps a line onto OpenStreetMap without routing again, so it cannot
swap in another street. Two of its services are used:

* ``trace_attributes`` gives, for each point of the line, the way it was
  matched to, its name, and where on that way the point lands. That is where
  the online mode's street names come from, and how a recorded track's GPS
  noise is taken out before BRouter sees it.
* ``trace_route`` describes the matched line as turn-by-turn manoeuvres. It is
  the fallback for turns: on the Lyon benchmark it found 39 of 48 reference
  manoeuvres where BRouter found all 48.

Three behaviours shape the code, all seen in real responses kept under
tests/data/valhalla_*.json. The bicycle profile is used: the pedestrian one
follows sidewalks and got left and right wrong. ``trace_route`` splits a match
into pieces, the first in ``trip`` and the rest in ``alternates``, each with its
own departure and arrival; reading ``trip`` alone drops most routes. And
Valhalla refuses a trace beyond a size limit (16,000 points or 200 km by
default), so long lines go in several overlapping requests.
"""

import re
import statistics
from dataclasses import dataclass
from itertools import accumulate

from . import cues as vocab
from . import geo, web
from .cues import Hint
from .model import CueType, Route, RoutePoint
from .route import classify_angle, resolve_cues, snap

DEFAULT_SERVER = "https://valhalla1.openstreetmap.de"

#: Share of the line that must be matched before we stop warning about it.
MIN_COVERAGE = 0.97
#: A manoeuvre placed further than this from the line is reported.
FAR_M = 30.0
#: A piece starting this far before the previous one ended re-describes ground
#: already covered: an alternative match, not a continuation.
OVERLAP_M = 200.0
#: Beyond this bearing change, a manoeuvre type that contradicts the side the
#: route turns to is overruled by the bearings.
CONTRADICTION_DEG = 45.0
#: Points and length per request, well inside Valhalla's default limits; the
#: public server took 3,413 points over 112 km in one request. Consecutive
#: requests share OVERLAP_POINTS points, each point being read from the
#: request it sits further inside.
MAX_POINTS = 4000
MAX_LENGTH_M = 100_000.0
OVERLAP_POINTS = 50
#: A run of points Valhalla could not match is drawn straight between its
#: matched neighbours when shorter than this; a longer one is left as it is.
MAX_GAP_M = 100.0

# Valhalla maneuver types (DirectionsLeg.Maneuver.Type). Those marked * were
# checked against live responses. A type not listed, or contradicted by the
# bearings, becomes the direction of the bearing change.
_START = {1, 2, 3}  # * 1
_ARRIVE = {4, 5, 6}  # * 4
_ROUNDABOUT_ENTER = 26  # *
_ROUNDABOUT_EXIT = 27  # *
_UTURN = {12, 13}
_TYPES = {
    7: CueType.STRAIGHT,  # becomes
    8: CueType.STRAIGHT,  # * continue
    9: CueType.SLIGHT_RIGHT,  # *
    10: CueType.RIGHT,  # *
    11: CueType.SHARP_RIGHT,  # *
    14: CueType.SHARP_LEFT,
    15: CueType.LEFT,  # *
    16: CueType.SLIGHT_LEFT,  # *
    17: CueType.STRAIGHT,  # ramp straight
    18: CueType.KEEP_RIGHT,  # ramp right
    19: CueType.KEEP_LEFT,  # ramp left
    20: CueType.KEEP_RIGHT,  # exit right
    21: CueType.KEEP_LEFT,  # exit left
    22: CueType.STRAIGHT,  # stay straight
    23: CueType.KEEP_RIGHT,  # * stay right
    24: CueType.KEEP_LEFT,  # * stay left
    25: CueType.STRAIGHT,  # merge
}
_RIGHT = {CueType.SLIGHT_RIGHT, CueType.RIGHT, CueType.SHARP_RIGHT, CueType.KEEP_RIGHT}
_LEFT = {CueType.SLIGHT_LEFT, CueType.LEFT, CueType.SHARP_LEFT, CueType.KEEP_LEFT}

#: How a French instruction names an unnamed way: "dans la piste cyclable".
_UNNAMED = re.compile(
    r".*\b(?:dans|sur|prendre|vers)\s+(?:la |le |les |l')?(.+?)\.?$", re.IGNORECASE
)


def decode_polyline6(encoded):
    """Valhalla's shape encoding: a Google polyline at six decimal places."""
    coords, index, lat, lon = [], 0, 0, 0
    while index < len(encoded):
        values = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        coords.append((lat / 1e6, lon / 1e6))
    return coords


def trace_request(points):
    return {
        "shape": [{"lat": round(p.lat, 6), "lon": round(p.lon, 6)} for p in points],
        "costing": "bicycle",
        "shape_match": "map_snap",
    }


def _url(server, service):
    return server.rstrip("/") + "/" + service


def chunks(points):
    """``(start, end, own_from, own_to)`` for each request a line needs.

    Request n covers ``points[start:end]`` and answers for the points in
    ``range(own_from, own_to)``: the ownership ranges tile the line exactly.
    """
    along = [0.0, *accumulate(geo.path_lengths(points))]
    spans, start = [], 0
    while True:
        end = start + 1
        while end < len(points) and (
            end - start < 2
            or (end - start < MAX_POINTS and along[end] - along[start] <= MAX_LENGTH_M)
        ):
            end += 1
        spans.append((start, end))
        if end >= len(points):
            break
        start = max(end - OVERLAP_POINTS, start + 1)
    result = []
    for n, (start, end) in enumerate(spans):
        own_from = 0 if n == 0 else (start + spans[n - 1][1]) // 2
        own_to = len(points) if n == len(spans) - 1 else (spans[n + 1][0] + end) // 2
        result.append((start, end, own_from, own_to))
    return result


# -- Street names and matched positions -----------------------------------------------

_MATCHED = ("matched", "interpolated")


@dataclass
class Match:
    """What Valhalla made of each point of a line."""

    #: Name of the way the point was matched to, or "".
    names: list
    #: The point moved onto that way; unmatched points stay where they were,
    #: short runs of them are drawn straight between their matched neighbours.
    points: list
    #: Median distance in metres between the line and the ways: 0 for a line
    #: drawn by a planner on OpenStreetMap, a few metres for a recorded track.
    offset_m: float


def _fill_gaps(points, moved, matched, along):
    i = 0
    while i < len(points):
        if matched[i]:
            i += 1
            continue
        j = i
        while j < len(points) and not matched[j]:
            j += 1
        before, after = i - 1, j
        if before >= 0 and after < len(points) and along[after] - along[before] < MAX_GAP_M:
            a, b = moved[before], moved[after]
            for k in range(i, j):
                t = (k - before) / (after - before)
                moved[k] = RoutePoint(
                    a.lat + t * (b.lat - a.lat), a.lon + t * (b.lon - a.lon), points[k].ele
                )
        i = j


def match(points, server=DEFAULT_SERVER):
    """A :class:`Match` for the line, in as many requests as its size needs."""
    names, moved, matched, offsets = [], [], [], []
    for start, end, own_from, own_to in chunks(points):
        body = trace_request(points[start:end])
        body["filters"] = {
            "attributes": [
                "edge.names",
                "matched.edge_index",
                "matched.point",
                "matched.type",
                "matched.distance_from_trace_point",
            ],
            "action": "include",
        }
        reply = web.post_json(_url(server, "trace_attributes"), body)
        edges = reply.get("edges", [])
        answers = reply.get("matched_points", [])
        if len(answers) != end - start:
            raise web.OnlineError(
                f"Valhalla a renvoyé {len(answers)} points calés pour {end - start} envoyés"
            )
        for k in range(own_from, own_to):
            answer, point = answers[k - start], points[k]
            edge = answer.get("edge_index")
            named = edge is not None and 0 <= edge < len(edges)
            names.append(", ".join(edges[edge].get("names", [])) if named else "")
            on_way = answer.get("type") in _MATCHED and "lat" in answer and "lon" in answer
            matched.append(on_way)
            if on_way:
                moved.append(RoutePoint(answer["lat"], answer["lon"], point.ele))
                offsets.append(answer.get("distance_from_trace_point", 0.0))
            else:
                moved.append(point)
    _fill_gaps(points, moved, matched, [0.0, *accumulate(geo.path_lengths(points))])
    return Match(names, moved, statistics.median(offsets) if offsets else 0.0)


# -- Turn-by-turn -----------------------------------------------------------------


def _pieces(reply):
    """[(maneuvers with their (lat, lon), matched length in m)] for each piece."""
    trips = [reply.get("trip")] + [alt.get("trip") for alt in reply.get("alternates", [])]
    pieces = []
    for trip in filter(None, trips):
        maneuvers = []
        for leg in trip.get("legs", []):
            shape = decode_polyline6(leg["shape"])
            maneuvers += [
                (m, shape[min(m["begin_shape_index"], len(shape) - 1)]) for m in leg["maneuvers"]
            ]
        if maneuvers:
            pieces.append((maneuvers, 1000.0 * trip.get("summary", {}).get("length", 0.0)))
    if not pieces:
        raise web.OnlineError("Valhalla n'a renvoyé aucun itinéraire pour cette trace")
    return pieces


def _in_order(pieces, points):
    """Keep the pieces that carry the match forward along the line."""
    along = [0.0, *accumulate(geo.path_lengths(points))]
    kept, previous_end = [], None
    for maneuvers, length in pieces:
        (first,), _ = snap([RoutePoint(*maneuvers[0][1])], points)
        if previous_end is not None and along[first] < along[previous_end] - OVERLAP_M:
            continue
        (last,), _ = snap([RoutePoint(*maneuvers[-1][1])], points[first:])
        kept.append((maneuvers, length))
        previous_end = first + last
    return kept


def _bearing_change(before, after):
    if before is None or after is None:
        return None
    return (after - before + 180.0) % 360.0 - 180.0


def _label(maneuver, from_text=True):
    if maneuver is None:
        return ""
    names = maneuver.get("street_names") or maneuver.get("begin_street_names")
    if names:
        return ", ".join(names)
    if from_text and (found := _UNNAMED.match(maneuver.get("instruction", ""))):
        return found.group(1)
    return ""


def _contradicts(kind, turn):
    if turn is None or abs(turn) < CONTRADICTION_DEG:
        return False
    return (kind in _RIGHT and turn < 0) or (kind in _LEFT and turn > 0)


def _hints(pieces, first=True, last=True):
    """(location, Hint) for every manoeuvre worth a cue, in route order.

    ``first`` and ``last`` say whether the request starts and ends the line:
    a departure or arrival anywhere else is a seam, not an instruction.
    """
    hints = []
    last_piece = len(pieces) - 1
    for p, (maneuvers, _length) in enumerate(pieces):
        for k, (m, (lat, lon)) in enumerate(maneuvers):
            kind = m["type"]
            if (
                (kind in _START and (p > 0 or not first))
                or (kind in _ARRIVE and (p < last_piece or not last))
                or kind == _ROUNDABOUT_EXIT  # folded into the entry
            ):
                continue
            turn = _bearing_change(m.get("bearing_before"), m.get("bearing_after"))
            if kind in _START:
                hint = Hint(CueType.STRAIGHT, _label(m, from_text=False))
            elif kind in _ARRIVE:
                hint = Hint(CueType.END_OF_ROUTE)
            elif kind == _ROUNDABOUT_ENTER:
                exit_ = next(
                    (x for x, _ in maneuvers[k + 1 :] if x["type"] == _ROUNDABOUT_EXIT), None
                )
                hint = Hint(vocab.ROUNDABOUT, _label(exit_), m.get("roundabout_exit_count"))
            elif kind in _UTURN:
                hint = Hint(vocab.UTURN, _label(m), None, turn)
            elif kind in _TYPES and not _contradicts(_TYPES[kind], turn):
                hint = Hint(_TYPES[kind], _label(m))
            else:
                hint = Hint(classify_angle(turn), _label(m))
            hints.append((RoutePoint(lat, lon), hint))
    return hints


def _km(metres):
    return f"{metres / 1000:.1f}".replace(".", ",")


def instructions(route, server=DEFAULT_SERVER):
    """A copy of ``route`` with cues from Valhalla's description of its line.

    The route keeps its own geometry and elevations; the manoeuvres are placed
    onto it.
    """
    warnings = list(route.warnings)
    along = [0.0, *accumulate(geo.path_lengths(route.points))]
    placed, far, matched = [], [], 0.0
    requests = chunks(route.points)
    for n, (start, end, own_from, own_to) in enumerate(requests):
        points = route.points[start:end]
        body = trace_request(points)
        body["directions_options"] = {"language": "fr-FR", "units": "kilometers"}
        pieces = _in_order(_pieces(web.post_json(_url(server, "trace_route"), body)), points)
        hints = _hints(pieces, first=n == 0, last=n == len(requests) - 1)
        indices, distances = snap([location for location, _ in hints], points)
        for (_, hint), i, distance in zip(hints, indices, distances):
            if i is not None and own_from <= start + i < own_to:
                placed.append((start + i, hint))
                if distance > FAR_M:
                    far.append(distance)
        # Credit each request with its matched length, pro rata of the part it owns.
        span = along[end - 1] - along[start]
        owned = along[min(own_to, len(along) - 1)] - along[own_from]
        if span > 0:
            matched += min(sum(length for _, length in pieces), span) * owned / span

    if far:
        warnings.append(
            f"{len(far)} instruction(s) calée(s) à plus de {FAR_M:.0f} m de la trace "
            f"(pire : {max(far):.0f} m)"
        )
    total = along[-1]
    if matched < MIN_COVERAGE * total:
        warnings.append(
            f"Valhalla n'a calé que {_km(matched)} km sur {_km(total)} km : "
            "pas d'instruction sur le reste"
        )

    cues = resolve_cues(placed, route.points, warnings)
    if not cues:
        raise web.OnlineError("Valhalla n'a produit aucune instruction pour cette trace")
    result = Route(
        route.points, cues, name=route.name, source=f"{route.source} + Valhalla", warnings=warnings
    )
    result.validate()
    return result
