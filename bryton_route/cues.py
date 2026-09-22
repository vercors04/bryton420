"""Reading a turn instruction off one GPX point.

Planners disagree about where the manoeuvre goes, so four encodings are
tried, most explicit first:

1. a turn code: BRouter's OsmAnd and Gpsies styles (``<turn>TSLR</turn>``,
   ``<type>TSLR</type>``), or OpenRouteService's numeric ``<type>``;
2. a symbol name: BRouter's Locus style and PlotARoute
   (``<sym>right_slight</sym>``, ``<sym>Left_slight</sym>``);
3. a bare instruction phrase: BRouter's ``<desc>`` and ``<name>``
   (``slight right``, ``Take exit 2``);
4. a sentence in French, English or German
   (``Tournez légèrement à droite sur Rue d'Alsace``).

Only codes corroborated against the reference file come out as a
:class:`CueType`. Roundabouts and u-turns have no confirmed Bryton code, so
they come back as :data:`ROUNDABOUT` or :data:`UTURN` and ``route.py`` turns
them into a plain direction from the measured angle.
"""

import re
from dataclasses import dataclass

from .model import CueType

ROUNDABOUT = "roundabout"
UTURN = "u-turn"

END = CueType.END_OF_ROUTE


@dataclass(frozen=True)
class Hint:
    kind: object  # a CueType, ROUNDABOUT or UTURN
    label: str = ""
    #: Roundabout exit number, when stated.
    exit: int | None = None
    #: Turn angle in degrees, when the planner provides one (BRouter does). Only
    #: U-turns use it: a roundabout's direction is measured on the route.
    angle: float | None = None


# -- 1. Turn codes ------------------------------------------------------------

#: BRouter voice hint commands (btools.router.VoiceHint), plus the spelled-out
#: symbols its Gpsies style writes for the three most common ones.
TURN_CODES = {
    "C": CueType.STRAIGHT,
    "STRAIGHT": CueType.STRAIGHT,
    "TL": CueType.LEFT,
    "LEFT": CueType.LEFT,
    "TSLL": CueType.SLIGHT_LEFT,
    "TSHL": CueType.SHARP_LEFT,
    "TR": CueType.RIGHT,
    "RIGHT": CueType.RIGHT,
    "TSLR": CueType.SLIGHT_RIGHT,
    "TSHR": CueType.SHARP_RIGHT,
    "KL": CueType.KEEP_LEFT,
    "KR": CueType.KEEP_RIGHT,
    "EL": CueType.KEEP_LEFT,
    "ER": CueType.KEEP_RIGHT,
    "TU": UTURN,
    "TLU": UTURN,
    "TRU": UTURN,
    "END": END,
}
_ROUNDABOUT_CODE = re.compile(r"^RN[DL]B-?(\d+)$")

#: OpenRouteService instruction types.
ORS_TYPES = {
    0: CueType.LEFT,
    1: CueType.RIGHT,
    2: CueType.SHARP_LEFT,
    3: CueType.SHARP_RIGHT,
    4: CueType.SLIGHT_LEFT,
    5: CueType.SLIGHT_RIGHT,
    6: CueType.STRAIGHT,
    7: ROUNDABOUT,  # enter roundabout
    8: ROUNDABOUT,  # exit roundabout
    9: UTURN,
    10: END,
    11: CueType.STRAIGHT,  # depart
    12: CueType.KEEP_LEFT,
    13: CueType.KEEP_RIGHT,
}


def _code(value):
    if not value:
        return None, None
    value = value.strip().upper()
    if match := _ROUNDABOUT_CODE.match(value):
        return ROUNDABOUT, int(match.group(1))
    return TURN_CODES.get(value), None


# -- 2 and 3. Symbols and bare phrases, compared as word sets --------------------

_WORDS = re.compile(r"[\s_,-]+")

PHRASES = {
    frozenset(words.split()): kind
    for words, kind in (
        ("straight", CueType.STRAIGHT),
        ("continue", CueType.STRAIGHT),
        ("start", CueType.STRAIGHT),
        ("left", CueType.LEFT),
        ("right", CueType.RIGHT),
        ("slight left", CueType.SLIGHT_LEFT),
        ("slight right", CueType.SLIGHT_RIGHT),
        ("sharp left", CueType.SHARP_LEFT),
        ("sharp right", CueType.SHARP_RIGHT),
        ("keep left", CueType.KEEP_LEFT),
        ("keep right", CueType.KEEP_RIGHT),
        ("left fork", CueType.KEEP_LEFT),
        ("right fork", CueType.KEEP_RIGHT),
        ("u turn", UTURN),
        ("uturn", UTURN),
        ("destination", END),
        ("finish", END),
    )
}
_TAKE_EXIT = re.compile(r"^take\s+exit\s+-?(\d+)$", re.IGNORECASE)


def _words(text):
    return frozenset(w for w in _WORDS.split(text.lower()) if w)


# -- 4. Sentences -----------------------------------------------------------------

_A = "[aàâ]"
_E = "[eéèê]"
#: Small words slipped between verb and side: "Restez sur la droite".
_FILLER = rf"(?:\s+(?:sur|on|the|la|le|les|{_A}))*\s+"
_RIGHT = r"(?:right|droite|rechts)\b"
_LEFT = r"(?:left|gauche|links)\b"
_TURN = r"\b(?:turn|tournez?|tourner|abbiegen|biegen)\w*"
_KEEP = r"\b(?:keep|restez?|rester|maintenez?|serrez?|halten)\b"
_SHARP = rf"\b(?:sharp|scharf|fort|tr{_E}s\s+serr)\w*"
_SLIGHT = rf"\b(?:slight|leicht|l{_E}g{_E}rement|legerement)\w*"

#: Most specific first: "slight right" has to win over "right", and a turn
#: onto "Rue de l'Arrivée" has to stay a turn.
SENTENCES = [
    (r"\b(?:rond[\s-]?point|roundabout|kreisverkehr|giratoire)\b", ROUNDABOUT),
    (r"\b(?:demi[\s-]?tour|u[\s-]?turn|wenden)\b", UTURN),
    (_SHARP + _FILLER + _RIGHT, CueType.SHARP_RIGHT),
    (_SHARP + _FILLER + _LEFT, CueType.SHARP_LEFT),
    (_SLIGHT + rf"\s*(?:{_A}\s+)?" + _RIGHT, CueType.SLIGHT_RIGHT),
    (_SLIGHT + rf"\s*(?:{_A}\s+)?" + _LEFT, CueType.SLIGHT_LEFT),
    (r"\bbear\s+" + _RIGHT, CueType.SLIGHT_RIGHT),
    (r"\bbear\s+" + _LEFT, CueType.SLIGHT_LEFT),
    (_KEEP + _FILLER + _RIGHT, CueType.KEEP_RIGHT),
    (_KEEP + _FILLER + _LEFT, CueType.KEEP_LEFT),
    (r"\b(?:exit|sortie|ausfahrt)\b.*\b" + _RIGHT, CueType.KEEP_RIGHT),
    (r"\b(?:exit|sortie|ausfahrt)\b.*\b" + _LEFT, CueType.KEEP_LEFT),
    (_TURN + rf"\s*(?:{_A}\s+)?" + _RIGHT, CueType.RIGHT),
    (_TURN + rf"\s*(?:{_A}\s+)?" + _LEFT, CueType.LEFT),
    (r"\b(?:right|droite|rechts)\s+(?:abbiegen|onto|sur)\b", CueType.RIGHT),
    (r"\b(?:left|gauche|links)\s+(?:abbiegen|onto|sur)\b", CueType.LEFT),
    (rf"\b(?:finish|arriv{_E}e?|arrive|destination|ziel)\b", END),
    (
        rf"\b(?:straight|tout\s+droit|geradeaus|continuez?|continue|poursuivez?|"
        rf"empruntez?|head|start|d{_E}part|depart|weiter)\b",
        CueType.STRAIGHT,
    ),
]
_SENTENCES = [(re.compile(p, re.IGNORECASE), kind) for p, kind in SENTENCES]

_TAGS = re.compile(r"<[^>]*>")
_STREET_SPLIT = re.compile(
    r"\s(?:onto|on|toward|towards|sur|vers|en\s+direction\s+de|direction|auf|Richtung)\s+",
    re.IGNORECASE,
)
#: A remainder that names a side rather than a road ("la droite sur ...").
_SIDE = re.compile(
    rf"^(?:{_A}\s+|la\s+|le\s+|the\s+)?(?:droite|gauche|right|left|rechts|links)\b", re.IGNORECASE
)
_TRAILING_SIDE = re.compile(
    r",?\s*(?:on|sur)\s+(?:the|la|le)\s+(?:left|right|gauche|droite)\s*$", re.IGNORECASE
)
_TRAILING_CLAUSE = re.compile(r"\s+(?:and|then|puis|et\s+continuez)\s+.*$", re.IGNORECASE)
_EXIT_NUMBER = re.compile(
    r"(\d+)\s*(?:st|nd|rd|th|e|er|re|ème|\.)?\s*(?:exit|sortie|ausfahrt)"
    r"|(?:exit|sortie|ausfahrt)\s*(?:n[o°]?\s*)?(\d+)",
    re.IGNORECASE,
)


def clean(text):
    """Strip markup and collapse whitespace."""
    return " ".join(_TAGS.sub(" ", text or "").split())


def street_name(sentence):
    """The road an instruction sentence names, or ""."""
    text = _TRAILING_SIDE.sub("", clean(sentence))
    # Earliest separator whose remainder reads like a road: "Route de Nogent
    # sur Seine" survives, "Restez sur la droite sur X" resolves to X.
    for match in _STREET_SPLIT.finditer(text):
        street = text[match.end() :].strip(" ,.;:-")
        if street and not _SIDE.match(street):
            return _TRAILING_CLAUSE.sub("", street).strip()
    return ""


def exit_number(sentence):
    if match := _EXIT_NUMBER.search(sentence or ""):
        return int(match.group(1) or match.group(2))
    return None


def phrase_kind(text):
    """Classify a bare phrase or a sentence. Returns (kind, exit number)."""
    text = clean(text)
    if not text:
        return None, None
    if match := _TAKE_EXIT.match(text):
        return ROUNDABOUT, int(match.group(1))
    if (kind := PHRASES.get(_words(text))) is not None:
        return kind, None
    for pattern, kind in _SENTENCES:
        if pattern.search(text):
            return kind, exit_number(text)
    return None, None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_hint(point, ors_types=False):
    """The instruction carried by a parsed GPX point, or None.

    ``ors_types`` enables OpenRouteService's numeric ``<type>`` table; other
    planners put free text such as "Waypoint" in that element.
    """
    ext = point.extensions
    texts = [t for t in map(clean, (point.cmt, point.desc, point.name)) if t]

    kind, exit_ = _code(ext.get("turn"))
    if kind is None and ors_types:
        kind = ORS_TYPES.get(_int(point.type if point.type is not None else ext.get("type")))
    if kind is None:
        kind, exit_ = _code(point.type)
    if kind is None:
        kind, exit_ = _code(point.sym)
    if kind is None and point.sym:
        kind = PHRASES.get(_words(point.sym))
    if kind is None:
        for text in texts:
            kind, exit_ = phrase_kind(text)
            if kind is not None:
                break
    if kind is None:
        return None

    if kind is ROUNDABOUT and exit_ is None:
        exit_ = exit_number(" ".join(texts))
    label = "" if kind is END else next(filter(None, map(street_name, texts)), "")
    try:
        angle = float(ext["turn-angle"])
    except (KeyError, ValueError):
        angle = None
    return Hint(kind, label, exit_ if kind is ROUNDABOUT else None, angle)
