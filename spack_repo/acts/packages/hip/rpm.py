"""Minimal, dependency-free RPM (v3) extractor.

Only what is needed to unpack AMD's ROCm binary RPMs: parse the lead plus the
two headers to find the payload, then stream the compressed `newc` cpio archive
that follows straight onto disk.

Pure stdlib on purpose: this runs inside a Spack `install()` on images that may
have neither `rpm2cpio`/`cpio` nor `bsdtar`. The rhel8 ROCm rpms use an xz
payload, which `lzma` handles out of the box. Streaming rather than slurping
keeps peak memory at one file (~150 MB for the largest LLVM binary) instead of
the ~1.1 GB the decompressed rocm-llvm payload would take.
"""

import gzip
import lzma
import os
import stat
import struct

RPM_LEAD_MAGIC = b"\xed\xab\xee\xdb"
RPM_HEADER_MAGIC = b"\x8e\xad\xe8\x01"
RPM_LEAD_SIZE = 96
RPMTAG_PAYLOADCOMPRESSOR = 1125  # rpmtag.h

CPIO_MAGIC = b"070701"  # "newc" format
CPIO_HEADER_SIZE = 110
CPIO_TRAILER = "TRAILER!!!"


def _read_exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("truncated stream: wanted {0} bytes, got {1}".format(size, len(data)))
    return data


def _read_header(stream, pad):
    """Read one rpm header section; return its index of {tag: value bytes}."""
    intro = _read_exact(stream, 16)
    if intro[:4] != RPM_HEADER_MAGIC:
        raise ValueError("bad rpm header magic")
    nindex, hsize = struct.unpack(">II", intro[8:16])
    entries = _read_exact(stream, 16 * nindex)
    store = _read_exact(stream, hsize)
    if pad:
        # the signature header (and only it) is padded to an 8 byte boundary
        _read_exact(stream, (8 - (hsize % 8)) % 8)
    index = {}
    for i in range(nindex):
        tag, _, offset, _ = struct.unpack(">IIiI", entries[16 * i : 16 * (i + 1)])
        index[tag] = store[offset:]
    return index


def _string_tag(index, tag):
    if tag not in index:
        return None
    value = index[tag]
    return value[: value.index(b"\x00")].decode("utf-8")


def _decompressing_stream(stream, compressor):
    if compressor in ("xz", "lzma"):
        return lzma.LZMAFile(stream)
    if compressor == "gzip":
        return gzip.GzipFile(fileobj=stream)
    if compressor == "zstd":
        # rhel9+ rpms; `compression.zstd` is python 3.14+, so fall back to the
        # `zstandard` module when it happens to be around.
        try:
            from compression.zstd import ZstdFile

            return ZstdFile(stream)
        except ImportError:
            pass
        try:
            from zstandard import ZstdDecompressor
        except ImportError:
            raise RuntimeError(
                "rpm has a zstd payload but no zstd decompressor is available "
                "(needs python 3.14+ or the `zstandard` module); use the rhel8 "
                "rpms, whose payload is xz"
            )
        return ZstdDecompressor().stream_reader(stream)
    raise RuntimeError("unsupported rpm payload compressor: {0}".format(compressor))


def _payload_stream(f):
    """Position `f` past the rpm headers and wrap it in its decompressor."""
    if _read_exact(f, RPM_LEAD_SIZE)[:4] != RPM_LEAD_MAGIC:
        raise ValueError("not an rpm file")
    _read_header(f, pad=True)  # signature header
    index = _read_header(f, pad=False)  # main header
    return _decompressing_stream(f, _string_tag(index, RPMTAG_PAYLOADCOMPRESSOR) or "gzip")


def extract_cpio(stream, dest):
    """Unpack a `newc` cpio archive into `dest`; return the paths written."""
    written = []
    position = 0
    # newc gives the content to the *last* member of a hardlink set; earlier
    # members repeat the (dev, ino) with a zero size. rpm uses this for the
    # /usr/lib/.build-id/... aliases of every installed binary.
    pending_links = {}

    def read(size):
        nonlocal position
        position += size
        return _read_exact(stream, size)

    def skip_padding():
        read((4 - (position % 4)) % 4)

    while True:
        header = read(CPIO_HEADER_SIZE)
        if header[:6] != CPIO_MAGIC:
            raise ValueError("bad cpio magic at offset {0}".format(position - CPIO_HEADER_SIZE))
        fields = [int(header[6 + 8 * i : 14 + 8 * i], 16) for i in range(13)]
        ino, mode, _, _, nlink, _, filesize, devmajor, devminor, _, _, namesize, _ = fields
        name = read(namesize)[:-1].decode("utf-8")
        skip_padding()
        if name == CPIO_TRAILER:
            break
        content = read(filesize)
        skip_padding()

        if name in (".", "./"):
            continue
        # rpm records absolute paths as "./opt/rocm-x.y.z/..."; keep them relative
        target = os.path.join(dest, name.lstrip("./"))

        if stat.S_ISDIR(mode):
            os.makedirs(target, exist_ok=True)
            continue

        os.makedirs(os.path.dirname(target), exist_ok=True)
        if stat.S_ISLNK(mode):
            if os.path.lexists(target):
                os.remove(target)
            os.symlink(content.decode("utf-8"), target)
            written.append(target)
            continue
        if not stat.S_ISREG(mode):
            continue  # devices/fifos: not present in these rpms

        key = (devmajor, devminor, ino)
        if nlink > 1 and filesize == 0:
            pending_links.setdefault(key, []).append(target)
            continue
        with open(target, "wb") as f:
            f.write(content)
        os.chmod(target, stat.S_IMODE(mode))
        written.append(target)
        for alias in pending_links.pop(key, []):
            if os.path.lexists(alias):
                os.remove(alias)
            os.link(target, alias)
            written.append(alias)

    if pending_links:
        raise RuntimeError("unresolved cpio hardlinks: {0}".format(sorted(pending_links.values())))
    return written


def extract_rpm(path, dest):
    """Extract an rpm's payload into `dest`; return the paths written."""
    with open(path, "rb") as f:
        return extract_cpio(_payload_stream(f), dest)
