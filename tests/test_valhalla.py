"""Valhalla map matching, against responses recorded from the public server.

Recorded for routes that involve nobody's home: the BRouter samples across
Lyon and the OpenRouteService demo route. No test touches the network.
"""

import json
from dataclasses import replace

import pytest

from bryton_route import geo, valhalla, web
from bryton_route.model import CueType as C, RoutePoint
from bryton_route.route import read_gpx

from conftest import DATA, data

STRONG = {C.LEFT, C.RIGHT, C.SHARP_LEFT, C.SHARP_RIGHT}


def recorded(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@pytest.fixture
def serve(monkeypatch):
    """Answer every POST with a recorded reply; returns what was sent."""
    sent = []

    def install(name):
        body = recorded(name)

        def post(url, request):
            sent.append((url, request))
            return body

        monkeypatch.setattr(web, "post_json", post)
        return sent

    return install


def bare(gpx_name):
    """The route of a GPX with its instructions stripped, as Komoot exports it."""
    return replace(read_gpx(data(gpx_name), allow_no_cues=True), cues=[], warnings=[])


def along(points):
    out = [0.0]
    for length in geo.path_lengths(points):
        out.append(out[-1] + length)
    return out


def test_polyline6_decoding():
    # The reference polyline from Google's documentation, read at 6 decimals.
    coords = valhalla.decode_polyline6("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    expected = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert [(round(a * 10, 5), round(b * 10, 5)) for a, b in coords] == expected


def test_trace_route_request(serve):
    sent = serve("valhalla_flat.json")
    route = bare("brouter_flat_osmand.gpx")
    valhalla.instructions(route)
    ((url, body),) = sent
    assert url == valhalla.DEFAULT_SERVER + "/trace_route"
    assert (body["costing"], body["shape_match"]) == ("bicycle", "map_snap")
    assert body["directions_options"]["language"] == "fr-FR"
    assert len(body["shape"]) == len(route.points)


def test_every_piece_of_the_match_is_used(serve):
    """Valhalla puts the first piece in "trip" and the rest in "alternates"."""
    serve("valhalla_flat.json")
    route = bare("brouter_flat_osmand.gpx")
    matched = valhalla.instructions(route)
    distance = along(route.points)
    assert any(distance[c.index] > 8000 for c in matched.cues)
    assert [c.type for c in matched.cues].count(C.END_OF_ROUTE) == 1
    assert not any("n'a calé que" in w for w in matched.warnings)


def test_a_partial_match_is_reported(monkeypatch):
    partial = recorded("valhalla_flat.json")
    partial.pop("alternates")
    monkeypatch.setattr(web, "post_json", lambda url, body: partial)
    matched = valhalla.instructions(bare("brouter_flat_osmand.gpx"))
    assert any("n'a calé que 5,0 km sur 11,8 km" in w for w in matched.warnings)


def test_turns_mostly_agree_with_brouter_on_the_same_ride(serve):
    serve("valhalla_flat.json")
    with_brouter = read_gpx(data("brouter_flat_osmand.gpx"))
    matched = valhalla.instructions(replace(with_brouter, cues=[], warnings=[]))
    distance = along(with_brouter.points)

    def nearest(cue):
        return min(
            ((abs(distance[b.index] - distance[cue.index]), b) for b in with_brouter.cues),
            key=lambda pair: pair[0],
        )

    turns = [c for c in matched.cues if c.type in STRONG]
    paired = [(c, b) for c in turns for gap, b in [nearest(c)] if gap <= 15]
    assert len(paired) >= 0.85 * len(turns)
    right = {C.RIGHT, C.SHARP_RIGHT}
    contradictions = [
        (c, b) for c, b in paired if b.type in STRONG and (c.type in right) != (b.type in right)
    ]
    # Valhalla alone does get a side wrong now and then; that is why it only
    # supplies the turns when BRouter cannot.
    assert len(contradictions) <= 1


def test_street_names_come_through(serve):
    serve("valhalla_flat.json")
    matched = valhalla.instructions(bare("brouter_flat_osmand.gpx"))
    labels = {c.label for c in matched.cues}
    assert {"Place Bellecour", "Avenue Jean Jaurès"} <= labels
    assert "piste cyclable" in labels  # an unnamed cycleway is still described


def test_a_roundabout_is_one_cue_with_its_exit_and_direction(serve):
    serve("valhalla_ors.json")
    matched = valhalla.instructions(bare("ors_directions.gpx"))
    labels = [c.label for c in matched.cues]
    assert "(1) Rodovia Governador Irineu Bornhausen, SC-390" in labels
    second = next(c for c in matched.cues if c.label == "(2) Lauro Müller (Centro)")
    assert second.type == C.LEFT  # entered heading 150°, left heading 71°
    assert not any("rond-point" in label for label in labels)


def test_a_gap_in_the_match_still_leaves_cues_in_order(serve):
    """In Lyon the ride takes stairs, which the bicycle match leaves out."""
    serve("valhalla_lyon.json")
    matched = valhalla.instructions(bare("brouter_osmand.gpx"))
    indices = [c.index for c in matched.cues]
    assert indices == sorted(set(indices))


def test_nothing_usable_is_an_error(monkeypatch):
    monkeypatch.setattr(web, "post_json", lambda url, body: {})
    with pytest.raises(web.OnlineError, match="aucun itinéraire"):
        valhalla.instructions(bare("brouter_flat_osmand.gpx"))


def test_a_planners_line_is_named_and_left_where_it_is(serve):
    sent = serve("valhalla_attributes_lyon_bike.json")
    route = read_gpx(data("osrm_lyon_bike_track.gpx"), allow_no_cues=True)
    matched = valhalla.match(route.points)
    assert len(matched.names) == len(matched.points) == len(route.points)
    assert {"Place Bellecour", "Rue Sala"} <= set(matched.names)
    assert matched.offset_m < 0.1
    ((url, body),) = sent
    assert url == valhalla.DEFAULT_SERVER + "/trace_attributes"
    assert "directions_options" not in body


def test_a_recorded_track_is_moved_onto_the_roads(serve):
    """The Lyon line again, with 4 m of correlated GPS noise, one point every 5 m."""
    serve("valhalla_attributes_lyon_bike_noisy.json")
    noisy = read_gpx(data("osrm_lyon_bike_noisy_track.gpx"), allow_no_cues=True)
    clean = read_gpx(data("osrm_lyon_bike_track.gpx"), allow_no_cues=True)
    matched = valhalla.match(noisy.points)
    assert matched.offset_m > 2
    # Noise lengthens the line by 3 %; back on the roads it measures what the planner drew.
    length = sum(geo.path_lengths(clean.points))
    assert sum(geo.path_lengths(noisy.points)) > 1.02 * length
    assert sum(geo.path_lengths(matched.points)) == pytest.approx(length, rel=0.005)


def test_the_wrong_number_of_matched_points_is_an_error(monkeypatch):
    monkeypatch.setattr(web, "post_json", lambda url, body: {"edges": [], "matched_points": []})
    route = read_gpx(data("osrm_lyon_bike_track.gpx"), allow_no_cues=True)
    with pytest.raises(web.OnlineError, match="points calés"):
        valhalla.match(route.points)


def test_a_short_unmatched_run_is_drawn_between_its_neighbours(monkeypatch):
    line = [RoutePoint(45.0 + i * 0.0001, 4.8, 200.0 + i) for i in range(10)]

    def post(url, body):
        answers = []
        for k, p in enumerate(body["shape"]):
            if k in (4, 5):
                answers.append({"type": "unmatched", "lat": p["lat"], "lon": p["lon"]})
            else:
                answers.append(
                    {"type": "matched", "lat": p["lat"], "lon": p["lon"] + 0.00001,
                     "edge_index": 0, "distance_from_trace_point": 0.8}
                )
        return {"edges": [{"names": ["Rue Sala"]}], "matched_points": answers}

    monkeypatch.setattr(web, "post_json", post)
    matched = valhalla.match(line)
    assert [round(p.lon, 5) for p in matched.points] == [4.80001] * 10
    assert matched.points[4].ele == 204.0
    assert matched.names[:4] == ["Rue Sala"] * 4 and matched.names[4:6] == ["", ""]


# -- Long lines go in several requests ---------------------------------------------


def test_requests_tile_a_long_line(monkeypatch):
    monkeypatch.setattr(valhalla, "MAX_POINTS", 30)
    monkeypatch.setattr(valhalla, "OVERLAP_POINTS", 6)
    monkeypatch.setattr(valhalla, "MAX_LENGTH_M", 1000.0)
    # 100 points 55 m apart: the length limit bites before the point limit.
    line = [RoutePoint(45.0 + i * 0.0005, 4.8) for i in range(100)]
    spans = valhalla.chunks(line)
    assert len(spans) > 5
    owned = [i for _, _, own_from, own_to in spans for i in range(own_from, own_to)]
    assert owned == list(range(100))
    for start, end, own_from, own_to in spans:
        assert start <= own_from < own_to <= end
        assert end - start <= 30
        assert geo.geodesic_m(line[start].lat, 4.8, line[end - 1].lat, 4.8) <= 1000.0
    assert valhalla.chunks(line[:2]) == [(0, 2, 0, 2)]


def test_street_names_survive_the_seams_between_requests(monkeypatch):
    monkeypatch.setattr(valhalla, "MAX_POINTS", 30)
    monkeypatch.setattr(valhalla, "OVERLAP_POINTS", 6)
    line = [RoutePoint(45.0 + i * 0.0001, 4.8) for i in range(100)]
    where = {round(p.lat, 6): i for i, p in enumerate(line)}
    requests = []

    def post(url, body):
        indices = [where[round(p["lat"], 6)] for p in body["shape"]]
        requests.append(indices)
        return {
            "edges": [{"names": [f"Rue {i}"]} for i in indices],
            "matched_points": [
                {"type": "matched", "lat": p["lat"], "lon": p["lon"], "edge_index": k,
                 "distance_from_trace_point": 0.0}
                for k, p in enumerate(body["shape"])
            ],
        }

    monkeypatch.setattr(web, "post_json", post)
    matched = valhalla.match(line)
    assert [r[0] for r in requests] == [0, 24, 48, 72]
    assert matched.names == [f"Rue {i}" for i in range(100)]


def _polyline6(coords):
    out, previous = [], (0, 0)
    for lat, lon in coords:
        now = (round(lat * 1e6), round(lon * 1e6))
        for value in (now[0] - previous[0], now[1] - previous[1]):
            value = ~(value << 1) if value < 0 else value << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        previous = now
    return "".join(out)


def test_turns_survive_the_seams_between_requests(monkeypatch):
    """A stand-in for trace_route: departs, turns right at every point whose
    index is a multiple of 7 (never at its own ends), and arrives."""
    monkeypatch.setattr(valhalla, "MAX_POINTS", 30)
    monkeypatch.setattr(valhalla, "OVERLAP_POINTS", 6)
    line = [RoutePoint(45.0 + i * 0.0005, 4.8) for i in range(100)]
    where = {round(p.lat, 6): i for i, p in enumerate(line)}

    def post(url, body):
        indices = [where[round(p["lat"], 6)] for p in body["shape"]]
        last = len(indices) - 1
        maneuvers = [{"type": 1, "begin_shape_index": 0, "street_names": ["Départ"]}]
        maneuvers += [
            {"type": 10, "begin_shape_index": k, "street_names": [f"Rue {indices[k]}"]}
            for k in range(1, last)
            if indices[k] % 7 == 0
        ]
        maneuvers.append({"type": 4, "begin_shape_index": last})
        shape = _polyline6([(p["lat"], p["lon"]) for p in body["shape"]])
        length = sum(geo.path_lengths([line[i] for i in indices])) / 1000
        return {"trip": {"legs": [{"shape": shape, "maneuvers": maneuvers}],
                         "summary": {"length": length}}}

    monkeypatch.setattr(web, "post_json", post)
    route = replace(read_gpx(data("osrm_lyon_bike_track.gpx"), allow_no_cues=True), points=line)
    result = valhalla.instructions(route)
    turns = [c.index for c in result.cues if c.type == C.RIGHT]
    assert turns == [i for i in range(1, 99) if i % 7 == 0]
    assert (result.cues[0].index, result.cues[0].type) == (0, C.STRAIGHT)
    assert (result.cues[-1].index, result.cues[-1].type) == (99, C.END_OF_ROUTE)
    assert len(result.cues) == len(turns) + 2
    assert not any("n'a calé que" in w for w in result.warnings)
