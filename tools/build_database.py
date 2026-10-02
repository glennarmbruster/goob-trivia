#!/usr/bin/env python3
"""Build a Goobs Trivia Finder SQLite database from JSONL or CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


TOKEN_RE = re.compile(r"[a-z0-9]+")
REQUIRED_FIELDS = {"id", "question", "answer", "category"}
APPLICATION_ID = 1196579154
DIFFICULTIES = {"easy", "medium", "hard", "expert"}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("’", "'").replace("‘", "'").replace("&", " and ")
    value = re.sub(r"\b(can)['’]?t\b", "cannot", value)
    value = re.sub(r"\b(won)['’]?t\b", "will not", value)
    value = re.sub(r"\b([a-z]+)n['’]t\b", r"\1 not", value)
    value = re.sub(r"\b([a-z]+)['’](re|ve|ll|d|m|s)\b", r"\1 \2", value)
    return " ".join(TOKEN_RE.findall(value))


def soundex(token: str) -> str:
    if not token:
        return ""
    codes = {
        **dict.fromkeys("bfpv", "1"),
        **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"),
        "l": "4",
        **dict.fromkeys("mn", "5"),
        "r": "6",
    }
    encoded: list[str] = []
    previous = codes.get(token[0], "")
    for char in token[1:]:
        current = codes.get(char, "")
        if current and current != previous:
            encoded.append(current)
        previous = current
    return (token[0].upper() + "".join(encoded) + "000")[:4]


def read_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    else:
        raise ValueError("Input must be .jsonl or .csv")
    seen: set[str] = set()
    for number, row in enumerate(rows, start=1):
        missing = REQUIRED_FIELDS.difference(row)
        if missing:
            raise ValueError(f"Row {number} is missing: {', '.join(sorted(missing))}")
        fact_id = str(row["id"]).strip()
        if not fact_id or fact_id in seen:
            raise ValueError(f"Row {number} has a blank or duplicate id: {fact_id!r}")
        seen.add(fact_id)
    return rows


def read_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [item.strip() for item in str(value).split("|") if item.strip()]
    return []


def display_list(value: object) -> str | None:
    items = read_list(value)
    return ", ".join(items) if items else None


def build(args: argparse.Namespace) -> dict:
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    manifest_path = Path(args.manifest).resolve()
    rows = read_rows(input_path)
    prepared: list[dict] = []
    for row in rows:
        kind = str(row.get("kind") or "fact").strip().lower()
        if kind not in {"fact", "song"}:
            raise ValueError(f"Invalid record kind for {row['id']}: {kind}")
        difficulty = str(row.get("difficulty") or "medium").strip().lower()
        if difficulty not in DIFFICULTIES:
            raise ValueError(f"Invalid difficulty for {row['id']}: {difficulty}")
        aliases = read_list(row.get("aliases"))
        tags = read_list(row.get("tags"))
        parts = [
            row["question"], row["answer"], row["category"], row.get("subcategory", ""), row.get("details", ""),
            row.get("work", ""), display_list(row.get("character_performers")) or "", display_list(row.get("recording_artists")) or "",
            row.get("lyrics", ""), *aliases, *tags,
        ]
        search_text = normalize(" ".join(str(part) for part in parts if part))
        if not search_text:
            raise ValueError(f"Fact {row['id']} has no searchable text")
        prepared.append({**row, "kind": kind, "difficulty": difficulty, "aliases_list": aliases, "tags_list": tags, "search_text": search_text})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    connection = sqlite3.connect(output_path)
    try:
        connection.executescript(f"""
            PRAGMA journal_mode=DELETE;
            PRAGMA page_size=4096;
            PRAGMA application_id={APPLICATION_ID};
            PRAGMA user_version=2;

            CREATE TABLE build_info (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
            CREATE TABLE facts (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL DEFAULT 'fact',
              question TEXT NOT NULL,
              answer TEXT NOT NULL,
              category TEXT NOT NULL,
              subcategory TEXT,
              difficulty TEXT NOT NULL,
              details TEXT,
              aliases TEXT NOT NULL,
              tags TEXT NOT NULL,
              source TEXT,
              source_url TEXT,
              evergreen INTEGER NOT NULL DEFAULT 1,
              priority REAL NOT NULL DEFAULT 50,
              work TEXT,
              character_performers TEXT,
              recording_artists TEXT,
              year INTEGER
            ) WITHOUT ROWID;
            CREATE INDEX facts_category ON facts(category, subcategory);
            CREATE VIRTUAL TABLE trivia_fts USING fts5(
              fact_id UNINDEXED,
              search_text,
              tokenize='unicode61 remove_diacritics 2',
              prefix='2 3'
            );
            CREATE TABLE lexicon (
              token TEXT PRIMARY KEY,
              document_frequency INTEGER NOT NULL,
              phonetic TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE INDEX lexicon_phonetic ON lexicon(phonetic, document_frequency DESC);
        """)
        document_frequency: Counter[str] = Counter()
        for row in prepared:
            connection.execute(
                """INSERT INTO facts(id, kind, question, answer, category, subcategory, difficulty, details, aliases, tags, source, source_url, evergreen, priority, work, character_performers, recording_artists, year)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(row["id"]), row["kind"], str(row["question"]).strip(), str(row["answer"]).strip(), str(row["category"]).strip(),
                    str(row.get("subcategory") or "").strip() or None, row["difficulty"], str(row.get("details") or "").strip() or None,
                    json.dumps(row["aliases_list"], ensure_ascii=False), json.dumps(row["tags_list"], ensure_ascii=False),
                    str(row.get("source") or "").strip() or None, str(row.get("source_url") or "").strip() or None,
                    1 if row.get("evergreen", True) not in (False, 0, "0", "false", "False") else 0,
                    float(row.get("priority") or 50),
                    str(row.get("work") or "").strip() or None,
                    display_list(row.get("character_performers")),
                    display_list(row.get("recording_artists")),
                    int(row["year"]) if row.get("year") not in (None, "") else None,
                ),
            )
            connection.execute("INSERT INTO trivia_fts(fact_id, search_text) VALUES (?, ?)", (str(row["id"]), row["search_text"]))
            document_frequency.update(set(row["search_text"].split()))
        connection.executemany(
            "INSERT INTO lexicon(token, document_frequency, phonetic) VALUES (?, ?, ?)",
            ((token, frequency, soundex(token)) for token, frequency in document_frequency.items()),
        )
        version = args.version or datetime.now(timezone.utc).strftime("%Y.%m.%d")
        categories = {str(row["category"]).strip() for row in prepared}
        disney_count = sum(1 for row in prepared if str(row["category"]).strip().casefold() == "disney")
        disney_song_count = sum(1 for row in prepared if row["kind"] == "song")
        info = {
            "name": "Goobs Trivia Finder Database",
            "version": version,
            "schema_version": "2",
            "fact_count": str(len(prepared)),
            "disney_fact_count": str(disney_count),
            "disney_song_count": str(disney_song_count),
            "category_count": str(len(categories)),
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        connection.executemany("INSERT INTO build_info(key, value) VALUES (?, ?)", info.items())
        connection.execute("INSERT INTO trivia_fts(trivia_fts) VALUES ('optimize')")
        connection.commit()
        check = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {check}")
        connection.execute("VACUUM")
    finally:
        connection.close()

    digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
    manifest = {
        "format": "goobs-trivia-finder-database",
        "schemaVersion": 2,
        "databaseVersion": version,
        "facts": len(prepared),
        "disneyFacts": disney_count,
        "disneySongs": disney_song_count,
        "categories": len(categories),
        "bytes": output_path.stat().st_size,
        "sha256": digest,
        "file": output_path.name,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--version")
    args = parser.parse_args()
    print(json.dumps(build(args), indent=2))


if __name__ == "__main__":
    main()
