#!/usr/bin/env python3
"""Prepare a broad, searchable trivia corpus from a local TriviaQA parquet file."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PACKAGES = ROOT / "data" / "tools" / "python"
if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))

import pyarrow.parquet as pq  # type: ignore  # noqa: E402


TOKEN_RE = re.compile(r"[a-z0-9]+")
SPACE_RE = re.compile(r"\s+")
STALE_RE = re.compile(r"\b(current|currently|incumbent|latest|most recent|right now|this year|today|reigning)\b", re.I)
POOR_QUESTION_RE = re.compile(r"(?:https?://|\btrue or false\b|\(\+/-|\+/-|_{2,}|\?\s*\?)", re.I)

DISNEY_TERMS = (
    "disney", "pixar", "mickey mouse", "minnie mouse", "donald duck", "goofy", "snow white", "cinderella",
    "sleeping beauty", "little mermaid", "beauty and the beast", "lion king", "aladdin", "one hundred and one dalmati",
    "101 dalmati", "jungle book", "aristocats", "lady and the tramp", "fox and the hound", "the rescuers", "dumbo",
    "pinocchio", "pocahontas", "mulan", "lilo & stitch", "lilo and stitch", "wreck-it ralph", "wreck it ralph",
    "toy story", "finding nemo", "monsters inc", "monsters, inc", "the incredibles", "inside out", "ratatouille",
    "wall-e", "wall e", "a bug's life", "moana", "encanto", "zootopia",
    "winnie the pooh", "mary poppins", "duck tales", "ducktales", "haunted mansion", "epcot", "disneyland"
)

CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Disney", DISNEY_TERMS),
    ("Geography", ("capital of", "country", "continent", "river", "mountain", "island", "ocean", "sea ", "border", "city", "state of", "flag", "currency", "national park", "geograph")),
    ("Science and Nature", ("chemical", "element", "planet", "solar system", "species", "animal", "bird", "mammal", "fish", "reptile", "plant", "tree", "flower", "physics", "biology", "anatom", "disease", "medical", "scientist", "invented", "discovered", "temperature", "mineral")),
    ("History", ("king ", "queen ", "emperor", "war ", "battle", "century", "ancient", "empire", "dynasty", "revolution", "treaty", "histor", "president", "prime minister", "monarch", "founded", "independence")),
    ("Sports", ("football", "soccer", "baseball", "basketball", "hockey", "tennis", "golf", "olympic", "world cup", "super bowl", "sport", "athlete", "championship", "wimbledon", "cricket", "rugby", "boxing", "formula one")),
    ("Music", ("song", "album", "singer", "band ", "music", "composer", "opera", "guitar", "piano", "grammy", "billboard", "recorded", "lyrics", "orchestra")),
    ("Movies and TV", ("film", "movie", "actor", "actress", "television", "tv ", "sitcom", "series", "cinema", "director", "oscar", "academy award", "character played", "starred")),
    ("Literature and Language", ("novel", "book", "author", "wrote", "writer", "poem", "poet", "play by", "shakespeare", "fictional", "literary", "word ", "language", "means", "named after")),
    ("Food and Drink", ("food", "drink", "cocktail", "wine", "beer", "spirit", "ingredient", "dish", "cuisine", "cheese", "fruit", "vegetable", "cooking", "restaurant")),
    ("Nautical and Travel", ("ship", "boat", "nautical", "sail", "ocean liner", "cruise", "airline", "airport", "voyage", "aboard")),
    ("Arts and Culture", ("painting", "painter", "artist", "museum", "sculpt", "architecture", "dance", "ballet", "fashion", "mythology", "god ", "goddess")),
]


def normalize_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(TOKEN_RE.findall(value))


def clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\u0091", "'").replace("\u0092", "'").replace("\u0093", '"').replace("\u0094", '"')
    text = "".join(char if char.isprintable() else " " for char in text)
    return SPACE_RE.sub(" ", text).strip()


def clean_question(value: object) -> str:
    question = clean_text(value).rstrip(" /")
    if question and question[-1] not in "?!.":
        question += "?"
    return question


def category_for(question: str) -> str:
    lowered = question.casefold()
    for category, needles in CATEGORY_RULES:
        if any(needle in lowered for needle in needles):
            return category
    return "General Knowledge"


def quality_score(question: str, answer: str, aliases: list[str], source: str) -> float:
    score = 50.0
    q_length = len(question)
    a_length = len(answer)
    if 35 <= q_length <= 150:
        score += 12
    elif q_length > 220 or q_length < 15:
        score -= 25
    if a_length <= 45:
        score += 8
    elif a_length > 100:
        score -= 25
    if question.endswith("?"):
        score += 4
    if re.match(r"^(who|what|which|where|when|how|name|in what|in which)", question, re.I):
        score += 5
    if POOR_QUESTION_RE.search(question):
        score -= 20
    if STALE_RE.search(question):
        score -= 16
    if aliases:
        score += min(5, len(aliases))
    if "wikipedia" in source.casefold():
        score += 2
    return score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/source/triviaqa/train.parquet")
    parser.add_argument("--curated", default="sample-data/trivia.jsonl")
    parser.add_argument("--disney", default="data/normalized/disney-wikidata.jsonl")
    parser.add_argument("--disney-music", default="data/normalized/disney-music-enriched.jsonl")
    parser.add_argument("--output", default="data/normalized/goobs-trivia-v4.jsonl")
    parser.add_argument("--target", type=int, default=100_000)
    args = parser.parse_args()

    table = pq.read_table(ROOT / args.input, columns=["question", "question_id", "question_source", "answer"])
    candidates: list[tuple[float, dict]] = []
    seen_questions: set[str] = set()
    for source_row in table.to_pylist():
        question = clean_question(source_row["question"])
        answer_data = source_row["answer"] or {}
        answer = clean_text(answer_data.get("value"))
        key = normalize_key(question)
        if not question or not answer or not key or key in seen_questions:
            continue
        if len(question) > 320 or len(answer) > 160:
            continue
        seen_questions.add(key)
        aliases = []
        answer_key = normalize_key(answer)
        for alias in answer_data.get("aliases") or []:
            cleaned = clean_text(alias)
            if cleaned and normalize_key(cleaned) != answer_key and cleaned not in aliases and len(cleaned) <= 100:
                aliases.append(cleaned)
            if len(aliases) == 8:
                break
        category = category_for(question)
        source = clean_text(source_row.get("question_source"))
        score = quality_score(question, answer, aliases, source)
        stale = bool(STALE_RE.search(question))
        source_name = urlparse(source).netloc.removeprefix("www.") if source.startswith("http") else source
        row = {
            "id": f"triviaqa-{source_row['question_id']}",
            "question": question,
            "answer": answer,
            "category": category,
            "subcategory": "TriviaQA",
            "difficulty": "medium",
            "details": "",
            "aliases": aliases,
            "tags": [],
            "source": source_name or "TriviaQA",
            "source_url": source if source.startswith("http") else "",
            "evergreen": not stale,
            "priority": max(15, min(90, round(score))),
        }
        candidates.append((score + (8 if category == "Disney" else 0), row))

    candidates.sort(key=lambda item: (-item[0], item[1]["id"]))
    curated_rows = [json.loads(line) for line in (ROOT / args.curated).read_text(encoding="utf-8").splitlines() if line.strip()]
    disney_path = ROOT / args.disney
    disney_rows = [json.loads(line) for line in disney_path.read_text(encoding="utf-8").splitlines() if line.strip()] if disney_path.exists() else []
    music_path = ROOT / args.disney_music
    music_rows = [json.loads(line) for line in music_path.read_text(encoding="utf-8").splitlines() if line.strip()] if music_path.exists() else []
    if music_rows:
        curated_rows = [row for row in curated_rows if row.get("kind") != "song"]
        disney_rows = [row for row in disney_rows if row.get("kind") != "song"]
    protected = curated_rows + disney_rows + music_rows
    selected = protected + [row for _, row in candidates[: max(0, args.target - len(protected))]]
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    categories: dict[str, int] = {}
    for row in selected:
        categories[row["category"]] = categories.get(row["category"], 0) + 1
    print(json.dumps({"inputRows": table.num_rows, "candidates": len(candidates), "curated": len(curated_rows), "wikidataDisney": len(disney_rows), "disneyMusic": len(music_rows), "selected": len(selected), "categories": dict(sorted(categories.items(), key=lambda item: -item[1]))}, indent=2))


if __name__ == "__main__":
    main()
