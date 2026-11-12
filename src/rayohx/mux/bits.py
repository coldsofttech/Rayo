"""Reading bit-packed headers, as used by video and audio codecs."""

from rayohx.errors import OutputFormatError


def unescape_nal(data: bytes) -> bytes:
    """Remove the emulation-prevention bytes (``00 00 03`` becomes ``00 00``) from a NAL unit."""
    if b"\x00\x00\x03" not in data:
        return data
    out = bytearray()
    zeros = 0
    for byte in data:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


class BitReader:
    """Reads bits from the most significant end of each byte."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0
        self._length = len(data) * 8

    @property
    def bits_left(self) -> int:
        """Bits not yet read."""
        return self._length - self._position

    def read(self, count: int) -> int:
        """Read ``count`` bits as an unsigned number."""
        if count == 0:
            return 0
        if count > self.bits_left:
            raise OutputFormatError("The media headers ended unexpectedly.")
        start, end = self._position, self._position + count
        first, last = start // 8, (end + 7) // 8
        chunk = int.from_bytes(self._data[first:last], "big")
        chunk >>= last * 8 - end
        self._position = end
        return chunk & ((1 << count) - 1)

    def flag(self) -> bool:
        """Read one bit as a boolean."""
        return self.read(1) == 1

    def skip(self, count: int) -> None:
        """Skip ``count`` bits."""
        if count > self.bits_left:
            raise OutputFormatError("The media headers ended unexpectedly.")
        self._position += count

    def ue(self) -> int:
        """Read an unsigned Exp-Golomb number."""
        zeros = 0
        while self.read(1) == 0:
            zeros += 1
            if zeros > 32:
                raise OutputFormatError("The media headers contain an invalid number.")
        return (1 << zeros) - 1 + self.read(zeros)

    def se(self) -> int:
        """Read a signed Exp-Golomb number."""
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)
