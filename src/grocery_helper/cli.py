"""
cli.py
------
Optional command-line interface for non-Streamlit usage (e.g., CI / scripts).

Entry point registered in pyproject.toml:
    [project.scripts]
    grocery-helper = "grocery_helper.cli:main"
"""

from __future__ import annotations

import argparse
import sys

from grocery_helper.utils import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grocery-helper",
        description="Grocery Helper — find optimal grocery pricing via AnyList",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── serve ─────────────────────────────────────────────────────────────
    serve_cmd = subparsers.add_parser("serve", help="Launch the Streamlit UI")
    serve_cmd.add_argument("--port", type=int, default=8501, help="Port to run on")
    serve_cmd.add_argument("--host", default="localhost", help="Host to bind to")

    # ── optimise ──────────────────────────────────────────────────────────
    optimise_cmd = subparsers.add_parser(
        "optimise", help="Run the pricing optimiser for a grocery list (CLI mode)"
    )
    optimise_cmd.add_argument("list_id", help="AnyList list ID to optimise")

    return parser


def cmd_serve(args: argparse.Namespace) -> None:
    """Launch the Streamlit app programmatically."""
    import subprocess

    import grocery_helper.app as _app_module

    subprocess.run(
        [
            "streamlit",
            "run",
            _app_module.__file__,
            "--server.port",
            str(args.port),
            "--server.address",
            args.host,
        ],
        check=True,
    )


def cmd_optimise(args: argparse.Namespace) -> None:
    """CLI optimise command — placeholder until PricingService is implemented."""
    print(f"Optimising list '{args.list_id}'…")
    print("TODO: Implement CLI optimise command in cli.py")


def main() -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "serve": cmd_serve,
        "optimise": cmd_optimise,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
