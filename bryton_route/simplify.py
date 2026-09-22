"""Geometry thinning that never moves a cue.

BRouter emits a vertex at every map node, several times denser than the files
Bryton's own app writes (about one point every 44 m in the reference). Dropping
vertices that sit within a couple of metres of the line -- horizontally and in
elevation -- is invisible on the Rider, keeps files small, and stays close to
what the device is known to handle.
"""

from math import cos, radians

from .model import Cue, Route

DEFAULT_TOLERANCE_M = 2.0


def _deviation_m(point, start, end):
    """How far ``point`` strays from segment start-end, in metres.

    The larger of the horizontal distance to the segment and the gap to the
    elevation interpolated along it, so climbs keep their profile.
    """
    kx = 111_320.0 * cos(radians(start.lat))
    ky = 110_540.0
    px, py = (point.lon - start.lon) * kx, (point.lat - start.lat) * ky
    ex, ey = (end.lon - start.lon) * kx, (end.lat - start.lat) * ky
    length_sq = ex * ex + ey * ey
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, (px * ex + py * ey) / length_sq))
    deviation = ((px - t * ex) ** 2 + (py - t * ey) ** 2) ** 0.5
    if None not in (point.ele, start.ele, end.ele):
        deviation = max(deviation, abs(point.ele - (start.ele + t * (end.ele - start.ele))))
    return deviation


def kept_indices(points, tolerance_m, pinned=()):
    """Ramer-Douglas-Peucker. Pinned vertices are always kept and never cut across."""
    anchors = sorted({0, len(points) - 1, *pinned})
    keep = set(anchors)
    for first, last in zip(anchors, anchors[1:]):
        stack = [(first, last)]
        while stack:
            a, b = stack.pop()
            if b - a < 2:
                continue
            worst, worst_i = max(
                (_deviation_m(points[i], points[a], points[b]), i) for i in range(a + 1, b)
            )
            if worst > tolerance_m:
                keep.add(worst_i)
                stack += [(a, worst_i), (worst_i, b)]
    return sorted(keep)


def simplify(route, tolerance_m=DEFAULT_TOLERANCE_M):
    """A copy of ``route`` with fewer vertices; every cue kept and re-indexed."""
    if tolerance_m <= 0 or len(route.points) < 3:
        return route
    keep = kept_indices(route.points, tolerance_m, [cue.index for cue in route.cues])
    position = {old: new for new, old in enumerate(keep)}
    return Route(
        [route.points[i] for i in keep],
        [Cue(position[cue.index], cue.type, cue.label) for cue in route.cues],
        name=route.name,
        source=route.source,
        warnings=list(route.warnings),
    )
