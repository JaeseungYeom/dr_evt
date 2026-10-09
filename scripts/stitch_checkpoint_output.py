#!/usr/bin/env python3
"""Stitch checkpoint/restart CSV segments using saved byte boundaries."""

import argparse
import os
from pathlib import Path
import re
import socket
import tempfile
from urllib.parse import unquote, urlparse
from typing import List, Optional, Tuple


def _generation(path: Path, active: Path) -> int:
    match = re.fullmatch(re.escape(active.name) + r"\.pre-restart\.(\d+)", path.name)
    if match is None:
        raise ValueError(f"invalid restart segment name: {path}")
    return int(match.group(1))


def _header(data: bytes, path: Path) -> bytes:
    newline = data.find(b"\n")
    if newline < 0:
        raise ValueError(f"CSV segment has no complete header: {path}")
    return data[: newline + 1]


def stitch(active: Path, destination: Path) -> None:
    archive_pattern = re.compile(re.escape(active.name) + r"\.pre-restart\.(\d+)")
    archives = [
        path
        for path in active.parent.glob(active.name + ".pre-restart.*")
        if archive_pattern.fullmatch(path.name)
    ]
    archives.sort(key=lambda path: _generation(path, active))
    if not archives:
        raise ValueError(f"no pre-restart segments found for {active}")

    active_data = active.read_bytes()
    expected_header = _header(active_data, active)
    pieces: List[bytes] = []
    for index, archive in enumerate(archives):
        data = archive.read_bytes()
        header = _header(data, archive)
        if header != expected_header:
            raise ValueError(f"CSV header differs in {archive}")
        boundary_path = Path(str(archive) + ".checkpoint-bytes")
        try:
            boundary = int(boundary_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError) as error:
            raise ValueError(f"invalid checkpoint boundary: {boundary_path}") from error
        if boundary < len(header) or boundary > len(data):
            raise ValueError(f"checkpoint boundary is outside {archive}")
        prefix = data[:boundary]
        if not prefix.endswith(b"\n"):
            raise ValueError(f"checkpoint boundary splits a CSV row in {archive}")
        pieces.append(prefix if index == 0 else prefix[len(header) :])

    pieces.append(active_data[len(expected_header) :])
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(expected_header)
            for index, piece in enumerate(pieces):
                if index == 0:
                    output.write(piece[len(expected_header) :])
                else:
                    output.write(piece)
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


class RedisClient:
    """Minimal binary-safe RESP2 client, avoiding a Python package dependency."""

    def __init__(self, uri: str):
        parsed = urlparse(uri)
        if parsed.scheme != "redis" or parsed.hostname is None:
            raise ValueError("only redis:// URIs are supported")
        self._socket = socket.create_connection((parsed.hostname, parsed.port or 6379))
        self._input = self._socket.makefile("rb")
        if parsed.password is not None:
            if parsed.username is not None:
                self.command("AUTH", unquote(parsed.username),
                             unquote(parsed.password))
            else:
                self.command("AUTH", unquote(parsed.password))
        database = parsed.path.lstrip("/")
        if database:
            self.command("SELECT", database)

    def command(self, *parts: object):
        encoded = [p if isinstance(p, bytes) else str(p).encode() for p in parts]
        request = [f"*{len(encoded)}\r\n".encode()]
        for part in encoded:
            request.extend((f"${len(part)}\r\n".encode(), part, b"\r\n"))
        self._socket.sendall(b"".join(request))
        return self._read()

    def _read(self):
        marker = self._input.read(1)
        line = self._input.readline()
        if marker == b"+":
            return line[:-2]
        if marker == b"-":
            raise ValueError("Redis error: " + line[:-2].decode(errors="replace"))
        if marker == b":":
            return int(line)
        if marker == b"$":
            size = int(line)
            if size < 0:
                return None
            value = self._input.read(size)
            self._input.read(2)
            return value
        if marker == b"*":
            count = int(line)
            return None if count < 0 else [self._read() for _ in range(count)]
        raise ValueError("invalid Redis response")


def _redis_copy(client: RedisClient, source: str, destination: str) -> None:
    payload = client.command("DUMP", source)
    if payload is None:
        raise ValueError(f"missing Redis key: {source}")
    client.command("RESTORE", destination, 0, payload, "REPLACE")


def _redis_rename_namespace(client: RedisClient, source: str, destination: str) -> None:
    job_ids = client.command("SMEMBERS", source + ":job_ids") or []
    resource_ids = client.command("ZRANGE", source + ":resources:by_time", 0, -1) or []
    suffixes = (
        ":csv", ":job_ids", ":by_submit", ":by_start", ":by_completion",
        ":by_resources", ":resources:csv", ":resources:by_time",
    )
    for suffix in suffixes:
        if client.command("EXISTS", source + suffix):
            client.command("RENAME", source + suffix, destination + suffix)
    for value in job_ids:
        job_id = value.decode()
        client.command("RENAME", source + ":job:" + job_id,
                       destination + ":job:" + job_id)
    for value in resource_ids:
        resource_id = value.decode()
        client.command("RENAME", source + ":resource:" + resource_id,
                       destination + ":resource:" + resource_id)


def _redis_csv_piece(client: RedisClient, prefix: str, suffix: str,
                     boundary_key: Optional[str]) -> bytes:
    value = client.command("GET", prefix + suffix)
    if value is None:
        return b""
    if boundary_key is None:
        return value
    raw_boundary = client.command("GET", prefix + boundary_key)
    if raw_boundary is None:
        raise ValueError(f"missing Redis boundary: {prefix + boundary_key}")
    boundary = int(raw_boundary)
    if boundary > len(value) or not value[:boundary].endswith(b"\n"):
        raise ValueError(f"invalid Redis CSV boundary for {prefix + suffix}")
    return value[:boundary]


def stitch_redis(uri: str, prefix: str) -> None:
    client = RedisClient(uri)
    archives: List[str] = []
    generation = 1
    while client.command(
        "EXISTS", f"{prefix}:pre-restart:{generation}:checkpoint:job_csv_bytes"
    ):
        archives.append(f"{prefix}:pre-restart:{generation}")
        generation += 1
    if not archives:
        raise ValueError(f"no pre-restart Redis namespaces found for {prefix}")

    temporary = f"{prefix}:stitch-active:{os.getpid()}"
    if client.command("EXISTS", temporary + ":csv"):
        raise ValueError(f"temporary Redis namespace already exists: {temporary}")
    _redis_rename_namespace(client, prefix, temporary)

    def combined_csv(suffix: str, boundary_suffix: str) -> bytes:
        active = _redis_csv_piece(client, temporary, suffix, None)
        header = _header(active, Path(prefix + suffix))
        result = bytearray(header)
        for archive in archives:
            piece = _redis_csv_piece(client, archive, suffix, boundary_suffix)
            if piece:
                if _header(piece, Path(archive + suffix)) != header:
                    raise ValueError(f"CSV header differs in {archive + suffix}")
                result.extend(piece[len(header):])
        result.extend(active[len(header):])
        return bytes(result)

    client.command("SET", prefix + ":csv",
                   combined_csv(":csv", ":checkpoint:job_csv_bytes"))
    if client.command("EXISTS", temporary + ":resources:csv"):
        client.command(
            "SET", prefix + ":resources:csv",
            combined_csv(":resources:csv", ":checkpoint:resource_csv_bytes"),
        )

    job_sources: List[Tuple[str, List[bytes]]] = []
    for archive in archives:
        ids = client.command("SMEMBERS", archive + ":checkpoint:job_ids") or []
        job_sources.append((archive, ids))
    job_sources.append((temporary, client.command("SMEMBERS", temporary + ":job_ids") or []))
    for source, ids in job_sources:
        for raw_id in ids:
            job_id = raw_id.decode()
            _redis_copy(client, source + ":job:" + job_id,
                        prefix + ":job:" + job_id)
            client.command("SADD", prefix + ":job_ids", job_id)
            for index in ("by_submit", "by_start", "by_completion", "by_resources"):
                score = client.command("ZSCORE", source + ":" + index, job_id)
                if score is not None:
                    client.command("ZADD", prefix + ":" + index, score, job_id)

    resource_sources: List[Tuple[str, List[bytes]]] = []
    for archive in archives:
        count_value = client.command("GET", archive + ":checkpoint:resource_count")
        count = int(count_value or 0)
        resource_sources.append((archive, [str(i).encode() for i in range(count)]))
    resource_sources.append(
        (temporary, client.command("ZRANGE", temporary + ":resources:by_time", 0, -1) or [])
    )
    next_resource_id = 0
    for source, ids in resource_sources:
        for raw_id in ids:
            resource_id = raw_id.decode()
            _redis_copy(client, source + ":resource:" + resource_id,
                        prefix + ":resource:" + str(next_resource_id))
            client.command("HSET", prefix + ":resource:" + str(next_resource_id),
                           "sample_id", next_resource_id)
            score = client.command(
                "ZSCORE", source + ":resources:by_time", resource_id
            )
            if score is not None:
                client.command(
                    "ZADD", prefix + ":resources:by_time", score, next_resource_id
                )
            next_resource_id += 1

    # The temporary namespace contains only a duplicate of the active segment.
    keys = client.command("KEYS", temporary + ":*") or []
    if keys:
        client.command("DEL", *keys)
    for archive in archives:
        keys = client.command("KEYS", archive + ":*") or []
        if keys:
            client.command("DEL", *keys)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stitch DR_EVT checkpoint output.")
    parser.add_argument("active", type=Path, nargs="?", help="current post-restart CSV")
    parser.add_argument("output", type=Path, nargs="?", help="stitched CSV destination")
    parser.add_argument("--redis-uri", help="Redis URI for in-place namespace stitching")
    parser.add_argument("--redis-prefix", help="Redis namespace to stitch in place")
    args = parser.parse_args()
    try:
        if args.redis_uri or args.redis_prefix:
            if not args.redis_uri or not args.redis_prefix or args.active or args.output:
                raise ValueError("Redis mode requires --redis-uri and --redis-prefix only")
            stitch_redis(args.redis_uri, args.redis_prefix)
        elif args.active is None or args.output is None:
            raise ValueError("file mode requires ACTIVE and OUTPUT")
        else:
            stitch(args.active, args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
