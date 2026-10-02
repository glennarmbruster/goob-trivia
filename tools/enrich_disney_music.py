#!/usr/bin/env python3
"""Merge Disney song metadata and privately attach lyrics from local parquet files."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / "data" / "tools" / "python"
if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))

import pyarrow.parquet as pq  # type: ignore  # noqa: E402


TOKEN_RE = re.compile(r"[a-z0-9]+")
CAST_ARTIST_WORKS = {
    "Walt Disney Records": "Walt Disney Records catalog",
    "The Muppets": "The Muppets franchise",
    "John Denver & The Muppets": "The Muppets franchise",
    "Original Broadway Cast of The Little Mermaid": "The Little Mermaid (Broadway musical)",
    "Original Broadway Cast of Frozen": "Frozen (Broadway musical)",
    "Original Broadway Cast of Beauty and the Beast": "Beauty and the Beast (Broadway musical)",
    "Original Broadway Cast of Aladdin": "Aladdin (Broadway musical)",
    "Original Broadway Cast of The Lion King": "The Lion King (Broadway musical)",
    "Original London Cast of Mary Poppins": "Mary Poppins (stage musical)",
    "Mary poppins - original london cast": "Mary Poppins (stage musical)",
    "Newsies Original Broadway Cast": "Newsies (Broadway musical)",
    "Newsies Ensemble (Film)": "Newsies",
    "Original Broadway Cast of Tarzan": "Tarzan (Broadway musical)",
    "High School Musical Cast": "High School Musical franchise",
    "Cast of High School Musical: The Musical: The Series": "High School Musical: The Musical: The Series",
    "Descendants Cast": "Descendants franchise",
    "Descendants 2 Cast": "Descendants 2",
    "Descendants 3 Cast": "Descendants 3",
    "Cast of Descendants": "Descendants franchise",
    "Cast of Descendants: The Royal Wedding": "Descendants: The Royal Wedding",
    "Cast of Camp Rock": "Camp Rock",
    "Cast of Frozen 2": "Frozen II",
    "The Cast of Frozen": "Frozen",
    "Cast - Frozen": "Frozen",
    "Maia Wilson & The Cast of Frozen": "Frozen",
    "Josh Gad, Kristen Bell, Idina Menzel, Jonathan Groff & Cast of Frozen II": "Frozen II",
    "Stephanie Beatriz, Olga Merediz, John Leguizamo, Adassa, Maluma & Encanto - Cast": "Encanto",
    "Stephanie Beatriz, Olga Merediz & Encanto - Cast": "Encanto",
    "ENCANTO (Stephanie Beatriz, Olga Merediz & Encanto - Cast)": "Encanto",
    "Cast of The Hunchback of Notre Dame": "The Hunchback of Notre Dame",
    "Cast of Mary Poppins Returns": "Mary Poppins Returns",
    "Disneyland Paris Lion King Ensemble Cast": "The Lion King: Rhythms of the Pride Lands",
    "Original South African Cast of The Lion King": "The Lion King (stage musical)",
    "Paige OHara, Richard White, Jesse Corti & The Chorus of Beauty and the Beast": "Beauty and the Beast",
    "Angela Lansbury, Jerry Orbach & The Chorus of Beauty and the Beast": "Beauty and the Beast",
    "The Chorus of Beauty and the Beast & Richard White": "Beauty and the Beast",
    "Hercules, Roz Ryan, Alan Menken, Cheryl Freeman, LaChanze, Vaneese Thomas & Lillias White": "Hercules",
    "Samuel E. Wright and The Lion King Ensemble": "The Lion King",
    "Lion King Soundtrack": "The Lion King",
    "Cast - Muppet Babies": "Muppet Babies",
    "The Muppets Barbershop Quartet": "The Muppets franchise",
    "Gonzo (Muppet)": "The Muppets franchise",
}


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(TOKEN_RE.findall(text))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def list_value(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value:
        return [part.strip() for part in str(value).split("|") if part.strip()]
    return []


def merge_unique(left: object, right: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in [*list_value(left), *list_value(right)]:
        key = normalize(item)
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", default="sample-data/trivia.jsonl")
    parser.add_argument("--wikidata", default="data/normalized/disney-wikidata.jsonl")
    parser.add_argument("--lyrics-glob", default="data/source/lyrics/*.parquet")
    parser.add_argument("--output", default="data/normalized/disney-music-enriched.jsonl")
    args = parser.parse_args()

    curated = [row for row in read_jsonl(ROOT / args.curated) if row.get("kind") == "song"]
    wikidata = [row for row in read_jsonl(ROOT / args.wikidata) if row.get("kind") == "song"]
    songs: dict[tuple[str, str], dict] = {}
    for row in [*wikidata, *curated]:
        key = (normalize(row.get("answer")), normalize(row.get("work")))
        if not all(key):
            continue
        if key not in songs:
            songs[key] = dict(row)
        else:
            existing = songs[key]
            prefer = row if row in curated else existing
            combined = dict(prefer)
            combined["aliases"] = merge_unique(existing.get("aliases"), row.get("aliases"))
            combined["tags"] = merge_unique(existing.get("tags"), row.get("tags"))
            combined["character_performers"] = merge_unique(existing.get("character_performers"), row.get("character_performers"))
            combined["recording_artists"] = merge_unique(existing.get("recording_artists"), row.get("recording_artists"))
            songs[key] = combined

    by_title: dict[str, list[tuple[tuple[str, str], dict]]] = {}
    for key, row in songs.items():
        by_title.setdefault(key[0], []).append((key, row))

    best: dict[tuple[str, str], tuple[float, str, str, int | None]] = {}
    parquet_files = sorted(glob.glob(str(ROOT / args.lyrics_glob)))
    for parquet_path in parquet_files:
        parquet = pq.ParquetFile(parquet_path)
        for batch in parquet.iter_batches(columns=["title", "artist", "year", "lyrics"], batch_size=25_000):
            for source in batch.to_pylist():
                title_key = normalize(source.get("title"))
                artist_key = normalize(source.get("artist"))
                source_year = int(source["year"]) if source.get("year") else None
                source_artist = str(source.get("artist") or "").strip()
                if source_artist in CAST_ARTIST_WORKS and title_key:
                    if source_artist == "Walt Disney Records" and title_key in by_title:
                        pass
                    else:
                        work = CAST_ARTIST_WORKS[source_artist]
                        cast_key = (title_key, normalize(work))
                        display_artists = [] if source_artist == "Walt Disney Records" else [source_artist]
                        display_year = None if source_artist == "Walt Disney Records" else source_year
                        if cast_key not in songs:
                            digest = hashlib.sha1(f"{title_key}|{artist_key}".encode()).hexdigest()[:16]
                            songs[cast_key] = {
                                "id": f"local-disney-song-{digest}",
                                "kind": "song",
                                "question": f"Which Disney-universe song associated with {work} is titled {source.get('title')}?",
                                "answer": str(source.get("title") or "").strip(),
                                "category": "Disney",
                                "subcategory": "Disney Music",
                                "difficulty": "hard",
                                "details": "Locally indexed Disney music record; missing credits remain intentionally blank until verified.",
                                "aliases": [f"{source.get('title')} {work}"],
                                "tags": ["song", "soundtrack", "cast recording"],
                                "source": "Private local lyric corpus",
                                "source_url": "",
                                "evergreen": True,
                                "priority": 64,
                                "work": work,
                                "character_performers": [],
                                "recording_artists": display_artists,
                                "year": display_year,
                                "lyrics": "",
                            }
                        else:
                            songs[cast_key]["recording_artists"] = merge_unique(songs[cast_key].get("recording_artists"), display_artists)
                        previous = best.get(cast_key)
                        if not previous or 100 > previous[0]:
                            best[cast_key] = (100, str(source.get("lyrics") or ""), source_artist, source_year)
                if title_key not in by_title:
                    continue
                for key, song in by_title[title_key]:
                    artists = [normalize(item) for item in list_value(song.get("recording_artists"))]
                    artist_match = max((1 if item == artist_key else 0.8 if item and (item in artist_key or artist_key in item) else 0 for item in artists), default=0)
                    expected_year = int(song["year"]) if song.get("year") else None
                    year_score = 0.0 if not expected_year or not source_year else max(0, 1 - abs(expected_year - source_year) / 20)
                    disney_hint = 1 if any(word in artist_key for word in ("disney", "soundtrack", "cast")) else 0
                    score = artist_match * 70 + year_score * 20 + disney_hint * 5
                    if artists and artist_match == 0 and disney_hint == 0:
                        continue
                    previous = best.get(key)
                    if not previous or score > previous[0]:
                        best[key] = (score, str(source.get("lyrics") or ""), str(source.get("artist") or ""), source_year)

    output_rows: list[dict] = []
    for key, row in sorted(songs.items(), key=lambda item: (item[0][1], item[0][0])):
        enriched = dict(row)
        if key in best and best[key][1].strip():
            enriched["lyrics"] = best[key][1]
            enriched["lyrics_source_artist"] = best[key][2]
            enriched["lyrics_source_year"] = best[key][3]
        output_rows.append(enriched)

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    matched = sum(bool(row.get("lyrics")) for row in output_rows)
    print(json.dumps({"songs": len(output_rows), "lyricsMatched": matched, "metadataOnly": len(output_rows) - matched, "parquetFiles": len(parquet_files), "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
