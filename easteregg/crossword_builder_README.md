# Crossword Builder

A small Python tool that generates kid-friendly themed crosswords from the
tagged `vocabulary.json` dictionary.

## Requirements

- Python 3.8+ (no third-party packages needed for JSON output)
- Optional, only for `--pdf`: `pip install reportlab`

Put `crossword_builder.py` and `vocabulary.json` in the same folder.

## Quick start

```bash
# See which themes your dictionary supports
python crossword_builder.py --list-themes

# Build a 9x9 space puzzle (prints grid + clues, writes space_9x9.json)
python crossword_builder.py --theme space

# Body puzzle, slightly more black squares, also make a printable PDF
python crossword_builder.py --theme body --black 0.28 --pdf

# Easiest words only (good for youngest solvers)
python crossword_builder.py --theme food --max-tier 1

# Reproduce the exact same puzzle later
python crossword_builder.py --theme animals --seed 12345
```

## Options

| Flag            | Meaning                                                            | Default          |
|-----------------|--------------------------------------------------------------------|------------------|
| `--vocab PATH`  | Path to the dictionary file                                        | `vocabulary.json`|
| `--theme NAME`  | Theme tag to feature (omit for a no-theme puzzle)                  | none             |
| `--list-themes` | Print available themes with word counts, then exit                 | —                |
| `--size N`      | Grid is N x N                                                      | `9`              |
| `--max-tier T`  | Hardest word difficulty to allow: 1 easiest … 3 hardest            | `2`              |
| `--black F`     | Target fraction of black squares (e.g. `0.22`, `0.30`)             | `0.26`           |
| `--nseed K`     | Force K long slots to be theme words (more = stronger theme)       | `2`              |
| `--min-theme M` | Stop early once M theme words are placed                           | `6`              |
| `--time S`      | Time budget in seconds                                             | `120`            |
| `--seed N`      | Random seed for reproducible output                                | random           |
| `--title STR`   | Puzzle title                                                       | "<Theme> Crossword" |
| `--emoji STR`   | Emoji shown in title/PDF                                           | 🧩               |
| `--out PATH`    | Output JSON path                                                   | `<theme>_<size>x<size>.json` |
| `--pdf`         | Also write a printable PDF (puzzle + answer key)                   | off              |
| `--no-theme-flag` | Omit the per-word `"theme": true/false` field in the JSON        | off              |

## Tips on getting a good grid

- **More black is *easier* to fill, not harder.** If a build fails or has few
  theme words, raise `--black` to `0.28`–`0.32`. Fewer black squares forces long
  words to cross each other, which the solver struggles with.
- **Raise `--max-tier`** to 2 or 3 to give the filler more words to work with.
- **Smaller grids** (`--size 7`) fill faster and look denser.
- The dictionary is the real lever: add words (with `tier`, `themes`, `clue`)
  to `vocabulary.json` and every puzzle gets easier to build and richer.
- Each run is randomized; re-run a few times (or sweep `--seed`) and keep the
  one you like best. The same `--seed` always reproduces the same puzzle.

## Output format

The JSON matches the schema used across the project:

```json
{
  "id": "space-9x9",
  "title": "Space Crossword",
  "emoji": "🔭",
  "difficulty": "medium",
  "grid": [ ["C","R","Y","#",...], ... ],
  "words": [
    { "id": "cloud", "word": "CLOUD", "clue": "...", "row": 0, "col": 0,
      "direction": "down", "number": 1, "theme": true },
    ...
  ]
}
```

`#` marks a black square. Each word lists its starting `row`/`col` (0-indexed),
`direction`, clue `number`, and whether it is a `theme` word.

## How it works (short version)

1. Build a word pool from the dictionary, filtered by `--theme` and `--max-tier`.
2. Generate many 180°-symmetric black-square masks at the target density,
   rejecting any with white runs shorter than 3 or disconnected regions.
3. Fill each mask with a most-constrained-variable backtracking search,
   forcing a few long slots to take theme words and preferring theme words
   elsewhere.
4. Keep the filled grid with the most theme words; validate that every run is a
   real dictionary word; number it; export JSON (and optionally PDF).

## Known limitations

- Best results are at 7×7 to 10×10. Larger grids (11×11+) are hard for this
  backtracking solver and may time out or come back theme-light.
- There is a real floor around ~20% black: below that, grids become very hard
  to fill with a kid vocabulary.
- A theme puzzle typically lands 5–10 theme words; the rest is general fill.
  Adding more words to the dictionary in that theme raises this.


## Automatic sanity checks (added)

Every puzzle is automatically verified before it is written — you do not need
to check anything by hand. The checker confirms:

- every across/down run is a real dictionary word (catches "phantom" words);
- no white cell is isolated;
- the word list exactly matches the grid in both directions;
- clue numbering is correct;
- every clue is present and non-blank;
- all white squares are connected;
- no word runs off-grid or hits a black square;
- duplicate answers are flagged.

A clean run exits with status **0**. If a hard check fails, nothing is written
and it exits **2**. If no puzzle could be built at all, it exits **1**. This
lets you use it safely in scripts.
