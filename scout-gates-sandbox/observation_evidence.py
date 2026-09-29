#!/usr/bin/env python3
"""Fail-closed research population contract for Horizon readers.

This module is intentionally independent of the scanner write path. It does
not import memory_store and does not open a database at import time. Callers
pass an existing SQLite connection.

Research readers must call assert_research_population before using
research_eligible_predicate. A failed check raises ResearchPopulationError.
There is no fallback to the full scan_results table or to is_test_record.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence


APPROVED_ORIGINS = frozenset(
    {
        "production",
        "test",
        "synthetic",
        "fixture",
        "backtest",
        "imported",
        "reconstructed",
    }
)

# b0-2026-09-28 labels the historical migration. capture-v1 labels provenance
# assigned when a new observation is created. Both may coexist. Any other
# version fails closed.
HISTORICAL_CLASSIFIER_VERSION = "b0-2026-09-28"
CAPTURE_CLASSIFIER_VERSION = "capture-v1"
APPROVED_CLASSIFIER_VERSIONS = frozenset(
    {
        HISTORICAL_CLASSIFIER_VERSION,
        CAPTURE_CLASSIFIER_VERSION,
    }
)

# SHA-256 over UTF-8 bytes of the eligible observation_uid values ordered by
# scan_result_id ascending. Each uid is followed by one newline (U+000A).
# There is no header and no other separator. The same uid sequence always
# hashes to the same digest. generated_at is not part of the hash.
ELIGIBLE_POPULATION_HASH_SPEC = (
    "sha256-utf8-observation-uid-lines-v1: "
    "ORDER BY scan_result_id ASC; each observation_uid encoded as UTF-8 "
    "and terminated by a single \\n"
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROVENANCE_ALIAS = "_observation_provenance"
_RESERVED_ALIASES = frozenset(
    {
        _PROVENANCE_ALIAS,
        "select",
        "from",
        "where",
        "and",
        "or",
        "not",
        "in",
        "join",
        "on",
        "as",
        "table",
        "index",
        "by",
        "group",
        "order",
        "limit",
        "exists",
        "case",
        "when",
        "then",
        "else",
        "end",
        "null",
        "is",
        "like",
        "between",
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "create",
        "into",
        "values",
        "set",
    }
)


class ResearchPopulationError(Exception):
    """The research population cannot be used.

    Callers must stop. They must not query the unfiltered scan_results table
    and must not substitute is_test_record.
    """


@dataclass(frozen=True)
class PopulationStamp:
    """Identity of the research-eligible population at one moment."""

    classifier_version: str
    eligible_population_count: int
    eligible_population_hash: str
    generated_at: str


def research_eligible_predicate(scan_results_alias: str = "sr") -> str:
    """SQL EXISTS predicate for research_eligible = 1.

    scan_results_alias must be a plain SQL identifier such as ``sr``.
    Arbitrary text is rejected so callers cannot inject SQL through the alias.
    """
    alias = _validated_alias(scan_results_alias)
    provenance = _PROVENANCE_ALIAS
    return (
        "EXISTS ("
        f"SELECT 1 FROM observation_provenance AS {provenance} "
        f"WHERE {provenance}.scan_result_id = {alias}.id "
        f"AND {provenance}.research_eligible = 1)"
    )


def assert_research_population(conn: sqlite3.Connection) -> None:
    """Raise ResearchPopulationError unless provenance is complete and consistent."""
    problems = population_problems(conn)
    if problems:
        raise ResearchPopulationError(
            "Research population is not usable. "
            "Refusing to fall back to scan_results or is_test_record. "
            + " ".join(problems)
        )


def classifier_identity(versions: Sequence[str]) -> str:
    """Join the versions present in one population, sorted lexically.

    One version stays unchanged. Two approved versions become
    ``b0-2026-09-28+capture-v1``.
    """
    return "+".join(sorted({version for version in versions if version}))


def population_stamp(conn: sqlite3.Connection, *, generated_at: Optional[str] = None) -> PopulationStamp:
    """Return the classifier identity, eligible count, and deterministic hash.

    Fails closed through assert_research_population. The classifier identity is
    the sorted composite of versions represented in the eligible population.
    generated_at is not part of that identity or the hash.
    """
    assert_research_population(conn)
    uids = _eligible_observation_uids(conn)
    return PopulationStamp(
        classifier_version=classifier_identity(_eligible_classifier_versions(conn)),
        eligible_population_count=len(uids),
        eligible_population_hash=hash_eligible_observation_uids(uids),
        generated_at=generated_at or _utc_now(),
    )


def hash_eligible_observation_uids(observation_uids: Sequence[str]) -> str:
    """Hash uids that are already ordered by scan_result_id ascending."""
    payload = "".join(f"{uid}\n" for uid in observation_uids).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def population_problems(conn: sqlite3.Connection) -> list[str]:
    """Describe every readiness failure. An empty list means the population is usable."""
    problems: list[str] = []
    if not _table_exists(conn, "scan_results"):
        problems.append("scan_results table is missing.")
    if not _table_exists(conn, "observation_provenance"):
        problems.append("observation_provenance table is missing.")
    if problems:
        return problems

    scan_count = _count(conn, "scan_results")
    provenance_count = _count(conn, "observation_provenance")
    if scan_count != provenance_count:
        problems.append(
            f"provenance count {provenance_count} != scan_results count {scan_count}."
        )

    missing = _missing_provenance_ids(conn)
    if missing:
        problems.append(
            "scan result lacks provenance: " + _sample(missing) + "."
        )

    duplicates = _duplicate_provenance_ids(conn)
    if duplicates:
        problems.append(
            "scan result has more than one provenance row: " + _sample(duplicates) + "."
        )

    orphans = _orphan_provenance_ids(conn)
    if orphans:
        problems.append(
            "provenance row lacks a scan_result: " + _sample(orphans) + "."
        )

    unknown = _unknown_origins(conn)
    if unknown:
        problems.append("unknown origin: " + ", ".join(unknown) + ".")

    invalid_flags = _invalid_research_eligible(conn)
    if invalid_flags:
        problems.append("invalid research_eligible: " + "; ".join(invalid_flags) + ".")

    eligible_violations = _eligible_rule_violations(conn)
    if eligible_violations:
        problems.append(
            "eligible row violates the production rule: " + "; ".join(eligible_violations) + "."
        )

    versions = _classifier_versions(conn)
    if not versions or any(version == "" for version in versions):
        problems.append("classifier version is missing.")
    unapproved = [version for version in versions if version not in APPROVED_CLASSIFIER_VERSIONS]
    if unapproved:
        problems.append("unapproved classifier version: " + ", ".join(unapproved) + ".")

    return problems


def _validated_alias(alias: str) -> str:
    if not isinstance(alias, str) or _IDENTIFIER_RE.fullmatch(alias) is None:
        raise ValueError(
            "scan_results alias must be a single SQL identifier such as 'sr'"
        )
    if alias.casefold() in _RESERVED_ALIASES:
        raise ValueError(f"scan_results alias {alias!r} is reserved")
    return alias


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    return int(row[0])


def _missing_provenance_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """
        SELECT sr.id
        FROM scan_results sr
        LEFT JOIN observation_provenance op ON op.scan_result_id = sr.id
        GROUP BY sr.id
        HAVING COUNT(op.observation_uid) = 0
        ORDER BY sr.id
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def _duplicate_provenance_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """
        SELECT scan_result_id
        FROM observation_provenance
        GROUP BY scan_result_id
        HAVING COUNT(*) > 1
        ORDER BY scan_result_id
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def _orphan_provenance_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        """
        SELECT op.scan_result_id
        FROM observation_provenance op
        LEFT JOIN scan_results sr ON sr.id = op.scan_result_id
        WHERE sr.id IS NULL
        ORDER BY op.scan_result_id
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def _unknown_origins(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT origin
        FROM observation_provenance
        ORDER BY origin
        """
    ).fetchall()
    return [str(row[0]) for row in rows if str(row[0]) not in APPROVED_ORIGINS]


def _invalid_research_eligible(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT observation_uid, research_eligible
        FROM observation_provenance
        WHERE research_eligible IS NULL
           OR research_eligible NOT IN (0, 1)
        ORDER BY scan_result_id
        """
    ).fetchall()
    return [f"{row[0]}={row[1]!r}" for row in rows]


def _eligible_rule_violations(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT observation_uid, origin, parent_observation_uid, research_eligible
        FROM observation_provenance
        WHERE research_eligible = 1
          AND (
                origin <> 'production'
                OR parent_observation_uid IS NOT NULL
              )
        ORDER BY scan_result_id
        """
    ).fetchall()
    violations: list[str] = []
    for uid, origin, parent, _eligible in rows:
        if origin != "production":
            violations.append(f"eligible non-production {uid} origin={origin}")
        if parent is not None:
            if origin == "production":
                violations.append(f"production child is research eligible {uid} parent={parent}")
            else:
                violations.append(f"eligible row has a parent {uid} parent={parent}")
    return violations


def _classifier_versions(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT COALESCE(classifier_version, '')
        FROM observation_provenance
        ORDER BY 1
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _eligible_classifier_versions(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT COALESCE(classifier_version, '')
        FROM observation_provenance
        WHERE research_eligible = 1
        ORDER BY 1
        """
    ).fetchall()
    return [str(row[0]) for row in rows if str(row[0])]


def _eligible_observation_uids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT observation_uid
        FROM observation_provenance
        WHERE research_eligible = 1
        ORDER BY scan_result_id ASC
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _sample(values: Sequence[object], limit: int = 20) -> str:
    shown = list(values[:limit])
    text = ", ".join(str(value) for value in shown)
    extra = len(values) - len(shown)
    if extra:
        text += f" (+{extra} more)"
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
