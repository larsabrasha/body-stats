"""Command line entry points.

``wiiscale run``      the daemon, what systemd starts
``wiiscale monitor``  live readings in the terminal, for setup and sanity checks
``wiiscale devices``  list input devices that look like a balance board
``wiiscale config``   show the effective configuration and exit
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .config import DEFAULT_CONFIG_PATH, Config, load_config


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _add_common_options(parser: argparse.ArgumentParser, *, root: bool) -> None:
    """Add --config/--log-level so they work on either side of the subcommand.

    The subcommand copies default to SUPPRESS: without it argparse would run
    the subparser second and overwrite whatever ``wiiscale --config X run``
    had already put in the namespace with the default again.
    """
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH if root else argparse.SUPPRESS,
        help=f"path to the YAML config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--log-level",
        default=None if root else argparse.SUPPRESS,
        help="override logging.level from the config",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wiiscale",
        description="Bridge a Wii Balance Board to Home Assistant over MQTT.",
    )
    parser.add_argument("--version", action="version", version=f"wiiscale {__version__}")
    _add_common_options(parser, root=True)

    sub = parser.add_subparsers(dest="command")
    commands = {
        "run": "run the bridge (default)",
        "monitor": "print live readings without touching MQTT",
        "devices": "list candidate input devices",
        "config": "print the effective configuration",
    }
    for name, help_text in commands.items():
        _add_common_options(sub.add_parser(name, help=help_text), root=False)
    return parser


def cmd_devices(config: Config) -> int:
    from .board import candidate_devices

    devices = candidate_devices()
    if not devices:
        print("No balance board found. Is it connected? See docs/pairing.md")
        return 1
    for device in devices:
        marker = "*" if config.board.device_name.lower() in device.name.lower() else " "
        print(f"{marker} {device.path}  {device.name}")
        device.close()
    print("\n(* = matches board.device_name in the config)")
    return 0


def cmd_monitor(config: Config) -> int:
    """Stream readings to the terminal.

    Useful for checking that the board reads ~0.00 kg when empty and that a
    known weight comes out right before wiring anything to Home Assistant.
    """
    from .board import wait_for_board
    from .measure import MeasurementTracker

    board = None
    try:
        board = wait_for_board(config.board)
        if board is None:
            return 1
        tracker = MeasurementTracker(config.measurement)
        print(f"Reading {board.name} ({board.path}). Ctrl-C to stop.\n")
        for sample in board.stream(timeout=1.0):
            if sample is None:
                continue
            measurement = tracker.feed(sample)
            total = tracker.adjusted_total(sample)
            cells = " ".join(f"{v:6.2f}" for v in sample.sensors)
            print(
                f"\r{tracker.state.value:9s} total {total:7.2f} kg  "
                f"tare {tracker.tare_kg:5.2f}  cells [{cells}]",
                end="",
                flush=True,
            )
            if measurement is not None:
                print(
                    f"\n>>> {measurement.weight_kg:.2f} kg  "
                    f"quality {measurement.quality}%  "
                    f"spread {measurement.spread_kg:.3f} kg  "
                    f"{measurement.sample_count} samples in {measurement.settle_s:.1f} s"
                    f"{'' if measurement.stable else '  (TIMED OUT, never settled)'}\n"
                )
    except KeyboardInterrupt:
        print()
    finally:
        if board is not None:
            board.close()
    return 0


def cmd_config(config: Config) -> int:
    import yaml

    print(yaml.safe_dump(dataclasses.asdict(config), sort_keys=False, default_flow_style=False))
    return 0


def cmd_run(config: Config) -> int:
    from .daemon import Daemon

    daemon = Daemon(config)
    daemon.install_signal_handlers()
    return daemon.run()


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (ValueError, OSError) as exc:
        print(f"wiiscale: {exc}", file=sys.stderr)
        return 2

    _setup_logging(args.log_level or config.logging.level)

    commands = {
        "run": cmd_run,
        "monitor": cmd_monitor,
        "devices": cmd_devices,
        "config": cmd_config,
    }
    handler = commands[args.command or "run"]
    try:
        return handler(config)
    except RuntimeError as exc:
        print(f"wiiscale: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
