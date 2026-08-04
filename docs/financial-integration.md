# Financial research-report integration

This repository includes the complete financial research-report handoff: an
upstream pipeline produces audited structured artifacts, then the Stage 0
adapter uses MemSlides as the design, revision, and export layer.

## Generate the structured research run

Install the optional research dependencies first:

```bash
pip install -e ".[research]"
```

Convert a PDF, Markdown, TXT, or existing DocumentBundle into the three audited
inputs consumed by the adapter:

```bash
memslides-research-run /path/to/report.pdf \
  --output-dir .memslides/research-run
```

The equivalent module command is:

```bash
python -m memslides.research_pipeline.research_run \
  /path/to/report.pdf \
  --output-dir .memslides/research-run
```

PDF parsing reads `MINERU_API_TOKEN`; automatic outline generation reads
`DEEPSEEK_API_KEY`. Do not store either value in the repository or generated
JSON. Use `--outline-input` to reuse a previously reviewed outline without an
outline-generation request. Existing output directories are protected unless
`--overwrite` is explicitly supplied.

## Boundary

The upstream research pipeline is responsible for document parsing,
evidence selection, outline generation, numeric fact normalization, chart/table
data construction, and numeric audit. The adapter accepts exactly these three
artifacts:

- `slide_outline.json`
- `visualizations/visualization_manifest.json` and the visualization JSON files
  it binds
- `numeric_audit.json`

It fails closed when the outline hash differs, an audited chart/table has no
passing audit record, an audit contains mismatches, or a manifest file escapes
its allowed directory.

For every accepted chart or table, the adapter converts the verified data to
MemSlides' structured visual renderer. Source images are copied from the
manifest's `asset_root`; they are not treated as numerically audited.
Repeated table headers are made display-safe with stable suffixes such as
`占比` and `占比（2）`; cell order and values are unchanged.

## Run the adapter

From the repository root:

```bash
python -m memslides.integrations.research_report \
  --outline /path/to/run/slide_outline.json \
  --visualization-manifest /path/to/run/visualizations/visualization_manifest.json \
  --numeric-audit /path/to/run/numeric_audit.json \
  --output-dir .memslides/financial-session
```

On PowerShell, use backticks instead of backslashes for multiline commands, or
put the command on one line.

## Outputs

The output directory becomes an ordinary MemSlides workspace fragment:

```text
.memslides/financial-session/
├── manuscript.md
├── asset_manifest.json
├── financial_evidence_manifest.json
├── generated_visuals/
│   ├── ...chart/table SVG files
│   └── ...renderer metadata
└── verified_assets/
    └── ...copied source images
```

- `manuscript.md` preserves slide order, titles, key messages, bullet points,
  evidence identifiers, and explicit visual references.
- `asset_manifest.json` follows the asset shape already consumed by
  `PageAssetPlanner`. Its extra `verification` object records the slide,
  visualization, source evidence, and audit status.
- `financial_evidence_manifest.json` is the immutable handoff receipt: it
  records the upstream canonical outline hash, byte-level input hashes, and the
  verified asset mapping.

MemSlides can then consume `manuscript.md` and `asset_manifest.json` in its
existing generation flow. Later LLM stages may design the slide around these
assets, but they do not regenerate the audited values.

## Generate a complete deck

After installing the project and setting `DEEPSEEK_API_KEY`, run the full
design and export pipeline with a fresh output directory:

```bash
python -m memslides.integrations.research_report.generate \
  --outline .memslides/research-run/slide_outline.json \
  --visualization-manifest .memslides/research-run/visualizations/visualization_manifest.json \
  --numeric-audit .memslides/research-run/numeric_audit.json \
  --template /path/to/school-template.pptx \
  --output-dir .memslides/financial-deck \
  --generation-timeout 3600 \
  --sjtu-branding
```

`--template` is optional. When supplied, the existing MemSlides template
analysis and quality-selection flow uses the PPTX as a structural or style
reference. Omitting it preserves the original model-designed generation flow.
Template semantic analysis resolves `template_analyze` through the same model
routing used by ordinary report generation even though financial generation
keeps content memory disabled. This enables narrative, section, and bullet-style
analysis without reading or writing historical memory.

`--generation-timeout` limits the complete DeckDesigner session in seconds and
defaults to 3600. A timeout fails the financial run explicitly instead of
leaving an indefinitely suspended process. `--sjtu-branding` is also optional;
when omitted, the normal financial HTML and export flow is unchanged.

When SJTU branding is enabled, the financial-only HTML postprocessor runs after
DeckDesigner has completed the slide HTML and before the first PPTX/PDF export.
It applies the packaged SJTU colors and artwork, adds the complete white SJTU
logo to content pages, and writes `sjtu_html_brand_report.json`. No additional
PPTX postprocessor or second PPTX export is required.

HTML rendering and PPTX/PDF export require Playwright Chromium. On Windows, a
normal Playwright installation is usually stored below
`$env:LOCALAPPDATA\ms-playwright`. If an explicit browser directory is needed,
point `MEMSLIDES_PLAYWRIGHT_BROWSERS_PATH` there; do not point it at an empty
project-local `.playwright-browsers` directory. For example:

```powershell
$env:MEMSLIDES_PLAYWRIGHT_BROWSERS_PATH = Join-Path $env:LOCALAPPDATA "ms-playwright"
$env:PLAYWRIGHT_BROWSERS_PATH = $env:MEMSLIDES_PLAYWRIGHT_BROWSERS_PATH
```

This command bypasses the Researcher LLM, feeds the prebuilt manuscript to the
native DeckDesigner, then runs the normal HTML repair and PPTX/PDF export. It
hashes the manuscript, evidence files, and every verified asset before design
and fails if any of them changes. A successful run writes
`financial_generation_receipt.json` with the output paths and integrity hashes.

## Chart compatibility

The adapter currently supports `line`, `column`, `bar`, `area`, `pie`, and
`scatter`. A multi-series `column` chart becomes a MemSlides `grouped_bar`.
`combo` is rejected deliberately because MemSlides has no equivalent structured
renderer yet; silently flattening it would change the financial meaning.
