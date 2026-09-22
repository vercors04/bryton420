"""GPX parsing: XML bytes in, plain objects out. No interpretation."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

_TEXT_FIELDS = ("name", "cmt", "desc", "sym", "type")


@dataclass
class GpxPoint:
    """A ``<wpt>``, ``<rtept>`` or ``<trkpt>``."""

    lat: float
    lon: float
    ele: float | None = None
    name: str | None = None
    cmt: str | None = None
    desc: str | None = None
    sym: str | None = None
    type: str | None = None
    #: Children of ``<extensions>``, flattened by local tag name.
    extensions: dict = field(default_factory=dict)


@dataclass
class GpxDoc:
    creator: str = ""
    name: str = ""
    desc: str = ""
    waypoints: list = field(default_factory=list)
    #: One point list per ``<rte>``.
    routes: list = field(default_factory=list)
    #: One point list per ``<trk>``, segments joined.
    tracks: list = field(default_factory=list)


def _tag(element):
    return element.tag.rpartition("}")[2]


def _text(element):
    text = (element.text or "").strip()
    return text or None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flatten(element, into):
    for child in element:
        text = _text(child)
        if text is not None:
            into[_tag(child)] = text
        _flatten(child, into)


def _point(element):
    lat, lon = _float(element.get("lat")), _float(element.get("lon"))
    if lat is None or lon is None:
        return None
    point = GpxPoint(lat, lon)
    for child in element:
        tag = _tag(child)
        if tag == "ele":
            point.ele = _float(_text(child))
        elif tag == "extensions":
            _flatten(child, point.extensions)
        elif tag in _TEXT_FIELDS:
            setattr(point, tag, _text(child))
    return point


def _points(elements, tag):
    return [p for e in elements if _tag(e) == tag and (p := _point(e)) is not None]


def parse(data):
    """Parse GPX bytes.

    Pass bytes rather than text so the XML declaration decides the encoding:
    guessing it from the system locale is how accented road names used to
    turn into mojibake on Windows.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"XML invalide : {exc}") from exc
    if _tag(root) != "gpx":
        raise ValueError(f"élément racine <{_tag(root)}>, <gpx> attendu")

    doc = GpxDoc(creator=root.get("creator") or "")
    for child in root:
        tag = _tag(child)
        if tag == "metadata":
            for meta in child:
                if _tag(meta) in ("name", "desc"):
                    setattr(doc, _tag(meta), _text(meta) or "")
        elif tag == "wpt":
            if (point := _point(child)) is not None:
                doc.waypoints.append(point)
        elif tag == "rte":
            if points := _points(child, "rtept"):
                doc.routes.append(points)
        elif tag == "trk":
            points = []
            for segment in child:
                if _tag(segment) == "trkseg":
                    points += _points(segment, "trkpt")
            if points:
                doc.tracks.append(points)
    return doc
