"""A native remuxer: reads MPEG-TS and fragmented MP4 and writes fragmented MP4.

Nothing here decodes or re-encodes media. Compressed samples are only re-wrapped from one container
into another (remuxing), written from the public specifications (ISO/IEC 13818-1, 14496-12,
14496-14, 14496-15 and 23008-2).
"""
