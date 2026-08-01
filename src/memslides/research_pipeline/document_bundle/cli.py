"""Command-line interface for strict MinerU parsing and raw conversion."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from memslides.research_pipeline.document_bundle.bundle import build_from_raw, parse_pdf
from memslides.research_pipeline.document_bundle.config import MinerUConfig
from memslides.research_pipeline.document_bundle.errors import DocumentBundleError
from memslides.research_pipeline.document_bundle.parser.mineru_client import MinerUClient
from memslides.research_pipeline.document_bundle.markdown import build_from_markdown


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-report-bundle")
    subcommands = parser.add_subparsers(dest="command", required=True)

    parse = subcommands.add_parser("parse", help="Parse a local PDF with MinerU API v4")
    parse.add_argument("pdf", type=Path)
    parse.add_argument("--output-root", type=Path, default=Path("output"))
    parse.add_argument("--data-id")
    parse.add_argument("--poll-interval", type=float, default=2.0)
    parse.add_argument("--poll-timeout", type=float, default=900.0)

    raw = subcommands.add_parser("from-raw", help="Build from four existing raw artifacts")
    raw.add_argument("pdf", type=Path)
    raw.add_argument("raw_directory", type=Path)
    raw.add_argument("bundle_directory", type=Path)
    raw.add_argument("--data-id")

    markdown = subcommands.add_parser("from-markdown", help="Build a DocumentBundle from Markdown/text")
    markdown.add_argument("source", type=Path)
    markdown.add_argument("bundle_directory", type=Path)
    markdown.add_argument("--data-id")
    markdown.add_argument("--source-format", choices=["auto", "markdown", "plain_text"], default="auto")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    source = arguments.source if arguments.command == "from-markdown" else arguments.pdf
    data_id = arguments.data_id or source.stem
    try:
        if arguments.command == "from-markdown":
            _, validation = build_from_markdown(
                arguments.source,
                arguments.bundle_directory,
                data_id,
                source_format=arguments.source_format,
            )
            bundle_directory = arguments.bundle_directory
        elif arguments.command == "from-raw":
            _, validation = build_from_raw(
                arguments.pdf, arguments.raw_directory, arguments.bundle_directory, data_id
            )
            bundle_directory = arguments.bundle_directory
        else:
            config = MinerUConfig(
                poll_interval_seconds=arguments.poll_interval,
                poll_timeout_seconds=arguments.poll_timeout,
            )
            with MinerUClient(config) as client:
                bundle_directory, _, validation = parse_pdf(
                    arguments.pdf, arguments.output_root, data_id, client
                )
        logging.info(
            "DocumentBundle written to %s with validation status=%s",
            bundle_directory,
            validation["status"],
        )
        return 0 if validation["status"] != "failed" else 2
    except (DocumentBundleError, OSError, ValueError) as exc:
        logging.error("Conversion failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
