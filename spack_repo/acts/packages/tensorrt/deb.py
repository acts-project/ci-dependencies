"""Minimal, dependency-free .deb extractor.

A Debian package is an `ar` archive holding `debian-binary`, `control.tar.*`
and `data.tar.*`; only the last is of interest here. NVIDIA compresses its
payloads with xz, which `tarfile` reads natively, so this needs no `ar`, `dpkg`
or `bsdtar` on the build image.

The payload is streamed rather than slurped: libnvinfer's data.tar.xz alone is
~1.9 GB compressed, and buffering it would cost that much resident memory
during an install that already runs alongside seven other packages.
"""

import tarfile

AR_MAGIC = b"!<arch>\n"
AR_HEADER_SIZE = 60

#: tarfile stream modes by payload extension. zstd is deliberately absent: it
#: needs python 3.14+ or an extra module, and NVIDIA's debs are xz.
_TAR_MODES = {
    "data.tar.xz": "r|xz",
    "data.tar.gz": "r|gz",
    "data.tar.bz2": "r|bz2",
    "data.tar": "r|",
}


class _BoundedReader:
    """Read-only view of the next `size` bytes of an open file."""

    def __init__(self, stream, size):
        self.stream = stream
        self.remaining = size

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        if size is None or size < 0:
            size = self.remaining
        data = self.stream.read(min(size, self.remaining))
        self.remaining -= len(data)
        return data


def extract_deb(path, dest):
    """Extract the data payload of a .deb into `dest`."""
    with open(path, "rb") as f:
        if f.read(len(AR_MAGIC)) != AR_MAGIC:
            raise ValueError("{0} is not a .deb (bad ar magic)".format(path))
        while True:
            header = f.read(AR_HEADER_SIZE)
            if len(header) < AR_HEADER_SIZE:
                raise ValueError("{0} has no data.tar member".format(path))
            # ar headers are fixed-width ascii: name[16], mtime, uid, gid, mode,
            # size[10] at offset 48, then a two byte terminator.
            name = header[:16].decode("ascii", "replace").strip().rstrip("/")
            size = int(header[48:58].decode("ascii").strip())

            mode = _TAR_MODES.get(name)
            if mode is None:
                if name.startswith("data.tar"):
                    raise RuntimeError("unsupported deb payload compression: {0}".format(name))
                f.seek(size + (size % 2), 1)  # members are padded to even length
                continue

            with tarfile.open(fileobj=_BoundedReader(f, size), mode=mode) as tar:
                try:
                    # `filter` is python 3.12+; on older interpreters the
                    # default extraction behaviour is what we get anyway.
                    tar.extractall(dest, filter="data")
                except TypeError:
                    tar.extractall(dest)
            return
