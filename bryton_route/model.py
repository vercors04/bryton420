"""The route model: what every GPX source is read into and the encoder writes."""

from dataclasses import dataclass, field
from enum import IntEnum


class CueType(IntEnum):
    """Manoeuvre codes, valued with the byte the Rider reads.

    Each member was checked against the reference file by correlating its code
    with the bearing change measured at its vertex (doc/format.md). Codes that
    appear there without being direction codes (0xC9, 0xD2-0xD5) are left out
    on purpose.
    """

    STRAIGHT = 0x01
    RIGHT = 0x02
    LEFT = 0x03
    SLIGHT_RIGHT = 0x04
    SLIGHT_LEFT = 0x05
    SHARP_RIGHT = 0x06
    SHARP_LEFT = 0x07
    KEEP_RIGHT = 0x08
    KEEP_LEFT = 0x09
    END_OF_ROUTE = 0x21


@dataclass(frozen=True)
class RoutePoint:
    lat: float
    lon: float
    ele: float | None = None


@dataclass(frozen=True)
class Cue:
    """An instruction on geometry vertex ``index`` -- never a point of its own."""

    index: int
    type: CueType | int
    label: str = ""


@dataclass
class Route:
    points: list
    cues: list = field(default_factory=list)
    name: str = ""
    source: str = ""
    warnings: list = field(default_factory=list)

    def validate(self):
        """Raise ValueError if the route cannot be written as a Bryton file."""
        count = len(self.points)
        if count < 2:
            raise ValueError(f"un itinéraire demande au moins 2 points, pas {count}")
        if count > 0xFFFF:
            raise ValueError(
                f"{count} points dépassent la limite du format (65 535) ; "
                "augmente --tolerance"
            )
        previous = -1
        for cue in self.cues:
            if not previous < cue.index < count:
                raise ValueError(
                    f"instruction sur le point {cue.index} hors de l'ordre du tracé"
                )
            previous = cue.index
