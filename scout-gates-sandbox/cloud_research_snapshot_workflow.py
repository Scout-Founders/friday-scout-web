#!/usr/bin/env python3
"""Helpers for cloud research snapshot artifact import in GitHub Actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


SNAPSHOT_ARTIFACT_NAME = "scout-research-snapshot"
SNAPSHOT_DB_FILENAME = "research_snapshot.db"
MANIFEST_FILENAMES = (
    "research_snapshot_manifest.json",
    "research_snapshot.manifest.json",
)


def parse_use_snapshot_artifact(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def resolve_snapshot_bundle(bundle_dir: Path) -> dict[str, Path]:
    bundle_dir = bundle_dir.expanduser().resolve()
    if not bundle_dir.exists():
        raise FileNotFoundError(f"Snapshot bundle directory not found: {bundle_dir}")

    snapshot_db_path = bundle_dir / SNAPSHOT_DB_FILENAME
    if not snapshot_db_path.exists():
        raise FileNotFoundError(
            f"Snapshot database not found in bundle: {snapshot_db_path}"
        )

    manifest_path: Optional[Path] = None
    for filename in MANIFEST_FILENAMES:
        candidate = bundle_dir / filename
        if candidate.exists():
            manifest_path = candidate
            break
    if manifest_path is None:
        expected = ", ".join(MANIFEST_FILENAMES)
        raise FileNotFoundError(
            f"Snapshot manifest not found in bundle {bundle_dir}. Expected one of: {expected}"
        )

    return {
        "bundleDir": bundle_dir,
        "snapshotDbPath": snapshot_db_path,
        "manifestPath": manifest_path,
    }


def build_import_command(
    *,
    bundle_dir: Path,
    target_db_path: Path,
    python_executable: str = "python3",
    importer_script: Optional[Path] = None,
) -> list[str]:
    bundle = resolve_snapshot_bundle(bundle_dir)
    script_path = importer_script or (Path(__file__).resolve().parent / "import_research_snapshot.py")
    return [
        python_executable,
        str(script_path),
        "--snapshot",
        str(bundle["snapshotDbPath"]),
        "--manifest",
        str(bundle["manifestPath"]),
        "--target",
        str(target_db_path),
    ]


def build_parser() -> argparse.ArgumentParser:
    import argparse

    parser = argparse.ArgumentParser(
        description="Cloud research snapshot workflow helpers for GitHub Actions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-bundle",
        help="Validate snapshot artifact bundle contents before import.",
    )
    validate.add_argument("--bundle-dir", type=Path, required=True)

    manifest = subparsers.add_parser(
        "manifest-path",
        help="Print resolved manifest path for a snapshot artifact bundle.",
    )
    manifest.add_argument("--bundle-dir", type=Path, required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate-bundle":
        resolve_snapshot_bundle(args.bundle_dir)
        return 0
    if args.command == "manifest-path":
        bundle = resolve_snapshot_bundle(args.bundle_dir)
        print(bundle["manifestPath"])
        return 0
    raise argparse.ArgumentError(None, f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
