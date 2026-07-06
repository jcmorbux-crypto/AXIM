import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import auth


class PasswordHashingTests(unittest.TestCase):
    def test_correct_password_verifies(self):
        hashed = auth.hash_password("correct horse battery staple")
        self.assertTrue(auth.verify_password("correct horse battery staple", hashed))

    def test_incorrect_password_fails(self):
        hashed = auth.hash_password("correct horse battery staple")
        self.assertFalse(auth.verify_password("wrong password", hashed))

    def test_same_password_hashes_differently_each_time(self):
        # Random salt per call - two hashes of the same password must not
        # be byte-identical, otherwise a leaked hash table trivially reveals
        # which users share a password.
        first = auth.hash_password("hunter2")
        second = auth.hash_password("hunter2")
        self.assertNotEqual(first, second)
        self.assertTrue(auth.verify_password("hunter2", first))
        self.assertTrue(auth.verify_password("hunter2", second))

    def test_verify_password_rejects_garbage_hash(self):
        self.assertFalse(auth.verify_password("anything", "not-a-real-hash"))
        self.assertFalse(auth.verify_password("anything", ""))


class SessionTokenTests(unittest.TestCase):
    def test_generate_session_token_returns_matching_hash(self):
        raw, token_hash = auth.generate_session_token()
        self.assertEqual(auth.hash_token(raw), token_hash)

    def test_tokens_are_unique(self):
        raw1, _ = auth.generate_session_token()
        raw2, _ = auth.generate_session_token()
        self.assertNotEqual(raw1, raw2)

    def test_hash_token_is_deterministic(self):
        raw, token_hash = auth.generate_session_token()
        self.assertEqual(auth.hash_token(raw), token_hash)
        self.assertEqual(auth.hash_token(raw), auth.hash_token(raw))


if __name__ == "__main__":
    unittest.main()
