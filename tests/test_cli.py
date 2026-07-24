"""Tests for the CLI argument parser and command dispatch."""
from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from grocery_helper.cli import build_parser, cmd_optimise, cmd_serve, main


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

    def test_optimise_positional(self) -> None:
        args = build_parser().parse_args(["optimise", "list_abc"])
        assert args.command == "optimise"
        assert args.list_id == "list_abc"

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
    def test_prints_list_id(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = build_parser().parse_args(["optimise", "my_list_123"])
        cmd_optimise(args)
        out = capsys.readouterr().out
        assert "my_list_123" in out

    def test_prints_todo(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = build_parser().parse_args(["optimise", "x"])
        cmd_optimise(args)
        out = capsys.readouterr().out
        assert "TODO" in out


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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_dispatches_optimise(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["grocery-helper", "optimise", "list_xyz"]):
            main()
        out = capsys.readouterr().out
        assert "list_xyz" in out

    def test_main_dispatches_serve(self) -> None:
        with patch("sys.argv", ["grocery-helper", "serve"]):
            with patch("grocery_helper.cli.cmd_serve") as mock_serve:
                main()
            mock_serve.assert_called_once()
