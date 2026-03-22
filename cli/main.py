"""VibeCheck CLI entry point."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibecheck",
        description="Competence-aware guardrail for AI-assisted coding",
    )
    subparsers = parser.add_subparsers(dest="command")

    # vibecheck auth
    auth_parser = subparsers.add_parser("auth", help="Configure OpenRouter API key")
    auth_parser.add_argument(
        "--from-env",
        action="store_true",
        help="Read OPENROUTER_API_KEY from environment instead of prompting",
    )

    # vibecheck cm <subcommand>
    cm_parser = subparsers.add_parser("cm", help="Competence model management")
    cm_sub = cm_parser.add_subparsers(dest="cm_command")
    cm_init_parser = cm_sub.add_parser("init", help="Launch competence model initialization survey")
    cm_init_parser.add_argument(
        "--preset",
        choices=["max", "min"],
        help="Skip survey and seed all concepts at max (0.9) or min (0.1) scores",
    )

    # vibecheck cc <subcommand>
    cc_parser = subparsers.add_parser("cc", help="Claude Code integration")
    cc_sub = cc_parser.add_subparsers(dest="cc_command")
    cc_sub.add_parser("init", help="Bootstrap Claude Code hook configuration")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "auth":
        from cli.auth import run_auth

        run_auth(from_env=args.from_env)

    elif args.command == "cm":
        if getattr(args, "cm_command", None) is None:
            parser.parse_args(["cm", "--help"])
        elif args.cm_command == "init":
            from cli.cm_init import run_cm_init

            run_cm_init(preset=getattr(args, "preset", None))

    elif args.command == "cc":
        if getattr(args, "cc_command", None) is None:
            parser.parse_args(["cc", "--help"])
        elif args.cc_command == "init":
            from cli.cc_init import run_cc_init

            run_cc_init()

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
