"""The Bryton route profile, checked against the files in tests/data/ex_bon."""

import pytest

from bryton_route import bryton, geo
from bryton_route.model import Cue, CueType, Route, RoutePoint

from conftest import REFERENCE_TRACKS


@pytest.fixture(scope="module")
def reference(reference_fit):
    return bryton.decode(reference_fit)


def small_route():
    points = [
        RoutePoint(45.7600, 4.8300, 170.0),
        RoutePoint(45.7610, 4.8300, 172.0),
        RoutePoint(45.7610, 4.8315, 175.0),
        RoutePoint(45.7620, 4.8315, 174.0),
        RoutePoint(45.7630, 4.8315, 180.0),
    ]
    cues = [Cue(1, CueType.RIGHT, "Rue Étienne Dolet"), Cue(2, CueType.LEFT, "Rue Mercière")]
    return Route(points, cues)


# -- What the reference file establishes ---------------------------------------


def test_reference_shape(reference):
    route, summary = reference
    assert summary["profile_version"] == bryton.PROFILE_VERSION
    assert summary["crc_ok"]
    assert len(route.points) == summary["points"] == summary["declared_points"] == 1842
    assert len(route.cues) == summary["declared_cues"] == 96
    assert summary["point_index_entries"] == 1842  # the "alphabet" is a point index
    assert route.cues[0].index == 0
    assert route.cues[-1] == Cue(1841, CueType.END_OF_ROUTE, "")


def test_summary_fields_9_and_10_are_ascent_and_descent(reference):
    route, summary = reference
    ascent, descent = bryton.elevation_gain(route.points)
    assert abs(ascent - summary["ascent_m"]) <= 1  # altitude is stored in 0.2 m steps
    assert abs(descent - summary["descent_m"]) <= 1


def test_cue_distance_runs_to_the_next_cue(reference):
    route, summary = reference
    segments = geo.path_lengths(route.points)
    stored = summary["cue_distances"]
    to_next = bryton.cue_distances(route.cues, segments)
    since_previous = [0] + [
        round(sum(segments[a.index : b.index])) for a, b in zip(route.cues, route.cues[1:])
    ]
    assert max(abs(s - c) for s, c in zip(stored, to_next)) <= 20
    assert max(abs(s - c) for s, c in zip(stored, since_previous)) > 1000


def test_every_cue_code_in_the_confirmed_set_matches_its_measured_turn(reference):
    from bryton_route.route import turn_angle

    route, _ = reference
    signs = {}
    for cue in route.cues:
        angle = turn_angle(route.points, cue.index)
        if isinstance(cue.type, CueType) and angle is not None:
            signs.setdefault(cue.type, []).append(angle)
    median = {t: sorted(a)[len(a) // 2] for t, a in signs.items()}
    for kind in (CueType.RIGHT, CueType.SLIGHT_RIGHT, CueType.SHARP_RIGHT, CueType.KEEP_RIGHT):
        assert median[kind] > 0, kind
    for kind in (CueType.LEFT, CueType.SLIGHT_LEFT, CueType.SHARP_LEFT):
        assert median[kind] < 0, kind


def test_device_tinfo_and_track_transcribe_the_fit(reference):
    route, summary = reference
    tinfo = bryton.read_tinfo((REFERENCE_TRACKS.with_suffix(".tinfo")).read_bytes())
    assert [cue for cue, _ in tinfo] == route.cues
    assert [distance for _, distance in tinfo] == summary["cue_distances"]
    track = bryton.read_track((REFERENCE_TRACKS.with_suffix(".track")).read_bytes())
    assert len(track) == len(route.points)
    for ours, device in zip(route.points, track):
        assert (ours.lat, ours.lon) == (device.lat, device.lon)
        assert abs(ours.ele - device.ele) <= 1


# -- Our encoder against the reference -----------------------------------------


def test_reencoding_the_app_route_gives_the_same_file_structure(reference):
    """Same messages, same layouts, same order, same label field widths."""
    route, summary = reference
    ours = bryton.decode(bryton.encode(route, total_distance=summary["distance_m"]))[1]
    assert ours["definitions"] == summary["definitions"]


def test_reencoding_the_app_route_preserves_every_value(reference, reference_fit):
    route, summary = reference
    data = bryton.encode(route, total_distance=summary["distance_m"])
    assert len(data) == len(reference_fit)
    again, ours = bryton.decode(data)
    assert again.points == route.points
    assert again.cues == route.cues
    same = ("north", "south", "east", "west", "distance_m", "alt_max_m", "alt_min_m", "ascent_m")
    for key in same:
        assert ours[key] == summary[key], key
    assert abs(ours["descent_m"] - summary["descent_m"]) <= 1
    drift = [abs(a - b) for a, b in zip(ours["cue_distances"], summary["cue_distances"])]
    assert max(drift) <= 20


# -- Encoder behaviour ------------------------------------------------------------


def test_cues_are_framed_like_the_app_does():
    data = bryton.encode(small_route())
    route, summary = bryton.decode(data)
    assert [c.type for c in route.cues] == [
        CueType.STRAIGHT, CueType.RIGHT, CueType.LEFT, CueType.END_OF_ROUTE
    ]
    assert route.cues[0].index == 0 and route.cues[-1].index == 4
    assert summary["cue_distances"][-1] == 0


def test_a_cue_on_the_last_point_becomes_the_end_marker():
    route = small_route()
    route.cues.append(Cue(4, CueType.RIGHT, "ignored"))
    decoded, _ = bryton.decode(bryton.encode(route))
    assert decoded.cues[-1] == Cue(4, CueType.END_OF_ROUTE, "")
    assert sum(c.type == CueType.END_OF_ROUTE for c in decoded.cues) == 1


def test_an_arrival_short_of_the_last_point_moves_onto_it():
    route = small_route()
    route.cues.append(Cue(3, CueType.END_OF_ROUTE))
    decoded, _ = bryton.decode(bryton.encode(route))
    assert [c for c in decoded.cues if c.type == CueType.END_OF_ROUTE] == [
        Cue(4, CueType.END_OF_ROUTE, "")
    ]


def test_label_field_widens_only_when_a_name_needs_32_bytes():
    route = small_route()
    before = bryton.decode(bryton.encode(route))[1]["definitions"]
    route.cues[1] = Cue(2, CueType.LEFT, "Quai du Maréchal Joffre, D 1006")  # 32 bytes
    decoded, summary = bryton.decode(bryton.encode(route))
    widths = [d[2][4][1] for d in summary["definitions"] if d[1] == bryton.MSG_CUE]
    assert [d[2][4][1] for d in before if d[1] == bryton.MSG_CUE] == [32]
    assert widths == [32, 33]
    assert decoded.cues[2].label == "Quai du Maréchal Joffre, D 1006"


def test_a_long_name_loses_its_road_number_then_its_last_words():
    assert bryton.fit_label("Avenue Jean Moulin, D 306") == "Avenue Jean Moulin, D 306"
    assert bryton.fit_label("(2) Avenue Charles de Gaulle, D 306") == (
        "(2) Avenue Charles de Gaulle"
    )
    assert bryton.fit_label("Rampe d'accès à la Via Ardèche") == "Rampe d'accès à la Via"
    assert bryton.fit_label("Avenue du Maréchal de Lattre de Tassigny, D 1") == (
        "Avenue du Maréchal de Lattre de"
    )
    long_word = "Schwarzwaldhochstraßenverbindungsweg"
    assert bryton.fit_label(long_word) == long_word.encode()[:32].decode("utf-8", "ignore")
    for text in ("Rampe d'accès à la Via Ardèche", "É" * 40, long_word):
        assert len(bryton.fit_label(text).encode("utf-8")) <= 32


def test_accents_are_kept_as_utf8():
    decoded, _ = bryton.decode(bryton.encode(small_route()))
    assert decoded.cues[1].label == "Rue Étienne Dolet"


def test_altitude_uses_the_fit_scale():
    for metres in (-500, -0.2, 0, 93, 818.4, 12000):
        assert abs(bryton.decode_altitude(bryton.encode_altitude(metres)) - metres) <= 0.1
    with pytest.raises(ValueError, match="altitude"):
        bryton.encode_altitude(13_000)


def test_missing_elevation_encodes_as_sea_level():
    route = Route([RoutePoint(45.0, 4.0), RoutePoint(45.001, 4.0)])
    decoded, _ = bryton.decode(bryton.encode(route))
    assert [p.ele for p in decoded.points] == [0.0, 0.0]


def test_invalid_route_is_refused():
    route = small_route()
    route.cues.append(Cue(1, CueType.LEFT))
    with pytest.raises(ValueError, match="ordre"):
        bryton.encode(route)
