"""Command-line entry point for the standalone research-run pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import ResearchRunPipelineError, run_research_pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a PDF/Markdown/TXT report into a verified research-run package"
    )
    parser.add_argument("input", type=Path, help="Report file or existing DocumentBundle")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--candidate-mode",
        choices=["active", "shadow", "disabled"],
        default="active",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-provider", choices=["auto", "deepseek", "siliconflow"])
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument(
        "--speaker-max-tokens",
        type=int,
        help="Output budget for the speaker-manuscript stage (default: 24000)",
    )
    parser.add_argument(
        "--speaker-max-attempts",
        type=int,
        help="Validation/retry attempts for the speaker-manuscript stage (default: 2)",
    )
    parser.add_argument("--timeout", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output, warnings = run_research_pipeline(
            args.input,
            args.output_dir,
            candidate_mode=args.candidate_mode,
            overwrite=args.overwrite,
            model=args.model,
            base_url=args.base_url,
            api_provider=args.api_provider,
            max_tokens=args.max_tokens,
            max_attempts=args.max_attempts,
            timeout=args.timeout,
            speaker_max_tokens=args.speaker_max_tokens,
            speaker_max_attempts=args.speaker_max_attempts,
        )
    except ResearchRunPipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Created: {output}")
    for warning in warnings:
        print(warning, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
