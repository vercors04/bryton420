"""From a GPX document to a Route: geometry, cue placement, warnings.

A GPX carries geometry in one place and instructions in another, and each
planner arranges them differently. This module answers the two questions
separately -- where is the geometry, where are the cues -- so most planners
need no special code.

Invariant: a cue sits on a geometry vertex and refers to it by index. It never
becomes a point of its own, so it cannot drag in a missing elevation or cut a
corner.
"""

import os
from bisect import bisect_left
from itertools import accumulate
from math import cos, radians

from . import cues as vocab
from . import geo, gpx
from .model import Cue, CueType, Route, RoutePoint


class UnsupportedGpx(ValueError):
    """The file parses, but cannot become a navigable route."""


#: Mean spacing above which the Rider visibly cuts corners between vertices.
SPARSE_SPACING_M = 60.0
#: Elevations are averaged over this distance either side of each point when
#: that divides the ascent by more than NOISY_ASCENT_RATIO: see _denoise_elevations.
ELE_WINDOW_M = 50.0
NOISY_ASCENT_RATIO = 1.5
#: Tracks (or routes) of one GPX are joined when each starts within this
#: distance of where the previous one ends: the legs of one ride.
JOIN_M = 50.0
#: Snapping radii, tried in turn; planners put cues exactly on a vertex.
SNAP_RADII_M = (2.0, 25.0)
#: How far along the route a first visit is followed, to catch a track that
#: leaves a vertex and comes straight back through it.
REVISIT_WINDOW_M = 30.0
#: Bearing change thresholds used to turn a roundabout into a direction,
#: calibrated on the reference file (see doc/format.md).
SLIGHT_DEG, NORMAL_DEG, SHARP_DEG = 20.0, 45.0, 110.0
ANGLE_WINDOW_M = 40.0
#: A roundabout's ring is where the route turns by at least RING_TURN_DEG every
#: RING_STEP_M -- any ring up to about 80 m across -- looked for up to
#: RING_MAX_M past the entry. The way out is the RING_WAY_OUT_M that follow it.
RING_STEP_M, RING_TURN_DEG, RING_MAX_M, RING_WAY_OUT_M = 5.0, 7.0, 150.0, 30.0

_SOURCES = (
    ("plotaroute", "plotaroute"),
    ("openrouteservice", "openrouteservice"),
    ("togpx", "openrouteservice"),
    ("bikerouter", "bikerouter"),
    ("osmandrouter", "brouter"),
    ("brouter", "brouter"),
    ("osmand", "osmand"),
    ("graphhopper", "graphhopper"),
    ("komoot", "komoot"),
    ("strava", "strava"),
    ("ridewithgps", "ridewithgps"),
    ("garmin", "garmin"),
)

NO_CUES = (
    "ce GPX ({source}) contient un tracé mais aucune instruction de navigation : "
    "le Rider n'afficherait qu'une ligne.\n"
    "Sans --offline, l'outil calcule les instructions en ligne à partir de la trace.\n"
    "Sur brouter.de ou bikerouter.de, tu peux aussi régler « turnInstructionMode » sur "
    "« osmand-style » avant d'exporter (voir le README).\n"
    "Option --allow-no-cues pour convertir la ligne seule malgré tout."
)


ONLY_ENDS = (
    "les instructions du GPX se limitent au départ et à l'arrivée : "
    "virages calculés en ligne (--offline pour s'en tenir au GPX)"
)


def has_turns(route):
    """Whether the route's cues hold a manoeuvre. Waypoints named "Départ" and
    "Arrivée" read as cues too, yet leave the rider with nothing to follow."""
    return any(c.type not in (CueType.STRAIGHT, CueType.END_OF_ROUTE) for c in route.cues)


def detect_source(doc):
    haystack = f"{doc.creator} {doc.desc}".lower()
    matches = (name for needle, name in _SOURCES if needle in haystack)
    return next(matches, doc.creator or "inconnue")


def read_gpx_file(path, allow_no_cues=False):
    with open(path, "rb") as handle:
        data = handle.read()
    name = os.path.splitext(os.path.basename(path))[0]
    return read_gpx(data, name=name, allow_no_cues=allow_no_cues)


def read_gpx(data, name="", allow_no_cues=False):
    """Parse GPX bytes into a validated :class:`Route`."""
    doc = gpx.parse(data)
    source = detect_source(doc)
    warnings = []

    raw, kind = _geometry(doc, warnings)
    points, remap = _dedupe(raw)
    if len(points) < 2:
        raise UnsupportedGpx("le tracé de ce GPX se réduit à un seul point")
    points, missing = _fill_elevations(points)
    if missing == len(points):
        warnings.append("aucune altitude dans le fichier : profil plat à 0 m sur le Rider")
    elif missing:
        warnings.append(f"altitude interpolée pour {missing} point(s)")
    if missing < len(points):
        points = _denoise_elevations(points, warnings)

    placed = _place_cues(doc, raw, kind, remap, points, source == "openrouteservice", warnings)
    if not placed:
        if not allow_no_cues:
            raise UnsupportedGpx(NO_CUES.format(source=source))

    cues = resolve_cues(placed, points, warnings)

    total = sum(geo.path_lengths(points))
    spacing = total / (len(points) - 1)
    if spacing > SPARSE_SPACING_M:
        warnings.append(
            f"un point tous les {spacing:.0f} m en moyenne : le Rider relie les points "
            "en ligne droite, le tracé coupera les virages"
        )
        if kind == "route":
            warnings.append(
                "le tracé vient de points <rte> seuls ; réexporte avec la trace "
                "complète (PlotARoute : l'export « waypoints + trace »)"
            )

    route = Route(points, cues, name=name or doc.name, source=source, warnings=warnings)
    route.validate()
    return route


def _geometry(doc, warnings):
    """The tracks, else the routes, with their kind: joined when each one
    starts where the previous one ends, the longest alone otherwise."""
    for candidates, kind in ((doc.tracks, "track"), (doc.routes, "route")):
        candidates = [c for c in candidates if len(c) >= 2]
        if not candidates:
            continue
        if all(_gap_m(a[-1], b[0]) <= JOIN_M for a, b in zip(candidates, candidates[1:])):
            return [p for c in candidates for p in c], kind
        best = max(candidates, key=lambda c: sum(geo.path_lengths(c)))
        warnings.append(
            f"{len(candidates)} tracés séparés dans ce GPX : seul le plus long, "
            f"de {sum(geo.path_lengths(best)) / 1000:.1f} km, est converti".replace(".", ",")
        )
        return best, kind
    raise UnsupportedGpx("ce GPX ne contient aucun tracé (<trk> ou <rte>)")


def _gap_m(a, b):
    return geo.geodesic_m(a.lat, a.lon, b.lat, b.lon)


def _dedupe(raw):
    """Drop repeated consecutive coordinates. Returns (points, raw -> point index)."""
    points, remap = [], []
    for p in raw:
        if not points or (p.lat, p.lon) != (points[-1].lat, points[-1].lon):
            points.append(RoutePoint(p.lat, p.lon, p.ele))
        remap.append(len(points) - 1)
    return points, remap


def _fill_elevations(points):
    """Interpolate missing elevations. Returns (points, how many were missing)."""
    known = [i for i, p in enumerate(points) if p.ele is not None]
    missing = len(points) - len(known)
    if not known or not missing:
        return points, missing
    out = list(points)

    def set_ele(i, ele):
        out[i] = RoutePoint(points[i].lat, points[i].lon, ele)

    for i in range(known[0]):
        set_ele(i, points[known[0]].ele)
    for i in range(known[-1] + 1, len(points)):
        set_ele(i, points[known[-1]].ele)
    for a, b in zip(known, known[1:]):
        for i in range(a + 1, b):
            set_ele(i, points[a].ele + (points[b].ele - points[a].ele) * (i - a) / (b - a))
    return out, missing


def _ascent(points):
    return sum(max(b.ele - a.ele, 0.0) for a, b in zip(points, points[1:]))


def _denoise_elevations(points, warnings):
    """Average a recorded track's elevations over ELE_WINDOW_M either side.

    Applied only when that takes off more than a third of the ascent: elevations
    from a planner's terrain model lose 1 to 17 % (reference file, BRouter and
    OpenRouteService exports), a GPS or barometer read every second several
    times their true climb.
    """
    along = [0.0, *accumulate(geo.path_lengths(points))]
    smoothed, total, low, high = [], 0.0, 0, 0
    for i, point in enumerate(points):
        while along[low] < along[i] - ELE_WINDOW_M:
            total -= points[low].ele
            low += 1
        while high < len(points) and along[high] <= along[i] + ELE_WINDOW_M:
            total += points[high].ele
            high += 1
        smoothed.append(RoutePoint(point.lat, point.lon, total / (high - low)))
    raw, calm = _ascent(points), _ascent(smoothed)
    if raw <= NOISY_ASCENT_RATIO * calm:
        return points
    warnings.append(
        f"altitudes bruitées (trace enregistrée ?) lissées : dénivelé +{calm:.0f} m "
        f"au lieu de +{raw:.0f} m"
    )
    return smoothed


def _hints(items, ors_types):
    return [(item, hint) for item in items if (hint := vocab.read_hint(item, ors_types))]


def _place_cues(doc, raw, kind, remap, points, ors_types, warnings):
    """Find the cues and the vertex each one sits on, as (index, Hint) pairs."""
    if kind == "track":
        # BRouter's OsmAnd style states each cue's track index outright.
        for rte in doc.routes:
            hints = _hints(rte, ors_types)
            offsets = [_int(point.extensions.get("offset")) for point, _ in hints]
            if hints and all(o is not None and 0 <= o < len(raw) for o in offsets):
                return [(remap[o], hint) for o, (_, hint) in zip(offsets, hints)]

    # Cues held apart from the geometry: waypoints, or a route next to a track.
    for items in [doc.waypoints] + (doc.routes if kind == "track" else []):
        if hints := _hints(items, ors_types):
            indices, distances = snap([point for point, _ in hints], points)
            far = [d for d in distances if d > SNAP_RADII_M[-1]]
            if far:
                warnings.append(
                    f"{len(far)} instruction(s) à plus de {SNAP_RADII_M[-1]:.0f} m du "
                    f"tracé (pire : {max(far):.0f} m) ; waypoints et tracé ne décrivent "
                    "peut-être pas le même itinéraire"
                )
            return [(i, hint) for i, (_, hint) in zip(indices, hints) if i is not None]

    # Cues on the geometry itself: OpenRouteService directions, BRouter's Locus
    # style, PlotARoute's route export. OpenRouteService repeats the current
    # instruction on every point of a step, so only a step's first point counts.
    placed = []
    step = None
    for i, point in enumerate(raw):
        this_step = point.extensions.get("step")
        if this_step is not None:
            if this_step == step:
                continue
            step = this_step
        if hint := vocab.read_hint(point, ors_types):
            placed.append((remap[i], hint))
    return placed


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _approx_sq(a, b):
    """Squared planar distance in m², good enough to compare nearby points."""
    dy = (b.lat - a.lat) * 110_540.0
    dx = (b.lon - a.lon) * 111_320.0 * cos(radians((a.lat + b.lat) * 0.5))
    return dx * dx + dy * dy


def _first_pass(target, points, start, radius):
    """The vertex a cue belongs to, on the first visit within ``radius`` of it.

    The visit is followed for REVISIT_WINDOW_M along the route, so a track that
    touches a junction, wanders a few metres and comes back through it resolves
    to the closest vertex, the later one on a tie: that is where the manoeuvre
    is made, and where BRouter puts it.
    """
    limit = radius * radius
    first = next(
        (i for i in range(start, len(points)) if _approx_sq(target, points[i]) <= limit), None
    )
    if first is None:
        return None
    best, best_sq = first, _approx_sq(target, points[first])
    walked = 0.0
    for i in range(first + 1, len(points)):
        walked += _approx_sq(points[i - 1], points[i]) ** 0.5
        if walked > REVISIT_WINDOW_M:
            break
        if (sq := _approx_sq(target, points[i])) <= best_sq:
            best, best_sq = i, sq
    return best


def snap(targets, points):
    """Place each target on a vertex, in route order.

    Taking the *first* pass that comes close, rather than the nearest vertex
    overall, is what keeps loops and out-and-back rides right: the start of a
    loop is also its end, and on the way back every junction is revisited.
    Returns (indices, distances in metres); an index is None when the route
    has run out of vertices.
    """
    indices, distances = [], []
    start = 0
    for target in targets:
        if start >= len(points):
            indices.append(None)
            distances.append(0.0)
            continue
        index = next(
            (i for r in SNAP_RADII_M if (i := _first_pass(target, points, start, r)) is not None),
            None,
        )
        if index is None:
            index = min(range(start, len(points)), key=lambda i: _approx_sq(target, points[i]))
        indices.append(index)
        nearest = points[index]
        distances.append(geo.geodesic_m(target.lat, target.lon, nearest.lat, nearest.lon))
        start = index + 1
    return indices, distances


def _segment_m(points, i):
    """Length in metres of the segment from vertex i to vertex i + 1."""
    a, b = points[i], points[i + 1]
    return geo.geodesic_m(a.lat, a.lon, b.lat, b.lon)


def turn_angle(points, index, window_m=ANGLE_WINDOW_M):
    """Signed bearing change at a vertex, over ``window_m`` either side.

    Negative is left, positive right. None at either end of the route.
    """
    before, walked = index, 0.0
    while before > 0 and walked < window_m:
        before -= 1
        walked += _segment_m(points, before)
    after, walked = index, 0.0
    while after < len(points) - 1 and walked < window_m:
        walked += _segment_m(points, after)
        after += 1
    if before == index or after == index:
        return None
    here = points[index]
    incoming = geo.bearing_deg(points[before].lat, points[before].lon, here.lat, here.lon)
    outgoing = geo.bearing_deg(here.lat, here.lon, points[after].lat, points[after].lon)
    return (outgoing - incoming + 180.0) % 360.0 - 180.0


def classify_angle(angle):
    """A confirmed cue code for a turn of ``angle`` degrees, negative to the left."""
    if angle is None or abs(angle) < SLIGHT_DEG:
        return CueType.STRAIGHT
    right = angle > 0
    if abs(angle) < NORMAL_DEG:
        return CueType.SLIGHT_RIGHT if right else CueType.SLIGHT_LEFT
    if abs(angle) < SHARP_DEG:
        return CueType.RIGHT if right else CueType.LEFT
    return CueType.SHARP_RIGHT if right else CueType.SHARP_LEFT


def direction(points, index, angle=None):
    """A confirmed cue code for the turn at a vertex; the angle is measured if None."""
    return classify_angle(turn_angle(points, index) if angle is None else angle)


def _position(points, along, metres):
    """(lat, lon) of the point ``metres`` along the route, clamped to its ends."""
    metres = max(0.0, min(metres, along[-1]))
    i = min(max(bisect_left(along, metres), 1), len(points) - 1)
    a, b = points[i - 1], points[i]
    span = along[i] - along[i - 1]
    t = 0.0 if span == 0 else (metres - along[i - 1]) / span
    return a.lat + t * (b.lat - a.lat), a.lon + t * (b.lon - a.lon)


def _heading(points, along, start_m, end_m):
    return geo.bearing_deg(*_position(points, along, start_m), *_position(points, along, end_m))


def _change(before, after):
    return (after - before + 180.0) % 360.0 - 180.0


def ring_length(points, index, along):
    """Metres from a roundabout's entry at vertex ``index`` to the end of its
    ring: the stretch where the route keeps turning by RING_TURN_DEG or more
    every RING_STEP_M. 0 when no such stretch follows the entry."""
    entry = along[index]
    ring, calm, d = 0.0, 0.0, RING_STEP_M
    previous = _heading(points, along, entry, entry + RING_STEP_M)
    while d < RING_MAX_M and entry + d < along[-1]:
        heading = _heading(points, along, entry + d, entry + d + RING_STEP_M)
        if abs(_change(previous, heading)) >= RING_TURN_DEG:
            ring, calm = d + RING_STEP_M, 0.0
        else:
            calm += RING_STEP_M
            if calm >= 3 * RING_STEP_M and (ring or d >= 30.0):
                break
        previous = heading
        d += RING_STEP_M
    return ring


def roundabout_angle(points, index, along=None):
    """Signed bearing change through a roundabout entered at vertex ``index``,
    from the way in to the way out past the ring. None at either end of the route."""
    if along is None:
        along = [0.0, *accumulate(geo.path_lengths(points))]
    entry = along[index]
    if entry == 0.0 or entry >= along[-1]:
        return None
    way_out = entry + ring_length(points, index, along) + RING_STEP_M
    if way_out >= along[-1]:
        return turn_angle(points, index)
    return _change(
        _heading(points, along, entry - ANGLE_WINDOW_M, entry),
        _heading(points, along, way_out, way_out + RING_WAY_OUT_M),
    )


def resolve_cues(placed, points, warnings):
    """Turn (index, Hint) pairs into Cues with confirmed codes only."""
    cues = []
    derived = 0
    along = None
    for index, hint in sorted(placed, key=lambda pair: pair[0]):
        kind, label = hint.kind, hint.label
        if kind is vocab.ROUNDABOUT:
            # Planners' own angles are ignored: BRouter's pointed left on all six
            # roundabouts measured, straight-on exits included, and Valhalla's
            # were wrong on all three of its own.
            if along is None:
                along = [0.0, *accumulate(geo.path_lengths(points))]
            kind = classify_angle(roundabout_angle(points, index, along))
            derived += 1
        elif kind is vocab.UTURN:
            kind = direction(points, index, hint.angle)
            derived += 1
        if hint.exit:
            label = f"({hint.exit}) {label}".strip()
        if cues and cues[-1].index == index:
            # Two instructions on one vertex, typically "arrive" then "depart"
            # at a via point: the later one says what to do next.
            cues[-1] = Cue(index, kind, label)
        else:
            cues.append(Cue(index, kind, label))

    if derived:
        warnings.append(
            f"direction de {derived} rond(s)-point(s) ou demi-tour(s) déduite de "
            "la forme du tracé (pas de code Bryton confirmé pour eux)"
        )
    # A via point can produce "arrive" mid-route; the Rider ends the route on it.
    ends = [i for i, cue in enumerate(cues) if cue.type == CueType.END_OF_ROUTE]
    for i in ends[:-1]:
        cues[i] = Cue(cues[i].index, CueType.STRAIGHT, cues[i].label)
    if len(ends) > 1:
        warnings.append(f"{len(ends) - 1} arrivée(s) intermédiaire(s) changée(s) en « tout droit »")
    return cues
