import pytest

from rayohx import OutputFormatError
from rayohx.mux.bits import BitReader, unescape_nal
from tests.core.mux.helpers import BitWriter, escape_nal


def test_reading_bits_across_byte_boundaries() -> None:
    reader = BitReader(bytes([0b10110011, 0b01010101, 0b11110000]))
    assert reader.read(1) == 1
    assert reader.read(3) == 0b011
    assert reader.read(8) == 0b00110101
    assert reader.bits_left == 12
    assert reader.read(0) == 0
    assert reader.read(12) == 0b010111110000
    assert reader.bits_left == 0


def test_flags_and_skipping() -> None:
    reader = BitReader(b"\xa0")
    assert reader.flag() is True
    assert reader.flag() is False
    reader.skip(1)
    assert reader.read(5) == 0


def test_reading_past_the_end_is_an_error() -> None:
    reader = BitReader(b"\xff")
    with pytest.raises(OutputFormatError, match="ended unexpectedly"):
        reader.read(9)
    with pytest.raises(OutputFormatError, match="ended unexpectedly"):
        reader.skip(9)


@pytest.mark.parametrize(
    ("bits", "unsigned", "signed"),
    [
        ("1", 0, 0),
        ("010", 1, 1),
        ("011", 2, -1),
        ("00100", 3, 2),
        ("00101", 4, -2),
        ("00110", 5, 3),
        ("0001000", 7, 4),
    ],
)
def test_exp_golomb_numbers(bits: str, unsigned: int, signed: int) -> None:
    data = int(bits.ljust(16, "0"), 2).to_bytes(2, "big")
    assert BitReader(data).ue() == unsigned
    assert BitReader(data).se() == signed


def test_a_run_of_zeros_is_not_a_number() -> None:
    with pytest.raises(OutputFormatError, match="invalid number"):
        BitReader(bytes(8)).ue()


def test_writer_and_reader_agree() -> None:
    writer = BitWriter()
    for value in (0, 1, 2, 17, 255, 65535):
        writer.ue(value)
    for value in (-5, 0, 3, 100):
        writer.se(value)
    reader = BitReader(writer.to_bytes())
    assert [reader.ue() for _ in range(6)] == [0, 1, 2, 17, 255, 65535]
    assert [reader.se() for _ in range(4)] == [-5, 0, 3, 100]


def test_emulation_prevention_bytes_are_removed() -> None:
    raw = bytes([1, 0, 0, 3, 1, 0, 0, 3, 0, 0, 0, 3, 5])
    assert unescape_nal(raw) == bytes([1, 0, 0, 1, 0, 0, 0, 0, 0, 5])
    assert unescape_nal(b"\x01\x02\x03") == b"\x01\x02\x03"
    assert unescape_nal(bytes([0, 0, 3])) == bytes([0, 0])


def test_escaping_and_unescaping_round_trip() -> None:
    payload = bytes([0, 0, 0, 0, 1, 0, 0, 2, 0, 0, 3, 9, 0, 0])
    escaped = escape_nal(payload)
    assert b"\x00\x00\x00" not in escaped
    assert b"\x00\x00\x01" not in escaped
    assert unescape_nal(escaped) == payload
