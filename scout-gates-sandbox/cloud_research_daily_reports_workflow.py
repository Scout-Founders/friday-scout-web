#!/usr/bin/env python3
"""Helpers for scout-daily-reports artifact import in GitHub Actions."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Optional

from ingest_scout_reports import (
    BUNDLE_FILENAME,
    DAILY_REPORTS_ARTIFACT_NAME,
    MANIFEST_FILENAME,
    MANIFEST_FILENAME_ALT,
    resolve_report_bundle_dir,
    validate_report_bundle_manifest,
)


def parse_use_daily_reports_artifact(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def resolve_daily_reports_bundle(bundle_dir: Path) -> dict[str, Path]:
    return resolve_report_bundle_dir(bundle_dir)


def build_import_command(
    *,
    bundle_dir: Path,
    python_executable: str = "python3",
    importer_script: Optional[Path] = None,
    validate_checksum: bool = True,
) -> list[str]:
    bundle = resolve_daily_reports_bundle(bundle_dir)
    script_path = importer_script or (Path(__file__).resolve().parent / "ingest_scout_reports.py")
    command = [
        python_executable,
        str(script_path),
        "--from-bundle-dir",
        str(bundle["bundleDir"]),
    ]
    if not validate_checksum:
        command.append("--skip-checksum")
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cloud research daily report artifact workflow helpers."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-bundle",
        help="Validate scout-daily-reports artifact bundle contents before import.",
    )
    validate.add_argument("--bundle-dir", type=Path, required=True)

    manifest = subparsers.add_parser(
        "manifest-path",
        help="Print resolved manifest path for a daily reports artifact bundle.",
    )
    manifest.add_argument("--bundle-dir", type=Path, required=True)

    bundle = subparsers.add_parser(
        "bundle-path",
        help="Print resolved bundle JSON path for a daily reports artifact bundle.",
    )
    bundle.add_argument("--bundle-dir", type=Path, required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "validate-bundle":
        resolved = resolve_daily_reports_bundle(args.bundle_dir)
        validate_report_bundle_manifest(
            manifest_path=resolved["manifestPath"],
            bundle_path=resolved["bundlePath"],
        )
        return 0
    if args.command == "manifest-path":
        resolved = resolve_daily_reports_bundle(args.bundle_dir)
        print(resolved["manifestPath"])
        return 0
    if args.command == "bundle-path":
        resolved = resolve_daily_reports_bundle(args.bundle_dir)
        print(resolved["bundlePath"])
        return 0
    raise argparse.ArgumentError(None, f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
