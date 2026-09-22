"""The online mode, against answers recorded from brouter.de and Valhalla.

The Lyon line is an OSRM bicycle route whose 48 manoeuvres and 33 street names
are known (osrm_lyon_bike.json); the answers of both servers for that line are
stored next to it. No test touches the network.
"""

import json
from dataclasses import replace
from itertools import accumulate
from urllib.parse import parse_qs, urlsplit

import pytest

from bryton_route import brouterweb, bryton, cli, convert, geo, online, web
from bryton_route.cues import Hint
from bryton_route.model import CueType as C, RoutePoint
from bryton_route.route import read_gpx, snap

from conftest import DATA, data

LINE = "osrm_lyon_bike_track.gpx"
RIGHT = {C.RIGHT, C.SHARP_RIGHT, C.SLIGHT_RIGHT, C.KEEP_RIGHT}
ISLAND = "brouter.de a répondu HTTP 400 : target island detected for section 25"
BUSY = (
    "brouter.de a répondu HTTP 400 : "
    "operation killed by thread-priority-watchdog after 0 seconds"
)


def recorded(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@pytest.fixture
def servers(monkeypatch):
    """brouter.de and Valhalla, replaced by their recorded answers for the Lyon line."""
    calls = {"get": [], "post": []}
    routed = data("brouter_reroute_lyon_bike_0.gpx")
    attributes = recorded("valhalla_attributes_lyon_bike.json")

    def get(url):
        calls["get"].append(url)
        return routed

    def post(url, body):
        calls["post"].append(url)
        return attributes

    monkeypatch.setattr(web, "get", get)
    monkeypatch.setattr(web, "post_json", post)
    return calls


def lyon():
    return read_gpx(data(LINE), allow_no_cues=True)


def flat_line():
    route = read_gpx(data("brouter_flat_osmand.gpx"), allow_no_cues=True)
    return replace(route, cues=[], warnings=[])


def lyon_steps():
    osrm = recorded("osrm_lyon_bike.json")["routes"][0]
    return [
        {
            "location": step["maneuver"]["location"],
            "type": step["maneuver"]["type"],
            "modifier": step["maneuver"].get("modifier") or "",
            "name": step.get("name") or "",
        }
        for leg in osrm["legs"]
        for step in leg["steps"]
    ]


def score(route, steps):
    """(found, on the right side, named correctly, total, total named) against
    OSRM's manoeuvres for the line."""
    along = [0.0, *accumulate(geo.path_lengths(route.points))]
    truth = []
    for step in steps:
        kind, modifier = step["type"], step["modifier"]
        if kind in ("depart", "arrive", "new name", "exit roundabout", "exit rotary"):
            continue
        if kind == "continue" and modifier in ("", "straight"):
            continue
        side = None if "roundabout" in kind or "rotary" in kind else modifier.split()[-1]
        location = RoutePoint(step["location"][1], step["location"][0])
        truth.append((location, side, step["name"]))
    indices, _ = snap([t[0] for t in truth], route.points)
    found = right_side = named = 0
    for (_, side, name), index in zip(truth, indices):
        near = [
            (abs(along[c.index] - along[index]), c.index, c)
            for c in route.cues
            if c.type != C.END_OF_ROUTE and abs(along[c.index] - along[index]) <= 25
        ]
        if not near:
            continue
        cue = min(near)[2]
        found += 1
        if side not in ("left", "right") or (side == "right") == (cue.type in RIGHT):
            right_side += 1
        label = cue.label.lower()
        if name and label and (name.lower() in label or label in name.lower()):
            named += 1
    return found, right_side, named, len(truth), sum(1 for t in truth if t[2])


# -- Quality and fallbacks -----------------------------------------------------


def test_brouter_turns_and_valhalla_names_on_a_known_route(servers):
    route = online.add_cues(lyon())
    assert (len(servers["get"]), len(servers["post"])) == (1, 1)
    # The OSRM line has no elevation, which read_gpx reports; nothing else may be wrong.
    assert [w for w in route.warnings if "altitude" not in w] == []
    assert route.source.endswith("+ BRouter + Valhalla")
    found, right_side, named, total, total_named = score(route, lyon_steps())
    assert (found, total) == (48, 48)
    assert right_side >= 46
    assert (named, total_named) >= (30, 33)


def test_a_roundabout_keeps_its_exit_number_in_front_of_the_name():
    assert online._named(Hint(C.RIGHT, "(2)"), "Avenue Berthelot").label == "(2) Avenue Berthelot"
    assert online._named(Hint(C.LEFT, ""), "Rue Sala").label == "Rue Sala"
    assert online._named(Hint(C.END_OF_ROUTE), "Rue Sala").label == ""


def test_brouter_refusing_the_line_falls_back_to_valhalla(monkeypatch):
    def refuse(url):
        raise web.OnlineError(ISLAND)

    monkeypatch.setattr(web, "get", refuse)
    monkeypatch.setattr(web, "post_json", lambda url, body: recorded("valhalla_flat.json"))
    route = online.add_cues(flat_line())
    assert any("target island" in w and "Valhalla seul" in w for w in route.warnings)
    assert route.source.endswith("+ Valhalla")
    assert any(c.label == "Avenue Jean Jaurès" for c in route.cues)


def test_a_line_a_bicycle_cannot_follow_is_not_retried(monkeypatch):
    attempts = []

    def refuse(url):
        attempts.append(url)
        raise web.OnlineError(ISLAND)

    monkeypatch.setattr(web, "get", refuse)
    with pytest.raises(web.OnlineError):
        brouterweb.cues_along(flat_line().points)
    assert len(attempts) == 1


def test_a_busy_brouter_is_asked_again(monkeypatch):
    monkeypatch.setattr(brouterweb, "RETRY_DELAYS_S", (0.0, 0.0))
    answers = [web.OnlineError(BUSY), data("brouter_reroute_lyon_bike_0.gpx")]

    def get(url):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(web, "get", get)
    placed, strays = brouterweb.cues_along(lyon().points)
    assert answers == []
    assert len(placed) > 40 and strays == []


def test_brouter_leaving_most_of_the_line_falls_back_to_valhalla(monkeypatch):
    def mostly_elsewhere(points, **options):
        return [], [(0, len(points) * 2 // 3, 120.0)]

    monkeypatch.setattr(brouterweb, "cues_along", mostly_elsewhere)
    monkeypatch.setattr(web, "post_json", lambda url, body: recorded("valhalla_flat.json"))
    route = online.add_cues(flat_line())
    assert any("ne suit pas la trace" in w and "Valhalla seul" in w for w in route.warnings)
    assert route.source.endswith("+ Valhalla")


def test_a_recorded_track_is_put_back_on_the_roads_before_brouter_sees_it(monkeypatch):
    """The Lyon line with 4 m of correlated GPS noise, one point every 5 m. Fed
    as it was to BRouter, it gave 772 cues: every noisy waypoint pulled BRouter
    onto a sidewalk or a parallel lane and back."""
    calls = []
    answers = {
        "trace_attributes": "valhalla_attributes_lyon_bike_noisy.json",
        "trace_route": "valhalla_route_lyon_bike_noisy.json",
    }

    def get(url):
        calls.append("brouter")
        return data("brouter_reroute_lyon_bike_noisy.gpx")

    def post(url, body):
        service = url.rsplit("/", 1)[-1]
        calls.append(service)
        return recorded(answers[service])

    monkeypatch.setattr(web, "get", get)
    monkeypatch.setattr(web, "post_json", post)
    noisy = read_gpx(data("osrm_lyon_bike_noisy_track.gpx"), allow_no_cues=True)
    route = online.add_cues(noisy)
    assert calls[:2] == ["trace_attributes", "brouter"]
    assert any("trace imprécise" in w and "recalée" in w for w in route.warnings)
    assert len(route.cues) < 80
    assert sum(geo.path_lengths(route.points)) == pytest.approx(
        sum(geo.path_lengths(lyon().points)), rel=0.005
    )
    found, right_side, named, total, total_named = score(route, lyon_steps())
    assert (found, total) >= (41, 48) and right_side >= 38 and named >= 29


def test_an_absurd_number_of_cues_is_reported():
    warnings = []
    along = [0.0, 2000.0]
    online._check_density([object()] * 40, along, warnings)
    assert warnings and "anormalement serrées" in warnings[0]
    online._check_density([object()] * 20, along, warnings := [])
    assert warnings == []


def test_turns_survive_when_street_names_are_unavailable(servers, monkeypatch):
    def down(url, body):
        raise web.OnlineError("valhalla1.openstreetmap.de injoignable")

    monkeypatch.setattr(web, "post_json", down)
    route = online.add_cues(lyon())
    assert score(route, lyon_steps())[0] == 48
    assert any("noms de rue indisponibles" in w for w in route.warnings)


# -- A passage BRouter does not follow -----------------------------------------

#: km 18.5-25.5 of an OSRM bicycle route from Lyon to Grenoble. Between km 2.3
#: and 4.0 of it BRouter leaves the line by up to 475 m, onto roads of its own
#: where it places some twenty turns; OSRM has no manoeuvre there at all.
PART = "osrm_lyon_grenoble_part_track.gpx"


@pytest.fixture
def detour_servers(monkeypatch):
    """brouter.de and Valhalla, replaced by their recorded answers for PART."""
    calls = []
    answers = {"trace_attributes": "attributes", "trace_route": "route"}

    def get(url):
        calls.append("brouter")
        return data("brouter_reroute_lyon_grenoble_part.gpx")

    def post(url, body):
        service = url.rsplit("/", 1)[-1]
        calls.append(service)
        return recorded(f"valhalla_{answers[service]}_lyon_grenoble_part.json")

    monkeypatch.setattr(web, "get", get)
    monkeypatch.setattr(web, "post_json", post)
    return calls


def part():
    route = read_gpx(data(PART), allow_no_cues=True)
    return route, [0.0, *accumulate(geo.path_lengths(route.points))]


def cues_between(route, along, start_m, end_m):
    return [c for c in route.cues if start_m <= along[c.index] <= end_m]


def test_the_passage_brouter_does_not_follow_is_found(detour_servers):
    line, along = part()
    placed, strays = brouterweb.cues_along(line.points)
    assert len(strays) == 1
    first, last, worst = strays[0]
    assert 2200 < along[first] < 2400 and 3900 < along[last] < 4100
    assert worst > 150
    assert sum(1 for i, _ in placed if first <= i <= last) > 15


def test_valhalla_describes_only_the_passage_brouter_does_not_follow(detour_servers):
    line, along = part()
    route = online.add_cues(line)
    assert detour_servers == ["trace_attributes", "brouter", "trace_route"]
    assert route.source.endswith("+ BRouter + Valhalla")
    assert any(
        "km 2,3–4,0" in w and "instructions de Valhalla sur ces passages" in w
        for w in route.warnings
    )
    # BRouter's detour is gone, and Valhalla has nothing to say on that straight road...
    assert cues_between(route, along, 2300, 4000) == []
    # ...while BRouter still places the turns elsewhere: Valhalla alone finds 4 of these 7.
    found, right_side, _, total, _ = score(route, recorded("osrm_lyon_grenoble_part_steps.json"))
    assert (found, right_side, total) == (7, 7, 7)


def test_roundabouts_point_the_way_out_and_name_the_street_past_the_ring(detour_servers):
    line, _ = part()
    route = online.add_cues(line)
    rings = [(c.type, c.label) for c in route.cues if c.label[:1] == "("]
    # The line's bearings before and after each ring: within 15 degrees at three
    # of them, 40 degrees to the right at the third. BRouter's angles said sharp
    # left, straight on, left, sharp left.
    assert [(kind, label.split(" ")[0]) for kind, label in rings] == [
        (C.STRAIGHT, "(1)"),
        (C.STRAIGHT, "(1)"),
        (C.SLIGHT_RIGHT, "(1)"),
        (C.STRAIGHT, "(2)"),
    ]
    # The third one's ring is called Rond-Point de Terre-Valet; the rider wants the exit.
    assert rings[2][1] == "(1) Avenue Jean Moulin, D 306"


def test_without_valhalla_the_passage_is_left_without_instructions(detour_servers, monkeypatch):
    def down(url, body):
        raise web.OnlineError("valhalla1.openstreetmap.de injoignable")

    monkeypatch.setattr(web, "post_json", down)
    line, along = part()
    route = online.add_cues(line)
    assert cues_between(route, along, 2300, 4000) == []
    assert any("aucune instruction sur ces passages" in w for w in route.warnings)
    assert score(route, recorded("osrm_lyon_grenoble_part_steps.json"))[0] == 7


def test_a_point_beside_a_long_straight_road_is_on_it():
    # BRouter draws a straight road as two vertices far apart: what counts is the
    # distance to the road between them, not to its ends.
    road = [RoutePoint(45.0, 4.80), RoutePoint(45.0, 4.83)]
    beside = [RoutePoint(45.0001, 4.80 + k * 0.001) for k in range(31)]
    assert brouterweb._strays(beside, road, 0) == []
    [(first, last, worst)] = brouterweb._strays([RoutePoint(45.001, 4.815)], road, 5)
    assert (first, last) == (5, 5) and 105 < worst < 115


# -- Long lines are split into several requests --------------------------------


def rtept(point, desc, offset, turn=""):
    extensions = f"<turn>{turn}</turn><turn-angle>90</turn-angle>" if turn else ""
    return (
        f'<rtept lat="{point.lat}" lon="{point.lon}"><desc>{desc}</desc>'
        f"<extensions>{extensions}<offset>{offset}</offset></extensions></rtept>"
    )


def synthetic_brouter(line, indices):
    """A stand-in for brouter.de: goes straight through the requested waypoints
    and turns right at every line point whose index is a multiple of 7, except
    at its own first and last waypoint, where BRouter never reports a turn."""
    points = [line[i] for i in indices]
    last = len(indices) - 1
    rte = [rtept(points[0], "start", 0)]
    rte += [rtept(points[k], "right", k, "TR") for k in range(1, last) if indices[k] % 7 == 0]
    rte.append(rtept(points[last], "destination", last))
    track = "".join(f'<trkpt lat="{p.lat}" lon="{p.lon}"/>' for p in points)
    return (
        '<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="OsmAndRouter" '
        'xmlns="http://www.topografix.com/GPX/1/1"><rte>' + "".join(rte) + "</rte>"
        "<trk><trkseg>" + track + "</trkseg></trk></gpx>"
    ).encode("utf-8")


def test_a_long_line_is_split_without_losing_a_turn_at_a_seam(monkeypatch):
    line = [RoutePoint(45.0 + i * 0.0005, 4.8) for i in range(100)]
    where = {(f"{p.lon:.6f}", f"{p.lat:.6f}"): i for i, p in enumerate(line)}
    requests = []

    def get(url):
        lonlats = parse_qs(urlsplit(url).query)["lonlats"][0].split("|")
        indices = [where[tuple(pair.split(","))] for pair in lonlats]
        requests.append(indices)
        return synthetic_brouter(line, indices)

    def every_point(points, tolerance):
        return list(range(len(points)))

    monkeypatch.setattr(web, "get", get)
    monkeypatch.setattr(brouterweb, "kept_indices", every_point)
    monkeypatch.setattr(brouterweb, "MAX_WAYPOINTS", 30)
    monkeypatch.setattr(brouterweb, "OVERLAP", 6)

    placed, strays = brouterweb.cues_along(line)

    assert len(requests) == 4
    assert strays == []
    turns = [i for i, hint in placed if hint.kind == C.RIGHT]
    assert turns == [i for i in range(1, 99) if i % 7 == 0]
    assert (placed[0][0], placed[0][1].kind) == (0, C.STRAIGHT)
    assert (placed[-1][0], placed[-1][1].kind) == (99, C.END_OF_ROUTE)


# -- Choosing the mode ---------------------------------------------------------


def with_one_instruction():
    """The Lyon line, plus a single instruction so that it counts as offline-ready."""
    doc = data(LINE)
    point = lyon().points[10]
    waypoint = (
        f'<wpt lat="{point.lat}" lon="{point.lon}">'
        "<desc>Turn right onto Rue Test</desc></wpt>"
    ).encode("utf-8")
    at = doc.index(b"<trk>")
    return doc[:at] + waypoint + doc[at:]


def test_cli_goes_online_for_a_bare_track(tmp_path, capsys, servers):
    gpx = tmp_path / "komoot.gpx"
    gpx.write_bytes(data(LINE))
    assert cli.main(["convert", str(gpx)]) == 0
    out = capsys.readouterr().out
    assert "brouter.de" in out and "valhalla1.openstreetmap.de" in out
    route, _ = bryton.decode((tmp_path / "komoot.fit").read_bytes())
    assert any(c.label == "Place Bellecour" for c in route.cues)


def test_cli_counts_its_requests_through_the_real_http_layer(tmp_path, capsys, monkeypatch):
    import io

    def urlopen(request, timeout):
        if "/brouter?" in request.full_url:
            return io.BytesIO(data("brouter_reroute_lyon_bike_0.gpx"))
        return io.BytesIO(data("valhalla_attributes_lyon_bike.json"))

    monkeypatch.setattr(web, "MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(web.urllib.request, "urlopen", urlopen)
    gpx = tmp_path / "komoot.gpx"
    gpx.write_bytes(data(LINE))
    assert cli.main(["doctor", str(gpx)]) == 0
    out = capsys.readouterr().out
    assert "requêtes      : valhalla1.openstreetmap.de 1, brouter.de 1" in out
    assert "Place Bellecour" in out
    assert web.on_request is None


def test_cli_goes_online_when_the_gpx_only_marks_start_and_finish(tmp_path, capsys, servers):
    points = lyon().points
    ends = "".join(
        f'<wpt lat="{p.lat}" lon="{p.lon}"><name>{name}</name></wpt>'
        for p, name in ((points[0], "Départ"), (points[-1], "Arrivée"))
    ).encode("utf-8")
    doc = data(LINE)
    at = doc.index(b"<trk>")
    gpx = tmp_path / "visugpx.gpx"
    gpx.write_bytes(doc[:at] + ends + doc[at:])
    assert cli.main(["convert", str(gpx)]) == 0
    assert servers["get"]
    assert "départ et à l'arrivée" in capsys.readouterr().out


def test_cli_offline_refuses_a_bare_track(tmp_path, servers):
    gpx = tmp_path / "komoot.gpx"
    gpx.write_bytes(data(LINE))
    assert cli.main(["convert", str(gpx), "--offline"]) == 2
    assert servers == {"get": [], "post": []}


def test_cli_stays_offline_when_the_gpx_has_instructions(tmp_path, servers):
    gpx = tmp_path / "export.gpx"
    gpx.write_bytes(with_one_instruction())
    assert cli.main(["convert", str(gpx)]) == 0
    assert servers == {"get": [], "post": []}
    route, _ = bryton.decode((tmp_path / "export.fit").read_bytes())
    assert any(c.label == "Rue Test" for c in route.cues)


def test_cli_online_flag_recomputes_instructions_that_exist(tmp_path, capsys, servers):
    gpx = tmp_path / "export.gpx"
    gpx.write_bytes(with_one_instruction())
    assert cli.main(["doctor", str(gpx), "--online"]) == 0
    out = capsys.readouterr().out
    assert servers["get"] and "Place Bellecour" in out and "Rue Test" not in out


def test_convert_modes(servers):
    _, route = convert(data(LINE))
    assert "BRouter" in route.source
    with pytest.raises(ValueError, match="aucune instruction"):
        convert(data(LINE), online=False)
    calls_before = len(servers["get"])
    convert(with_one_instruction())
    assert len(servers["get"]) == calls_before
    convert(with_one_instruction(), online=True)
    assert len(servers["get"]) == calls_before + 1
