"""Building MP4 boxes."""

import struct


def box(kind: bytes, *parts: bytes) -> bytes:
    """A box: 4-byte size, 4-byte type, then the parts."""
    body = b"".join(parts)
    return struct.pack(">I4s", 8 + len(body), kind) + body


def full_box(kind: bytes, version: int, flags: int, *parts: bytes) -> bytes:
    """A box with a version and 24 bits of flags before its content."""
    return box(kind, struct.pack(">I", (version << 24) | flags), *parts)


def u16(value: int) -> bytes:
    """A big-endian unsigned 16-bit number."""
    return struct.pack(">H", value)


def u32(value: int) -> bytes:
    """A big-endian unsigned 32-bit number."""
    return struct.pack(">I", value)


def u64(value: int) -> bytes:
    """A big-endian unsigned 64-bit number."""
    return struct.pack(">Q", value)


def descriptor(tag: int, *parts: bytes) -> bytes:
    """An MPEG-4 systems descriptor, with its variable-length size field."""
    body = b"".join(parts)
    size = len(body)
    encoded = bytearray()
    for shift in (21, 14, 7):  # always four bytes, which every reader accepts
        encoded.append(((size >> shift) & 0x7F) | 0x80)
    encoded.append(size & 0x7F)
    return bytes([tag]) + bytes(encoded) + body
