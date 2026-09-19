from pathlib import Path

import pytest

from wiiscale.cli import _build_parser
from wiiscale.config import DEFAULT_CONFIG_PATH


@pytest.fixture
def parser():
    return _build_parser()


@pytest.mark.parametrize("command", ["run", "monitor", "devices", "config"])
def test_common_options_accepted_after_the_subcommand(parser, command):
    # The README documents "wiiscale monitor --log-level DEBUG", so the flags
    # have to work on the right of the subcommand, not only on the left.
    args = parser.parse_args([command, "--log-level", "DEBUG", "--config", "/tmp/a.yaml"])
    assert args.command == command
    assert args.log_level == "DEBUG"
    assert args.config == Path("/tmp/a.yaml")


@pytest.mark.parametrize("command", ["run", "monitor", "devices", "config"])
def test_common_options_accepted_before_the_subcommand(parser, command):
    # The order deploy/wiiscale.service uses.
    args = parser.parse_args(["--config", "/tmp/b.yaml", "--log-level", "WARNING", command])
    assert args.command == command
    assert args.log_level == "WARNING"
    assert args.config == Path("/tmp/b.yaml")


def test_subcommand_does_not_reset_options_given_before_it(parser):
    # Regression: with a plain parents= copy the subparser would run second and
    # overwrite config with the default again.
    args = parser.parse_args(["--config", "/tmp/c.yaml", "run"])
    assert args.config == Path("/tmp/c.yaml")
    assert args.log_level is None


def test_defaults_without_any_flags(parser):
    args = parser.parse_args([])
    assert args.command is None
    assert args.config == DEFAULT_CONFIG_PATH
    assert args.log_level is None
