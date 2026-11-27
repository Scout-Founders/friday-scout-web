#!/usr/bin/env python3
"""Heading-recognition tests for emailed Scout recommendations.

Fixtures are synthetic. Production report bodies are not stored here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _parse(text: str) -> dict:
    from sent_pick_parser import parse_scout_email_picks

    return parse_scout_email_picks(text)


def _identities(text: str) -> list[tuple[str, str]]:
    parsed = _parse(text)
    return [(pick["email_classification"], pick["ticker"]) for pick in parsed["picks"]]


class HeadingRecognitionTests(unittest.TestCase):
    def test_plain_markdown_headings(self) -> None:
        text = """\
## MACRO BRIEF
Quiet tape.

## TOP PICK — AAPL (CALL)
**STRATEGY: Bull Call Spread**

## SECONDARY PICK — MSFT (PUT)
Strategy: Bear put spread.

## WATCH LIST
- NVDA: waiting on volume
"""
        parsed = _parse(text)
        self.assertEqual(parsed["parse_mode"], "editorial")
        self.assertEqual(
            _identities(text),
            [("top", "AAPL"), ("secondary", "MSFT"), ("watch", "NVDA")],
        )
        by_class = {pick["email_classification"]: pick for pick in parsed["picks"]}
        self.assertEqual(by_class["top"]["direction"], "CALL")
        self.assertEqual(by_class["top"]["strategy_text"], "Bull Call Spread")
        self.assertEqual(by_class["secondary"]["direction"], "PUT")
        self.assertEqual(by_class["secondary"]["strategy_text"], "Bear put spread")
        self.assertIsNone(by_class["watch"]["direction"])
        self.assertIsNone(by_class["watch"]["strategy_text"])

    def test_emoji_prefixed_headings(self) -> None:
        text = """\
## MACRO BRIEF
Holiday tape.

## 🏆 TOP PICK — HL (Hecla Mining)
### STRATEGY: Bull Call Spread
Setup notes do not name a call or put contract.

## 📌 SECONDARY PICK — KTOS (Kratos Defense)
### STRATEGY: Bull Call Spread

## 👀 WATCH LIST
**IONQ ($23.54)** — quantum name on the list
**ACMR ($40.28)** — equipment name on the list
**PAYC ($247.49)** — on the short radar, not given a contract
"""
        parsed = _parse(text)
        self.assertEqual(
            _identities(text),
            [
                ("top", "HL"),
                ("secondary", "KTOS"),
                ("watch", "IONQ"),
                ("watch", "ACMR"),
                ("watch", "PAYC"),
            ],
        )
        by_ticker = {pick["ticker"]: pick for pick in parsed["picks"]}
        self.assertIsNone(by_ticker["HL"]["direction"])
        self.assertEqual(by_ticker["HL"]["strategy_text"], "Bull Call Spread")
        self.assertIsNone(by_ticker["KTOS"]["direction"])
        self.assertEqual(by_ticker["KTOS"]["strategy_text"], "Bull Call Spread")
        for ticker in ("IONQ", "ACMR", "PAYC"):
            self.assertIsNone(by_ticker[ticker]["direction"])
            self.assertIsNone(by_ticker[ticker]["strike"])
            self.assertIsNone(by_ticker[ticker]["expiration"])
            self.assertIsNone(by_ticker[ticker]["strategy_text"])

    def test_bold_headings_and_whitespace(self) -> None:
        text = """\
MACRO BRIEF
Open.

   **TOP PICK — COST (CALL)**

\t**Secondary Pick - WMT**

**WATCH LIST**
- TGT: no side stated
"""
        parsed = _parse(text)
        self.assertEqual(
            _identities(text),
            [("top", "COST"), ("secondary", "WMT"), ("watch", "TGT")],
        )
        by_class = {pick["email_classification"]: pick for pick in parsed["picks"]}
        self.assertEqual(by_class["top"]["direction"], "CALL")
        self.assertIsNone(by_class["secondary"]["direction"])
        self.assertIsNone(by_class["secondary"]["strategy_text"])
        self.assertIsNone(by_class["watch"]["direction"])

    def test_capitalization_variations(self) -> None:
        text = """\
macro brief
Quiet.

top pick
nvda (call) strength. Strategy: long call.

Secondary pick
meta — bearish setup. Strategy: bear put spread.

watch list
1. AVGO — one-liner
"""
        self.assertEqual(
            _identities(text),
            [("top", "NVDA"), ("secondary", "META"), ("watch", "AVGO")],
        )

    def test_report_without_recommendations(self) -> None:
        text = """\
## MACRO BRIEF
Index futures are flat and no name is being featured.

## SECTOR TABLE
Energy lagging.
"""
        parsed = _parse(text)
        self.assertEqual(parsed["parse_mode"], "editorial")
        self.assertEqual(parsed["picks"], [])

    def test_narrative_mentions_are_not_sections(self) -> None:
        text = """\
## MACRO BRIEF
The top pick from last month was discussed only as background.
A watch list can be useful, but this paragraph is not one.
Secondary pick language in a sentence does not open a section.

## SECTOR TABLE
No featured name.
"""
        parsed = _parse(text)
        self.assertEqual(parsed["picks"], [])

    def test_long_sentence_starting_with_keyword_is_not_a_section(self) -> None:
        text = """\
## MACRO BRIEF
Context only.

Top pick language continues across this whole line without naming a symbol anywhere in it.

## SECTOR TABLE
Done.
"""
        self.assertEqual(_parse(text)["picks"], [])

    def test_malformed_section_without_ticker_is_skipped(self) -> None:
        text = """\
MACRO BRIEF
Quiet.

TOP PICK
Company remains unidentified throughout.

SECONDARY PICK
MSFT (CALL) is the only complete recommendation.
"""
        parsed = _parse(text)
        self.assertEqual(_identities(text), [("secondary", "MSFT")])
        self.assertEqual(parsed["picks"][0]["direction"], "CALL")

    def test_duplicate_recommendations_collapse(self) -> None:
        text = """\
MACRO BRIEF
Quiet.

TOP PICK — AAPL (CALL)
Strategy: bull call spread.

TOP PICK — AAPL (CALL)
Repeated heading for the same name.

WATCH LIST
- NVDA: first
- NVDA: duplicate line
"""
        parsed = _parse(text)
        self.assertEqual(_identities(text), [("top", "AAPL"), ("watch", "NVDA")])
        self.assertEqual(len(parsed["picks"]), 2)

    def test_missing_direction_and_strategy_stay_missing(self) -> None:
        text = """\
MACRO BRIEF
Quiet.

TOP PICK
COST — relative strength with no option side and no strategy label.

WATCH LIST
- TGT: no direction stated
"""
        parsed = _parse(text)
        top = parsed["picks"][0]
        watch = parsed["picks"][1]
        self.assertIsNone(top["direction"])
        self.assertIsNone(top["strategy_text"])
        self.assertIsNone(top["strike"])
        self.assertIsNone(top["expiration"])
        self.assertIsNone(watch["direction"])
        self.assertIsNone(watch["strategy_text"])
        self.assertEqual(top["parser_version"], "sent-pick-parser-2")

    def test_strategy_markdown_cleanup_does_not_change_words(self) -> None:
        from sent_pick_parser import _normalize_strategy_text

        self.assertEqual(_normalize_strategy_text("Bull Call Spread**"), "Bull Call Spread")
        self.assertEqual(_normalize_strategy_text("**Bear put spread**"), "Bear put spread")
        self.assertEqual(_normalize_strategy_text("cash-secured put"), "cash-secured put")

    def test_other_emoji_prefixes_that_would_miss_the_old_pattern(self) -> None:
        text = """\
### 📌 Secondary Pick: AMD (PUT)
Strategy: bear put spread.

# 🥇 Top Pick — QCOM (CALL)
Strategy: long call.
"""
        # Secondary is recognized even when it appears before the top heading.
        self.assertEqual(
            _identities(text),
            [("secondary", "AMD"), ("top", "QCOM")],
        )


class SentPickIngestIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        import memory_store as ms

        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "parser_ingest.db"
        self._patchers = [
            patch.object(ms, "DB_PATH", self._db_path),
            patch.object(ms, "_DB_INITIALIZED", False),
        ]
        for patcher in self._patchers:
            patcher.start()
        ms.init_db()

    def tearDown(self) -> None:
        for patcher in self._patchers:
            patcher.stop()
        self._tmpdir.cleanup()

    def _doc(self) -> dict:
        return {
            "report_id": "scout_v6_fixture_1",
            "report_type": "daily_scan",
            "report_version": "6",
            "market_date": "2026-11-26",
            "generated_at": "2026-11-26T08:49:00-06:00",
            "raw_report_text": """\
## MACRO BRIEF
Synthetic brief.

## 🏆 TOP PICK — HL
### STRATEGY: Bull Call Spread

## 📌 SECONDARY PICK — KTOS (CALL)
### STRATEGY: Bull Call Spread

## 👀 WATCH LIST
**IONQ** — listed
""",
            "structured_report_json": {
                "picks": [
                    {
                        "ticker": "KTOS",
                        "direction": "CALL",
                        "strategy": "CASH-SECURED PUT",
                        "scout_score": 96,
                    },
                    {
                        "ticker": "IONS",
                        "direction": "CALL",
                        "strategy": "BULL CALL SPREAD",
                        "scout_score": 100,
                    },
                ],
                "total_passed": 4,
            },
            "email_attempted": True,
            "email_sent": True,
            "email_sent_at": "2026-11-26T08:49:00-06:00",
        }

    def test_candidate_strategy_does_not_replace_emailed_strategy(self) -> None:
        import ingest_scout_reports as isr

        isr.ingest_scout_report_document(self._doc())
        emailed = {row["ticker"]: row for row in isr.list_sent_picks()}
        self.assertEqual(set(emailed), {"HL", "KTOS", "IONQ"})
        self.assertEqual(emailed["KTOS"]["emailClassification"], "secondary")
        self.assertEqual(emailed["KTOS"]["direction"], "CALL")
        self.assertEqual(emailed["KTOS"]["strategyText"], "Bull Call Spread")
        self.assertNotIn("IONS", emailed)
        report = isr.list_ingested_daily_reports()[0]
        candidates = {
            row["ticker"]: row for row in isr.list_scan_candidates_for_report(report)
        }
        self.assertEqual(candidates["KTOS"]["strategy"], "CASH-SECURED PUT")
        self.assertEqual(candidates["KTOS"]["role"], "scan_candidate")
        self.assertEqual(emailed["HL"]["emailClassification"], "top")
        self.assertIsNone(emailed["HL"]["direction"])
        stored = emailed["KTOS"]["pick"]
        self.assertEqual(stored["parser_version"], "sent-pick-parser-2")

    def test_repeated_ingestion_keeps_ids_and_report_body(self) -> None:
        import ingest_scout_reports as isr

        doc = self._doc()
        isr.ingest_scout_report_document(doc)
        first = isr.list_sent_picks()
        first_ids = [row["sentPickId"] for row in first]
        self.assertEqual(
            first_ids,
            [
                "scout_v6_fixture_1|top|HL",
                "scout_v6_fixture_1|secondary|KTOS",
                "scout_v6_fixture_1|watch|IONQ",
            ],
        )
        isr.ingest_scout_report_document(doc)
        second = isr.list_sent_picks()
        self.assertEqual([row["sentPickId"] for row in second], first_ids)
        self.assertEqual(len(isr.list_ingested_daily_reports()), 1)
        report = isr.list_ingested_daily_reports()[0]
        self.assertEqual(report["reportId"], "scout_v6_fixture_1")
        self.assertIn("## 🏆 TOP PICK — HL", report["rawReportText"])
        created = {row["sentPickId"]: row["createdAt"] for row in first}
        for row in second:
            self.assertEqual(row["createdAt"], created[row["sentPickId"]])

    def test_findings_are_not_duplicated_on_repeat(self) -> None:
        import ingest_scout_reports as isr
        from memory_store import connect

        doc = self._doc()
        isr.ingest_scout_report_document(doc)
        first = isr.generate_findings_from_daily_reports(limit=10)
        second = isr.generate_findings_from_daily_reports(limit=10)
        self.assertGreaterEqual(first["generated"], 1)
        self.assertEqual(second["generated"], 0)
        self.assertGreaterEqual(second["skippedDuplicates"], first["generated"])
        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM research_findings WHERE finding_type = 'sent_pick_observation'"
            ).fetchone()[0]
        self.assertEqual(count, first["generated"])


if __name__ == "__main__":
    unittest.main()
