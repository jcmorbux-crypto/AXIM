import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "parsers"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from signal_parser import parse_signal
from fixtures.provider_corpus import PRODUCTION_PARSER_CORPUS


class ProviderCorpusRegressionTests(unittest.TestCase):
    """Locks in parsers/signal_parser.py's verified-correct behavior
    against a real-shaped, multi-provider message corpus (see
    tests/fixtures/provider_corpus.py's own docstring for sourcing) -
    the same class of regression protection
    core/provider_profile.check_for_drift gives a LIVE source at
    runtime, applied here at test time to the parser itself. A future
    change that silently breaks one of these real formats fails loudly
    here instead of only showing up as a quiet drift flag days later."""

    def test_corpus_covers_a_real_spread_of_formats(self):
        # A sanity floor, not a magic number - catches this file being
        # gutted or the corpus import silently returning nothing.
        self.assertGreaterEqual(len(PRODUCTION_PARSER_CORPUS), 15)

    def test_every_fixture_matches_its_verified_expectation(self):
        for label, message, expected in PRODUCTION_PARSER_CORPUS:
            with self.subTest(label=label):
                result = parse_signal(message)
                if expected is None:
                    self.assertIsNone(result, f"{label!r} was expected to be rejected (no signal), got {result!r}")
                else:
                    self.assertIsNotNone(result, f"{label!r} was expected to parse, got None")
                    for key, value in expected.items():
                        self.assertEqual(
                            result[key], value,
                            f"{label!r}: expected {key}={value!r}, got {result[key]!r}",
                        )

    def test_no_fixture_silently_duplicates_a_label(self):
        labels = [label for label, _, _ in PRODUCTION_PARSER_CORPUS]
        self.assertEqual(len(labels), len(set(labels)), "duplicate fixture label(s) found")


if __name__ == "__main__":
    unittest.main()
