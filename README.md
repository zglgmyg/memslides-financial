<h1 align="center">
  MemSlides: A Hierarchical Memory-Driven Agent Framework for Personalized Slide Generation with Multi-turn Local Revision
</h1>

<p align="center">
  <strong>Personalized presentation agents with user profile memory, working memory, tool memory, and scoped slide-local revision.</strong>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2606.17162"><img alt="Paper" src="assets/badges/paper.svg"></a>
  <a href="https://memslides.github.io/"><img alt="Project Page" src="assets/badges/project-page.svg"></a>
  <a href="#demo-video"><img alt="Demo Video" src="assets/badges/demo-video.svg"></a>
  <a href="https://hub.docker.com/r/huohua325/memslides"><img alt="Docker Hub" src="assets/badges/docker-hub.svg"></a>
  <a href="https://memslides.com/"><img alt="Website" src="assets/badges/website.svg"></a>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="Node" src="https://img.shields.io/badge/node-20-339933?logo=node.js&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-green">
</p>

<p align="center">
  &#11088; <strong>If MemSlides is useful for your research or slide-generation workflow, please consider starring this repository to help others discover it.</strong>
</p>

## News

<!-- NEWS:START -->
<!-- Add new milestones as the first table row. Keep dates in YYYY-MM-DD format. -->
<table>
  <tr>
    <td width="150" align="center">
      <strong>2026-07-15</strong><br>
      <sub>Product milestone</sub>
    </td>
    <td>
      <strong>&#128640; The MemSlides demo website crossed 200 registered users.</strong><br>
      Thank you for supporting the MemSlides community. Feedback is always welcome:
      <a href="https://github.com/huohua325/Memslides/issues">open an issue</a>
      or send us feedback through the demo website.
    </td>
  </tr>
  <tr>
    <td width="150" align="center">
      <strong>2026-07-03</strong><br>
      <sub>Product milestone</sub>
    </td>
    <td>
      <strong>&#128640; The MemSlides demo website crossed 100 users.</strong><br>
      Thank you for supporting the MemSlides community. Feedback is always welcome:
      <a href="https://github.com/huohua325/Memslides/issues">open an issue</a>
      or send us feedback through the demo website.
    </td>
  </tr>
  <tr>
    <td width="150" align="center">
      <strong>2026-06-26</strong><br>
      <sub>Product milestone</sub>
    </td>
    <td>
      <strong>&#128640; The MemSlides live demo crossed 50 verified users.</strong><br>
      Thank you to everyone trying the demo and helping us improve the personalized slide-generation workflow:
      <a href="https://memslides.com/">try the live demo</a>.
    </td>
  </tr>
  <tr>
    <td width="150" align="center">
      <strong>2026-06-25</strong><br>
      <sub>GitHub milestone</sub>
    </td>
    <td>
      <strong>&#11088; MemSlides crossed 100 GitHub stars.</strong><br>
      Thank you for helping the project reach its first community star milestone:
      <a href="https://github.com/huohua325/Memslides/stargazers">see stargazers</a>.
    </td>
  </tr>
  <tr>
    <td width="150" align="center">
      <strong>2026-06-24</strong><br>
      <sub>Community milestone</sub>
    </td>
    <td>
      <strong>&#127942; MemSlides reached #1 Paper of the Day on Hugging Face Daily Papers.</strong><br>
      Thank you for the early attention from the research community. See the
      <a href="https://huggingface.co/papers/2606.17162">#1 Paper of the day</a>,
      and <a href="https://huggingface.co/spaces/huohua325/MemSlides">showcase Space</a>.
    </td>
  </tr>
</table>
<!-- NEWS:END -->

## Demo Video

https://github.com/user-attachments/assets/a92ab49e-bc5c-4e90-8c0a-0f23b08a8857

## Overview

MemSlides treats presentation generation as a stateful authoring process rather
than a one-shot source-to-slides conversion task. It separates personalization
signals by lifetime: persistent user profile memory captures recurring
cross-job preferences, working memory carries active session constraints across
revision rounds, and tool memory stores reusable execution experience for
reliable localized editing.

Long-term memory stores intent-conditioned user profile memory for round-0
personalization and tool memory for reusable execution experience. Working
memory maintains active preferences, session state, and revision constraints
within the current deck. During revision, MemSlides projects user feedback onto
the smallest affected slide region and applies scoped local patches instead of
repeatedly regenerating the full deck.

<p align="center">
  <img src="assets/figures/memslides_memory_workflow.png" width="92%" alt="MemSlides hierarchical memory and localized revision overview">
</p>

## Highlights

- **Intent-conditioned user profile memory** routes personalization by
  presentation intent, then applies preferences over theme, visual style,
  layout, template use, content strategy, and general presentation habits.
- **Multi-turn working memory** preserves temporary preferences, session
  constraints, and edit-state records across feedback turns in the same deck.
- **Tool memory** retrieves prior task and tool-chain experience before similar
  edit operations to reduce repeated execution failures.
- **Scoped slide-local revision** updates the smallest affected slide region
  instead of repeatedly rewriting the full deck.

## Evidence

<p align="center">
  <img src="assets/figures/user_profile_preference_memory_lifecycle.png" width="45%" alt="User profile memory lifecycle">
  <img src="assets/figures/tool_memory.png" width="45%" alt="Tool memory flow">
</p>

<p align="center">
  <img src="assets/figures/localized_modify_example.png" width="72%" alt="Localized modify example">
</p>

- User profile memory supports persona-aware round-0 personalization by routing
  intent-matched preferences into the current job.
- Working memory carries active session constraints and temporary preferences
  across multi-turn revision.
- Tool memory stores reusable execution experience so future localized edits
  can avoid repeated failures.
- Scoped local revision keeps the edit surface close to the requested element,
  reducing unintended drift in already aligned slide content.

## Quick Start

Install from source:

```bash
sudo apt-get update
sudo apt-get install -y libreoffice fontconfig fonts-noto-cjk poppler-utils

conda env create -f environment.yml
conda activate memslides
pip install -e ".[research]"

python -m playwright install chromium ffmpeg
python -m memslides.experiment --help
```

Run the built-in smoke suite:

```bash
python -m memslides.experiment run smoke_minimal \
  --output-base .memslides/experiments \
  --parallel 1
```

The same experiment can run inside the Docker environment:

```bash
docker compose build
docker compose run --rm memslides python -m memslides.experiment run smoke_minimal \
  --output-base /app/.cache/memslides/experiments \
  --parallel 1
```

`smoke_minimal` is only a small verification suite. Users can pass any local
suite YAML path or packaged suite name to `python -m memslides.experiment run`.

## Configuration

MemSlides needs user-provided model and service credentials for real generation
experiments. Keep credentials outside git and provide them through environment
variables, `.env`, or a private YAML file selected with `MEMSLIDES_CONFIG_FILE`
or `--config`.

The packaged public config is `src/memslides/memslides.yaml`; its placeholders
are expanded from the current process environment when the YAML is loaded.
Generated outputs, caches, private YAML files, and credentials must not be
committed.

This fork defaults its text and tool-calling routes to DeepSeek V4 through the
OpenAI-compatible endpoint. Set `DEEPSEEK_API_KEY` before generation. See
[docs/deepseek.md](docs/deepseek.md) for model routing, local embeddings, and
the current text-only limitation.

For Docker runs with a private YAML file:

```bash
docker compose -f docker-compose.yml -f docker-compose.private.yml run --rm memslides \
  python -m memslides.experiment run smoke_minimal \
  --output-base /app/.cache/memslides/experiments \
  --parallel 1
```

The override maps `./memslides.private.yaml` to
`/run/secrets/memslides.private.yaml` and sets
`MEMSLIDES_CONFIG_FILE=/run/secrets/memslides.private.yaml` inside the
container.

## Experiment CLI

The suite runner is the main public entry point:

```bash
python -m memslides.experiment run smoke_minimal --output-base .memslides/experiments --parallel 1
python -m memslides.experiment report .memslides/experiments/smoke_minimal
python -m memslides.experiment personas
```

Core generation, revision, and template induction commands remain available for
scripted local use:

```bash
python -m memslides generate --instruction "Create a one-slide project summary" --num-pages 1
python -m memslides revise --workspace .memslides/session --feedback "Tighten the title"
python -m memslides template induct --template-file template.pptx
```

## Financial Research Integration

The financial path is a separate, fail-closed workflow. A complete financial
deck must include both verified references and SJTU branding. It never accepts,
analyzes, or applies a PowerPoint template; do not pass `--template` to the
financial generator. Ordinary template support remains available only to the
ordinary MemSlides generation path.

The command accepts either Markdown or PDF. For Markdown input, the same-stem
PDF must be the matching version of the report. For direct PDF input, MinerU's
extracted Markdown supplies the citation anchors as well as the PDF appendix
source catalog. Set `DEEPSEEK_API_KEY` and `MINERU_API_TOKEN` before running the
workflow.

### One-command financial workflow

Install the project once in a virtual environment, including the research
dependencies, and install the bundled browser used by the HTML renderer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[research]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

Set `DEEPSEEK_API_KEY` and `MINERU_API_TOKEN`, then run the complete workflow:

```powershell
.\.venv\Scripts\memslides.exe financial-report `
  "data\reports\agent\report.md" `
  --output-dir ".memslides\financial\report" `
  --max-attempts 3 `
  --generation-timeout 7200
```

By default the command discovers `report.pdf` and, when present,
`report_parsed.json` beside `report.md`. If parsed JSON is absent, the command
generates it from Markdown automatically. Use `--pdf` or `--parsed-json` only
when those files have different names. The command always enables verified citations, SJTU branding, and
slide-aligned PowerPoint speaker notes; these features cannot be disabled.
`--resume` continues a run whose input hashes have not changed, while
`--overwrite` starts that output directory again. A successful run contains:

To exercise the PDF parsing path independently, pass the PDF itself as input:

```powershell
.\.venv\Scripts\memslides.exe financial-report `
  "data\reports\agent\report.pdf" `
  --output-dir ".memslides\financial\report-pdf" `
  --generation-timeout 7200
```

```text
<output-dir>/
  research/          audited outline, visuals, numeric audit, speaker manuscript
  citations/         source catalog, citation units, validation report
  deck/              final HTML, PPTX, branding and generation receipts
  run_manifest.json  resumable per-stage state and input hashes
  final_receipt.json mandatory-feature compliance result
```

The command fails closed when citation inputs are missing, no cited ID can be
verified, branding is incomplete, speaker notes are not embedded, or the PPTX
is missing. Individual IDs absent from the PDF appendix are excluded and listed
in the receipts while the remaining verified references continue. For a long
report, increase `--max-tokens` and `--max-attempts`.

### Legacy stage-by-stage financial workflow on PowerShell

Define fresh output directories and the matching inputs:

```powershell
$Python = ".\.venv\Scripts\python.exe"
$Markdown = "D:\path\to\report.md"
$Pdf = "D:\path\to\report.pdf"
$ParsedJson = "D:\path\to\report_parsed.json"
$ResearchRun = ".memslides\research-runs\report-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$DeckRun = ".memslides\runs\report-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$Artifacts = "D:\path\to\report_citations"
```

Generate the audited outline, visualizations, and numeric audit from Markdown:

```powershell
& $Python -m memslides.research_pipeline.research_run `
  $Markdown `
  --output-dir $ResearchRun `
  --candidate-mode active `
  --max-attempts 3 `
  --timeout 600
```

Parse the matching PDF appendix, build citation units, and validate Markdown
citations against PDF sources:

```powershell
& $Python -c "from memslides.integrations.research_report.citation_appendix import parse_pdf_citation_appendix; print(parse_pdf_citation_appendix(r'''$Pdf''', r'''$Artifacts'''))"

$SourceCatalog = Join-Path $Artifacts "citation_source_catalog.json"
$CitationUnits = Join-Path $Artifacts "citation_units.json"
$ValidationReport = Join-Path $Artifacts "citation_validation_report.json"

& $Python -c "from memslides.integrations.research_report.citation_units import write_citation_units; print(write_citation_units(r'''$ParsedJson''', r'''$CitationUnits'''))"
& $Python -c "from memslides.integrations.research_report.citation_validation import write_citation_validation_report; print(write_citation_validation_report(r'''$CitationUnits''', r'''$SourceCatalog''', r'''$ValidationReport'''))"
```

Review `citation_validation_report.json` before continuing. IDs in
`source_missing` are excluded from later citation matching.

Generate the financial HTML and baseline PPTX with mandatory SJTU branding.
The financial generator deliberately has no `--template` argument:

```powershell
$Outline = Join-Path $ResearchRun "slide_outline.json"
$VisualizationManifest = Join-Path $ResearchRun "visualizations\visualization_manifest.json"
$NumericAudit = Join-Path $ResearchRun "numeric_audit.json"

& $Python -m memslides.integrations.research_report.generate `
  --outline $Outline `
  --visualization-manifest $VisualizationManifest `
  --numeric-audit $NumericAudit `
  --output-dir $DeckRun `
  --generation-timeout 7200 `
  --sjtu-branding
```

Run the required reference sidecar against the generated HTML:

```powershell
$HtmlDir = Join-Path $DeckRun "outputs"

& $Python -m memslides.integrations.research_report.citation_sidecar `
  --html-dir $HtmlDir `
  --outline $Outline `
  --citation-units $CitationUnits `
  --validation-report $ValidationReport `
  --source-catalog $SourceCatalog
```

The sidecar modifies HTML and appends the PDF-ordered reference appendix; it
does not create a PPTX. Export the modified HTML again to produce the final
referenced and SJTU-branded deck:

```powershell
$OutputPptx = Join-Path $DeckRun "financial-report-referenced-sjtu.pptx"

& $Python -c "import asyncio; from pathlib import Path; from memslides.utils.webview import convert_html_to_pptx; asyncio.run(convert_html_to_pptx(Path(r'''$HtmlDir'''), Path(r'''$OutputPptx'''), '16:9'))"
```

The final deliverable is `$OutputPptx`. See
[docs/financial-integration.md](docs/financial-integration.md) for the audited
artifact contracts.

### SJTU HTML branding details

The required `--sjtu-branding` stage applies the packaged 16:9 SJTU visual
template artwork to outline `title` and `closing` pages and inserts the complete
SJTU logo in the upper-right corner of every content page. It does not recolor
content pages. The built-in financial path uses these packaged SJTU assets; it
does not analyze an external PowerPoint template.

The branding step inserts the background as a full-slide image in the HTML; it
does not modify a native PowerPoint master or run after PPTX generation. The
standalone citation sidecar described below also does not invoke branding.

### Reference sidecar details

The required financial citation stage is an additive sidecar that runs after DeckDesigner
has produced the final slide HTML. It does not change the outline prompt,
`slide_outline.json`, content generation, or the normal HTML-to-PPTX exporter.
It expects three precomputed citation artifacts:

- `citation_source_catalog.json`, parsed from the matching PDF appendix with
  MinerU in the appendix's original source order;
- `citation_units.json`, built from citation markers in the parsed Markdown JSON;
- `citation_validation_report.json`, which retains only citation IDs present in
  both the Markdown-derived units and the PDF source catalog.

For each slide, `evidence_refs` limits candidate citation units to the referenced
blocks. The sidecar extracts visible claim nodes from the final HTML and asks
DeepSeek only to map claim IDs to candidate unit IDs. It then resolves the unit
IDs to source IDs deterministically. DeepSeek cannot create block IDs, citation
IDs, dates, domains, URLs, or source numbers.

Web-source descriptions are normalized once and cached next to the source
catalog as `citation_reference_catalog.json`. DeepSeek may extract an explicit
title or produce a grounded descriptive title such as `相关报道`; publisher and
document-number fields must still occur verbatim in the PDF description. Dates
and domains remain deterministic, and the code never constructs a URL from a
domain. Non-web sources continue to use deterministic formatting.

Run the sidecar against an existing final HTML directory:

```bash
python -m memslides.integrations.research_report.citation_sidecar \
  --html-dir /path/to/deck/outputs \
  --outline /path/to/slide_outline.json \
  --citation-units /path/to/citation_units.json \
  --validation-report /path/to/citation_validation_report.json \
  --source-catalog /path/to/citation_source_catalog.json
```

The command requires `DEEPSEEK_API_KEY` and modifies the HTML files in place.
It removes prior sidecar marks and appendix pages before rebuilding them, so the
same HTML directory can be processed again. Source normalization is cached, but
claim-to-unit matching is currently performed sequentially on every run and may
take several minutes without intermediate console progress.

Source numbers are global and follow the PDF appendix order. Body pages contain
gray-black bracketed marks such as `[1]` and `[1,3]`; they do not contain a
second source footer. All sources are listed after the existing deck in
`附录` pages, with ten sources per page and the final page containing the
remainder. The sidecar updates HTML only; run the normal HTML-to-PPTX export
after it completes.

The HTML marks use `<sup class="reference-mark">`, but the current structured
PPTX exporter does not yet map that run to PowerPoint's native superscript
property. Consequently, the exported baseline may differ from the HTML preview.

## Security And Privacy

- Keep API keys in environment variables, `.env`, or private YAML files.
- Do not commit `.env`, `.memslides/`, generated workspaces, or private config
  files.
- Network acquisition is optional and depends on user-provided search or model
  credentials.
- External URLs and downloaded assets should be reviewed before presenting.

## Citation

If you find MemSlides useful, please cite our paper.

```bibtex
@misc{jin2026memslides,
  title={MemSlides: A Hierarchical Memory Driven Agent Framework for Personalized Slide Generation with Multi-turn Local Revision},
  author={Ye Jin and Yangyang Xu and Jun Zhu and Yibo Yang},
  year={2026},
  eprint={2606.17162},
  archivePrefix={arXiv},
  primaryClass={cs.CL},
  doi={10.48550/arXiv.2606.17162},
  url={https://arxiv.org/abs/2606.17162},
}
```

## License

See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
