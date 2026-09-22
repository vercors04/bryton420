"""Real exports from bikerouter.de's web interface, kept out of git.

They start where their author rides from, so they live in tests/data/local/,
which .gitignore excludes. The tests are skipped when the files are absent.
"""

from collections import Counter

import pytest

from bryton_route import bryton
from bryton_route.model import CueType as C
from bryton_route.route import UnsupportedGpx, read_gpx

from conftest import DATA


def local(name):
    path = DATA / "local" / name
    if not path.exists():
        pytest.skip(f"{name} absent (export personnel, hors git)")
    return path.read_bytes()


def test_bikerouter_default_auto_choose_exports_no_instructions():
    with pytest.raises(UnsupportedGpx, match="turnInstructionMode"):
        read_gpx(local("bikerouter_auto_choose.gpx"))


def test_bikerouter_osmand_style_export_converts():
    route = read_gpx(local("bikerouter_osmand_style.gpx"))
    # In osmand-style the web client rewrites the creator to "OsmAndRouter".
    assert route.source == "brouter"
    written = bryton.decode(bryton.encode(route))[0].cues
    assert Counter(cue.type for cue in written) == {
        C.SLIGHT_RIGHT: 22, C.RIGHT: 20, C.SLIGHT_LEFT: 19, C.LEFT: 13,
        C.STRAIGHT: 7, C.KEEP_RIGHT: 5, C.END_OF_ROUTE: 1,
    }
    assert written[-1].index == len(route.points) - 1


def test_both_exports_describe_the_same_ride():
    """Only the instruction setting differed between the two exports."""
    with_cues = read_gpx(local("bikerouter_osmand_style.gpx"))
    line_only = read_gpx(local("bikerouter_auto_choose.gpx"), allow_no_cues=True)
    assert with_cues.points == line_only.points
