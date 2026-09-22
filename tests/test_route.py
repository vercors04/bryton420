"""Real planner exports, end to end."""

from collections import Counter
from math import cos, radians, sin

import pytest

from bryton_route import bryton, convert, geo
from bryton_route import cues as vocab
from bryton_route.cues import Hint
from bryton_route.gpx import GpxPoint
from bryton_route.model import Cue, CueType, RoutePoint
from bryton_route.route import (
    UnsupportedGpx,
    classify_angle,
    direction,
    read_gpx,
    resolve_cues,
    roundabout_angle,
    snap,
)

from conftest import data

C = CueType


def written_cues(route):
    return bryton.decode(bryton.encode(route))[0].cues


# -- BRouter (brouter.de / bikerouter.de) ----------------------------------------


def test_the_three_brouter_styles_give_the_same_file():
    """Locus (cues on track points), OsmAnd (route with offsets), Gpsies (waypoints)."""
    locus, osmand, gpsies = (
        written_cues(read_gpx(data(f"brouter_{style}.gpx")))
        for style in ("locus", "osmand", "gpsies")
    )
    assert locus == osmand == gpsies
    assert Counter(c.type for c in osmand) == {
        C.STRAIGHT: 19, C.RIGHT: 15, C.LEFT: 10, C.SLIGHT_RIGHT: 7,
        C.SLIGHT_LEFT: 5, C.SHARP_RIGHT: 2, C.SHARP_LEFT: 2, C.END_OF_ROUTE: 1,
    }


def test_snapping_waypoints_matches_brouter_offsets_on_an_out_and_back():
    """The ride comes back on the same streets: every junction is passed twice."""
    # The OsmAnd style adds a "start" and a "destination" around the 49 hints.
    by_offset = read_gpx(data("brouter_out_and_back_osmand.gpx")).cues[1:-1]
    by_snapping = read_gpx(data("brouter_out_and_back_gpsies.gpx")).cues
    assert len(by_snapping) == len(by_offset) == 49
    assert [c.index for c in by_snapping] == [c.index for c in by_offset]
    for offset_cue, snapped_cue in zip(by_offset, by_snapping):
        # Gpsies style writes keep-right with the slight-right symbol.
        expected = C.SLIGHT_RIGHT if offset_cue.type == C.KEEP_RIGHT else offset_cue.type
        assert snapped_cue.type == expected


def test_brouter_export_without_instructions_is_refused_with_the_fix():
    doc = data("brouter_osmand.gpx")
    track_only = doc[: doc.index(b"<rte>")] + doc[doc.index(b"</rte>") + 6 :]
    with pytest.raises(UnsupportedGpx, match="turnInstructionMode"):
        read_gpx(track_only)


# -- OpenRouteService --------------------------------------------------------------


def test_ors_directions_give_one_cue_per_step():
    route = read_gpx(data("ors_directions.gpx"))
    assert route.source == "openrouteservice"
    assert len(route.points) > 1900
    # 15 steps, but at the via point "arrive" and "head east" share a vertex:
    # the instruction that says what to do next is the one kept.
    assert len(route.cues) == 14
    assert Cue(981, C.STRAIGHT, "SC-390") in route.cues
    assert not any("<" in c.label for c in route.cues)
    assert [c.type for c in route.cues].count(C.END_OF_ROUTE) == 1
    assert route.cues[-1].type == C.END_OF_ROUTE


VIA_ROUTE = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
<wpt lat="45.7603" lon="4.8300"><desc>Arrive at Place Sathonay</desc></wpt>
<wpt lat="45.7609" lon="4.8300"><desc>FINISH</desc></wpt>
<trk><trkseg>
<trkpt lat="45.7600" lon="4.8300"/><trkpt lat="45.7603" lon="4.8300"/>
<trkpt lat="45.7606" lon="4.8300"/><trkpt lat="45.7609" lon="4.8300"/>
</trkseg></trk></gpx>"""


def test_an_arrival_before_the_end_becomes_straight_on():
    route = read_gpx(VIA_ROUTE)
    assert [(c.index, c.type) for c in route.cues] == [(1, C.STRAIGHT), (3, C.END_OF_ROUTE)]
    assert any("intermédiaire" in w for w in route.warnings)


# -- PlotARoute ---------------------------------------------------------------------


def test_plotaroute_track_with_waypoints():
    route = read_gpx(data("plotaroute_right_turn.gpx"))
    assert [(c.index, c.type, c.label) for c in route.cues] == [
        (0, C.STRAIGHT, "Evandale Road"),
        (3, C.RIGHT, "Loughborough Road"),
        (5, C.END_OF_ROUTE, ""),
    ]
    assert {p.ele for p in route.points} == {8.0}  # no dip to 0 m at the turn


def test_plotaroute_left_turn():
    assert C.LEFT in [c.type for c in read_gpx(data("plotaroute_left_turn.gpx")).cues]


def test_plotaroute_french_route_export():
    route = read_gpx(data("plotaroute_route_fr.gpx"))
    assert [(c.index, c.type, c.label) for c in route.cues] == [
        (0, C.STRAIGHT, "Rue de la République"),
        (1, C.RIGHT, "Rue Étienne Dolet"),
        (2, C.LEFT, "Rue Mercière"),
        (3, C.STRAIGHT, "(2) Avenue Berthelot"),
        (4, C.SLIGHT_RIGHT, "Rue d'Alsace"),
        (5, C.KEEP_RIGHT, "Boulevard des Trois Croix"),
        (6, C.SHARP_LEFT, "Route de Nogent sur Seine"),
        (7, C.END_OF_ROUTE, ""),
    ]
    assert any("coupera les virages" in w for w in route.warnings)
    assert any("<rte>" in w for w in route.warnings)


# -- Refusals and repairs -------------------------------------------------------------

GEOMETRY_ONLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="StravaGPX" xmlns="http://www.topografix.com/GPX/1/1">
<trk><trkseg>
<trkpt lat="45.7600" lon="4.8300"><ele>170</ele></trkpt>
<trkpt lat="45.7603" lon="4.8300"><ele>171</ele></trkpt>
<trkpt lat="45.7606" lon="4.8303"><ele>172</ele></trkpt>
</trkseg></trk></gpx>"""


def test_geometry_only_gpx_is_refused_unless_allowed():
    with pytest.raises(UnsupportedGpx, match="aucune instruction"):
        read_gpx(GEOMETRY_ONLY)
    route = read_gpx(GEOMETRY_ONLY, allow_no_cues=True)
    assert route.cues == [] and route.source == "strava"


def test_gpx_without_geometry_is_refused():
    with pytest.raises(UnsupportedGpx, match="aucun tracé"):
        read_gpx(b'<gpx xmlns="http://www.topografix.com/GPX/1/1"><wpt lat="1" lon="2"/></gpx>')


def test_missing_elevations_are_interpolated():
    route = read_gpx(GEOMETRY_ONLY.replace(b"<ele>171</ele>", b""), allow_no_cues=True)
    assert route.points[1].ele == 171.0
    assert any("interpolée" in w for w in route.warnings)


# -- Placement ---------------------------------------------------------------------------


def square_loop():
    """A 400 m square ridden anticlockwise, starting and ending at the same corner."""
    corners = [(45.7600, 4.8300), (45.7600, 4.8350), (45.7640, 4.8350), (45.7640, 4.8300)]
    points = []
    for (lat1, lon1), (lat2, lon2) in zip(corners, corners[1:] + corners[:1]):
        points += [
            RoutePoint(lat1 + (lat2 - lat1) * k / 10, lon1 + (lon2 - lon1) * k / 10)
            for k in range(10)
        ]
    return points + [RoutePoint(*corners[0])]


def test_loop_start_and_finish_land_on_opposite_ends():
    points = square_loop()
    start = GpxPoint(45.7600, 4.8300)
    turn = GpxPoint(45.7600, 4.8350)
    finish = GpxPoint(45.7600, 4.8300)
    indices, distances = snap([start, turn, finish], points)
    assert indices == [0, 10, len(points) - 1]
    assert max(distances) < 1


def test_a_vertex_revisited_after_a_short_detour_resolves_to_the_later_visit():
    """BRouter tracks sometimes step off a junction and straight back onto it."""
    node = RoutePoint(45.7610, 4.8300)
    points = [
        RoutePoint(45.7600, 4.8300),
        node,
        RoutePoint(45.76105, 4.83005),  # about 7 m away and back
        node,
        RoutePoint(45.7610, 4.8310),
    ]
    assert snap([GpxPoint(node.lat, node.lon)], points)[0] == [3]


def test_direction_from_angle():
    points = square_loop()
    assert direction(points, 10) == C.LEFT  # east then north: a left turn
    assert direction(points, 5) == C.STRAIGHT
    assert direction(points, 0, angle=150) == C.SHARP_RIGHT
    assert direction(points, 0, angle=-30) == C.SLIGHT_LEFT


def roundabout(exit_deg):
    """Eastward into a 30 m roundabout, anticlockwise round it as in France, and
    out on the far side of the exit at ``exit_deg`` (270 south, 360 east, 450
    north). Returns (points, index of the entry)."""
    k_lat, k_lon = 1 / 111_195.0, 1 / (111_195.0 * cos(radians(45.0)))

    def at(x, y):
        return RoutePoint(45.0 + y * k_lat, 4.8 + x * k_lon)

    points = [at(x, 0.0) for x in range(-100, -15, 5)]
    entry = len(points)
    points += [at(15 * cos(radians(a)), 15 * sin(radians(a))) for a in range(180, exit_deg + 1, 10)]
    out_x, out_y = cos(radians(exit_deg)), sin(radians(exit_deg))
    points += [at((15 + d) * out_x, (15 + d) * out_y) for d in range(5, 100, 5)]
    return points, entry


def test_a_roundabout_points_the_way_out_whatever_the_planner_says():
    for exit_deg, expected in ((270, C.RIGHT), (360, C.STRAIGHT), (450, C.LEFT)):
        points, entry = roundabout(exit_deg)
        assert classify_angle(roundabout_angle(points, entry)) == expected
        # BRouter's own angle points left on every roundabout: it is ignored.
        hint = Hint(vocab.ROUNDABOUT, "Rue Sala", exit=(exit_deg - 180) // 90, angle=-122)
        [cue] = resolve_cues([(entry, hint)], points, [])
        assert cue.type == expected and cue.label == f"({(exit_deg - 180) // 90}) Rue Sala"


def gpx_of_tracks(*tracks):
    body = "".join(
        "<trk><trkseg>"
        + "".join(f'<trkpt lat="{lat}" lon="{lon}"><ele>100</ele></trkpt>' for lat, lon in track)
        + "</trkseg></trk>"
        for track in tracks
    )
    return (
        '<?xml version="1.0"?><gpx version="1.1" creator="test" '
        f'xmlns="http://www.topografix.com/GPX/1/1">{body}</gpx>'
    ).encode()


def test_recorded_elevation_noise_is_smoothed_away():
    import random

    rng = random.Random(7)
    # 2 km climbing 40 m, one point every 5 m, barometer-like jitter of 0.5 m.
    track = [(45.0 + i * 0.000045, 4.8, 100 + 40 * i / 400 + rng.gauss(0, 0.5)) for i in range(401)]
    body = "".join(
        f'<trkpt lat="{la}" lon="{lo}"><ele>{e:.1f}</ele></trkpt>' for la, lo, e in track
    )
    gpx = (
        '<?xml version="1.0"?><gpx version="1.1" creator="test" '
        f'xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>{body}</trkseg></trk></gpx>'
    ).encode()
    route = read_gpx(gpx, allow_no_cues=True)
    [warning] = [w for w in route.warnings if "bruitées" in w]
    ascent = bryton.elevation_gain(route.points)[0]
    assert 38 <= ascent <= 60
    assert f"+{ascent:.0f} m" in warning


def test_a_planners_elevations_are_left_alone():
    for name in ("brouter_osmand.gpx", "ors_directions.gpx", "plotaroute_route_fr.gpx"):
        route = read_gpx(data(name))
        assert not any("bruitées" in w for w in route.warnings), name


def test_tracks_that_follow_on_are_joined():
    first = [(45.0 + i * 0.001, 4.8) for i in range(10)]
    second = [(45.009 + i * 0.001, 4.8) for i in range(10)]  # starts where the first ends
    route = read_gpx(gpx_of_tracks(first, second), allow_no_cues=True)
    assert len(route.points) == 19
    assert not any("séparés" in w for w in route.warnings)


def test_separate_tracks_keep_the_longest_and_say_so():
    short = [(45.0 + i * 0.001, 4.8) for i in range(5)]
    long = [(46.0 + i * 0.001, 4.8) for i in range(20)]
    route = read_gpx(gpx_of_tracks(short, long), allow_no_cues=True)
    assert route.points[0].lat == 46.0 and len(route.points) == 20
    assert any("2 tracés séparés" in w and "2,1 km" in w for w in route.warnings)


# -- Library entry point -------------------------------------------------------------------


def test_convert_thins_the_geometry_but_keeps_distance_and_cues():
    raw = data("brouter_osmand.gpx")
    full = read_gpx(raw)
    fit_bytes, written = convert(raw)
    route, summary = bryton.decode(fit_bytes)
    assert len(route.points) < len(full.points)
    assert summary["distance_m"] == round(sum(geo.path_lengths(full.points)))
    assert len(route.cues) == len(written_cues(full))
