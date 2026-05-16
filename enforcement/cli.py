"""Command line entrypoint for the drift scanner."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import load_config, merge_cli_config
from .drift_scanner import scan
from .reporting import render_json_report, render_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Surface likely overlap between staging notes and canonical playbook guidance.",
    )
    parser.add_argument("--config", type=Path, help="JSON scanner configuration file.")
    parser.add_argument("--notes-root", action="append", default=[], help="Filesystem root containing staging notes.")
    parser.add_argument("--playbook-root", action="append", default=[], help="Filesystem root containing canonical playbook guidance.")
    parser.add_argument("--workspace-root", type=str, help="Workspace root used with an authoritative repository inventory.")
    parser.add_argument("--organization", type=str, help="GitHub organization to enumerate as the preferred authoritative inventory source.")
    parser.add_argument("--workspace-manifest", type=str, help="Optional caller-owned workspace repository manifest for scoped or narrowed scans.")
    parser.add_argument(
        "--organization-repository",
        action="append",
        default=[],
        help="Repository name from organization inventory, e.g. ctrl-alt-keith/ai-workflow-playbook.",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Additional root-relative ignore glob, e.g. archive/** for directory contents.",
    )
    parser.add_argument("--similarity-threshold", type=float, help="Token similarity threshold from 0 to 1.")
    parser.add_argument("--min-heading-matches", type=int, help="Minimum repeated headings needed for a heading candidate.")
    parser.add_argument("--min-phrase-words", type=int, help="Words per normalized phrase.")
    parser.add_argument("--min-phrase-matches", type=int, help="Minimum repeated phrases needed for a phrase candidate.")
    parser.add_argument("--max-candidates", type=int, help="Maximum candidates to report.")
    parser.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="Report format. Default is human-readable text.",
    )
    parser.add_argument(
        "--fail-on-candidates",
        action="store_true",
        help="Optional non-zero exit when overlap candidates are found. Default is exit 0.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        base_config = load_config(args.config) if args.config else None
        config = merge_cli_config(
            base_config,
            notes_roots=args.notes_root,
            playbook_roots=args.playbook_root,
            ignore_patterns=args.ignore,
            workspace_root=args.workspace_root,
            organization=args.organization,
            workspace_manifest=args.workspace_manifest,
            organization_repositories=args.organization_repository,
            similarity_threshold=args.similarity_threshold,
            min_heading_matches=args.min_heading_matches,
            min_phrase_words=args.min_phrase_words,
            min_phrase_matches=args.min_phrase_matches,
            max_candidates=args.max_candidates,
        )
        result = scan(config)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output_format == "json":
        print(render_json_report(result))
    else:
        print(render_report(result))
    if args.fail_on_candidates and result.candidates:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
