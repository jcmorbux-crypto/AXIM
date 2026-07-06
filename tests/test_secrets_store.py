import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "core"))

import secrets_store


class SecretsStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self._original_key_file = secrets_store.KEY_FILE
        secrets_store.KEY_FILE = Path(self._tmp_dir.name) / ".secret_key"

    def tearDown(self):
        secrets_store.KEY_FILE = self._original_key_file
        self._tmp_dir.cleanup()

    def test_encrypt_then_decrypt_roundtrips(self):
        ciphertext = secrets_store.encrypt("hunter2")
        self.assertEqual(secrets_store.decrypt(ciphertext), "hunter2")

    def test_ciphertext_does_not_contain_plaintext(self):
        ciphertext = secrets_store.encrypt("my-api-hash-value")
        self.assertNotIn("my-api-hash-value", ciphertext)

    def test_key_is_generated_once_and_reused(self):
        secrets_store.encrypt("a")
        self.assertTrue(secrets_store.KEY_FILE.exists())
        key1 = secrets_store.KEY_FILE.read_bytes()
        secrets_store.encrypt("b")
        key2 = secrets_store.KEY_FILE.read_bytes()
        self.assertEqual(key1, key2)

    def test_decrypt_garbage_returns_none(self):
        self.assertIsNone(secrets_store.decrypt("not-a-real-token"))

    def test_none_passthrough(self):
        self.assertIsNone(secrets_store.encrypt(None))
        self.assertIsNone(secrets_store.decrypt(None))

    def test_mask(self):
        self.assertEqual(secrets_store.mask("15551234567"), "*******4567")
        self.assertIsNone(secrets_store.mask(None))
        self.assertIsNone(secrets_store.mask(""))


if __name__ == "__main__":
    unittest.main()
