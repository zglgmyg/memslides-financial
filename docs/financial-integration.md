# Financial research-report integration

This repository includes a Stage 0 adapter for using MemSlides as the design,
revision, and export layer behind an audited financial research pipeline.

## Boundary

The upstream research pipeline remains responsible for document parsing,
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
  --outline /path/to/run/slide_outline.json \
  --visualization-manifest /path/to/run/visualizations/visualization_manifest.json \
  --numeric-audit /path/to/run/numeric_audit.json \
  --output-dir .memslides/financial-deck
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
