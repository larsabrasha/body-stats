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


class FakeBoard:
    """One connection's worth of samples, then the board goes away."""

    def __init__(self, samples):
        self._samples = samples
        self.closed = False
        self.name = "Fake Balance Board"
        self.path = "/dev/input/eventX"

    def stream(self, timeout=1.0):
        yield from self._samples

    def close(self):
        self.closed = True


def test_monitor_waits_for_the_board_again_after_a_disconnect(monkeypatch, capsys):
    # Regression: monitor used to return when the board disconnected, so it
    # quit every time the board powered itself down between weighings.
    from wiiscale import backend, cli
    from wiiscale.config import Config
    from wiiscale.measure import Sample

    boards = [
        FakeBoard([Sample(timestamp=0.0, sensors=(1.0, 1.0, 1.0, 1.0))]),
        FakeBoard([Sample(timestamp=1.0, sensors=(2.0, 2.0, 2.0, 2.0))]),
        None,  # third wait gives up, which is how the loop ends
    ]
    handed_out = []

    def fake_wait(board_config, stop=None):
        board = boards[len(handed_out)]
        handed_out.append(board)
        return board

    monkeypatch.setattr(backend, "wait_for_board", fake_wait)

    assert cli.cmd_monitor(Config()) == 1
    assert len(handed_out) == 3, "monitor stopped waiting after the first board"
    assert all(b.closed for b in handed_out if b is not None)
    assert "Waiting for it to come back" in capsys.readouterr().out
