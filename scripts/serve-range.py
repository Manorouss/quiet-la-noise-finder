#!/usr/bin/env python3
"""Static server with HTTP Range support, for testing PMTiles layers locally.

  serve-range.py --root out --port 4173 --mount /county-layers/=../../work/county_layers/current

Each --mount maps a URL prefix to a directory. python -m http.server ignores Range,
which PMTiles needs.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

mimetypes.add_type("application/vnd.pmtiles", ".pmtiles")
mimetypes.add_type("application/geo+json", ".geojson")


def make_handler(root: Path, mounts: list[tuple[str, Path]]):
    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            path = unquote(urlsplit(path).path)
            for prefix, directory in mounts:
                if path.startswith(prefix):
                    return str(directory / path[len(prefix):])
            target = root / path.lstrip("/")
            if target.is_dir() and (target / "index.html").exists():
                return str(target / "index.html")
            return str(target)

        def end_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Range")
            self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, ETag")
            self.send_header("Accept-Ranges", "bytes")
            super().end_headers()

        def do_GET(self) -> None:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            path = Path(self.translate_path(self.path))
            if not match or not path.is_file():
                return super().do_GET()
            size = path.stat().st_size
            start, end = match.groups()
            start_i = int(start) if start else max(0, size - int(end))
            end_i = min(int(end), size - 1) if start and end else size - 1
            if start_i > end_i or start_i >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Type", self.guess_type(str(path)))
            self.send_header("Content-Range", f"bytes {start_i}-{end_i}/{size}")
            self.send_header("Content-Length", str(end_i - start_i + 1))
            self.end_headers()
            with path.open("rb") as handle:
                handle.seek(start_i)
                self.wfile.write(handle.read(end_i - start_i + 1))

        def log_message(self, *args) -> None:
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--mount", action="append", default=[], help="/prefix/=directory")
    args = parser.parse_args()
    mounts = [(prefix, Path(directory).resolve()) for prefix, directory in (m.split("=", 1) for m in args.mount)]
    root = args.root.resolve()

    ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(root, mounts)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
