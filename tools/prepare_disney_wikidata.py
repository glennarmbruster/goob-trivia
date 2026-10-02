#!/usr/bin/env python3
"""Build a structured Deep Disney fact pack from Wikidata's query service."""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "GoobsTriviaFinder/0.1 (private personal offline trivia project)"
COMPANIES = "wd:Q1047410 wd:Q127552 wd:Q191224"
ALL_DISNEY_COMPANIES = COMPANIES + " wd:Q367466 wd:Q242446 wd:Q3327964 wd:Q1392453 wd:Q3565642"
EXPANDED_COMPANIES = "wd:Q367466 wd:Q242446 wd:Q3327964 wd:Q1392453 wd:Q3565642"
MINIMUM_REQUEST_INTERVAL = 65.0
last_request_at = 0.0


QUERIES = {
    "works": f"""
      SELECT DISTINCT ?work ?workLabel ?workDescription ?date WHERE {{
        VALUES ?company {{ {COMPANIES} }}
        ?work wdt:P272 ?company.
        OPTIONAL {{ ?work wdt:P577 ?date. }}
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "characters": f"""
      SELECT DISTINCT ?character ?characterLabel ?characterDescription ?work ?workLabel WHERE {{
        VALUES ?company {{ {COMPANIES} }}
        ?work wdt:P272 ?company.
        ?character wdt:P1441 ?work.
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "voices": f"""
      SELECT DISTINCT ?character ?characterLabel ?actor ?actorLabel WHERE {{
        VALUES ?company {{ {COMPANIES} }}
        ?work wdt:P272 ?company.
        ?character wdt:P1441 ?work; wdt:P725 ?actor.
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "songs": f"""
      SELECT DISTINCT ?song ?songLabel ?songDescription ?work ?workLabel ?performer ?performerLabel ?character ?characterLabel ?date WHERE {{
        VALUES ?company {{ {ALL_DISNEY_COMPANIES} }}
        ?work wdt:P272 ?company.
        ?song wdt:P9899 ?work.
        OPTIONAL {{
          ?song p:P175 ?performance.
          ?performance ps:P175 ?performer.
          OPTIONAL {{ ?performance pq:P453 ?character. }}
        }}
        OPTIONAL {{ ?song wdt:P577 ?date. }}
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "universe_works": f"""
      SELECT DISTINCT ?work ?workLabel ?workDescription ?date WHERE {{
        VALUES ?company {{ {EXPANDED_COMPANIES} }}
        ?work wdt:P272 ?company.
        OPTIONAL {{ ?work wdt:P577 ?date. }}
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "universe_entities": f"""
      SELECT DISTINCT ?character ?characterLabel ?characterDescription ?work ?workLabel WHERE {{
        VALUES ?company {{ {EXPANDED_COMPANIES} }}
        ?work wdt:P272 ?company.
        ?character wdt:P1441 ?work.
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "universe_voices": f"""
      SELECT DISTINCT ?character ?characterLabel ?actor ?actorLabel WHERE {{
        VALUES ?company {{ {EXPANDED_COMPANIES} }}
        ?work wdt:P272 ?company.
        ?character wdt:P1441 ?work; wdt:P725 ?actor.
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "directors": f"""
      SELECT DISTINCT ?work ?workLabel ?person ?personLabel WHERE {{
        VALUES ?company {{ {COMPANIES} }}
        ?work wdt:P272 ?company; wdt:P57 ?person.
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "composers": f"""
      SELECT DISTINCT ?work ?workLabel ?person ?personLabel WHERE {{
        VALUES ?company {{ {COMPANIES} }}
        ?work wdt:P272 ?company; wdt:P86 ?person.
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
    "based_on": f"""
      SELECT DISTINCT ?work ?workLabel ?source ?sourceLabel WHERE {{
        VALUES ?company {{ {COMPANIES} }}
        ?work wdt:P272 ?company; wdt:P144 ?source.
        SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
      }}
    """,
}


def entity_id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def value(binding: dict, key: str) -> str:
    return binding.get(key, {}).get("value", "").strip()


def valid_label(label: str, uri: str) -> bool:
    return bool(label and label != entity_id(uri) and not label.startswith("http"))


CHARACTER_HINTS = re.compile(
    r"\b(character|protagonist|antagonist|villain|superhero|princess|prince|pirate|jedi|droid|robot|person|human|"
    r"dog|cat|mouse|bear|duck|rabbit|fish|bird|dragon|monster|witch|wizard|fairy|mermaid|lion|horse|deer|"
    r"elephant|puppet|muppet|vampire|alien|extraterrestrial|animal|creature|god|goddess|deity)\b",
    re.I,
)
NON_CHARACTER_HINTS = re.compile(
    r"\b(fictional (location|planet|dimension|organization|organisation|object|vehicle|weapon|species|city|country|"
    r"kingdom|universe)|geographic region|paramilitary force|musical composition|soundtrack album)\b",
    re.I,
)


def character_like(description: str) -> bool:
    return bool(description and CHARACTER_HINTS.search(description) and not NON_CHARACTER_HINTS.search(description))


def useful_description(description: str, character: str, work: str) -> bool:
    if not description:
        return False
    words = re.findall(r"[a-z0-9]+", description.casefold())
    ignored = {
        "a", "an", "the", "and", "of", "from", "in", "for", "by", "fictional", "character", "characters",
        "disney", "pixar", "animated", "animation", "film", "movie", "series", "franchise",
        *re.findall(r"[a-z0-9]+", character.casefold()),
        *re.findall(r"[a-z0-9]+", work.casefold()),
    }
    distinctive = [word for word in words if word not in ignored]
    return len(set(distinctive)) >= 2


def fetch(name: str, query: str, cache_dir: Path, refresh: bool) -> list[dict]:
    global last_request_at
    cache_path = cache_dir / f"{name}.json"
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    url = ENDPOINT + "?format=json&query=" + urllib.parse.quote(query)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    error: Exception | None = None
    for attempt in range(4):
        try:
            remaining = MINIMUM_REQUEST_INTERVAL - (time.monotonic() - last_request_at)
            while remaining > 0:
                time.sleep(min(55, remaining))
                remaining = MINIMUM_REQUEST_INTERVAL - (time.monotonic() - last_request_at)
            last_request_at = time.monotonic()
            with urllib.request.urlopen(request, timeout=120) as response:
                rows = json.load(response)["results"]["bindings"]
            cache_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            print(f"Cached Wikidata query {name}: {len(rows)} rows", flush=True)
            return rows
        except urllib.error.HTTPError as exc:
            error = exc
            if exc.code == 429:
                time.sleep(55)
                continue
            time.sleep(2 ** attempt)
        except Exception as exc:  # pragma: no cover - network retry
            error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Wikidata query {name!r} failed") from error


def record(fact_id: str, question: str, answer: str, subcategory: str, difficulty: str, details: str, aliases: list[str], tags: list[str], priority: int, source_ids: list[str]) -> dict:
    urls = [f"https://www.wikidata.org/wiki/{item}" for item in source_ids]
    return {
        "id": fact_id,
        "question": question,
        "answer": answer,
        "category": "Disney",
        "subcategory": subcategory,
        "difficulty": difficulty,
        "details": details,
        "aliases": aliases,
        "tags": tags,
        "source": "Wikidata",
        "source_url": urls[0],
        "evergreen": True,
        "priority": priority,
    }


def grouped_people(rows: list[dict], item_key: str, label_key: str, person_key: str, person_label_key: str) -> dict[tuple[str, str], list[tuple[str, str]]]:
    grouped: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        item_uri, item_label = value(row, item_key), value(row, label_key)
        person_uri, person_label = value(row, person_key), value(row, person_label_key)
        if not valid_label(item_label, item_uri) or not valid_label(person_label, person_uri):
            continue
        pair = (entity_id(person_uri), person_label)
        if pair not in grouped[(entity_id(item_uri), item_label)]:
            grouped[(entity_id(item_uri), item_label)].append(pair)
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/normalized/disney-wikidata.jsonl")
    parser.add_argument("--cache", default="data/source/wikidata")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--allow-partial", action="store_true", help="Build from completed cache files without requesting missing datasets")
    parser.add_argument("--fetch", action="append", choices=tuple(QUERIES), help="Request only this dataset; may be repeated")
    args = parser.parse_args()
    cache_dir = ROOT / args.cache
    cache_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, list[dict]] = {}
    for name, query in QUERIES.items():
        cache_path = cache_dir / f"{name}.json"
        if args.fetch and name not in args.fetch:
            data[name] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else []
        elif args.allow_partial and not cache_path.exists():
            data[name] = []
        else:
            data[name] = fetch(name, query, cache_dir, args.refresh)
    facts: list[dict] = []
    seen_ids: set[str] = set()

    def add(row: dict) -> None:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            facts.append(row)

    first_dates: dict[tuple[str, str], str] = {}
    descriptions: dict[tuple[str, str], str] = {}
    for row in [*data["works"], *data["universe_works"]]:
        uri, label = value(row, "work"), value(row, "workLabel")
        if not valid_label(label, uri):
            continue
        key = (entity_id(uri), label)
        description = value(row, "workDescription")
        if description:
            descriptions[key] = description
        date = value(row, "date")
        if date and (key not in first_dates or date < first_dates[key]):
            first_dates[key] = date
    for (work_id, work_label), date in first_dates.items():
        year = date[:4]
        if year.isdigit():
            add(record(f"wd-release-{work_id}", f"In what year was the Disney or Pixar production {work_label} first released?", year, work_label, "medium", descriptions.get((work_id, work_label), ""), [f"{work_label} release year", f"when did {work_label} come out"], ["release date", "year"], 72, [work_id]))

    for row in [*data["characters"], *data["universe_entities"]]:
        char_uri, char_label = value(row, "character"), value(row, "characterLabel")
        work_uri, work_label = value(row, "work"), value(row, "workLabel")
        if not valid_label(char_label, char_uri) or not valid_label(work_label, work_uri):
            continue
        char_id, work_id = entity_id(char_uri), entity_id(work_uri)
        description = value(row, "characterDescription")
        add(record(
            f"wd-character-work-{char_id}-{work_id}",
            f"Which Disney or Pixar production features {char_label}?",
            work_label,
            work_label,
            "hard",
            description,
            [f"{char_label} Disney character", f"{work_label} character {char_label}", description],
            ["person character place or thing", "appearance"],
            68,
            [char_id, work_id],
        ))
        if useful_description(description, char_label, work_label) and len(description) <= 180 and char_label.casefold() not in description.casefold():
            add(record(
                f"wd-character-description-{char_id}-{work_id}",
                f"What person, character, place, or thing from {work_label} is described as {description}?",
                char_label,
                work_label,
                "expert",
                f"The character is associated with {work_label}.",
                [description, f"{work_label} {description}"],
                ["person character place or thing", "description", "deep trivia"],
                66,
                [char_id, work_id],
            ))

    for (char_id, char_label), people in grouped_people([*data["voices"], *data["universe_voices"]], "character", "characterLabel", "actor", "actorLabel").items():
        names = [label for _, label in people]
        answer = ", ".join(names[:4])
        add(record(f"wd-voice-{char_id}", f"Which performer is credited by Wikidata as a voice of the Disney or Pixar character {char_label}?", answer, "Voice Cast", "hard", "A character may have different performers across productions or languages.", [f"voice of {char_label}", *names], ["voice actor", "character"], 63, [char_id, *[item for item, _ in people[:4]]]))

    songs: dict[tuple[str, str, str, str], dict] = {}
    for row in data["songs"]:
        song_uri, song_label = value(row, "song"), value(row, "songLabel")
        work_uri, work_label = value(row, "work"), value(row, "workLabel")
        if not valid_label(song_label, song_uri) or not valid_label(work_label, work_uri):
            continue
        description = value(row, "songDescription")
        if song_label.casefold() == work_label.casefold() or song_label.casefold().startswith("music of the "):
            continue
        if re.search(r"\b(soundtrack album|film score|television score)\b", description, re.I):
            continue
        key = (entity_id(song_uri), song_label, entity_id(work_uri), work_label)
        entry = songs.setdefault(key, {"description": description, "performers": [], "characters": [], "dates": []})
        performer_uri, performer_label = value(row, "performer"), value(row, "performerLabel")
        character_uri, character_label = value(row, "character"), value(row, "characterLabel")
        if performer_uri and valid_label(performer_label, performer_uri) and performer_label not in entry["performers"]:
            entry["performers"].append(performer_label)
        if character_uri and valid_label(character_label, character_uri) and character_label not in entry["characters"]:
            entry["characters"].append(character_label)
        if value(row, "date"):
            entry["dates"].append(value(row, "date"))
    for (song_id, song_label, work_id, work_label), entry in songs.items():
        dates = sorted(entry["dates"])
        year = int(dates[0][:4]) if dates and dates[0][:4].isdigit() else None
        song_row = record(
            f"wd-song-{song_id}-{work_id}",
            f"Which Disney-universe song from {work_label} is titled {song_label}?",
            song_label,
            "Disney Music",
            "hard",
            entry["description"],
            [f"{song_label} from {work_label}", *entry["performers"], *entry["characters"]],
            ["song", "soundtrack", "Disney music"],
            75,
            [song_id, work_id],
        )
        song_row.update({
            "kind": "song",
            "work": work_label,
            "character_performers": entry["characters"],
            "recording_artists": entry["performers"],
            "year": year,
            "lyrics": "",
        })
        add(song_row)

    relation_specs = [
        ("directors", "director", "Who directed {work}?", "director", 72),
        ("composers", "composer", "Who composed the music for {work}?", "composer", 70),
    ]
    for dataset, relation, question_template, tag, priority in relation_specs:
        for (work_id, work_label), people in grouped_people(data[dataset], "work", "workLabel", "person", "personLabel").items():
            names = [label for _, label in people]
            add(record(f"wd-{relation}-{work_id}", question_template.format(work=work_label), ", ".join(names), work_label, "hard", "", [f"{work_label} {relation}", *names], [tag, "production"], priority, [work_id, *[item for item, _ in people]]))

    based_on: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for row in data["based_on"]:
        work_uri, work_label = value(row, "work"), value(row, "workLabel")
        source_uri, source_label = value(row, "source"), value(row, "sourceLabel")
        if valid_label(work_label, work_uri) and valid_label(source_label, source_uri):
            pair = (entity_id(source_uri), source_label)
            if pair not in based_on[(entity_id(work_uri), work_label)]:
                based_on[(entity_id(work_uri), work_label)].append(pair)
    for (work_id, work_label), sources in based_on.items():
        labels = [label for _, label in sources]
        add(record(f"wd-based-on-{work_id}", f"On what earlier work or source is the Disney production {work_label} based?", ", ".join(labels), work_label, "hard", "", [f"inspiration for {work_label}", *labels], ["source material", "adaptation"], 69, [work_id, *[item for item, _ in sources]]))

    facts.sort(key=lambda row: row["id"])
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in facts:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"facts": len(facts), "sourceRows": {name: len(rows) for name, rows in data.items()}, "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
