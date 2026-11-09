"""
cli.py
------
Command-line interface for non-Streamlit usage (e.g., CI / scripts / terminal).

Entry point registered in pyproject.toml:
    [project.scripts]
    grocery-helper = "grocery_helper.cli:main"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from decimal import Decimal

from grocery_helper.anylist_client import AnyListAPIError, AnyListAuthError, AnyListClient
from grocery_helper.config import settings
from grocery_helper.pricing import PricingService
from grocery_helper.utils import configure_logging, format_currency

logger = logging.getLogger(__name__)


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
    optimise_cmd.add_argument(
        "--zip",
        dest="store_zip",
        default=settings.default_store_zip or "90210",
        help="Store ZIP code for local pricing (default: from config or 90210)",
    )
    optimise_cmd.add_argument(
        "--demo",
        action="store_true",
        help="Run in offline demo mode using sample lists and mock store prices",
    )
    optimise_cmd.add_argument(
        "--max-stores",
        dest="max_stores",
        type=int,
        default=None,
        help="Maximum number of stores to include in the shopping route (e.g. 1 or 2)",
    )
    optimise_cmd.add_argument(
        "--stores",
        dest="allowed_stores",
        nargs="+",
        default=None,
        help="Specific store names to consider (e.g. --stores ALDI 'Trader Joe\\'s')",
    )
    optimise_cmd.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output raw JSON instead of formatted text",
    )

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


async def _async_optimise(args: argparse.Namespace) -> int:
    """Async execution logic for the optimise CLI command."""
    is_demo = args.demo or not (settings.anylist_email and settings.anylist_password)

    client = AnyListClient(demo_mode=is_demo)
    pricing = PricingService(store_zip=args.store_zip)

    try:
        async with client:
            await client.authenticate()
            grocery_list = await client.get_list(args.list_id)
    except (AnyListAuthError, AnyListAPIError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    cart = await pricing.optimise(
        grocery_list,
        allowed_stores=args.allowed_stores,
        max_stores=args.max_stores,
    )

    if args.as_json:
        data = cart.model_dump(mode="json")
        data["list_name"] = grocery_list.name
        print(json.dumps(data, indent=2))
        return 0

    mode_str = "Demo Mode" if is_demo else "Live AnyList"
    print("=" * 70)
    print(f"🛒 Grocery Helper: {grocery_list.name} ({len(grocery_list.items)} items)")
    print(f"ZIP Code: {args.store_zip} | Mode: {mode_str}")
    print("=" * 70)
    print(f"{'Item':<24} {'Qty':<6} {'Best Store':<16} {'Price':<10} {'Savings'}")
    print("-" * 70)

    for comp in cart.comparisons:
        cheapest_store = comp.cheapest.store_name if comp.cheapest else "N/A"
        effective_price = format_currency(comp.cheapest.effective_price) if comp.cheapest else "-"
        qty_dec = Decimal(str(comp.item.quantity))
        savings_str = format_currency(comp.savings * qty_dec) if comp.savings else "None"
        qty_str = f"{comp.item.quantity:g} {comp.item.unit}"
        print(
            f"{comp.item.name:<24} {qty_str:<6} {cheapest_store:<16} "
            f"{effective_price:<10} {savings_str}"
        )

    print("-" * 70)
    print(
        f"Optimised Split Basket: {format_currency(cart.total_cost)} "
        f"(Potential Savings: {format_currency(cart.estimated_savings)})"
    )

    if cart.single_store_totals:
        print("\nSingle-Store Basket Totals:")
        best_store_tuple = cart.best_single_store
        best_store_name = best_store_tuple[0] if best_store_tuple else ""

        for store, store_total in sorted(cart.single_store_totals.items(), key=lambda x: x[1]):
            delta = store_total - cart.total_cost
            delta_str = f"(+{format_currency(delta)} vs split)" if delta > 0 else "(matches split)"
            rec_tag = " ⭐ Best single-trip store" if store == best_store_name else ""
            print(f"  • {store:<14}: {format_currency(store_total)} {delta_str}{rec_tag}")

    print("=" * 70)
    return 0


def cmd_optimise(args: argparse.Namespace) -> None:
    """CLI optimise command entry point."""
    exit_code = asyncio.run(_async_optimise(args))
    if exit_code != 0:
        sys.exit(exit_code)


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
