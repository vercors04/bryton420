"""Online mode: turn instructions and street names for a GPX that has none.

Measured on an 11.4 km bicycle route across Lyon whose 48 manoeuvres and 33
street names were known, each method seeing only the bare line:

    method                          found   right side   street names
    BRouter re-routed on the line   48/48     46/48          0/33
    Valhalla map matching           39/48     36/39         26/33
    BRouter turns + Valhalla names  48/48     46/48         30/33

So BRouter places the turns and Valhalla names the streets. Where BRouter
leaves the line -- a road its bicycle profile avoids -- its turns lead onto its
own roads, so Valhalla's instructions replace them over that passage only.
When BRouter refuses the line, or follows too little of it, Valhalla supplies
every turn.

Valhalla is asked first, because its match also tells a planner's line (on the
roads to the centimetre) from a recorded track (metres off, zigzagging). The
latter is replaced by its matched positions before BRouter sees it: with the
same Lyon route recorded with simulated GPS noise, 772 cues became 74, and 41
of the 48 manoeuvres were found, 38 on the right side, 29 of 33 streets named.
"""

from bisect import bisect_left, bisect_right
from dataclasses import replace
from itertools import accumulate
from math import isinf

from . import brouterweb, geo, valhalla, web
from .cues import Hint
from .model import CueType, Route
from .route import resolve_cues, ring_length

#: Around a passage BRouter does not follow, its turns lead onto its own
#: roads: Valhalla's instructions replace them over the passage and this margin.
MARGIN_M = 150.0
#: Beyond this share of the line off BRouter's route, Valhalla takes over entirely.
MAX_STRAY_SHARE = 0.5
#: A line further than this from the roads, as a median, is a recorded or
#: hand-drawn track: 0.00 m for a planner's Lyon line, 2.5 m once GPS noise
#: was added to it.
NOISY_M = 1.0
#: More cues than this per kilometre is reported: planners' exports reach 9 in
#: a city centre, a GPS-noisy track fed straight to BRouter reached 68.
MAX_CUES_PER_KM = 15
#: A street name is read this far past the cue, on the way the rider takes.
NAME_AHEAD_M = 15.0
#: ...and given up on if nothing named turns up within this further distance.
NAME_SEARCH_M = 80.0


def _km(metres):
    return f"{metres / 1000:.1f}".replace(".", ",")


def _roundabout(hint):
    return hint.label.startswith("(") and hint.label.endswith(")")


def _name_after(names, along, index, ahead_m=NAME_AHEAD_M):
    target = along[index] + ahead_m
    for k in range(index, len(names)):
        if along[k] < target:
            continue
        if along[k] > target + NAME_SEARCH_M:
            break
        if names[k]:
            return names[k]
    return ""


def _named(hint, name):
    """Put a street name on a cue, keeping a roundabout's "(2)" exit prefix."""
    if hint.kind == CueType.END_OF_ROUTE or not name:
        return hint
    if _roundabout(hint):
        return Hint(hint.kind, f"{hint.label} {name}")
    return Hint(hint.kind, hint.label or name)


def _street_names(placed, names, points, along):
    """Each cue named after the street it leads onto; for a roundabout, the
    street past the ring rather than the ring itself, when that one has a name."""
    named = []
    for i, hint in placed:
        name = ""
        if _roundabout(hint):
            name = _name_after(names, along, i, NAME_AHEAD_M + ring_length(points, i, along))
        name = name or _name_after(names, along, i)
        named.append((i, _named(hint, name)))
    return named


def _passages(strays, along):
    """Stray ranges widened by MARGIN_M along the line and merged: [(first, last)]."""
    passages = []
    for first, last, _worst in strays:
        start = bisect_left(along, along[first] - MARGIN_M)
        end = bisect_right(along, along[last] + MARGIN_M) - 1
        if passages and start <= passages[-1][1]:
            passages[-1][1] = max(passages[-1][1], end)
        else:
            passages.append([start, end])
    return [tuple(p) for p in passages]


def _inside(index, passages):
    return any(first <= index <= last for first, last in passages)


def _where(strays, along):
    parts = []
    for first, last, worst in strays[:3]:
        far = "plus de 150 m" if isinf(worst) else f"{worst:.0f} m"
        start, end = _km(along[first]), _km(along[last])
        where = f"km {start}" if start == end else f"km {start}–{end}"
        parts.append(f"{where}, jusqu'à {far}")
    if len(strays) > 3:
        parts.append(f"et {len(strays) - 3} autre(s)")
    return " ; ".join(parts)


def _valhalla_only(route, warnings, reason, server):
    warnings.append(f"{reason} ; virages calculés par Valhalla seul, moins complets")
    return valhalla.instructions(replace(route, warnings=warnings), server=server)


def _on_the_roads(route, warnings, server):
    """Valhalla's match of the line: street names, and for a recorded track,
    the route moved onto the roads. Returns (route, names or None, tolerance)."""
    try:
        matched = valhalla.match(route.points, server=server)
    except web.OnlineError as exc:
        warnings.append(f"noms de rue indisponibles ({exc})")
        return route, None, brouterweb.WAYPOINT_TOLERANCE_M
    if matched.offset_m <= NOISY_M:
        return route, matched.names, brouterweb.WAYPOINT_TOLERANCE_M
    warnings.append(
        f"trace imprécise, à {matched.offset_m:.0f} m des routes en moyenne "
        "(trace enregistrée ou tracée à main levée ?) : recalée sur les routes"
    )
    return replace(route, points=matched.points), matched.names, brouterweb.NOISY_TOLERANCE_M


def _check_density(cues, along, warnings):
    kilometres = along[-1] / 1000
    if kilometres >= 1 and len(cues) > MAX_CUES_PER_KM * kilometres:
        warnings.append(
            f"{len(cues)} instructions pour {_km(along[-1])} km, anormalement serrées : "
            "vérifie la liste avec doctor avant de partir"
        )


def add_cues(route, brouter=brouterweb.DEFAULT_SERVER, valhalla_server=valhalla.DEFAULT_SERVER):
    """A copy of ``route`` whose cues are computed online from its line."""
    warnings = list(route.warnings)
    route, names, tolerance = _on_the_roads(route, warnings, valhalla_server)
    along = [0.0, *accumulate(geo.path_lengths(route.points))]
    try:
        placed, strays = brouterweb.cues_along(
            route.points, server=brouter, tolerance_m=tolerance
        )
    except web.OnlineError as exc:
        return _valhalla_only(route, warnings, str(exc), valhalla_server)

    passages = _passages(strays, along)
    strayed = sum(along[last] - along[first] for first, last in passages)
    if strayed > MAX_STRAY_SHARE * along[-1]:
        reason = f"BRouter ne suit pas la trace sur {_km(strayed)} km"
        return _valhalla_only(route, warnings, reason, valhalla_server)

    if names is not None:
        placed = _street_names(placed, names, route.points, along)

    if passages:
        placed = [(i, hint) for i, hint in placed if not _inside(i, passages)]
        where = _where(strays, along)
        try:
            matched = valhalla.instructions(replace(route, warnings=[]), server=valhalla_server)
        except web.OnlineError as exc:
            warnings.append(
                f"BRouter s'écarte de la trace ({where}) et Valhalla n'a pas répondu "
                f"({exc}) : aucune instruction sur ces passages"
            )
        else:
            placed += [
                (cue.index, Hint(cue.type, cue.label))
                for cue in matched.cues
                if _inside(cue.index, passages)
            ]
            warnings.append(
                f"BRouter s'écarte de la trace par endroits ({where}) : "
                "instructions de Valhalla sur ces passages"
            )

    cues = resolve_cues(placed, route.points, warnings)
    _check_density(cues, along, warnings)
    result = Route(
        route.points,
        cues,
        name=route.name,
        source=f"{route.source} + BRouter + Valhalla",
        warnings=warnings,
    )
    result.validate()
    return result
