from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from memslides.contracts import DeckRequest, RevisionRequest, SessionOptions
from memslides.session import MemSlidesSession
from memslides.templates.induction import induct_template


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _session_options(args: argparse.Namespace) -> SessionOptions:
    return SessionOptions(
        config_file=Path(args.config).expanduser() if getattr(args, "config", None) else None,
        workspace=Path(args.workspace).expanduser() if getattr(args, "workspace", None) else None,
        session_id=getattr(args, "session", None),
        language=getattr(args, "language", "en"),
    )


async def _generate(args: argparse.Namespace) -> None:
    payload = _load_json(Path(args.input).expanduser()) if args.input else {"instruction": args.instruction}
    if args.num_pages is not None and "num_pages" not in payload:
        payload["num_pages"] = args.num_pages
    session = MemSlidesSession(options=_session_options(args))
    try:
        result = await session.generate(DeckRequest(**payload))
        print(result.model_dump_json(indent=2))
    finally:
        await session.close()


async def _revise(args: argparse.Namespace) -> None:
    session = MemSlidesSession(options=_session_options(args))
    try:
        result = await session.revise(RevisionRequest(feedback=args.feedback))
        print(result.model_dump_json(indent=2))
    finally:
        await session.close()


async def _template_induct(args: argparse.Namespace) -> None:
    result = await induct_template(
        Path(args.template_file).expanduser(),
        output_dir=Path(args.output_dir).expanduser() if args.output_dir else None,
        workspace=Path(args.workspace).expanduser() if args.workspace else None,
    )
    payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    if args.output_json:
        output_path = Path(args.output_json).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(output_path))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _financial_report(args: argparse.Namespace) -> None:
    from memslides.integrations.research_report.workflow import (
        FinancialReportWorkflowError,
        run_financial_report_workflow,
    )

    try:
        result = await run_financial_report_workflow(
            args.input,
            args.output_dir,
            pdf_path=args.pdf,
            parsed_json_path=args.parsed_json,
            config_path=args.config,
            resume=args.resume,
            overwrite=args.overwrite,
            instruction=args.instruction,
            generation_timeout=args.generation_timeout,
            model=args.model,
            base_url=args.base_url,
            api_provider=args.api_provider,
            citation_model=args.citation_model,
            max_tokens=args.max_tokens,
            max_attempts=args.max_attempts,
            speaker_max_tokens=args.speaker_max_tokens,
            speaker_max_attempts=args.speaker_max_attempts,
            timeout=args.timeout,
        )
    except FinancialReportWorkflowError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memslides")
    parser.add_argument("--config", help="Path to a MemSlides YAML config")
    parser.add_argument("--workspace", help="Workspace directory")
    parser.add_argument("--session", help="Session id")
    parser.add_argument("--language", choices=["zh", "en"], default="en")

    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate a deck from a request")
    generate.add_argument("--input", help="JSON file containing a DeckRequest payload")
    generate.add_argument("--instruction", help="Inline instruction when --input is omitted")
    generate.add_argument("--num-pages", type=int, help="Optional slide count for inline instructions")
    generate.set_defaults(func=_generate)

    revise = subparsers.add_parser("revise", help="Revise an existing session workspace")
    revise.add_argument("--feedback", required=True, help="Natural-language revision feedback")
    revise.set_defaults(func=_revise)

    financial = subparsers.add_parser(
        "financial-report",
        help="Generate a cited, SJTU-branded PPTX with speaker notes",
    )
    financial.add_argument(
        "input",
        help="Markdown report (same-stem PDF required) or a PDF report",
    )
    financial.add_argument("--output-dir", required=True)
    financial.add_argument("--pdf", help="PDF override (default: same stem as Markdown)")
    financial.add_argument(
        "--parsed-json",
        help="Parsed JSON override (default: discover <stem>_parsed.json or generate it)",
    )
    financial.add_argument("--resume", action="store_true")
    financial.add_argument("--overwrite", action="store_true")
    financial.add_argument("--instruction", default="")
    financial.add_argument("--generation-timeout", type=float, default=3600)
    financial.add_argument("--model")
    financial.add_argument("--base-url")
    financial.add_argument("--api-provider", choices=["auto", "deepseek", "siliconflow"])
    financial.add_argument("--citation-model", default="deepseek-v4-flash")
    financial.add_argument(
        "--max-tokens",
        type=int,
        help="Initial output-token budget; confirmed truncation increases it automatically",
    )
    financial.add_argument("--max-attempts", type=int)
    financial.add_argument("--speaker-max-tokens", type=int)
    financial.add_argument("--speaker-max-attempts", type=int)
    financial.add_argument("--timeout", type=int)
    financial.set_defaults(func=_financial_report)

    template = subparsers.add_parser("template", help="Template utilities")
    template_subparsers = template.add_subparsers(dest="template_command", required=True)
    induct = template_subparsers.add_parser("induct", help="Induct a reusable PPTX template")
    induct.add_argument("--template-file", required=True, help="Path to a PPTX template")
    induct.add_argument("--output-dir", help="Directory for the induced template profile")
    induct.add_argument("--output-json", help="Optional JSON result path")
    induct.set_defaults(func=_template_induct)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "generate" and not args.input and not args.instruction:
        parser.error("generate requires --input or --instruction")
    result = args.func(args)
    if asyncio.iscoroutine(result):
        asyncio.run(result)


if __name__ == "__main__":
    main()
