"""The README's numbers, regenerated offline from the shipped data and compared byte for byte with
the committed result files. Any change that moves a published number fails here."""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from implied import __main__ as cli

DATA = os.path.join(cli.REPO, "data")


def listing():
    """Every file under data/ with its size and modification time, so a rewrite in place shows."""
    out = set()
    for root, _, files in os.walk(DATA):
        for f in files:
            st = os.stat(os.path.join(root, f))
            out.add((root, f, st.st_size, st.st_mtime_ns))
    return out


def run(*argv):
    """Offline, and the shipped data must come out exactly as it went in."""
    before = listing()
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["implied", *argv]), contextlib.redirect_stdout(out), \
         mock.patch("urllib.request.urlopen", side_effect=AssertionError("golden run went online")):
        cli.main()
    if listing() != before:
        raise AssertionError(f"golden run wrote into data/: {sorted(listing() - before)[:3]}")
    return out.getvalue()


def committed(name):
    with open(os.path.join(DATA, name)) as f:
        return f.read()


@unittest.skipUnless(os.path.isdir(os.path.join(DATA, "nfl")), "shipped data not present")
class TestGolden(unittest.TestCase):
    def test_nfl_backtest(self):
        self.assertEqual(run("backtest"), committed("backtest_passyds.json"))

    def test_one_yard(self):
        self.assertEqual(run("one-yard"), committed("one_yard_20261104.json"))

    def test_spx_day(self):
        day = os.path.join(DATA, "snapshots", "20261104")
        out = run("spx-day", "--snapshot", day, "--settled", os.path.join(day, "settled-KXINX-26NOV04H1600.json"))
        self.assertEqual(out, committed("spx_day_20261104.json"))


if __name__ == "__main__":
    unittest.main()
