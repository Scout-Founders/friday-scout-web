#!/usr/bin/env python3
"""Local immutable publication for evidence-contract-1.

Objects are written under an explicit directory that must not be the canonical
database directory or the historical evidence archive. Identical bytes are an
idempotent retry. Different bytes for the same key fail. There is no shared
JSONL catalog to append to. Each run gets its own catalog object. A latest
pointer is optional and is not required to discover runs.

No network writes are performed.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix
    fcntl = None

from evidence_contract import (
    ENTITY_PRIMARY_FIELDS,
    CatalogEntry,
    EvidenceCheckpointManifest,
    EvidenceContractError,
    RunStartRecord,
    TerminalPublicationManifest,
    canonical_json_bytes,
    record_to_dict,
    sha256_bytes,
    validate_catalog_entry,
    validate_checkpoint,
    validate_run_start,
    validate_terminal,
)


class ImmutableConflict(EvidenceContractError):
    """An immutable key already holds different bytes."""


class GenerationPreconditionError(EvidenceContractError):
    """The latest-pointer generation did not match."""


class PublicationValidationError(EvidenceContractError):
    """A checkpoint chain or chunk does not validate."""


@dataclass(frozen=True)
class WriteResult:
    object_key: str
    sha256: str
    created: bool
    identical_retry: bool


def assert_safe_store_root(root: Path) -> Path:
    """Refuse the canonical database tree and the evidence archive."""
    resolved = root.expanduser().resolve()
    archive = (Path.home() / "Scout Evidence Archive").resolve()
    sandbox = Path(__file__).resolve().parent
    protected = (archive, sandbox, sandbox / "scout_memory.db")
    for item in protected:
        if _same_tree(resolved, item) or _same_tree(item, resolved):
            raise EvidenceContractError(
                "evidence store root is inside a protected directory"
            )
    return resolved


def _same_tree(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def jsonl_bytes(records: Sequence[Mapping]) -> bytes:
    if not records:
        return b""
    lines = [canonical_json_bytes(record) for record in records]
    return b"\n".join(lines) + b"\n"


def gzip_deterministic(data: bytes) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as handle:
        handle.write(data)
    return buffer.getvalue()


def gunzip_bytes(data: bytes) -> bytes:
    return gzip.decompress(data)


def checkpoint_object_key(checkpoint: EvidenceCheckpointManifest) -> str:
    return "runs/{0}/checkpoints/{1:04d}.json".format(
        checkpoint.acquisition_run_id, checkpoint.sequence
    )


def run_start_object_key(run_id: str) -> str:
    return "runs/{0}/start.json".format(run_id)


def terminal_object_key(run_id: str) -> str:
    return "runs/{0}/terminal.json".format(run_id)


def catalog_object_key(run_id: str) -> str:
    return "catalog/{0}.json".format(run_id)


def chunk_object_key(run_id: str, entity: str, sequence: int) -> str:
    return "runs/{0}/chunks/{1}-{2:04d}.jsonl.gz".format(run_id, entity, sequence)


def validate_publication_set(
    *,
    start: RunStartRecord,
    checkpoints: Sequence[EvidenceCheckpointManifest],
    terminal: TerminalPublicationManifest,
    checkpoint_bodies: Mapping[str, bytes],
    gzip_chunks: Mapping[str, bytes],
) -> None:
    """Validate an immutable chain. Does not rewrite any object."""
    validate_run_start(start)
    validate_terminal(terminal)
    if start.acquisition_run_id != terminal.acquisition_run_id:
        raise PublicationValidationError("terminal cites a different run")
    if start.run_start_id != terminal.run_start_id:
        raise PublicationValidationError("terminal cites a different run start")
    ordered = tuple(sorted(checkpoints, key=lambda item: item.sequence))
    if not ordered:
        raise PublicationValidationError("terminal requires checkpoints")
    sequences = [item.sequence for item in ordered]
    if sequences != list(range(1, len(ordered) + 1)):
        raise PublicationValidationError("checkpoint sequences must be contiguous from 1")
    for index, checkpoint in enumerate(ordered):
        validate_checkpoint(checkpoint)
        if checkpoint.acquisition_run_id != start.acquisition_run_id:
            raise PublicationValidationError("checkpoint cites a different run")
        if checkpoint.published_at < start.started_at:
            raise PublicationValidationError("checkpoint precedes the run start")
        if index == 0:
            if checkpoint.previous_checkpoint_id is not None:
                raise PublicationValidationError("checkpoint chain predecessor mismatch")
        elif checkpoint.previous_checkpoint_id != ordered[index - 1].checkpoint_id:
            raise PublicationValidationError("checkpoint chain predecessor mismatch")
        body = checkpoint_bodies.get(checkpoint.checkpoint_id)
        if body is None:
            raise PublicationValidationError("missing checkpoint body")
        if canonical_json_bytes(checkpoint) != body:
            raise PublicationValidationError("checkpoint body does not match the record")
    if tuple(item.checkpoint_id for item in ordered) != terminal.checkpoint_ids:
        raise PublicationValidationError("terminal checkpoint list does not match the chain")
    declared = dict(terminal.checkpoint_sha256)
    for checkpoint in ordered:
        body = checkpoint_bodies[checkpoint.checkpoint_id]
        digest = sha256_bytes(body)
        if declared.get(checkpoint.checkpoint_id) != digest:
            raise PublicationValidationError("incorrect checkpoint checksum")
        if terminal.published_at < checkpoint.published_at:
            raise PublicationValidationError("terminal precedes a checkpoint")
    seen_chunk_keys = set()
    for checkpoint in ordered:
        for chunk in checkpoint.chunks:
            if chunk.object_key in seen_chunk_keys:
                continue
            seen_chunk_keys.add(chunk.object_key)
            payload = gzip_chunks.get(chunk.object_key)
            if payload is None:
                raise PublicationValidationError("missing chunk")
            try:
                raw = gunzip_bytes(payload)
            except OSError as exc:
                raise PublicationValidationError("chunk is not gzip") from exc
            if sha256_bytes(raw) != chunk.sha256:
                raise PublicationValidationError("incorrect checksum")
            if len(raw) != chunk.uncompressed_byte_length:
                raise PublicationValidationError("incorrect chunk length")
            rows = _jsonl_rows(raw)
            if len(rows) != chunk.row_count:
                raise PublicationValidationError("incorrect row count")
            _reject_duplicate_rows(chunk.entity, rows)


def _jsonl_rows(raw: bytes) -> list:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise PublicationValidationError("jsonl chunk must end with a newline")
    rows = []
    for line in raw.splitlines():
        if not line:
            raise PublicationValidationError("jsonl chunk contains a blank line")
        try:
            parsed = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationValidationError("jsonl chunk is not JSON") from exc
        if not isinstance(parsed, dict):
            raise PublicationValidationError("jsonl row must be an object")
        rows.append(parsed)
    return rows


def _reject_duplicate_rows(entity: str, rows: Sequence[Mapping]) -> None:
    field_name = ENTITY_PRIMARY_FIELDS.get(entity)
    if field_name is None:
        raise PublicationValidationError("unknown chunk entity")
    seen = set()
    for row in rows:
        identity = row.get(field_name)
        if not isinstance(identity, str) or not identity:
            raise PublicationValidationError("chunk row is missing its primary identity")
        if identity in seen:
            raise PublicationValidationError("duplicate entity identity")
        seen.add(identity)


# The future cloud adapter must implement these semantics. This local store
# only demonstrates them on one filesystem. It is not proof of GCS behavior.
CLOUD_ADAPTER_REQUIREMENTS = (
    "conditional create with ifGenerationMatch=0 for every immutable object",
    "identical bytes for an existing generation are an idempotent retry",
    "different bytes for an existing immutable name are a hard conflict",
    "write a temporary object, then link or compose it into the final name",
    "a crash remnant is not a published object and is not promoted",
    "catalog entries are individual immutable objects, not an appended JSONL",
    "the latest pointer uses a generation precondition and is optional",
    "historical runs are listed from run-start and catalog prefixes",
    "checkpoint chains are validated before a terminal is treated as complete",
    "a repeated chunk object key is one payload, not a second copy of the rows",
)


class LocalEvidenceStore:
    """Process-local stand-in for an immutable object store."""

    def __init__(self, root: Path):
        self.root = assert_safe_store_root(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, object_key: str, data: bytes) -> WriteResult:
        """Publish by exclusive create. A partial file is not the object.

        os.link is the local stand-in for a conditional create. Two writers
        of different bytes cannot both become the published object.
        """
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return self._existing_result(object_key, path, data)
        partial = Path(str(path) + ".partial")
        try:
            descriptor = os.open(str(partial), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            for _ in range(50):
                if path.exists():
                    return self._existing_result(object_key, path, data)
                time.sleep(0.01)
            raise EvidenceContractError(
                "a partial object is already present and was not promoted"
            )
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(str(partial), str(path))
        except FileExistsError:
            os.unlink(partial)
            return self._existing_result(object_key, path, data)
        os.unlink(partial)
        return WriteResult(object_key, sha256_bytes(data), True, False)

    def write_partial(self, object_key: str, data: bytes) -> str:
        """Leave a crash remnant. The published name stays absent."""
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = Path(str(path) + ".partial")
        descriptor = os.open(str(partial), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return str(Path(object_key + ".partial"))

    def list_partials(self) -> list:
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*.partial"))

    def discard_partials(self) -> int:
        """Remove crash remnants. Do not promote them."""
        removed = 0
        for relative in self.list_partials():
            self._path(relative).unlink()
            removed += 1
        return removed

    def _existing_result(self, object_key: str, path: Path, data: bytes) -> WriteResult:
        existing = path.read_bytes()
        if existing == data:
            return WriteResult(object_key, sha256_bytes(data), False, True)
        raise ImmutableConflict(object_key)

    def put_run_start(self, start: RunStartRecord) -> WriteResult:
        validate_run_start(start)
        return self.put_bytes(
            run_start_object_key(start.acquisition_run_id),
            canonical_json_bytes(start),
        )

    def put_chunk(
        self,
        run_id: str,
        entity: str,
        sequence: int,
        records: Sequence[Mapping],
    ) -> Dict[str, object]:
        field_name = ENTITY_PRIMARY_FIELDS.get(entity)
        if field_name is None:
            raise PublicationValidationError("unknown chunk entity")
        prepared = []
        seen = set()
        for record in records:
            if hasattr(record, "__dataclass_fields__"):
                body = record_to_dict(record)
            elif isinstance(record, Mapping):
                body = dict(record)
            else:
                raise PublicationValidationError("chunk record must be a mapping")
            identity = body.get(field_name)
            if not isinstance(identity, str) or not identity:
                raise PublicationValidationError("chunk row is missing its primary identity")
            if identity in seen:
                raise PublicationValidationError("duplicate entity identity")
            seen.add(identity)
            prepared.append(body)
        raw = jsonl_bytes(prepared)
        payload = gzip_deterministic(raw)
        key = chunk_object_key(run_id, entity, sequence)
        result = self.put_bytes(key, payload)
        return {
            "object_key": key,
            "sha256": sha256_bytes(raw),
            "row_count": len(prepared),
            "uncompressed_byte_length": len(raw),
            "write": result,
        }

    def put_checkpoint(self, checkpoint: EvidenceCheckpointManifest) -> WriteResult:
        validate_checkpoint(checkpoint)
        return self.put_bytes(
            checkpoint_object_key(checkpoint),
            canonical_json_bytes(checkpoint),
        )

    def put_terminal(
        self, terminal: TerminalPublicationManifest, *, publish_catalog: bool = True
    ) -> WriteResult:
        validate_terminal(terminal)
        result = self.put_bytes(
            terminal_object_key(terminal.acquisition_run_id),
            canonical_json_bytes(terminal),
        )
        if not publish_catalog:
            return result
        catalog = CatalogEntry(
            evidence_schema_version=terminal.evidence_schema_version,
            acquisition_run_id=terminal.acquisition_run_id,
            terminal_id=terminal.terminal_id,
            catalog_object_key=terminal.catalog_object_key,
            execution_status=terminal.execution_status,
            published_at=terminal.published_at,
        )
        validate_catalog_entry(catalog)
        self.put_bytes(catalog.catalog_object_key, canonical_json_bytes(catalog))
        return result

    def put_pointer(self, role: str, acquisition_run_id: str, catalog_key: str, expected_generation: int) -> WriteResult:
        """Compare-and-swap a mutable pointer. Identical targets are a no-op.

        This is process-local. It is not a cloud generation precondition.
        """
        if expected_generation < 0:
            raise GenerationPreconditionError("generation must be >= 0")
        key = "pointers/latest-{0}.json".format(role)
        path = self._path(key)
        lock_path = self._path(key + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = open(str(lock_path), "a+")
        try:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            return self._write_pointer_locked(
                key, path, role, acquisition_run_id, catalog_key, expected_generation
            )
        finally:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    def _write_pointer_locked(
        self, key, path, role, acquisition_run_id, catalog_key, expected_generation
    ) -> WriteResult:
        desired_identity = {
            "acquisition_run_id": acquisition_run_id,
            "catalog_object_key": catalog_key,
            "role": role,
        }
        if path.exists():
            current = json.loads(path.read_bytes().decode("utf-8"))
            current_identity = {
                "acquisition_run_id": current.get("acquisition_run_id"),
                "catalog_object_key": current.get("catalog_object_key"),
                "role": current.get("role"),
            }
            if current_identity == desired_identity:
                return WriteResult(key, sha256_bytes(path.read_bytes()), False, True)
            if current.get("generation") != expected_generation:
                raise GenerationPreconditionError(key)
            generation = expected_generation + 1
        else:
            if expected_generation != 0:
                raise GenerationPreconditionError(key)
            generation = 1
        body = {
            "acquisition_run_id": acquisition_run_id,
            "catalog_object_key": catalog_key,
            "evidence_schema_version": "evidence-contract-1",
            "generation": generation,
            "role": role,
        }
        data = canonical_json_bytes(body)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return WriteResult(key, sha256_bytes(data), True, False)

    def list_run_start_ids(self) -> list:
        ids = []
        runs = self.root / "runs"
        if not runs.exists():
            return ids
        for path in sorted(runs.glob("*/start.json")):
            ids.append(path.parent.name)
        return ids

    def list_catalog_run_ids(self) -> list:
        catalog = self.root / "catalog"
        if not catalog.exists():
            return []
        return sorted(path.stem for path in catalog.glob("*.json"))

    def discover_run_ids(self) -> list:
        """Historical discovery does not read the latest pointer."""
        return sorted(set(self.list_run_start_ids()) | set(self.list_catalog_run_ids()))

    def has_terminal(self, run_id: str) -> bool:
        return self._path(terminal_object_key(run_id)).exists()

    def read_bytes(self, object_key: str) -> bytes:
        return self._path(object_key).read_bytes()

    def _path(self, object_key: str) -> Path:
        if object_key.startswith("/") or ".." in object_key.split("/"):
            raise EvidenceContractError("object key must be relative")
        path = (self.root / object_key).resolve()
        if self.root.resolve() != path and self.root.resolve() not in path.parents:
            raise EvidenceContractError("object key escapes the store root")
        return path
