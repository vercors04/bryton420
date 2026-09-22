"""The Bryton route profile: the seven private FIT messages a Rider reads.

Nothing here is guessed. Layout and semantics come from a file written by the
Bryton Active app and the navigation files a Rider 420 (firmware R057) built
from it, kept in ``tests/data/ex_bon``; ``doc/format.md`` gives the evidence
for each field, and the tests check the encoder against that file.
"""

import struct

from . import fit, geo
from .model import Cue, CueType, Route, RoutePoint

#: Profile version written by the Bryton Active app.
PROFILE_VERSION = 2088

MSG_FORMAT = 248
MSG_POINT = 249
MSG_CUE = 250
MSG_POINT_INDEX = 251
MSG_POINT_COUNT = 252
MSG_CUE_COUNT = 253
MSG_SUMMARY = 254

_U16 = ((1, 2, fit.UINT16),)

# (local type, global number, fields), in the order the app writes them.
_FORMAT = (7, MSG_FORMAT, ((1, 2, fit.UINT16), (2, 2, fit.UINT16)))
_SUMMARY = (1, MSG_SUMMARY, (
    (1, 2, fit.UINT16),   # point count
    (2, 4, fit.SINT32),   # northernmost latitude
    (3, 4, fit.SINT32),   # southernmost latitude
    (4, 4, fit.SINT32),   # easternmost longitude
    (5, 4, fit.SINT32),   # westernmost longitude
    (6, 4, fit.UINT32),   # total distance, metres
    (7, 2, fit.UINT16),   # maximum altitude, FIT scale
    (8, 2, fit.UINT16),   # minimum altitude, FIT scale
    (9, 2, fit.UINT16),   # total ascent, metres
    (10, 2, fit.UINT16),  # total descent, metres
))
_POINT_INDEX = (2, MSG_POINT_INDEX, _U16)
_CUE_COUNT = (3, MSG_CUE_COUNT, _U16)
_CUE_LOCAL = 4
_POINT_COUNT = (5, MSG_POINT_COUNT, _U16)
_POINT = (6, MSG_POINT, (
    (1, 4, fit.SINT32),   # latitude, microdegrees
    (2, 4, fit.SINT32),   # longitude, microdegrees
    (3, 2, fit.UINT16),   # altitude, FIT scale
))

#: Longest road name the app stores, in bytes, not counting the NUL.
LABEL_BYTES = 32

#: The app opens the cue table with a 32-byte label field and re-defines the
#: message one byte wider as soon as a name needs all 32 bytes plus its NUL.
_LABEL_FIELD_START = 32

ALT_SCALE = 5
ALT_OFFSET = 500


def _cue_fields(label_field):
    return (
        (1, 2, fit.UINT16),            # index of the point the cue sits on
        (2, 1, fit.ENUM),              # CueType
        (3, 4, fit.UINT32),            # distance to the next cue, metres
        (4, 4, fit.UINT32),            # always zero
        (5, label_field, fit.STRING),  # road name
    )


def encode_coord(degrees):
    return round(degrees * 1_000_000)


def decode_coord(raw):
    return raw / 1_000_000


def encode_altitude(metres):
    """Metres to the standard FIT altitude scale: (m + 500) * 5 in a uint16."""
    raw = round(((metres or 0.0) + ALT_OFFSET) * ALT_SCALE)
    if not 0 <= raw <= 0xFFFF:
        raise ValueError(f"altitude {metres} m hors de la plage -500..12607 m")
    return raw


def decode_altitude(raw):
    return raw / ALT_SCALE - ALT_OFFSET


def elevation_gain(points):
    """(total ascent, total descent) in metres."""
    up = down = 0.0
    previous = None
    for point in points:
        if point.ele is None:
            continue
        if previous is not None:
            delta = point.ele - previous
            up += max(delta, 0.0)
            down += max(-delta, 0.0)
        previous = point.ele
    return up, down


def cue_distances(cues, segments):
    """Cue field 3: the along-route distance from each cue to the next one."""
    ends = [cue.index for cue in cues[1:]] + [len(segments)]
    return [round(sum(segments[cue.index : end])) for cue, end in zip(cues, ends)]


def framed_cues(route):
    """The cues as the app frames them: one on the first point, END on the last."""
    cues = list(route.cues)
    last = len(route.points) - 1
    if cues and cues[-1].type == CueType.END_OF_ROUTE and cues[-1].index != last:
        cues.pop()  # an arrival placed short of the last point moves onto it
    if not cues or cues[0].index != 0:
        cues.insert(0, Cue(0, CueType.STRAIGHT))
    if cues[-1].index == last:
        cues[-1] = Cue(last, CueType.END_OF_ROUTE)
    else:
        cues.append(Cue(last, CueType.END_OF_ROUTE))
    return cues


def fit_label(text, limit=LABEL_BYTES):
    """A road name within ``limit`` UTF-8 bytes. Trailing parts go first, as in
    "Avenue Jean Moulin, D 306", then the name is cut between two words."""
    if len(text.encode("utf-8")) <= limit:
        return text
    parts = [part.strip() for part in text.split(",")]
    while len(parts) > 1 and len(", ".join(parts).encode("utf-8")) > limit:
        parts.pop()
    text = ", ".join(parts)
    if len(text.encode("utf-8")) <= limit:
        return text
    cut = fit.truncate_utf8(text.encode("utf-8"), limit).decode("utf-8")
    if text[len(cut) : len(cut) + 1] not in ("", " "):
        space = cut.rfind(" ")
        if space >= limit // 2:
            cut = cut[:space]
    return cut.rstrip(" ,-'")


def _u16(value):
    return min(round(value), 0xFFFF)


def encode(route, total_distance=None):
    """Serialise a Route into the bytes of a ``PlanTrip/*.fit`` file.

    ``total_distance`` (metres) lets a caller that simplified the geometry keep
    reporting the length of the original route.
    """
    route.validate()
    points = route.points
    cues = framed_cues(route)
    segments = geo.path_lengths(points)
    if total_distance is None:
        total_distance = sum(segments)
    lats = [encode_coord(p.lat) for p in points]
    lons = [encode_coord(p.lon) for p in points]
    alts = [encode_altitude(p.ele) for p in points]
    ascent, descent = elevation_gain(points)

    w = fit.FitWriter(PROFILE_VERSION)

    def emit(message, *values):
        local, global_num, fields = message
        w.define(local, global_num, fields)
        w.write(local, *values)

    emit(_FORMAT, 0, 1)
    emit(
        _SUMMARY,
        len(points), max(lats), min(lats), max(lons), min(lons),
        round(total_distance), max(alts), min(alts), _u16(ascent), _u16(descent),
    )

    local, global_num, fields = _POINT_INDEX
    w.define(local, global_num, fields)
    for index in range(len(points)):
        w.write(local, index)

    emit(_CUE_COUNT, len(cues))

    width = _LABEL_FIELD_START
    w.define(_CUE_LOCAL, MSG_CUE, _cue_fields(width))
    for cue, distance in zip(cues, cue_distances(cues, segments)):
        label = fit_label(cue.label).encode("utf-8")
        if len(label) + 1 > width:
            width = len(label) + 1
            w.define(_CUE_LOCAL, MSG_CUE, _cue_fields(width))
        w.write(_CUE_LOCAL, cue.index, int(cue.type), distance, 0, label)

    emit(_POINT_COUNT, len(points))

    local, global_num, fields = _POINT
    w.define(local, global_num, fields)
    for lat, lon, alt in zip(lats, lons, alts):
        w.write(local, lat, lon, alt)

    return w.to_bytes()


def _cue_type(value):
    try:
        return CueType(value)
    except ValueError:
        return value  # a code outside the confirmed set: keep the raw byte


def decode(data):
    """Read ``PlanTrip/*.fit`` bytes back into ``(route, summary)``."""
    f = fit.read(data)
    summaries = f.by_global(MSG_SUMMARY)
    if not summaries:
        raise ValueError("fichier FIT valide, mais pas un itinéraire Bryton")
    s = summaries[0]
    counts = {num: f.by_global(num) for num in (MSG_CUE_COUNT, MSG_POINT_COUNT)}

    points = [
        RoutePoint(decode_coord(lat), decode_coord(lon), decode_altitude(alt))
        for lat, lon, alt in f.by_global(MSG_POINT)
    ]
    cue_messages = f.by_global(MSG_CUE)
    cues = [Cue(v[0], _cue_type(v[1]), v[4]) for v in cue_messages]

    summary = {
        "profile_version": f.profile_version,
        "crc_ok": f.crc_ok,
        "points": s[0],
        "north": decode_coord(s[1]),
        "south": decode_coord(s[2]),
        "east": decode_coord(s[3]),
        "west": decode_coord(s[4]),
        "distance_m": s[5],
        "alt_max_m": decode_altitude(s[6]),
        "alt_min_m": decode_altitude(s[7]),
        "ascent_m": s[8] if len(s) > 8 else None,
        "descent_m": s[9] if len(s) > 9 else None,
        "declared_cues": counts[MSG_CUE_COUNT][0][0] if counts[MSG_CUE_COUNT] else None,
        "declared_points": counts[MSG_POINT_COUNT][0][0] if counts[MSG_POINT_COUNT] else None,
        "point_index_entries": len(f.by_global(MSG_POINT_INDEX)),
        "cue_distances": [v[2] for v in cue_messages],
        "definitions": f.definitions,
    }
    return Route(points=points, cues=cues, source="bryton"), summary


def read_tinfo(data):
    """Decode a device-written ``Tracks/*.tinfo``: the cue table as the Rider stored it.

    44 bytes per cue: uint16 point index, uint16 type, uint32 distance, uint32
    flag, 32-byte road name.
    """
    if len(data) % 44:
        raise ValueError(".tinfo : la taille n'est pas un multiple de 44 octets")
    out = []
    for offset in range(0, len(data), 44):
        index, kind, distance, _flag = struct.unpack_from("<HHII", data, offset)
        label = data[offset + 12 : offset + 44].split(b"\0", 1)[0]
        out.append((Cue(index, _cue_type(kind), label.decode("utf-8", "replace")), distance))
    return out


def read_track(data):
    """Decode a device-written ``Tracks/*.track``: the points as the Rider stored them.

    16 bytes per point: microdegree latitude and longitude, altitude in plain
    metres, and a field that is zero in every sample seen.
    """
    if len(data) % 16:
        raise ValueError(".track : la taille n'est pas un multiple de 16 octets")
    return [
        RoutePoint(lat / 1_000_000, lon / 1_000_000, float(ele))
        for lat, lon, ele, _zero in struct.iter_unpack("<iiii", data)
    ]
