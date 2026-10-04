import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from steam_agent.memory import auth, insight_store


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.directory.name) / "auth.db")
        self.path_patch = patch.object(auth, "SQLITE_DB_PATH", self.db_path)
        self.path_patch.start()
        self.insight_path_patch = patch.object(insight_store, "SQLITE_DB_PATH", self.db_path)
        self.insight_path_patch.start()

    def tearDown(self):
        self.insight_path_patch.stop()
        self.path_patch.stop()
        self.directory.cleanup()

    def test_passwords_use_argon2_and_session_tokens_are_hashed(self):
        ok, _ = auth.register("alice", "correct horse battery staple")
        self.assertTrue(ok)
        ok, _ = auth.login("alice", "correct horse battery staple")
        self.assertTrue(ok)

        token = auth.create_session("alice")
        self.assertEqual(auth.get_session_user(token), "alice")

        conn = sqlite3.connect(self.db_path)
        password_hash, token_hash = conn.execute(
            "SELECT password_hash, (SELECT token_hash FROM sessions LIMIT 1) FROM users"
        ).fetchone()
        conn.close()
        self.assertTrue(password_hash.startswith("$argon2"))
        self.assertNotIn(token, token_hash)
        self.assertEqual(token_hash, hashlib.sha256(token.encode()).hexdigest())

    def test_revoked_session_cannot_authenticate(self):
        auth.register("alice", "correct horse battery staple")
        token = auth.create_session("alice")
        auth.revoke_session(token)
        self.assertIsNone(auth.get_session_user(token))

    def test_legacy_sha256_password_is_upgraded_after_login(self):
        auth.init_auth_table()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO users(username, password_hash) VALUES(?, ?)",
            ("legacy", hashlib.sha256(b"legacy-password").hexdigest()),
        )
        conn.commit()
        conn.close()

        self.assertEqual(auth.login("legacy", "legacy-password")[0], True)
        conn = sqlite3.connect(self.db_path)
        password_hash = conn.execute(
            "SELECT password_hash FROM users WHERE username='legacy'"
        ).fetchone()[0]
        conn.close()
        self.assertTrue(password_hash.startswith("$argon2"))

    def test_users_can_bind_the_same_steam_id(self):
        auth.register("alice", "correct horse battery staple")
        auth.register("bob", "correct horse battery staple")
        self.assertTrue(auth.bind_steam_id("alice", "76561198000000001")[0])
        self.assertTrue(auth.bind_steam_id("bob", "76561198000000001")[0])


if __name__ == "__main__":
    unittest.main()
