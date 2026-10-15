# Synthetic Document Factory

Generate labelled document-layout datasets with a local LLM: PDFs, page images and exact bounding boxes in COCO
and YOLO format.

If you want to train a layout detection model (YOLO, DETR, LayoutLMv3), you need pages where every title,
paragraph, list, table and figure has a box and a label. Drawing those boxes by hand is slow, so I built this to
do it for me. A local [Ollama](https://ollama.com) model writes the document, the tool lays it out in a random
style, and it records where each element lands while the PDF is being drawn. That way the boxes are exact, not
guessed afterwards.

![A generated page with its labelled boxes drawn on](docs/preview.png)

## What you get

- Real text, tables and charts written by a local LLM. You don't need an API key and it costs nothing.
- Boxes taken straight from the PDF renderer, including paragraphs and tables that continue onto the next page or
  column. The tests read the text back out of every box to check it.
- The same 11 labels and ids as [DocLayNet](https://github.com/DS4SD/DocLayNet), so you can mix the output with
  that dataset.
- Lots of layout variety: one or two columns, Times or Helvetica, four font sizes, three margin sizes, A4 or
  Letter, running headers, numbered or plain headings, and justified or left-aligned text.
- Cheap augmentation: `--variants 5` renders the same text in 5 different layouts without any extra LLM calls.
- A page target. It adds or trims text until the document has the number of pages you asked for.
- Bar, line, area and pie charts drawn with matplotlib.

## Quick start

1. Install Ollama from [ollama.com/download](https://ollama.com/download) and pull a model:

   ```bash
   ollama pull mistral
   ```

2. Set up the project:

   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. If you want a different model, put it in a `.env` file. Without one, the tool uses `mistral:latest` on
   `localhost:11434`.

   ```
   OLLAMA_URL=http://localhost:11434
   OLLAMA_MODEL=llama3:latest
   ```

4. Generate a dataset:

   ```bash
   .venv/bin/python main.py --topic "Indian Railways" --pages 3 --tables 1 --charts 1 --variants 3 --preview
   ```

If you run `.venv/bin/python main.py` with no flags, it asks you for the topic, pages, tables and charts.

## Options

| Flag | Meaning | Default |
| --- | --- | --- |
| `--topic` | What the document is about | asked |
| `--pages` | Target number of pages (1–200) | asked |
| `--tables` | Tables in the document (0–5) | asked |
| `--charts` | Charts in the document (0–5) | asked |
| `--layout` | `single` column, `double` column, or `random` per variant | `random` |
| `--variants` | Render the same text in this many layouts | `1` |
| `--seed` | Make the random layouts repeatable | none |
| `--dpi` | Page image resolution | `150` |
| `--preview` | Also save every page with its boxes drawn on | off |

## Output

```
output/indian_railways/
├── pdf/indian_railways_1.pdf          one PDF per layout variant
├── images/indian_railways_1_p01.png   one image per page
├── labels/indian_railways_1_p01.txt   YOLO boxes: class cx cy w h (0–1)
├── annotations.json                   COCO boxes in pixels, plus each element's text
├── data.yaml                          ready for YOLO training
├── content.json                       the generated text, tables and chart data
└── preview/indian_railways_1_p01.png  pages with boxes drawn on (--preview)
```

Each COCO annotation also stores the text inside the box, which helps if you're training a text-aware model like
LayoutLM.

### Labels

| Id | Label | Used for |
| --- | --- | --- |
| 1 | Caption | "Table 1 …" and "Figure 1 …" captions |
| 2 | Footnote | not generated, kept so the ids match DocLayNet |
| 3 | Formula | not generated, kept so the ids match DocLayNet |
| 4 | List-item | each bullet or numbered point |
| 5 | Page-footer | page number |
| 6 | Page-header | running header with the document title |
| 7 | Picture | charts |
| 8 | Section-header | section and sub-section headings |
| 9 | Table | tables (each piece, if a table is split across pages) |
| 10 | Text | body paragraphs (each piece, if split) |
| 11 | Title | document title |

YOLO class numbers are the id minus one.

## Training a detector

```bash
pip install ultralytics
yolo detect train data=output/indian_railways/data.yaml model=yolov8n.pt imgsz=1024 epochs=50
```

`data.yaml` points both `train` and `val` at the same images. Split them into separate folders before you train
for real.

## How it works

```
Topic + page count
  -> Outline    the LLM plans the section titles
  -> Write      two LLM calls per section, so small models still reach the word target
  -> Polish     weak paragraphs (generic openers, filler endings, repeats) get rewritten
  -> Visuals    the LLM returns table and chart data as JSON; charts are drawn with matplotlib
  -> Render     reportlab lays out the PDF while a tracking frame records every element's box
  -> Fit pages  add or trim text and re-render until the page count is right
  -> Variants   re-render the same text in new random layouts
  -> Export     page images, YOLO labels, COCO annotations and data.yaml
```

## Project layout

```
main.py            the entry point: content pipeline, page-count loop, variants, export
core/llm.py        talks to Ollama: outline, sections, rewrites, tables, charts
core/assembler.py  layout styles, PDF rendering and box tracking
core/dataset.py    page images, COCO and YOLO export
tests/             test suite (runs without Ollama)
```

## Running the tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

## Limitations

- Text is English only (Latin alphabet), because the PDFs use the built-in PDF fonts.
- A document takes several minutes on a laptop, since the local LLM writes every section. Use `--variants` to
  get more labelled pages out of the same text.

## License

MIT. See `LICENSE`.
