# How to Add Crosswords

Edit `crosswords.json` to add new puzzles. Each puzzle looks like this:

```json
{
  "id": "unique-id",
  "title": "Puzzle Title",
  "emoji": "🦁",
  "difficulty": "easy",
  "grid": [
    ["C","A","T","#","D"],
    ["#","#","#","#","O"],
    ["#","B","I","R","D"]
  ],
  "words": [
    {
      "id": "cat",
      "word": "CAT",
      "clue": "A furry pet that says meow",
      "row": 0, "col": 0,
      "direction": "across",
      "number": 1
    }
  ]
}
```

## Grid rules
- Use capital letters for cells
- Use `#` for black (blocked) cells
- All rows must have the same length

## Word rules
- `row` and `col` are 0-based (top-left is 0,0)
- `direction` is `"across"` or `"down"`
- `number` is the clue number shown in the grid
- The letters in `word` must match the grid exactly

## Password
The password is set in `index.html` at the top of the `<script>` tag:
```js
const PASSWORD = "family2024";
```
Change it to whatever you like.

## Hosting
Open `index.html` directly in a browser, or host both files on any static hosting
(GitHub Pages, Netlify, Google Drive, etc.).
