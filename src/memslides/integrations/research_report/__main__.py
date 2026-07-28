"""Command-line entry point for the research-report adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapter import ResearchReportAdapterError, adapt_research_report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert audited research-report artifacts into MemSlides inputs."
    )
    parser.add_argument("--outline", required=True, type=Path, help="Path to slide_outline.json")
    parser.add_argument(
        "--visualization-manifest", required=True, type=Path, help="Path to visualization_manifest.json"
    )
    parser.add_argument("--numeric-audit", required=True, type=Path, help="Path to numeric_audit.json")
    parser.add_argument("--output-dir", required=True, type=Path, help="MemSlides workspace")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = adapt_research_report(
            outline_path=args.outline,
            visualization_manifest_path=args.visualization_manifest,
            numeric_audit_path=args.numeric_audit,
            output_dir=args.output_dir,
        )
    except ResearchReportAdapterError as exc:
        raise SystemExit(f"research-report adapter failed: {exc}") from exc
    print(
        json.dumps(
            {
                "workspace": str(result.workspace),
                "manuscript": str(result.manuscript),
                "asset_manifest": str(result.asset_manifest),
                "evidence_manifest": str(result.evidence_manifest),
                "asset_count": result.asset_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

