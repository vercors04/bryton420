"""Minimal FIT (Flexible and Interoperable Data Transfer) container codec.

A Bryton route file is a standard FIT file whose messages use private global
numbers. This module handles the container only and knows nothing about
routes; ``bryton.py`` holds the route profile.
"""

import struct
from dataclasses import dataclass

# Base types appearing in Bryton route files.
ENUM = 0x00
UINT16 = 0x84
SINT32 = 0x85
UINT32 = 0x86
STRING = 0x07

# Every numeric base type of the FIT protocol, as a struct format.
_FORMATS = {
    0x00: "B", 0x01: "b", 0x02: "B", 0x0A: "B", 0x0D: "B",
    0x83: "h", 0x84: "H", 0x8B: "H",
    0x85: "i", 0x86: "I", 0x8C: "I",
    0x88: "f", 0x89: "d",
}

HEADER_SIZE = 14
MAGIC = b".FIT"

_DEFINITION = 0x40
_DEVELOPER_FIELDS = 0x20
_COMPRESSED_TIMESTAMP = 0x80

_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)


def crc16(data, crc=0):
    """The FIT protocol CRC-16."""
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            tmp = _CRC_TABLE[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc ^= tmp ^ _CRC_TABLE[nibble]
    return crc


def truncate_utf8(raw, limit):
    """Cut ``raw`` to at most ``limit`` bytes without splitting a character."""
    if len(raw) <= limit:
        return raw
    cut = limit
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:  # continuation byte
        cut -= 1
    return raw[:cut]


class FitWriter:
    """Accumulates definition and data messages, then serialises the file."""

    def __init__(self, profile_version, protocol_version=0x10):
        self.profile_version = profile_version
        self.protocol_version = protocol_version
        self._body = bytearray()
        self._layouts = {}

    def define(self, local, global_num, fields):
        """Emit a definition message. ``fields`` holds (number, size, base type)."""
        fields = tuple(fields)
        self._layouts[local] = fields
        self._body += struct.pack(
            "<BBBHB", _DEFINITION | local, 0, 0, global_num, len(fields)
        )
        for field in fields:
            self._body += bytes(field)

    def write(self, local, *values):
        """Emit a data message against the last definition of ``local``."""
        fields = self._layouts.get(local)
        if fields is None:
            raise ValueError(f"message local {local} écrit avant d'être défini")
        if len(values) != len(fields):
            raise ValueError(
                f"message local {local} : {len(fields)} champs attendus, "
                f"{len(values)} fournis"
            )
        self._body.append(local)
        for (number, size, base), value in zip(fields, values):
            if base == STRING:
                raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
                # Strings are NUL terminated: keep room for the terminator.
                self._body += truncate_utf8(raw, size - 1).ljust(size, b"\0")
                continue
            try:
                self._body += struct.pack("<" + _FORMATS[base], value)
            except struct.error as exc:
                raise ValueError(
                    f"champ {number} du message local {local} : {value!r} hors "
                    f"limites ({exc})"
                ) from exc

    def to_bytes(self):
        header = struct.pack(
            "<BBHI4s",
            HEADER_SIZE,
            self.protocol_version,
            self.profile_version,
            len(self._body),
            MAGIC,
        )
        header += struct.pack("<H", crc16(header))
        data = header + self._body
        return data + struct.pack("<H", crc16(data))


@dataclass
class FitFile:
    profile_version: int
    #: False only when a non-zero CRC does not match. Zero means "not set".
    crc_ok: bool
    #: (local type, global number, fields) for every definition, in file order.
    definitions: list
    #: (global number, values) for every data message, in file order.
    messages: list

    def by_global(self, global_num):
        return [values for number, values in self.messages if number == global_num]


def read(data):
    """Decode FIT bytes. Raises ValueError on anything malformed."""
    if len(data) < HEADER_SIZE + 2:
        raise ValueError("fichier trop court pour être un FIT")
    header_size = data[0]
    if header_size not in (12, 14):
        raise ValueError(f"taille d'en-tête FIT inattendue : {header_size}")
    if data[8:12] != MAGIC:
        raise ValueError("signature .FIT absente : ce n'est pas un fichier FIT")
    profile_version, data_size = struct.unpack_from("<HI", data, 2)
    end = header_size + data_size
    if end + 2 > len(data):
        raise ValueError("fichier FIT tronqué")
    stored_crc = struct.unpack_from("<H", data, end)[0]
    crc_ok = stored_crc in (0, crc16(data[:end]))

    layouts = {}
    definitions = []
    messages = []
    pos = header_size
    while pos < end:
        head = data[pos]
        if head & _COMPRESSED_TIMESTAMP:
            raise ValueError("en-têtes à horodatage compressé non pris en charge")
        local = head & 0x0F
        if head & _DEFINITION:
            if head & _DEVELOPER_FIELDS:
                raise ValueError("champs développeur non pris en charge")
            endian = ">" if data[pos + 2] == 1 else "<"
            global_num = struct.unpack_from(endian + "H", data, pos + 3)[0]
            count = data[pos + 5]
            fields = tuple(
                tuple(data[pos + 6 + 3 * i : pos + 9 + 3 * i]) for i in range(count)
            )
            layouts[local] = (global_num, fields, endian)
            definitions.append((local, global_num, fields))
            pos += 6 + 3 * count
            continue

        if local not in layouts:
            raise ValueError(f"message de type local {local} jamais défini")
        global_num, fields, endian = layouts[local]
        pos += 1
        values = []
        for _number, size, base in fields:
            if pos + size > end:
                raise ValueError("enregistrement FIT tronqué")
            chunk = data[pos : pos + size]
            pos += size
            fmt = _FORMATS.get(base)
            if base == STRING:
                values.append(chunk.split(b"\0", 1)[0].decode("utf-8", "replace"))
            elif fmt and struct.calcsize(fmt) == size:
                values.append(struct.unpack(endian + fmt, chunk)[0])
            else:
                values.append(bytes(chunk))
        messages.append((global_num, values))

    return FitFile(profile_version, crc_ok, definitions, messages)
