#!/usr/bin/env python3
"""Verify a Goobs Trivia Finder database and its manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    database = Path(args.database)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    connection = sqlite3.connect(database)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        facts = connection.execute("SELECT count(*) FROM facts").fetchone()[0]
        disney = connection.execute("SELECT count(*) FROM facts WHERE lower(category) = 'disney'").fetchone()[0]
        disney_songs = connection.execute("SELECT count(*) FROM facts WHERE kind = 'song'").fetchone()[0]
        categories = connection.execute("SELECT count(DISTINCT category) FROM facts").fetchone()[0]
        indexed = connection.execute("SELECT count(*) FROM trivia_fts").fetchone()[0]
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        regression_queries = {
            "little AND mermaid AND crab": "Sebastian",
            "ursula AND eels": "Flotsam and Jetsam",
            "scuttle AND fork": "A dinglehopper",
            "dalmatians AND villain": "Cruella de Vil",
            "girl AND green AND hoodie": "Vanellope von Schweetz",
            "mulan AND cricket": "Cri-Kee",
            "prince AND john AND snake": "Sir Hiss",
            "australia AND capital": "Canberra",
            "bond AND metal AND teeth": "Richard Kiel",
            "port AND ship AND left": "The left side",
            "down AND wetter": "Under the Sea",
            "cold AND bothered AND anyway": "Let It Go",
            "pressure AND drip": "Surface Pressure",
        }
        for query, expected_answer in regression_queries.items():
            match = connection.execute(
                "SELECT facts.answer FROM trivia_fts JOIN facts ON facts.id = trivia_fts.fact_id WHERE trivia_fts MATCH ? ORDER BY bm25(trivia_fts) LIMIT 1",
                (query,),
            ).fetchone()
            assert match and match[0] == expected_answer, (query, match, expected_answer)
    finally:
        connection.close()
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    checks = {
        "integrity": integrity,
        "facts": facts,
        "disneyFacts": disney,
        "disneySongs": disney_songs,
        "categories": categories,
        "indexedFacts": indexed,
        "applicationId": app_id,
        "schemaVersion": schema_version,
        "bytes": database.stat().st_size,
        "sha256": digest,
        "regressionQueries": len(regression_queries),
    }
    assert integrity == "ok"
    assert app_id == 1196579154
    assert schema_version == 2
    assert facts == indexed == manifest["facts"]
    assert disney == manifest["disneyFacts"]
    assert disney_songs == manifest["disneySongs"]
    assert categories == manifest["categories"]
    assert database.stat().st_size == manifest["bytes"]
    assert digest == manifest["sha256"]
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
