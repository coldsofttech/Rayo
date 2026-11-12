"""Reading MP4 boxes."""

import struct
from collections.abc import Iterator


def iter_boxes(
    data: bytes | bytearray, start: int = 0, end: int | None = None
) -> Iterator[tuple[bytes, int, int, int]]:
    """Yield ``(type, offset, size, header_size)`` for each box between ``start`` and ``end``.

    Stops at a box that does not fit. A size of zero means "to the end".
    """
    end = len(data) if end is None else end
    position = start
    while position + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, position)
        header = 8
        if size == 1:
            if position + 16 > end:
                return
            size = struct.unpack_from(">Q", data, position + 8)[0]
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            return
        yield kind, position, size, header
        position += size


def find_box(
    data: bytes | bytearray, path: tuple[bytes, ...], start: int = 0, end: int | None = None
) -> tuple[int, int, int] | None:
    """Follow ``path`` of nested box types; return ``(offset, size, header_size)`` or ``None``."""
    found: tuple[int, int, int] | None = None
    for kind in path:
        found = None
        for box_kind, offset, size, header in iter_boxes(data, start, end):
            if box_kind == kind:
                found = (offset, size, header)
                break
        if found is None:
            return None
        start, end = found[0] + found[2], found[0] + found[1]
    return found
