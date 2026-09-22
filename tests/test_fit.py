"""The generic FIT container."""

import struct

import pytest

from bryton_route import fit


def test_crc_matches_the_one_bryton_active_wrote(reference_fit):
    data_size = struct.unpack_from("<I", reference_fit, 4)[0]
    end = 14 + data_size
    assert struct.unpack_from("<H", reference_fit, 12)[0] == fit.crc16(reference_fit[:12])
    assert struct.unpack_from("<H", reference_fit, end)[0] == fit.crc16(reference_fit[:end])


def test_truncate_utf8_never_splits_a_character():
    raw = "Rampe d'accès à la Via Ardèche".encode()
    assert len(raw) == 33
    for limit in range(len(raw) + 2):
        cut = fit.truncate_utf8(raw, limit)
        assert len(cut) <= limit
        cut.decode()


def test_round_trip_with_real_length_and_crc():
    w = fit.FitWriter(profile_version=2088)
    w.define(1, 254, [(1, 2, fit.UINT16), (2, 4, fit.SINT32)])
    w.write(1, 7, -1_651_189)
    data = w.to_bytes()
    assert struct.unpack_from("<I", data, 4)[0] == len(data) - 16
    parsed = fit.read(data)
    assert parsed.crc_ok and parsed.profile_version == 2088
    assert parsed.by_global(254) == [[7, -1_651_189]]


def test_strings_are_nul_terminated_and_cut_on_a_character():
    w = fit.FitWriter(profile_version=1)
    w.define(0, 250, [(5, 6, fit.STRING)])
    w.write(0, "Vitréeee")
    assert fit.read(w.to_bytes()).by_global(250) == [["Vitr"]]  # "é" would not fit whole


def test_reader_follows_a_redefinition_mid_stream():
    w = fit.FitWriter(profile_version=1)
    w.define(4, 250, [(1, 2, fit.UINT16), (5, 8, fit.STRING)])
    w.write(4, 1, "court")
    w.define(4, 250, [(1, 2, fit.UINT16), (5, 20, fit.STRING)])
    w.write(4, 2, "nettement plus long")
    parsed = fit.read(w.to_bytes())
    assert parsed.by_global(250) == [[1, "court"], [2, "nettement plus long"]]
    assert [d[2][1][1] for d in parsed.definitions] == [8, 20]


def test_corruption_is_detected():
    w = fit.FitWriter(profile_version=1)
    w.define(0, 1, [(1, 2, fit.UINT16)])
    w.write(0, 5)
    data = bytearray(w.to_bytes())
    data[-3] ^= 0xFF
    assert fit.read(bytes(data)).crc_ok is False


@pytest.mark.parametrize(
    "data, message",
    [
        (b"\x0e\x10", "trop court"),
        (b"x" * 40, "taille d'en-tête"),
        (b"\x0e" + b"x" * 39, "signature"),
    ],
)
def test_non_fit_input_is_rejected(data, message):
    with pytest.raises(ValueError, match=message):
        fit.read(data)


def test_out_of_range_value_is_reported():
    w = fit.FitWriter(profile_version=1)
    w.define(0, 1, [(1, 2, fit.UINT16)])
    with pytest.raises(ValueError, match="hors limites"):
        w.write(0, 70_000)
