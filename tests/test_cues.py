"""Reading instructions off GPX points."""

import pytest

from bryton_route import cues
from bryton_route.cues import ROUNDABOUT, UTURN
from bryton_route.gpx import GpxPoint
from bryton_route.model import CueType

C = CueType


@pytest.mark.parametrize(
    "sentence, kind, street",
    [
        (
            "Restez sur la droite sur Boulevard des Trois Croix",
            C.KEEP_RIGHT,
            "Boulevard des Trois Croix",
        ),
        ("Tournez fort à gauche sur Rue de la Motte", C.SHARP_LEFT, "Rue de la Motte"),
        ("Tournez fortement à droite sur Rue X", C.SHARP_RIGHT, "Rue X"),
        ("Tournez légèrement à droite sur Rue d'Alsace", C.SLIGHT_RIGHT, "Rue d'Alsace"),
        ("Tournez à droite sur Route de Nogent sur Seine", C.RIGHT, "Route de Nogent sur Seine"),
        (
            "Au rond-point, prenez la 3 sortie vers Boulevard Paul Painlevé",
            ROUNDABOUT,
            "Boulevard Paul Painlevé",
        ),
        ("Empruntez le rond-point", ROUNDABOUT, ""),
        ("FINISH", C.END_OF_ROUTE, ""),
        ("Start on Boulevard de Vitré", C.STRAIGHT, "Boulevard de Vitré"),
        ("Arrive at SC-390, on the left", C.END_OF_ROUTE, ""),
        ("Head east on <b>SC-390</b>", C.STRAIGHT, "SC-390"),
        ("Keep right onto A 12", C.KEEP_RIGHT, "A 12"),
        ("Turn sharp right onto Main St", C.SHARP_RIGHT, "Main St"),
        ("Make a U-turn", UTURN, ""),
        ("Turn right onto Loughborough Road", C.RIGHT, "Loughborough Road"),
        ("Rechts abbiegen auf Hauptstraße", C.RIGHT, "Hauptstraße"),
        ("Im Kreisverkehr die 2. Ausfahrt nehmen", ROUNDABOUT, ""),
    ],
)
def test_sentences(sentence, kind, street):
    assert cues.phrase_kind(sentence)[0] == kind
    assert cues.street_name(sentence) == street


@pytest.mark.parametrize(
    "phrase, kind",
    [
        ("straight", C.STRAIGHT), ("left", C.LEFT), ("right", C.RIGHT),
        ("slight left", C.SLIGHT_LEFT), ("sharp right", C.SHARP_RIGHT),
        ("keep left", C.KEEP_LEFT), ("u-turn", UTURN), ("destination", C.END_OF_ROUTE),
    ],
)
def test_brouter_bare_phrases(phrase, kind):
    assert cues.phrase_kind(phrase)[0] == kind


def test_roundabout_exit_number():
    assert cues.phrase_kind("Take exit 3") == (ROUNDABOUT, 3)
    assert cues.exit_number("Au rond-point, prenez la 2 sortie vers X") == 2
    assert cues.exit_number("take the 1st exit onto X") == 1


@pytest.mark.parametrize(
    "point, kind",
    [
        (GpxPoint(0, 0, desc="right", extensions={"turn": "TR", "turn-angle": "91"}), C.RIGHT),
        (GpxPoint(0, 0, desc="keep right", extensions={"turn": "KR"}), C.KEEP_RIGHT),
        (GpxPoint(0, 0, name="slight right", sym="tslr", type="TSLR"), C.SLIGHT_RIGHT),
        (GpxPoint(0, 0, name="left", sym="left", type="Left"), C.LEFT),
        (GpxPoint(0, 0, sym="right_slight"), C.SLIGHT_RIGHT),
        (GpxPoint(0, 0, sym="Left_slight"), C.SLIGHT_LEFT),
        (GpxPoint(0, 0, sym="left_sharp"), C.SHARP_LEFT),
    ],
    ids=[
        "osmand", "osmand-keep", "gpsies-code", "gpsies-word", "locus", "plotaroute", "locus-sharp"
    ],
)
def test_point_encodings(point, kind):
    assert cues.read_hint(point).kind == kind


def test_turn_angle_and_roundabout_code_are_read():
    point = GpxPoint(0, 0, desc="Take exit 2", extensions={"turn": "RNDB2", "turn-angle": "-87"})
    hint = cues.read_hint(point)
    assert (hint.kind, hint.exit, hint.angle) == (ROUNDABOUT, 2, -87.0)


def test_markers_that_are_not_instructions():
    for point in (
        GpxPoint(0, 0, sym="pass_place", type="Via"),
        GpxPoint(0, 0, sym="Flag, Blue", type="Waypoint", name="Col"),
        GpxPoint(0, 0, extensions={"turn": "BL"}),
    ):
        assert cues.read_hint(point) is None


def test_flag_symbol_falls_through_to_the_sentence():
    hint = cues.read_hint(GpxPoint(0, 0, sym="Flag, Blue", cmt="Tournez à droite sur Rue A"))
    assert (hint.kind, hint.label) == (C.RIGHT, "Rue A")


def test_ors_numeric_types_only_when_enabled():
    point = GpxPoint(0, 0, desc="Head east on <b>SC-390</b>", extensions={"type": "1"})
    assert cues.read_hint(point, ors_types=True).kind == C.RIGHT
    assert cues.read_hint(point).kind == C.STRAIGHT  # from the sentence


def test_end_marker_has_no_label():
    assert cues.read_hint(GpxPoint(0, 0, desc="Arrive at Rua X, on the right")).label == ""
