"""
The child process one neural OCR engine runs in:  python -m app.docproc.engines.worker <engine id>

PaddlePaddle and PyTorch cannot share a process: once Paddle is loaded, PyTorch's own weight initialisation fails inside Paddle's engine
(docTR then cannot build its model). Each heavy engine therefore lives in a process of its own, started on first use by isolated.py,
which also means a crash or a memory blow-up in one engine cannot take the server down.

Protocol, over this process's stdin and stdout, one request at a time:
    request   4-byte big-endian length, then the page image (JPEG bytes)
    response  4-byte big-endian length, then JSON  {"ok": true, "words": [[x0, y0, x1, y1, text, confidence], ...]}
                                                or {"ok": false, "error": "..."}
Libraries print progress to stdout, which would corrupt the frames, so at start the real stdout is set aside for the protocol and
everything else that is printed goes to stderr (isolated.py keeps that in a log file).
"""
import json
import os
import struct
import sys
from typing import Any, BinaryIO, Optional

from app.docproc.engines.ocr import local


def read_frame(stream: BinaryIO) -> Optional[bytes]:
    header = stream.read(4)
    if len(header) < 4:
        return None                                       # the parent closed the pipe: time to exit
    (n,) = struct.unpack(">I", header)
    data = stream.read(n)
    return data if len(data) == n else None


def write_frame(stream: BinaryIO, payload: bytes) -> None:
    stream.write(struct.pack(">I", len(payload)) + payload)
    stream.flush()


def serve(engine: Any, inp: BinaryIO, out: BinaryIO) -> None:
    """Answer requests until the input closes. `engine` needs a `_words(jpeg) -> [Word]`."""
    while True:
        jpeg = read_frame(inp)
        if jpeg is None:
            return
        try:
            words = engine._words(jpeg)
            reply = {"ok": True, "words": [[w.x0, w.y0, w.x1, w.y1, w.text, w.conf] for w in words]}
        except Exception as e:                            # one bad page must not end the process: say what happened and carry on
            reply = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}
        write_frame(out, json.dumps(reply).encode())


def main(argv: list) -> int:
    engine = local.get(argv[0]) if argv else None
    if engine is None:
        print(f"usage: python -m app.docproc.engines.worker <{'|'.join(e.id for e in local.engines())}>", file=sys.stderr)
        return 2
    local.IN_WORKER = True
    proto = os.fdopen(os.dup(1), "wb", buffering=0)       # the pipe the parent reads
    os.dup2(2, 1)                                         # whatever the libraries print now goes to stderr
    sys.stdout = sys.stderr
    serve(engine, sys.stdin.buffer, proto)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
