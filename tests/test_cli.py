"""Tests for the CLI argument parser and command dispatch."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from grocery_helper.cli import build_parser, cmd_flyers, cmd_optimise, cmd_serve, main

# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_returns_parser(self) -> None:
        import argparse

        assert isinstance(build_parser(), argparse.ArgumentParser)

    def test_serve_defaults(self) -> None:
        args = build_parser().parse_args(["serve"])
        assert args.command == "serve"
        assert args.port == 8501
        assert args.host == "localhost"

    def test_serve_custom_port_and_host(self) -> None:
        args = build_parser().parse_args(["serve", "--port", "9000", "--host", "0.0.0.0"])
        assert args.port == 9000
        assert args.host == "0.0.0.0"

    def test_optimise_defaults_and_flags(self) -> None:
        args = build_parser().parse_args(
            ["optimise", "list_001", "--demo", "--json", "--zip", "10001"]
        )
        assert args.command == "optimise"
        assert args.list_id == "list_001"
        assert args.demo is True
        assert args.as_json is True
        assert args.store_zip == "10001"

    def test_missing_subcommand_exits(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_unknown_subcommand_exits(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["unknown"])


# ---------------------------------------------------------------------------
# cmd_optimise
# ---------------------------------------------------------------------------


class TestCmdOptimise:
    def test_optimise_demo_table_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = build_parser().parse_args(["optimise", "list_001", "--demo"])
        cmd_optimise(args)
        out = capsys.readouterr().out
        assert "Weekly Shop" in out
        assert "Optimised Split Basket" in out
        assert "Single-Store Basket Totals" in out
        assert "Best single-trip store" in out

    def test_optimise_demo_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = build_parser().parse_args(["optimise", "list_001", "--demo", "--json"])
        cmd_optimise(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["list_id"] == "list_001"
        assert data["list_name"] == "Weekly Shop"
        assert "total_cost" in data
        assert "store_assignments" in data
        assert "single_store_totals" in data

    def test_optimise_with_max_stores_cli(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = build_parser().parse_args(
            ["optimise", "list_001", "--demo", "--max-stores", "1", "--json"]
        )
        cmd_optimise(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assigned_stores = set(data["store_assignments"].values())
        assert len(assigned_stores) == 1

    def test_optimise_invalid_list_id_exits(self) -> None:
        args = build_parser().parse_args(["optimise", "non_existent_id", "--demo"])
        with pytest.raises(SystemExit) as exc_info:
            cmd_optimise(args)
        assert exc_info.value.code == 1

    def test_optimise_max_stores_text_output_shows_assigned_store(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = build_parser().parse_args(["optimise", "list_001", "--demo", "--max-stores", "1"])
        cmd_optimise(args)
        out = capsys.readouterr().out
        # Best single store for list_001 is ALDI; every assigned item row should reflect ALDI
        assert "Assigned Store" in out
        assert "ALDI" in out
        assert "Whole Foods" not in out.split("Single-Store Basket Totals")[0]

    def test_optimise_invalid_max_stores_exits(self) -> None:
        args = build_parser().parse_args(["optimise", "list_001", "--demo", "--max-stores", "0"])
        with pytest.raises(SystemExit) as exc_info:
            cmd_optimise(args)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# cmd_serve
# ---------------------------------------------------------------------------


class TestCmdServe:
    def test_calls_subprocess_run(self) -> None:
        args = build_parser().parse_args(["serve", "--port", "8888", "--host", "127.0.0.1"])
        with patch("subprocess.run") as mock_run:
            cmd_serve(args)
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert "streamlit" in call_args
            assert "8888" in call_args
            assert "127.0.0.1" in call_args

    def test_passes_check_true(self) -> None:
        args = build_parser().parse_args(["serve"])
        with patch("subprocess.run") as mock_run:
            cmd_serve(args)
            _, kwargs = mock_run.call_args
            assert kwargs.get("check") is True

    def test_missing_streamlit_exits(self) -> None:
        args = build_parser().parse_args(["serve"])
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(SystemExit) as exc_info:
                cmd_serve(args)
            assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_dispatches_optimise(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["grocery-helper", "optimise", "list_001", "--demo"]):
            main()
        out = capsys.readouterr().out
        assert "Weekly Shop" in out

    def test_main_dispatches_serve(self) -> None:
        with patch("sys.argv", ["grocery-helper", "serve"]):
            with patch("grocery_helper.cli.cmd_serve") as mock_serve:
                main()
            mock_serve.assert_called_once()

    def test_optimise_live_flyer_flags_parsed(self) -> None:
        args = build_parser().parse_args(
            [
                "optimise",
                "list_001",
                "--demo",
                "--live-flyers",
                "--advertised-only",
                "--wf-store-id",
                "10380",
                "--no-prime",
            ]
        )
        assert args.live_flyers is True
        assert args.advertised_only is True
        assert args.wf_store_id == "10380"
        assert args.no_prime is True

    def test_flyers_subcommand_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        from decimal import Decimal
        from unittest.mock import AsyncMock

        from grocery_helper.models import FlyerDeal, Unit

        mb_deals = [
            FlyerDeal(
                store_name="Market Basket",
                product_name="Market Basket Milk",
                sale_price=Decimal("2.50"),
                regular_price=Decimal("3.99"),
                unit=Unit.GALLON,
            )
        ]
        wf_deals = [
            FlyerDeal(
                store_name="Whole Foods",
                product_name="Organic Bananas",
                sale_price=Decimal("0.69"),
                prime_price=Decimal("0.49"),
                unit=Unit.LB,
            )
        ]
        flipp_deals = [
            FlyerDeal(
                store_name="Hannaford",
                product_name="Store Milk",
                sale_price=Decimal("2.99"),
                unit=Unit.GALLON,
            )
        ]
        with (
            patch("grocery_helper.cli.MarketBasketFlyerProvider") as mock_mb_cls,
            patch("grocery_helper.cli.WholeFoodsFlyerProvider") as mock_wf_cls,
            patch("grocery_helper.cli.FlippFlyerProvider") as mock_flipp_cls,
            patch(
                "sys.argv",
                ["grocery-helper", "flyers", "--demo", "--list-id", "list_001", "--json"],
            ),
        ):
            mock_mb_cls.return_value.fetch_flyer = AsyncMock(return_value=mb_deals)
            mock_wf_cls.return_value.fetch_flyer = AsyncMock(return_value=wf_deals)
            mock_flipp_cls.return_value.fetch_flyer = AsyncMock(return_value=flipp_deals)
            main()

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["count"] == 3
        assert len(data["deals"]) == 3
        assert any(m["store"] == "Market Basket" for m in data["list_matches"])
        assert any(m["store"] == "Hannaford" for m in data["list_matches"])

    def test_flyers_flipp_only_args(self) -> None:
        args = build_parser().parse_args(
            ["flyers", "--store", "flipp", "--zip", "02139", "--merchants", "Hannaford", "ALDI"]
        )
        assert args.store == "flipp"
        assert args.store_zip == "02139"
        assert args.merchants == ["Hannaford", "ALDI"]

    def test_flyers_dispatch_callable(self) -> None:
        with patch("grocery_helper.cli.cmd_flyers") as mock_flyers:
            with patch("sys.argv", ["grocery-helper", "flyers"]):
                main()
            mock_flyers.assert_called_once()
        assert callable(cmd_flyers)
