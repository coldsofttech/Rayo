"""An MPEG-TS demuxer: from 188-byte packets to the elementary streams' PES packets.

It follows the program tables (PAT and PMT) to find the streams, reassembles each stream's PES
packets, and reads their timestamps (33-bit values at 90 kHz, unwrapped so a rollover does not look
like a jump back in time). The data may arrive in pieces of any size.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PACKET = 188
SYNC = 0x47
WRAP = 1 << 33

STREAM_H264 = 0x1B
STREAM_HEVC = 0x24
STREAM_AAC_ADTS = 0x0F
STREAM_MP3 = 0x03
STREAM_MP3_B = 0x04
STREAM_AC3 = 0x81
STREAM_EAC3 = 0x87
STREAM_PRIVATE = 0x06

#: Descriptor tags that identify private streams (type 0x06) carrying Dolby audio.
DESCRIPTOR_AC3 = 0x6A
DESCRIPTOR_EAC3 = 0x7A

_PES_STREAM_IDS_WITH_HEADER = frozenset(range(0xC0, 0xF0)) | {0xBD}


@dataclass(frozen=True, slots=True)
class TsStream:
    """One elementary stream listed in the program map."""

    pid: int
    stream_type: int
    descriptors: tuple[tuple[int, bytes], ...] = ()

    def has_descriptor(self, tag: int) -> bool:
        """Whether the stream carries a descriptor with this tag."""
        return any(found == tag for found, _ in self.descriptors)


@dataclass(slots=True)
class PesPacket:
    """The payload of one PES packet with its timestamps in 90 kHz ticks (unwrapped)."""

    pid: int
    payload: bytes
    pts: int | None
    dts: int | None


@dataclass(slots=True)
class _Assembly:
    """A PES packet or a table section being put together from several TS packets."""

    chunks: list[bytes] = field(default_factory=list)
    size: int = 0


class TsDemuxer:
    """Feed it TS data; it calls back with the stream list and each stream's PES packets."""

    def __init__(
        self,
        on_pes: Callable[[PesPacket, TsStream], None],
        on_streams: Callable[[list[TsStream]], None] | None = None,
    ) -> None:
        self._on_pes = on_pes
        self._on_streams = on_streams
        self._pending = b""
        self._pmt_pid: int | None = None
        self._program: int | None = None
        self._streams: dict[int, TsStream] = {}
        self._sections: dict[int, _Assembly] = {}
        self._pes: dict[int, _Assembly] = {}
        self._last_ts: dict[int, int] = {}
        self._last_cc: dict[int, int] = {}
        self._skipped_bytes = 0
        self.scrambled_packets = 0

    @property
    def streams(self) -> list[TsStream]:
        """The streams found so far."""
        return list(self._streams.values())

    # --- input -----------------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        """Process more TS data. A partial packet at the end waits for the next call."""
        if self._pending:
            data = self._pending + data
            self._pending = b""
        position = self._first_sync(data)
        end = len(data)
        while position + PACKET <= end:
            if data[position] != SYNC:
                found = self._first_sync(data, position + 1)
                self._skipped_bytes += found - position
                position = found
                continue
            self._packet(data, position)
            position += PACKET
        self._pending = data[position:]

    def flush(self) -> None:
        """Deliver the last PES packet of every stream. Call once at the end."""
        for pid in list(self._pes):
            self._finish_pes(pid)
        self._pending = b""

    def _first_sync(self, data: bytes, start: int = 0) -> int:
        """Find a position where several packets in a row start with the sync byte."""
        position = start
        while position < len(data):
            position = data.find(b"\x47", position)
            if position < 0:
                return len(data)
            ahead = position + PACKET
            if ahead >= len(data) or data[ahead] == SYNC:
                return position
            position += 1
        return len(data)

    # --- packets ---------------------------------------------------------------------------

    def _packet(self, data: bytes, offset: int) -> None:
        b1, b2, b3 = data[offset + 1], data[offset + 2], data[offset + 3]
        pid = ((b1 & 0x1F) << 8) | b2
        if b3 & 0xC0:
            self.scrambled_packets += 1
            return
        control = (b3 >> 4) & 3
        if not control & 1 or pid == 0x1FFF:  # no payload, or a null packet
            return
        start = offset + 4
        if control & 2:
            start += 1 + data[start]
        end = offset + PACKET
        if start >= end:
            return
        counter = b3 & 0xF
        previous = self._last_cc.get(pid)
        self._last_cc[pid] = counter
        if previous is not None and counter != (previous + 1) & 0xF and counter != previous:
            logger.debug("Continuity error on PID %d", pid)
        payload = data[start:end]
        start_flag = bool(b1 & 0x40)
        if pid == 0 or pid == self._pmt_pid:
            self._section(pid, payload, start_flag)
        elif pid in self._streams:
            self._pes_data(pid, payload, start_flag)

    # --- tables ----------------------------------------------------------------------------

    def _section(self, pid: int, payload: bytes, start_flag: bool) -> None:
        assembly = self._sections.setdefault(pid, _Assembly())
        if start_flag:
            pointer = payload[0]
            payload = payload[1 + pointer :]
            assembly.chunks, assembly.size = [], 0
        elif not assembly.chunks:
            return
        assembly.chunks.append(payload)
        assembly.size += len(payload)
        data = b"".join(assembly.chunks)
        if len(data) < 3:
            return
        length = ((data[1] & 0x0F) << 8) | data[2]
        if len(data) < 3 + length:
            return
        assembly.chunks, assembly.size = [], 0
        section = data[: 3 + length]
        if section[0] == 0 and pid == 0:
            self._parse_pat(section)
        elif section[0] == 2:
            self._parse_pmt(section)

    def _parse_pat(self, section: bytes) -> None:
        body = section[8:-4]
        for index in range(0, len(body) - 3, 4):
            program = (body[index] << 8) | body[index + 1]
            pid = ((body[index + 2] & 0x1F) << 8) | body[index + 3]
            if program != 0:
                if self._pmt_pid != pid:
                    self._pmt_pid = pid
                    self._program = program
                return

    def _parse_pmt(self, section: bytes) -> None:
        if len(section) < 16:
            return
        program_info = ((section[10] & 0x0F) << 8) | section[11]
        index = 12 + program_info
        end = len(section) - 4
        found: dict[int, TsStream] = {}
        while index + 5 <= end:
            stream_type = section[index]
            pid = ((section[index + 1] & 0x1F) << 8) | section[index + 2]
            info = ((section[index + 3] & 0x0F) << 8) | section[index + 4]
            descriptors = _descriptors(section[index + 5 : index + 5 + info])
            found[pid] = TsStream(pid, stream_type, descriptors)
            index += 5 + info
        if found != self._streams:
            for pid in set(self._pes) - set(found):
                self._finish_pes(pid)
            self._streams = found
            if self._on_streams is not None:
                self._on_streams(list(found.values()))

    # --- PES -------------------------------------------------------------------------------

    def _pes_data(self, pid: int, payload: bytes, start_flag: bool) -> None:
        if start_flag:
            self._finish_pes(pid)
            self._pes[pid] = _Assembly([payload], len(payload))
            return
        assembly = self._pes.get(pid)
        if assembly is not None:
            assembly.chunks.append(payload)
            assembly.size += len(payload)

    def _finish_pes(self, pid: int) -> None:
        assembly = self._pes.pop(pid, None)
        stream = self._streams.get(pid)
        if assembly is None or stream is None:
            return
        data = b"".join(assembly.chunks)
        if len(data) < 9 or data[:3] != b"\x00\x00\x01":
            return
        stream_id = data[3]
        length = (data[4] << 8) | data[5]
        if length and len(data) > 6 + length:
            data = data[: 6 + length]
        if stream_id not in _PES_STREAM_IDS_WITH_HEADER:
            return
        flags = data[7] >> 6
        header_end = 9 + data[8]
        pts = dts = None
        if flags & 2 and len(data) >= 14:
            pts = self._unwrap(pid, _timestamp(data, 9))
        if flags == 3 and len(data) >= 19:
            dts = self._unwrap(pid, _timestamp(data, 14))
        self._on_pes(
            PesPacket(pid, data[header_end:], pts, dts if dts is not None else pts), stream
        )

    def _unwrap(self, pid: int, value: int) -> int:
        last = self._last_ts.get(pid)
        if last is None:
            result = value
        else:
            delta = (value - last) % WRAP
            result = last + delta - WRAP if delta > WRAP // 2 else last + delta
        self._last_ts[pid] = result
        return result


def _timestamp(data: bytes, offset: int) -> int:
    return (
        ((data[offset] >> 1) & 7) << 30
        | data[offset + 1] << 22
        | (data[offset + 2] >> 1) << 15
        | data[offset + 3] << 7
        | (data[offset + 4] >> 1)
    )


def _descriptors(data: bytes) -> tuple[tuple[int, bytes], ...]:
    found = []
    index = 0
    while index + 2 <= len(data):
        tag, length = data[index], data[index + 1]
        found.append((tag, data[index + 2 : index + 2 + length]))
        index += 2 + length
    return tuple(found)
