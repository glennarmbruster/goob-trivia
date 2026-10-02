# Goobs Trivia Finder

Goobs Trivia Finder is a standalone, offline-first PWA for finding likely trivia answers from partial remembered clues. It is separate from Goobs Song Finder and Goobs Games.

The app searches an imported SQLite database entirely on the device. No account, analytics, backend, or Internet connection is used for normal searches.

## Current milestone

The first working milestone includes:

- Installable iPhone and Android PWA shell
- Private SQLite database import into browser OPFS storage
- Fast full-text candidate retrieval and fuzzy reranking
- Misspelling tolerance and partial-clue matching
- Answer, question, category, subcategory, difficulty, details, and match score
- Offline readiness and database integrity test
- A verified 81-fact starter pack, including 66 Deep Disney facts
- A reusable database builder for much larger private corpora

The starter pack is a functional test collection, not the intended production database.

The current v4 production database contains 85,852 searchable records across 12 categories, including 9,948 Disney-universe records and 667 structured Disney Music records. Of those songs, 648 have private lyric indexing and 19 are metadata-only. The database is approximately 90 MB.

## Data layout

Keep code and data separate:

```text
goobs-trivia-finder/
  src/                 application source
  tools/               database builder and verifier
  sample-data/         small committable demonstration corpus
  public/              small demonstration database and icons
  data/source/         local-only imported source material
  data/normalized/     local-only normalized trivia records
  data/generated/      local-only production SQLite databases
  tmp/                 local-only processing files
```

The `.gitignore` excludes `data/`, imported corpora, generated production databases, indexes, secrets, and temporary files. Only the small starter database in `public/` is intentionally committed.

## Record format

The builder accepts JSONL or CSV. Each record has:

- `id` — stable unique identifier
- `question` — canonical question wording
- `answer` — canonical answer
- `category` — broad category such as Disney or Geography
- `subcategory` — film, franchise, era, or topic
- `difficulty` — easy, medium, hard, or expert
- `details` — concise supporting explanation
- `aliases` — alternate phrasings, visual descriptions, misspellings, and clue fragments
- `tags` — searchable entities and concepts
- `source` and `source_url` — provenance fields
- `evergreen` — false for facts requiring periodic review
- `priority` — 0–100 importance weight

Disney Music records additionally support:

- `kind: "song"`
- `work` — originating movie, show, special, attraction, or game
- `character_performers` — the characters singing in the story
- `recording_artists` — the real vocalists or recording artists
- `year`
- `lyrics` — local search-only lyric text; the app does not display it

Visual clues such as “girl in the green hoodie” belong in `aliases`. This is especially important for Deep Disney because generic fact datasets rarely describe costumes, colors, props, minor animals, or brief character appearances.

## Build and verify

```sh
pnpm install
pnpm build:sample-db
pnpm verify:sample-db
pnpm build
```

For a production corpus, supply a local JSONL file to `tools/build_database.py`. Keep the input and generated SQLite file under `data/` so they cannot be committed accidentally.

## Search architecture

1. Normalize the clue for case, punctuation, accents, and contractions.
2. Remove low-value question words for candidate retrieval.
3. Correct likely spelling errors using the local lexicon, edit distance, and phonetic matches.
4. Retrieve a bounded candidate set from a SQLite FTS5 prefix index.
5. Rerank candidates using token coverage, phrase match, word order, proximity, answer match, and editorial priority.
6. Return the ten strongest answers.

The database contains the prebuilt FTS and spelling lexicon, so a phone does not build an index during installation.

## Production corpus target

Recommended first production target:

- 50,000–75,000 general cruise/pub trivia facts
- 10,000–20,000 Deep Disney facts
- 5,000–10,000 aliases for visual clues, alternate names, and common question phrasings

Disney is a first-class collection covering Disney animation, live action, Pixar, Marvel, Star Wars, the Muppets, Disney Channel, parks, resorts, attractions, cruise ships, music, characters, animals/species, villains, sidekicks, voice actors, settings, objects, plot details, and company history. Generated facts should be reviewed against reliable sources before being marked production-ready.

Disney Music uses the same lyric-fragment search principles as Goobs Song Finder, while remaining a separate app and database. Results show the song, originating work, character singer, real performer, year, and match score. Full lyrics are indexed locally for identification and are not shown in the interface.

`tools/prepare_disney_wikidata.py` generates structured Disney, Pixar, Marvel, Star Wars, Muppets, and Disney television relationships. It uses cached responses and deliberately paces requests to respect Wikidata Query Service limits. Run it separately with `pnpm prepare:disney`; a normal production build does not depend on that external service being available.

`tools/enrich_disney_music.py` merges structured song metadata with private local lyric files. It preserves missing credits as blank instead of guessing and produces a local-only search corpus. Full lyrics and generated production databases remain excluded from Git.

## Deployment

This project should use its own repository and GitHub Pages path. Do not place it inside Goobs Games or overwrite the existing Song Finder deployment.

The production trivia database should be transferred privately and imported on the phone. It should not be committed to GitHub unless its contents and size are intentionally approved for that purpose.
