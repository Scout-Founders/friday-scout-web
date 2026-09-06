#!/usr/bin/env python3
"""Export production Firestore scout_reports into the scout-daily-reports artifact bundle.

CI / publisher use only. Does not modify production email, SMTP, or hosted VPS config.
Never prints credential contents.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Union

from ingest_scout_reports import (
    BUNDLE_FILENAME,
    BUNDLE_SCHEMA_VERSION,
    MANIFEST_FILENAME,
    build_report_bundle_manifest,
    fetch_scout_report_documents,
    firestore_credentials_available,
    normalize_firestore_document,
    utc_now_iso,
    validate_report_bundle_manifest,
)


DEFAULT_EXPORT_LIMIT = 100
DEFAULT_LOOKBACK_DAYS = 7


class ExportError(RuntimeError):
    """Raised when the daily-reports export cannot complete safely."""


def _parse_structured_for_bundle(value: Any) -> Any:
    if value is None or value == "":
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"unparsed": text}
        if isinstance(parsed, (dict, list)):
            return parsed
        return {"value": parsed}
    return {"value": value}


def bundle_record_from_normalized(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert normalize_firestore_document output into an artifact report object."""
    report_id = str(payload.get("report_id") or "").strip()
    report_type = payload.get("report_type")
    if not report_id:
        raise ExportError("Normalized report is missing report_id.")
    if report_type in (None, ""):
        raise ExportError(f"Normalized report {report_id} is missing report_type.")

    return {
        "report_id": report_id,
        "report_type": report_type,
        "report_version": payload.get("report_version"),
        "market_date": payload.get("market_date"),
        "generated_at": payload.get("generated_at"),
        "status_prefix": payload.get("status_prefix"),
        "email_subject": payload.get("email_subject"),
        "raw_report_text": payload.get("raw_report_text"),
        "structured_report_json": _parse_structured_for_bundle(
            payload.get("structured_report_json")
        ),
        "source_scan_run_id": payload.get("source_scan_run_id"),
        "claude_model": payload.get("claude_model"),
        "underlying_data_timestamp": payload.get("underlying_data_timestamp"),
        "email_attempted": bool(payload.get("email_attempted")),
        "email_sent": bool(payload.get("email_sent")),
        "email_sent_at": payload.get("email_sent_at"),
        "email_error": payload.get("email_error"),
    }


def normalize_documents_for_export(documents: list[Any]) -> list[dict[str, Any]]:
    """Normalize Firestore docs, fail clearly on malformed entries, dedupe by report_id."""
    by_report_id: dict[str, dict[str, Any]] = {}
    for index, doc in enumerate(documents):
        try:
            normalized = normalize_firestore_document(doc)
            record = bundle_record_from_normalized(normalized)
        except Exception as exc:
            raise ExportError(
                f"Malformed scout_reports document at index {index}: {exc}"
            ) from exc

        report_id = record["report_id"]
        existing = by_report_id.get(report_id)
        if existing is None:
            by_report_id[report_id] = record
            continue

        # Prefer the newest generated_at when duplicates appear.
        existing_ts = str(existing.get("generated_at") or "")
        candidate_ts = str(record.get("generated_at") or "")
        if candidate_ts >= existing_ts:
            by_report_id[report_id] = record

    reports = list(by_report_id.values())
    reports.sort(
        key=lambda item: (
            str(item.get("generated_at") or ""),
            str(item.get("report_id") or ""),
        )
    )
    return reports


def resolve_since(
    *,
    since: Optional[str] = None,
    lookback_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Resolve incremental export window.

    Explicit --since wins. Otherwise optional lookback_days builds an ISO lower bound.
    """
    if since is not None and str(since).strip():
        return str(since).strip()
    if lookback_days is None:
        return None
    days = int(lookback_days)
    if days < 0:
        raise ExportError("--lookback-days must be >= 0.")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    start = current - timedelta(days=days)
    return start.isoformat()


def build_report_bundle_payload(
    reports: list[dict[str, Any]],
    *,
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": generated_at or utc_now_iso(),
        "reports": reports,
    }


def write_report_bundle(
    output_dir: Union[str, Path],
    reports: list[dict[str, Any]],
    *,
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Write scout_daily_reports.json + manifest into output_dir."""
    directory = Path(output_dir).expanduser().resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExportError(f"Cannot create output directory {directory}: {exc}") from exc

    bundle_path = directory / BUNDLE_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    payload = build_report_bundle_payload(reports, generated_at=generated_at)

    try:
        bundle_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ExportError(f"Failed to write report bundle {bundle_path}: {exc}") from exc

    try:
        manifest = build_report_bundle_manifest(
            bundle_path=bundle_path,
            generated_at=payload["generated_at"],
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        raise ExportError(f"Failed to write report bundle manifest: {exc}") from exc

    try:
        validate_report_bundle_manifest(
            manifest_path=manifest_path,
            bundle_path=bundle_path,
        )
    except Exception as exc:
        raise ExportError(f"Generated bundle failed validation: {exc}") from exc

    return {
        "ok": True,
        "outputDir": str(directory),
        "bundlePath": str(bundle_path),
        "manifestPath": str(manifest_path),
        "reportCount": len(reports),
        "schemaVersion": BUNDLE_SCHEMA_VERSION,
        "generatedAt": payload["generated_at"],
        "checksumSha256": manifest["checksum_sha256"],
        "emailSentTrue": sum(1 for report in reports if report.get("email_sent")),
        "emailSentFalse": sum(1 for report in reports if not report.get("email_sent")),
    }


def export_scout_daily_reports(
    *,
    output_dir: Union[str, Path],
    since: Optional[str] = None,
    lookback_days: Optional[int] = None,
    limit: int = DEFAULT_EXPORT_LIMIT,
    documents: Optional[list[Any]] = None,
) -> dict[str, Any]:
    """Fetch (or accept) scout_reports docs and write the artifact bundle."""
    resolved_since = resolve_since(since=since, lookback_days=lookback_days)
    bounded_limit = max(int(limit), 1)

    if documents is None:
        if not firestore_credentials_available():
            raise ExportError(
                "Firestore credentials unavailable. Set SCOUT_FIRESTORE_SERVICE_ACCOUNT_JSON "
                "(and optionally SCOUT_FIRESTORE_PROJECT_ID), or GOOGLE_APPLICATION_CREDENTIALS. "
                "Credential contents are never logged."
            )
        try:
            fetched = fetch_scout_report_documents(
                limit=bounded_limit,
                since=resolved_since,
            )
        except ExportError:
            raise
        except Exception as exc:
            raise ExportError(
                f"Firestore scout_reports read failed (credentials or query error): {exc}"
            ) from exc
        source_docs = list(fetched or [])
    else:
        source_docs = list(documents)

    reports = normalize_documents_for_export(source_docs)
    if len(reports) > bounded_limit:
        # Keep the newest reports when an injected document list exceeds --limit.
        reports = reports[-bounded_limit:]

    result = write_report_bundle(output_dir, reports)
    result["since"] = resolved_since
    result["limit"] = bounded_limit
    result["fetchedCount"] = len(source_docs)
    result["dedupedCount"] = len(reports)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export Firestore scout_reports into scout_daily_reports.json + manifest "
            "for the scout-daily-reports GitHub Actions artifact."
        )
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory that will receive scout_daily_reports.json and manifest.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Optional ISO lower bound on generated_at for incremental export.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help=(
            "If --since is omitted, export reports with generated_at within this many "
            f"days (CI default is often {DEFAULT_LOOKBACK_DAYS})."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_EXPORT_LIMIT,
        help=f"Max Firestore documents to fetch (default {DEFAULT_EXPORT_LIMIT}).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = export_scout_daily_reports(
            output_dir=args.output_dir,
            since=args.since,
            lookback_days=args.lookback_days,
            limit=max(int(args.limit), 1),
        )
    except ExportError as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": f"Unexpected export failure: {exc}",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
