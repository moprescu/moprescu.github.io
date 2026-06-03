#!/usr/bin/env python3
"""
Crossword Builder
=================
Generates kid-friendly themed crossword puzzles from a tagged vocabulary file.

Usage examples
--------------
  # List the themes available in your vocabulary file
  python crossword_builder.py --list-themes

  # Build a 9x9 "space" puzzle, default settings
  python crossword_builder.py --theme space

  # Bigger grid, more black squares allowed, save a PDF too
  python crossword_builder.py --theme body --size 9 --black 0.27 --pdf

  # Restrict to easiest words (tier 1 only), write JSON to a file
  python crossword_builder.py --theme food --max-tier 1 --out food_puzzle.json

  # Reproduce an exact puzzle later with the same seed
  python crossword_builder.py --theme animals --seed 12345

Output
------
  - Prints the grid + clue list to the terminal.
  - Writes a JSON file (compact, in the schema used throughout the project).
  - Optionally writes a printable PDF (needs: pip install reportlab).

The vocabulary file is expected to be the project's vocabulary.json:
  { "meta": {...},
    "words": [ {"word","length","tier","themes":[...],"clue"}, ... ] }

Author: built collaboratively; MIT-style, do whatever you like with it.
"""

import argparse
import json
import random
import sys
import time
from collections import defaultdict, deque


# --------------------------------------------------------------------------
# Vocabulary loading
# --------------------------------------------------------------------------
def load_vocab(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    words = data["words"] if isinstance(data, dict) and "words" in data else data
    clue = {}
    by_word = {}
    for w in words:
        WORD = w["word"].upper()
        clue[WORD] = w.get("clue", WORD)
        by_word[WORD] = {
            "tier": w.get("tier", 2),
            "themes": set(w.get("themes", [])),
        }
    return by_word, clue


def all_themes(by_word):
    c = defaultdict(int)
    for info in by_word.values():
        for t in info["themes"]:
            c[t] += 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def build_pools(by_word, theme, max_tier, min_len=3, max_len=11):
    """Return (theme_words, fill_words) sets filtered by tier and length."""
    theme_words, fill_words = set(), set()
    for w, info in by_word.items():
        if not (min_len <= len(w) <= max_len):
            continue
        if info["tier"] > max_tier:
            continue
        fill_words.add(w)
        if theme is None or theme in info["themes"]:
            theme_words.add(w)
    return theme_words, fill_words


# --------------------------------------------------------------------------
# Grid geometry helpers
# --------------------------------------------------------------------------
def slots_of(mask, N):
    """Return list of (dir, row, col, length) for every run of length >= 2."""
    slots = []
    for r in range(N):
        c = 0
        while c < N:
            if mask[r][c] == ".":
                s = c
                while c < N and mask[r][c] == ".":
                    c += 1
                if c - s >= 2:
                    slots.append(("A", r, s, c - s))
            else:
                c += 1
    for c in range(N):
        r = 0
        while r < N:
            if mask[r][c] == ".":
                s = r
                while r < N and mask[r][c] == ".":
                    r += 1
                if r - s >= 2:
                    slots.append(("D", s, c, r - s))
            else:
                r += 1
    return slots


def cells(slot):
    d, r, c, L = slot
    return [(r, c + i) if d == "A" else (r + i, c) for i in range(L)]


def connected(mask, N):
    whites = [(r, c) for r in range(N) for c in range(N) if mask[r][c] == "."]
    if not whites:
        return False
    seen = {whites[0]}
    dq = deque([whites[0]])
    while dq:
        r, c = dq.popleft()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < N and 0 <= nc < N and mask[nr][nc] == "." and (nr, nc) not in seen:
                seen.add((nr, nc))
                dq.append((nr, nc))
    return len(seen) == len(whites)


def gen_symmetric_mask(N, seed, black_frac):
    """Generate a 180-degree symmetric mask with no run shorter than 3 white cells."""
    rng = random.Random(seed)
    g = [["."] * N for _ in range(N)]
    coords = [(r, c) for r in range(N) for c in range(N) if (r, c) <= (N - 1 - r, N - 1 - c)]
    rng.shuffle(coords)
    target = int(N * N * black_frac)
    placed = 0
    for (r, c) in coords:
        if placed >= target:
            break
        rr, cc = N - 1 - r, N - 1 - c
        if g[r][c] == "#":
            continue
        g[r][c] = "#"
        g[rr][cc] = "#"
        m = ["".join(row) for row in g]
        # every white run must be length >= 3, and grid stays connected
        if all(s[3] >= 3 for s in slots_of(m, N)) and connected(m, N):
            placed += 1 if (r, c) == (rr, cc) else 2
        else:
            g[r][c] = "."
            g[rr][cc] = "."
    return ["".join(row) for row in g]


# --------------------------------------------------------------------------
# The fill engine: most-constrained-variable backtracking
# --------------------------------------------------------------------------
def make_index(words):
    by_len = defaultdict(list)
    for w in words:
        by_len[len(w)].append(w)
    idx = {}
    for L, ws in by_len.items():
        d = defaultdict(set)
        for w in ws:
            for i, ch in enumerate(w):
                d[(i, ch)].add(w)
        idx[L] = d
    return by_len, idx


def fill_grid(mask, N, words, theme_words, strong_words, idx, by_len,
              tries, seed, nseed):
    """
    Backtracking fill.  `nseed` long slots are forced to take a 'strong'
    theme word, the rest are filled freely (theme words preferred when
    they fit).  Returns a grid (list of rows) or None / "timeout".
    """
    random.seed(seed)
    slots = slots_of(mask, N)
    grid = {}
    used = set()
    cnt = [0]

    # the nseed longest 4-7 letter slots are 'forced' to be strong theme words
    forceable = sorted([s for s in slots if 4 <= s[3] <= 7], key=lambda s: -s[3])
    forced = set(id(s) for s in forceable[:nseed])

    def candidates(slot, pool=None):
        d, r, c, L = slot
        res = None
        for i, cell in enumerate(cells(slot)):
            if cell in grid:
                s = idx.get(L, {}).get((i, grid[cell]), set())
                res = s if res is None else (res & s)
        base = by_len.get(L, []) if res is None else list(res)
        if pool is not None:
            return [w for w in base if w in pool]
        return base

    def bt(remaining):
        if not remaining:
            return True
        cnt[0] += 1
        if cnt[0] > tries:
            raise TimeoutError
        # forced (theme) slots first, else most-constrained slot
        frem = [s for s in remaining if id(s) in forced]
        if frem:
            best = min(frem, key=lambda s: len(candidates(s, strong_words)))
            cl = candidates(best, strong_words)
            is_forced = True
        else:
            best = min(remaining, key=lambda s: len(candidates(s)))
            cl = candidates(best)
            is_forced = False
        rng = random.Random(cnt[0] * 13 + seed)
        if is_forced:
            order = cl[:]
            rng.shuffle(order)
        else:
            themed = [w for w in cl if w in theme_words]
            rest = [w for w in cl if w not in theme_words]
            rng.shuffle(themed)
            rng.shuffle(rest)
            order = themed + rest
        rem2 = [s for s in remaining if s != best]
        for w in order:
            if w in used:
                continue
            placed = []
            ok = True
            for cell, ch in zip(cells(best), w):
                if cell in grid:
                    if grid[cell] != ch:
                        ok = False
                        break
                else:
                    grid[cell] = ch
                    placed.append(cell)
            if ok:
                used.add(w)
                if bt(rem2):
                    return True
                used.discard(w)
            for cell in placed:
                del grid[cell]
        return False

    try:
        if bt(slots):
            return [["#" if mask[r][c] == "#" else grid[(r, c)]
                     for c in range(N)] for r in range(N)]
    except TimeoutError:
        return "timeout"
    return None


# --------------------------------------------------------------------------
# Validation + numbering + export
# --------------------------------------------------------------------------
def grid_runs(grid, N):
    """Return list of (row, col, dir, word) for every across/down run >= 2."""
    cell = {(r, c): grid[r][c] for r in range(N) for c in range(N) if grid[r][c] != "#"}
    runs = []
    for r in range(N):
        c = 0
        while c < N:
            if (r, c) in cell:
                s = c
                while c < N and (r, c) in cell:
                    c += 1
                if c - s >= 2:
                    runs.append((r, s, "A", "".join(cell[(r, k)] for k in range(s, c))))
            else:
                c += 1
    for c in range(N):
        r = 0
        while r < N:
            if (r, c) in cell:
                s = r
                while r < N and (r, c) in cell:
                    r += 1
                if r - s >= 2:
                    runs.append((s, c, "D", "".join(cell[(k, c)] for k in range(s, r))))
            else:
                r += 1
    return runs


def validate(grid, N, valid_words):
    """Every maximal run must be a real word. Returns list of bad words ([] = clean)."""
    return [w for (_, _, _, w) in grid_runs(grid, N) if w not in valid_words]


# ==========================================================================
# Sanity checks  -- run automatically on every generated puzzle so you do
# not have to eyeball anything.  Returns (ok: bool, report: list[str]).
# ==========================================================================
def sanity_check(pz, by_word, clue, N):
    """
    Exhaustive automatic verification of a finished puzzle dict.
    Catches: phantom words, isolated cells, off-dictionary entries,
    numbering errors, geometry/coordinate mismatches, duplicate words,
    missing/blank clues, two-letter words, disconnected fill, and
    grid/word-list disagreement.  Nothing here needs human judgement.
    """
    report = []
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        report.append("FAIL: " + msg)

    def warn(msg):
        report.append("WARN: " + msg)

    grid = pz["grid"]
    words = pz["words"]
    valid = set(by_word.keys())

    # ---- 0. grid shape -------------------------------------------------
    if len(grid) != N or any(len(row) != N for row in grid):
        fail(f"grid is not {N}x{N}")
        return ok, report  # everything else assumes shape is right

    # ---- 1. every maximal run is a real dictionary word ----------------
    runs = grid_runs(grid, N)
    run_set = {}
    for (r, c, d, w) in runs:
        run_set[(r, c, d)] = w
        if w not in valid:
            fail(f"phantom/non-dictionary word '{w}' at ({r},{c}) {d}")
        if len(w) < 3:
            warn(f"two-letter word '{w}' at ({r},{c}) {d} "
                 "(allowed, but unusual for kids)")

    # ---- 2. no white cell is isolated (must be in >=1 run) -------------
    in_a_run = set()
    for (r, c, d, w) in runs:
        for cell in cells((d, r, c, len(w))):
            in_a_run.add(cell)
    for r in range(N):
        for c in range(N):
            if grid[r][c] != "#" and (r, c) not in in_a_run:
                fail(f"isolated white cell at ({r},{c}) belongs to no word")

    # ---- 3. word-list matches the grid exactly ------------------------
    listed = {}
    for w in words:
        d = "A" if w["direction"] == "across" else "D"
        key = (w["row"], w["col"], d)
        listed[key] = w["word"]
        # 3a. the listed word must actually be in the grid at that spot
        got = run_set.get(key)
        if got is None:
            fail(f"word-list entry {w['word']} at "
                 f"({w['row']},{w['col']}) {w['direction']} has no run in the grid")
        elif got != w["word"]:
            fail(f"mismatch at ({w['row']},{w['col']}) {w['direction']}: "
                 f"list says {w['word']}, grid has {got}")
    # 3b. every grid run must appear in the word list
    for key, w in run_set.items():
        if key not in listed:
            fail(f"grid run '{w}' at {key} is missing from the word list")

    # ---- 4. clues present, non-blank, and reasonable ------------------
    for w in words:
        cl = w.get("clue", "")
        if not cl or not cl.strip():
            fail(f"word '{w['word']}' has a blank clue")
        elif cl.strip().upper() == w["word"]:
            warn(f"word '{w['word']}' has a placeholder clue equal to the answer")
        # clue should not literally contain the answer word
        elif w["word"] in cl.upper().split():
            warn(f"clue for '{w['word']}' contains the answer word")

    # ---- 5. numbering is correct & consistent --------------------------
    # recompute canonical numbering and compare
    starts = sorted(set((r, c) for (r, c, d, w) in runs))
    canon = {}
    n = 0
    for r in range(N):
        for c in range(N):
            if (r, c) in starts:
                n += 1
                canon[(r, c)] = n
    for w in words:
        expect = canon.get((w["row"], w["col"]))
        if expect is None:
            fail(f"{w['word']} starts at ({w['row']},{w['col']}) "
                 "which is not a valid word-start cell")
        elif expect != w["number"]:
            fail(f"{w['word']} numbered {w['number']} but should be {expect}")

    # ---- 6. duplicate answers -----------------------------------------
    seen = {}
    for w in words:
        if w["word"] in seen:
            warn(f"duplicate answer '{w['word']}' "
                 f"(#{seen[w['word']]} and #{w['number']})")
        else:
            seen[w["word"]] = w["number"]

    # ---- 7. fill connectivity (all white cells one connected region) --
    mask = ["".join("#" if grid[r][c] == "#" else "." for c in range(N))
            for r in range(N)]
    if not connected(mask, N):
        fail("white squares are not all connected (puzzle splits into pieces)")

    # ---- 8. coordinate bounds -----------------------------------------
    for w in words:
        for (r, c) in cells(("A" if w["direction"] == "across" else "D",
                             w["row"], w["col"], len(w["word"]))):
            if not (0 <= r < N and 0 <= c < N):
                fail(f"{w['word']} runs off the grid at ({r},{c})")
            elif grid[r][c] == "#":
                fail(f"{w['word']} overlaps a black square at ({r},{c})")

    if ok and not report:
        report.append("All checks passed.")
    elif ok:
        report.insert(0, "All hard checks passed (warnings below are advisory).")
    return ok, report



def build_puzzle_dict(grid, N, clue, theme_words, pid, title, emoji, difficulty):
    runs = grid_runs(grid, N)
    starts = sorted(set((r, c) for (r, c, d, w) in runs))
    num = {}
    n = 0
    for r in range(N):
        for c in range(N):
            if (r, c) in starts:
                n += 1
                num[(r, c)] = n
    words = []
    for (r, c, d, w) in runs:
        words.append({
            "id": w.lower(),
            "word": w,
            "clue": clue.get(w, w),
            "row": r, "col": c,
            "direction": "across" if d == "A" else "down",
            "number": num[(r, c)],
            "theme": w in theme_words,
        })
    words.sort(key=lambda x: (x["number"], x["direction"]))
    return {
        "id": pid, "title": title, "emoji": emoji, "difficulty": difficulty,
        "size": {"rows": N, "cols": N},
        "grid": [[grid[r][c] for c in range(N)] for r in range(N)],
        "words": words,
    }


def to_compact_json(pz, include_theme_flag=True):
    rows = ["{",
            f'  "id": {json.dumps(pz["id"])},',
            f'  "title": {json.dumps(pz["title"], ensure_ascii=False)},',
            f'  "emoji": {json.dumps(pz["emoji"], ensure_ascii=False)},',
            f'  "difficulty": {json.dumps(pz["difficulty"])},',
            '  "grid": [']
    g = pz["grid"]
    for i, gr in enumerate(g):
        rows.append("    [" + ", ".join(json.dumps(ch) for ch in gr) + "]" +
                    ("," if i < len(g) - 1 else ""))
    rows.append("  ],")
    ws = pz["words"]
    idw = max(len(json.dumps(w["id"])) for w in ws)
    wdw = max(len(json.dumps(w["word"])) for w in ws)
    clw = max(len(json.dumps(w["clue"], ensure_ascii=False)) for w in ws)
    rows.append('  "words": [')
    for i, w in enumerate(ws):
        ids = (json.dumps(w["id"]) + ",").ljust(idw + 1)
        wds = (json.dumps(w["word"]) + ",").ljust(wdw + 1)
        cls = (json.dumps(w["clue"], ensure_ascii=False) + ",").ljust(clw + 1)
        comma = "," if i < len(ws) - 1 else ""
        tail = ""
        if include_theme_flag:
            tail = f', "theme": {json.dumps(w["theme"])}'
        rows.append(f'    {{ "id": {ids} "word": {wds} "clue": {cls} '
                    f'"row": {w["row"]}, "col": {w["col"]}, '
                    f'"direction": {json.dumps(w["direction"]).ljust(8)}, '
                    f'"number": {w["number"]}{tail} }}{comma}')
    rows.append("  ]")
    rows.append("}")
    return "\n".join(rows) + "\n"


def print_grid(grid, N):
    for r in range(N):
        print("  " + " ".join(grid[r][c] for c in range(N)))


def print_clues(pz):
    across = [w for w in pz["words"] if w["direction"] == "across"]
    down = [w for w in pz["words"] if w["direction"] == "down"]
    print("\nACROSS")
    for w in across:
        star = " *" if w["theme"] else ""
        print(f"  {w['number']:>2}. {w['clue']} ({len(w['word'])}){star}")
    print("\nDOWN")
    for w in down:
        star = " *" if w["theme"] else ""
        print(f"  {w['number']:>2}. {w['clue']} ({len(w['word'])}){star}")
    print("\n(* = theme word)")


# --------------------------------------------------------------------------
# Optional PDF export (requires reportlab)
# --------------------------------------------------------------------------
def export_pdf(pz, path):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import inch
        from reportlab.lib import colors
    except ImportError:
        print("  [pdf] reportlab not installed - skipping PDF. "
              "Install with: pip install reportlab", file=sys.stderr)
        return False

    grid = pz["grid"]
    R = pz["size"]["rows"]
    C = pz["size"]["cols"]
    words = pz["words"]
    numpos = {(w["row"], w["col"]): w["number"] for w in words}
    across = [w for w in words if w["direction"] == "across"]
    down = [w for w in words if w["direction"] == "down"]

    c = canvas.Canvas(path, pagesize=letter)
    W, H = letter
    ACCENT = colors.HexColor("#1d6e8c")
    BLOCK = colors.HexColor("#cfd8e3")

    def draw(cx, cy, cell, ans=False):
        for r in range(R):
            for col in range(C):
                x = cx + col * cell
                y = cy - r * cell
                if grid[r][col] == "#":
                    c.setFillColor(BLOCK)
                    c.rect(x, y - cell, cell, cell, fill=1, stroke=1)
                else:
                    c.setFillColor(colors.white)
                    c.rect(x, y - cell, cell, cell, fill=1, stroke=1)
                    c.setFillColor(colors.black)
                    if (r, col) in numpos:
                        c.setFont("Helvetica", 6)
                        c.drawString(x + 1.5, y - 7, str(numpos[(r, col)]))
                    if ans:
                        c.setFont("Helvetica-Bold", 13)
                        c.drawCentredString(x + cell / 2, y - cell + cell * 0.30, grid[r][col])

    def clue_block(title, items, x, y, maxw):
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(ACCENT)
        c.drawString(x, y, title)
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9.5)
        y -= 0.24 * inch
        for w in items:
            line = f'{w["number"]}. {w["clue"]} ({len(w["word"])})'
            cur = ""
            for wd in line.split():
                t = (cur + " " + wd).strip()
                if c.stringWidth(t, "Helvetica", 9.5) > maxw:
                    c.drawString(x, y, cur)
                    y -= 0.18 * inch
                    cur = wd
                else:
                    cur = t
            if cur:
                c.drawString(x, y, cur)
                y -= 0.18 * inch
            y -= 0.02 * inch
        return y

    # page 1 - puzzle
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(W / 2, H - 0.9 * inch, f'{pz["emoji"]}  {pz["title"]}')
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 11)
    c.drawCentredString(W / 2, H - 1.15 * inch, "A crossword to solve together")
    cell = min(0.5 * inch, (6.5 * inch) / C)
    gx = (W - C * cell) / 2
    gy = H - 1.5 * inch
    draw(gx, gy, cell, False)
    ytop = gy - R * cell - 0.4 * inch
    clue_block("Across", across, 0.7 * inch, ytop, 3.4 * inch)
    clue_block("Down", down, 4.3 * inch, ytop, 3.4 * inch)
    c.showPage()
    # page 2 - answers
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(W / 2, H - 0.9 * inch, "Answer Key")
    gx = (W - C * cell) / 2
    gy = H - 1.4 * inch
    draw(gx, gy, cell, True)
    c.showPage()
    c.save()
    return True


# --------------------------------------------------------------------------
# Main driver
# --------------------------------------------------------------------------
def generate(by_word, clue, theme, size, max_tier, black_frac,
             nseed, min_theme, time_budget, base_seed, verbose=True):
    theme_words_all, fill_words_all = build_pools(
        by_word, theme, max_tier, min_len=3, max_len=size)
    if theme and not theme_words_all:
        raise SystemExit(f"No words found for theme '{theme}' at tier <= {max_tier}.")

    # "strong" theme words = the theme words themselves (used for forced slots)
    strong = set(theme_words_all)
    all_words = fill_words_all | theme_words_all
    by_len, idx = make_index(all_words)

    N = size
    t0 = time.time()
    seen = set()
    best = None
    tried = 0
    solved = 0

    # collect masks first (cheap), sorted lowest-black first
    masks = []
    seed = base_seed
    while len(masks) < 1500 and time.time() - t0 < min(20, time_budget / 3):
        m = gen_symmetric_mask(N, seed, black_frac)
        seed += 1
        key = "".join(m)
        if key in seen:
            continue
        seen.add(key)
        sl = slots_of(m, N)
        if len(sl) < max(8, N + 3):
            continue
        bf = sum(row.count("#") for row in m) / (N * N)
        masks.append((bf, -len(sl), m))
    masks.sort()

    if verbose:
        print(f"  generated {len(masks)} candidate grids; filling...", file=sys.stderr)

    for bf, _negn, m in masks:
        tried += 1
        for fs in range(3):
            r = fill_grid(m, N, all_words, theme_words_all, strong,
                          idx, by_len, tries=200000, seed=base_seed + fs, nseed=nseed)
            if isinstance(r, list):
                solved += 1
                runs = grid_runs(r, N)
                themed = [w for (_, _, _, w) in runs if w in theme_words_all]
                score = (len(themed), len(runs))
                if best is None or score > best[0]:
                    best = (score, r, m, themed)
                break
        if best and best[0][0] >= min_theme:
            break
        if time.time() - t0 > time_budget:
            break

    if verbose:
        dt = time.time() - t0
        print(f"  tried {tried} grids, {solved} filled, {dt:.1f}s", file=sys.stderr)

    if best is None:
        return None
    return best[1], best[3]  # grid, theme words used


def main():
    ap = argparse.ArgumentParser(
        description="Generate a themed crossword from a tagged vocabulary file.")
    ap.add_argument("--vocab", default="vocabulary.json",
                    help="path to vocabulary.json (default: ./vocabulary.json)")
    ap.add_argument("--theme", default=None,
                    help="theme tag to feature (e.g. space, body, food). "
                         "Omit for a no-theme puzzle.")
    ap.add_argument("--list-themes", action="store_true",
                    help="print available themes (with word counts) and exit")
    ap.add_argument("--size", type=int, default=9, help="grid size NxN (default 9)")
    ap.add_argument("--max-tier", type=int, default=2,
                    help="hardest word tier to allow: 1 easiest, 3 hardest (default 2)")
    ap.add_argument("--black", type=float, default=0.26,
                    help="target fraction of black squares, e.g. 0.22 or 0.30 (default 0.26)")
    ap.add_argument("--nseed", type=int, default=2,
                    help="how many long slots to force to be theme words (default 2)")
    ap.add_argument("--min-theme", type=int, default=6,
                    help="stop early once this many theme words are placed (default 6)")
    ap.add_argument("--time", type=float, default=120,
                    help="time budget in seconds (default 120)")
    ap.add_argument("--seed", type=int, default=None,
                    help="random seed for reproducible puzzles (default: random)")
    ap.add_argument("--title", default=None, help="puzzle title")
    ap.add_argument("--emoji", default="🧩", help="puzzle emoji")
    ap.add_argument("--out", default=None, help="output JSON path (default <theme>_puzzle.json)")
    ap.add_argument("--pdf", action="store_true", help="also write a printable PDF")
    ap.add_argument("--no-theme-flag", action="store_true",
                    help="omit the per-word \"theme\" flag in the JSON")
    args = ap.parse_args()

    by_word, clue = load_vocab(args.vocab)

    if args.list_themes:
        print("Available themes (word count at any tier):\n")
        for t, n in all_themes(by_word).items():
            print(f"  {t:14} {n}")
        return

    base_seed = args.seed if args.seed is not None else random.randint(1, 10**9)
    theme = args.theme
    title = args.title or (f"{theme.title()} Crossword" if theme else "Crossword")
    pid = (theme or "puzzle") + f"-{args.size}x{args.size}"

    print(f"Building '{title}'  (theme={theme}, size={args.size}, "
          f"max_tier={args.max_tier}, black={args.black:.0%}, seed={base_seed})",
          file=sys.stderr)

    result = generate(
        by_word, clue, theme, args.size, args.max_tier, args.black,
        args.nseed, args.min_theme, args.time, base_seed)

    if result is None:
        print("\nNo puzzle could be built with these settings. Try:\n"
              "  - a higher --black (e.g. 0.28-0.32) — counterintuitively easier to fill\n"
              "  - a higher --max-tier (more words available)\n"
              "  - a smaller --size, or a longer --time budget\n", file=sys.stderr)
        sys.exit(1)

    grid, themed = result
    N = args.size

    # (sanity check runs after the puzzle dict is built, below)

    pz = build_puzzle_dict(grid, N, clue, set(themed) | (
        build_pools(by_word, theme, args.max_tier, 3, N)[0] if theme else set()),
        pid, title, args.emoji,
        "easy" if args.max_tier == 1 else "medium")

    # ---- automatic sanity check (no manual verification needed) ----
    ok, report = sanity_check(pz, by_word, clue, N)
    print("\nSanity check:", file=sys.stderr)
    for line in report:
        print("  " + line, file=sys.stderr)
    if not ok:
        print("\nPuzzle FAILED sanity checks - not writing output. "
              "Try a different --seed or settings.", file=sys.stderr)
        sys.exit(2)

    black_pct = round(sum(row.count("#") for row in grid) / (N * N) * 100)
    print(f"\nGrid: {N}x{N}   black squares: {black_pct}%   "
          f"words: {len(pz['words'])}   theme words: {sum(w['theme'] for w in pz['words'])}\n")
    print_grid(grid, N)
    print_clues(pz)

    out = args.out or (pid.replace("-", "_") + ".json")
    with open(out, "w", encoding="utf-8") as f:
        f.write(to_compact_json(pz, include_theme_flag=not args.no_theme_flag))
    print(f"\nWrote {out}", file=sys.stderr)

    if args.pdf:
        pdf_path = out.rsplit(".", 1)[0] + ".pdf"
        if export_pdf(pz, pdf_path):
            print(f"Wrote {pdf_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
