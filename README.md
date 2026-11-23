# booktools

**Turn a folder of Markdown chapters into print-ready paperback PDFs (Amazon KDP,
IngramSpark), a reflowable EPUB3, and exact full-wrap cover templates, from one
command-line tool driven by a per-book config file. Each book also gets a local
research library (GraphRAG): local models embed your sources, a judge checks every
passage, and citations become Chicago footnotes plus a bibliography.**

booktools is built for agent-run book projects: an AI "chief editor" (or a person)
calls it from the shell whenever a workflow needs word counts, a build, or cover math.
Nothing book-specific is hard-coded. Titles, trim size, margins, and every printer
measurement come from the book's own `context/book-index.yaml`. When a measurement is
missing, the tool stops and tells the agent exactly what to look up, rather than guessing.

## The tools

| Tool | Command | What it does |
|---|---|---|
| **Doctor** | `python -m booktools doctor` | Checks pandoc, WeasyPrint (and its Pango libraries), the PDF reader, git, and epubcheck. Prints the install fix for anything missing. |
| **Stats** | `python -m booktools stats` | Counts words in every manuscript file and compares each to its `word_budget`, flagging chapters more than ±10% off. Shows the book total against the word target. Needs no pandoc. |
| **POD PDF** | `python -m booktools pdf --target kdp` | Builds the print interior: Markdown, then pandoc HTML, then a WeasyPrint PDF. Applies the trim and margins from config. Chapters open on right-hand pages, with mirrored running heads, a TOC with real page numbers, and prompt/code boxes that never split across pages. Prints the exact page and word count, enforces an optional page ceiling, and snapshots the build to a `builds` Git branch. |
| **POD PDF (strict black)** | `python -m booktools pdf --target ingram` | Same build using the IngramSpark stylesheet: every color is pure `#000000` or `#ffffff` for B&W presses. `--pdf-variant pdf/x-1a` is passed through to WeasyPrint. |
| **EPUB** | `python -m booktools epub` | Builds a reflowable EPUB3 for Kindle, Apple Books, and Kobo: one file per chapter, an auto-generated TOC, wrapping prompt/code blocks, an optional cover image, and print-only pages removed. Runs epubcheck if it is installed. |
| **Spine & cover** | `python -m booktools spine` | Reads the page count from the built PDF and calculates spine width and full-wrap cover size (bleed + back + spine + front + bleed) for each printer, in inches, mm, and pixels at 300 dpi. Says whether spine text is allowed. Writes an SVG wrap template per printer with bleed, trim, folds, safe zones, and the barcode area marked. `--pages N` gives early estimates. |

| **New book** | `python -m booktools new DEST --template DIR --vars vars.yaml` | Creates a book folder from a starter template. It fills every `{{placeholder}}` and removes the optional parts you switched off. It refuses an incomplete vars file or any leftover placeholder, and leaves no half-made folder behind. Then it runs `git init` with an initial commit and a tag. |

### Research library (per book, all local)

| Tool | Command | What it does |
|---|---|---|
| **Ingest** | `python -m booktools research ingest` | Reads everything new in `research/inbox/`: PDF (page by page), HTML, DOCX, MD and TXT. Also fetches each new URL in `research/urls.txt` and saves a local copy. Chunks the text, keeping page numbers, and embeds it through a local OpenAI-compatible server such as LM Studio. A local model drafts citation metadata into `research/sources.yaml` (`verified: false` until you check it) and extracts an entity/relation graph. `--notes` ingests your raw notes as uncitable material for clustering. |
| **Query** | `python -m booktools research query "question"` | The retrieval agent: vector search plus a one-hop expansion through the entity graph. A local judge scores each candidate 0–3 and must quote it word for word, and the code then checks that the quote really is in the source. Only verified, relevant passages come back, each with a ready citation such as `[@delgado2021coral, p. 3]`; they are logged as approved. `--json` gives agent-readable output. |
| **Themes** | `python -m booktools research themes --chapters 8..12` | Brain Dump → Cluster → Outline. Agglomerative clustering (cosine distance) groups your notes into chapter themes and suggested sections, picking the best count in your range. HDBSCAN flags ideas that fit nowhere. A local model names each theme. Writes `context/themes.md`, with an **Unplaced ideas** list for you to decide on. |
| **Bibliography** | `python -m booktools research bibliography` | Checks every `[@key, p. N]` in the manuscript. The key must exist, its source should be verified, and the page must match a judge-approved passage. Writes CSL-JSON. After that, `pdf` and `epub` add citeproc automatically: **Chicago notes-bibliography** footnotes at the foot of the printed page, endnotes in the EPUB, and a generated Bibliography. `--strict` fails on any unverified or unapproved citation. |
| **Status / Graph** | `python -m booktools research status` · `research graph --export g.graphml` | Shows library counts, models, and sources still unverified. Exports the entity graph for Gephi or yEd. |

Exit codes: `0` ok · `1` tool or build error · `2` missing input or specs (read the message) · `3` over the page ceiling.

Any command accepts `--project PATH`. Without it, booktools searches upward from the
current folder for `context/book-index.yaml`.

## Install

Requires Python 3.10+.

```powershell
git clone https://github.com/shawnbasey/book-tools.git
python -m pip install -e ./book-tools
python -m booktools doctor
```

External tools:

| Tool | Windows | macOS / Linux |
|---|---|---|
| pandoc 3.x | `winget install JohnMacFarlane.Pandoc` | `brew install pandoc` / package manager |
| WeasyPrint | installed by pip, **plus** Pango: `winget install MSYS2.MSYS2`, then `C:\msys64\usr\bin\bash.exe -lc "pacman -S --noconfirm mingw-w64-ucrt-x86_64-pango"` | `brew install pango` / `apt install libpango-1.0-0 libpangoft2-1.0-0` |
| pypdf | installed by pip | installed by pip |
| epubcheck | optional | optional |

On Windows, booktools finds `C:\msys64\ucrt64\bin` automatically. For any other
location, set `WEASYPRINT_DLL_DIRECTORIES`.

## Project layout

```
my-book/
  context/book-index.yaml   book: and production: settings (start from templates/book-index.template.yaml)
  manuscript/NN-*.md        00 = front matter (placed before the TOC); 01..NN = body, in numeric order
  assets/cover/             SVG wrap templates are written here; ebook-cover.jpg is read from here
  dist/                     build output (keep out of Git)
```

Manuscript conventions the stylesheets rely on:

- Chapter H1: `# Title {.chapter}`. The print CSS adds "Chapter N".
- Front-matter H1s: `{.front-matter .unnumbered .unlisted}`. The print title page also adds `.title-page .print-only`.
- Back-matter H1s: `{.back-matter .unnumbered}`.
- Prompts: fenced code blocks tagged `prompt`. Callouts: `::: callout` and `::: warning` fenced divs.
- Each file's YAML frontmatter may carry `status` and `word_budget` for `stats`.

## Specs: why booktools never guesses printer numbers

Spine thickness, margins, bleed, and barcode areas depend on the printer, trim size,
paper, ink, and sometimes page count, and printers revise them. So booktools ships no
defaults for them. If a value is missing from `production:`, the command exits with
an **AGENT ACTION REQUIRED** block that lists:

- each missing key and what it means,
- where to find the current value (KDP Cover Calculator and "Set Trim Size, Bleed,
  and Margins"; IngramSpark File Creation Guide and Cover Template Generator),
- last-known reference numbers, clearly marked as hints to verify, never defaults.

After filling them in, record `specs_source` and `specs_verified: YYYY-MM-DD` for each
printer. Builds print a `VERIFY:` warning until that date is set, or once it is more than
180 days old.

## Research configuration

Add a `research:` block to the book's `context/book-index.yaml`:

```yaml
research:
  endpoint: http://localhost:1234/v1      # LM Studio or any OpenAI-compatible local server
  models: {embed: embed.book, extractor: local.extractor, judge: local.judge}
  notes_dir: context/raw-material         # raw notes for `ingest --notes` and `themes`
  citation_style: chicago-notes-bibliography
  judge_min_score: 2                      # 0-3; lower scores are never citable
  chunk_words: 300                        # optional
  gpu_lock: ../tools/gpu_lock.py          # optional single-GPU lock adapter
```

- **Models** are role names looked up in the nearest `MODELS.md` above the book folder
  (`| \`role\` | vendor | use | \`model-id\` | ...`), or plain model ids. The embedding model is
  fixed per library. To change it, delete `research/library.sqlite` and ingest again.
- **The library** is one SQLite file per book holding sources, chunks with vectors, entities and
  relations. It needs no server and moves with the book.
- **Rules for writers** (the citation discipline the agents follow) are up to each project; the
  companion scaffold `nonfiction-writing-suite` ships a `citations.md` rule.
- **`gpu_lock`** (optional) points to a Python module with `holders()`, `is_small()` and `Claim`.
  booktools refuses to load a large model while another one holds the GPU.

## Styles

Default stylesheets ship in `booktools/styles/`: `pod_kdp.css`, `pod_ingram.css`, and
`kindle.css`. Page size and margins are generated from config and layered on top, so
the same CSS works at any trim size. To restyle one book, copy a stylesheet into the
book folder and point `production.print_targets.<target>.css` (or `production.epub.css`)
at it.

## Adding a printer

Add a block under `production.print_targets` (for example `lulu:`) with the same keys,
then run `python -m booktools pdf --target lulu`. Without a `pod_lulu.css`, the
strict-black stylesheet is used.

## Why a CLI library rather than an MCP server

Agents with shell access can call `python -m booktools` with no server to run or
configure. The output is plain text with exit codes, which is easy to test. If a
shell-less client ever needs these tools, a thin MCP wrapper can import `booktools`
and expose the same functions.

## License

MIT. See [LICENSE](LICENSE).

`booktools/styles/csl/chicago-notes-bibliography.csl` comes from the
[Citation Style Language styles repository](https://github.com/citation-style-language/styles)
and is licensed under [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
