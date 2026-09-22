from bryton_route.model import Cue, CueType, Route, RoutePoint
from bryton_route.simplify import simplify


def line(count, ele=100.0):
    return [RoutePoint(45.76 + i * 0.0001, 4.83, ele) for i in range(count)]


def test_collinear_points_collapse_to_the_ends_and_the_cues():
    route = Route(line(50), [Cue(10, CueType.RIGHT, "A"), Cue(30, CueType.LEFT, "B")])
    thinned = simplify(route, 2.0)
    assert len(thinned.points) == 4
    assert [(c.type, c.label) for c in thinned.cues] == [(CueType.RIGHT, "A"), (CueType.LEFT, "B")]
    assert [thinned.points[c.index] for c in thinned.cues] == [route.points[10], route.points[30]]


def test_a_real_bend_is_kept():
    points = line(21)
    points[10] = RoutePoint(points[10].lat, 4.8301, 100.0)  # about 8 m off the line
    assert RoutePoint(points[10].lat, 4.8301, 100.0) in simplify(Route(points), 2.0).points


def test_a_bump_in_elevation_is_kept():
    points = line(21)
    points[10] = RoutePoint(points[10].lat, points[10].lon, 110.0)
    thinned = simplify(Route(points), 2.0)
    assert points[10] in thinned.points
    assert len(thinned.points) < len(points)


def test_zero_tolerance_keeps_everything():
    route = Route(line(20))
    assert simplify(route, 0) is route
