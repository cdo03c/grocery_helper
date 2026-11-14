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
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from grocery_helper.anylist_client import AnyListAPIError, AnyListAuthError, AnyListClient
from grocery_helper.config import settings
from grocery_helper.flyers import (
    FlippFlyerProvider,
    MarketBasketFlyerProvider,
    WholeFoodsFlyerProvider,
    match_flyer_deals,
)
from grocery_helper.models import FlyerDeal
from grocery_helper.pricing import PricingService
from grocery_helper.utils import configure_logging, format_currency, truncate

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
        help="Specific store names to consider (e.g. --stores ALDI 'Market Basket' 'Whole Foods')",
    )
    optimise_cmd.add_argument(
        "--live-flyers",
        action="store_true",
        dest="live_flyers",
        help="Overlay live weekly advertised sale prices from Market Basket and Whole Foods",
    )
    optimise_cmd.add_argument(
        "--advertised-only",
        action="store_true",
        dest="advertised_only",
        help="For Market Basket and Whole Foods, use only advertised weekly flyer items",
    )
    optimise_cmd.add_argument(
        "--wf-store-id",
        dest="wf_store_id",
        default=settings.whole_foods_store_id,
        help=(
            "Whole Foods store ID for sales flyer lookup "
            f"(default: {settings.whole_foods_store_id})"
        ),
    )
    optimise_cmd.add_argument(
        "--no-prime",
        action="store_true",
        dest="no_prime",
        help="Use regular Whole Foods sale prices instead of Prime member prices",
    )
    optimise_cmd.add_argument(
        "--no-flipp",
        action="store_true",
        dest="no_flipp",
        help="Disable generic nearby store circular lookups via Flipp when --live-flyers is set",
    )
    optimise_cmd.add_argument(
        "--flipp-merchants",
        dest="flipp_merchants",
        nargs="+",
        default=None,
        help="Restrict Flipp lookup to specific store names (e.g. 'Star Market' Hannaford ALDI)",
    )
    optimise_cmd.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output raw JSON instead of formatted text",
    )

    # ── flyers ────────────────────────────────────────────────────────────
    flyers_cmd = subparsers.add_parser(
        "flyers",
        help="List active weekly on-sale items from Market Basket, Whole Foods, and Flipp",
    )
    flyers_cmd.add_argument(
        "--store",
        choices=["all", "mb", "wf", "flipp"],
        default="all",
        help=(
            "Which flyer source(s) to list: all, mb (Market Basket), "
            "wf (Whole Foods), or flipp (all nearby grocery stores by ZIP)"
        ),
    )
    flyers_cmd.add_argument(
        "--zip",
        dest="store_zip",
        default=settings.default_store_zip or "02139",
        help="ZIP/postal code for Flipp circular discovery (default: from config or 02139)",
    )
    flyers_cmd.add_argument(
        "--merchants",
        nargs="+",
        default=None,
        help="Optional Flipp merchant filter (e.g. --merchants 'Star Market' Hannaford ALDI)",
    )
    flyers_cmd.add_argument(
        "--wf-store-id",
        dest="wf_store_id",
        default=settings.whole_foods_store_id,
        help=f"Whole Foods store ID (default: {settings.whole_foods_store_id})",
    )
    flyers_cmd.add_argument(
        "--no-prime",
        action="store_true",
        dest="no_prime",
        help="Use regular Whole Foods sale prices instead of Prime member prices",
    )
    flyers_cmd.add_argument(
        "--search",
        default=None,
        help="Filter on-sale items by keyword (name, brand, or department)",
    )
    flyers_cmd.add_argument(
        "--list-id",
        dest="list_id",
        default=None,
        help="Optional AnyList list ID to highlight matching on-sale items for your list",
    )
    flyers_cmd.add_argument(
        "--demo",
        action="store_true",
        help="When matching --list-id, load from sample demo lists",
    )
    flyers_cmd.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output raw JSON instead of formatted text",
    )

    return parser


def cmd_serve(args: argparse.Namespace) -> None:
    """Launch the Streamlit app programmatically."""
    app_path = Path(__file__).with_name("app.py")
    try:
        subprocess.run(
            [
                "streamlit",
                "run",
                str(app_path),
                "--server.port",
                str(args.port),
                "--server.address",
                args.host,
            ],
            check=True,
        )
    except FileNotFoundError:
        print("Error: 'streamlit' executable not found on PATH.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


async def _async_optimise(args: argparse.Namespace) -> int:
    """Async execution logic for the optimise CLI command."""
    is_demo = args.demo or not (settings.anylist_email and settings.anylist_password)

    client = AnyListClient(demo_mode=is_demo)
    pricing = PricingService(
        store_zip=args.store_zip,
        enable_live_flyers=True if getattr(args, "live_flyers", False) else None,
        enable_flipp=False if getattr(args, "no_flipp", False) else None,
        flyer_only_for_live_stores=bool(getattr(args, "advertised_only", False)),
        whole_foods_store_id=getattr(args, "wf_store_id", None),
        whole_foods_prime=False if getattr(args, "no_prime", False) else None,
        flipp_merchants=getattr(args, "flipp_merchants", None),
    )

    try:
        async with client:
            await client.authenticate()
            grocery_list = await client.get_list(args.list_id)
    except (AnyListAuthError, AnyListAPIError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    try:
        cart = await pricing.optimise(
            grocery_list,
            allowed_stores=args.allowed_stores,
            max_stores=args.max_stores,
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    if args.as_json:
        data = cart.model_dump(mode="json")
        data["list_name"] = grocery_list.name
        print(json.dumps(data, indent=2))
        return 0

    mode_str = "Demo Mode" if is_demo else "Live AnyList"
    print("=" * 74)
    print(f"🛒 Grocery Helper: {grocery_list.name} ({len(grocery_list.items)} items)")
    print(f"ZIP Code: {args.store_zip} | Mode: {mode_str}")
    print("=" * 74)
    print(f"{'Item':<24} {'Qty':<10} {'Assigned Store':<16} {'Price':<10} {'Savings'}")
    print("-" * 74)

    for comp in cart.comparisons:
        assigned_store = cart.store_assignments.get(comp.item.id)
        assigned_sp = next(
            (p for p in comp.prices if p.store_name == assigned_store),
            comp.cheapest,
        )
        store_label = assigned_sp.store_name if assigned_sp else "N/A"
        assigned_price = comp.normalized_price(assigned_sp) if assigned_sp else None
        effective_price = format_currency(assigned_price) if assigned_price is not None else "-"
        qty_dec = Decimal(str(comp.item.quantity))

        savings_str = "None"
        if assigned_price is not None and len(comp.prices) >= 2:
            max_price = max(comp.normalized_price(p) for p in comp.prices)
            item_savings = max(Decimal("0.00"), max_price - assigned_price) * qty_dec
            if item_savings > 0:
                savings_str = format_currency(item_savings)

        qty_str = f"{comp.item.quantity:g} {comp.item.unit}"
        print(
            f"{truncate(comp.item.name, 23):<24} {qty_str:<10} {store_label:<16} "
            f"{effective_price:<10} {savings_str}"
        )

    print("-" * 74)
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


async def _async_flyers(args: argparse.Namespace) -> int:
    """Async execution logic for the flyers CLI command."""
    use_prime = not bool(getattr(args, "no_prime", False))
    deals: list[FlyerDeal] = []
    errors: list[str] = []

    fetch_tasks: list[tuple[str, asyncio.Task[list[FlyerDeal]]]] = []
    if args.store in {"all", "mb"}:
        mb_provider = MarketBasketFlyerProvider()
        fetch_tasks.append(("Market Basket", asyncio.create_task(mb_provider.fetch_flyer())))
    if args.store in {"all", "wf"}:
        wf_provider = WholeFoodsFlyerProvider(
            store_id=args.wf_store_id,
            use_prime=use_prime,
        )
        fetch_tasks.append(("Whole Foods", asyncio.create_task(wf_provider.fetch_flyer())))
    if args.store in {"all", "flipp"}:
        flipp_provider = FlippFlyerProvider(
            postal_code=getattr(args, "store_zip", "02139") or "02139",
            merchants=getattr(args, "merchants", None),
        )
        fetch_tasks.append(("Flipp", asyncio.create_task(flipp_provider.fetch_flyer())))

    for store_label, task in fetch_tasks:
        try:
            fetched = await task
            if store_label == "Flipp" and args.store == "all":
                fetched = [
                    d for d in fetched if d.store_name != MarketBasketFlyerProvider.STORE_NAME
                ]
            deals.extend(fetched)
        except Exception as err:
            errors.append(f"{store_label}: {err}")

    for err_msg in errors:
        print(f"Warning: failed to fetch {err_msg}", file=sys.stderr)

    if not deals and errors:
        return 1

    search = (args.search or "").strip().lower()
    if search:
        deals = [
            d
            for d in deals
            if search in d.product_name.lower()
            or search in (d.brand or "").lower()
            or search in (d.description or "").lower()
            or search in (d.department or "").lower()
        ]

    matched_items: list[dict[str, object]] = []
    if args.list_id:
        is_demo = args.demo or not (settings.anylist_email and settings.anylist_password)
        client = AnyListClient(demo_mode=is_demo)
        try:
            async with client:
                await client.authenticate()
                grocery_list = await client.get_list(args.list_id)
        except (AnyListAuthError, AnyListAPIError) as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1

        stores = sorted({d.store_name for d in deals})
        for item in grocery_list.items:
            for store in stores:
                store_deals = [d for d in deals if d.store_name == store]
                best = match_flyer_deals(item.name, store_deals, limit=1)
                if not best:
                    continue
                deal, score = best[0]
                eff = deal.effective_sale_price(use_prime=use_prime)
                matched_items.append(
                    {
                        "list_item": item.name,
                        "store": store,
                        "product_name": deal.product_name,
                        "effective_price": str(eff) if eff is not None else None,
                        "sale_price": str(deal.sale_price) if deal.sale_price is not None else None,
                        "prime_price": (
                            str(deal.prime_price) if deal.prime_price is not None else None
                        ),
                        "regular_price": (
                            str(deal.regular_price) if deal.regular_price is not None else None
                        ),
                        "deal_text": deal.deal_text,
                        "match_score": round(score, 3),
                    }
                )

    if args.as_json:
        payload: dict[str, object] = {
            "count": len(deals),
            "deals": [d.model_dump(mode="json") for d in deals],
        }
        if args.list_id:
            payload["list_matches"] = matched_items
        print(json.dumps(payload, indent=2))
        return 0

    print("=" * 84)
    print(f"🏷️  Weekly On-Sale Flyer Items ({len(deals)} deals)")
    print("=" * 84)
    if args.list_id:
        print(f"Matches for List '{args.list_id}' ({len(matched_items)}):")
        for match in matched_items:
            price_str = (
                f"${match['effective_price']}"
                if match["effective_price"] is not None
                else str(match["deal_text"] or "-")
            )
            print(
                f"  • {match['list_item']} -> [{match['store']}] "
                f"{match['product_name']} ({price_str})"
            )
        print("-" * 84)

    print(f"{'Store':<15} {'Product':<38} {'Sale Price':<16} {'Deal / Reg'}")
    print("-" * 84)
    for deal in deals:
        eff = deal.effective_sale_price(use_prime=use_prime)
        price_disp = (
            f"{format_currency(eff)}/{deal.unit.value}"
            if (eff is not None and deal.unit.value != "each")
            else (format_currency(eff) if eff is not None else "-")
        )
        reg_disp = format_currency(deal.regular_price) if deal.regular_price is not None else "-"
        detail = deal.deal_text or f"Reg {reg_disp}"
        print(
            f"{deal.store_name:<15} {truncate(deal.product_name, 37):<38} "
            f"{truncate(price_disp, 15):<16} {truncate(detail, 24)}"
        )
    print("=" * 84)
    return 0


def cmd_flyers(args: argparse.Namespace) -> None:
    """CLI flyers command entry point."""
    exit_code = asyncio.run(_async_flyers(args))
    if exit_code != 0:
        sys.exit(exit_code)


def main() -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "serve": cmd_serve,
        "optimise": cmd_optimise,
        "flyers": cmd_flyers,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
