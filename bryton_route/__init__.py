"""GPX to Bryton Rider 420 route converter.

    fit_bytes, route = convert(gpx_bytes, name="Ma sortie")

Standard library only, so the same code runs from a terminal, under Termux on
a phone, or in a browser through Pyodide. See README.md.
"""

from . import geo
from . import online as _online
from .bryton import decode, encode
from .route import NO_CUES, ONLY_ENDS, UnsupportedGpx, has_turns, read_gpx, read_gpx_file
from .simplify import DEFAULT_TOLERANCE_M
from .simplify import simplify as _simplify
from .web import OnlineError

__version__ = "0.4.0"
__all__ = [
    "OnlineError",
    "UnsupportedGpx",
    "convert",
    "decode",
    "encode",
    "read_gpx",
    "read_gpx_file",
]


def convert(data, name="", tolerance_m=DEFAULT_TOLERANCE_M, online=None, allow_no_cues=False):
    """GPX bytes in; ``(PlanTrip .fit bytes, route written)`` out.

    ``online``: None uses the GPX's own instructions and goes online only when
    they hold no turn; True always computes them online; False never goes online.
    """
    route = read_gpx(data, name=name, allow_no_cues=True)
    if online or (online is None and not has_turns(route)):
        if route.cues and not online:
            route.warnings.append(ONLY_ENDS)
        route = _online.add_cues(route)
    elif not route.cues and not allow_no_cues:
        raise UnsupportedGpx(NO_CUES.format(source=route.source))
    length = sum(geo.path_lengths(route.points))
    route = _simplify(route, tolerance_m)
    return encode(route, total_distance=length), route
