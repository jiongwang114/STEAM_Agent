from contextlib import ExitStack
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from steam_agent.api import main as api_main
from steam_agent.api.main import app
from steam_agent.memory import auth, game_profile, insight_store, message_store, thread_title


class ApiSecurityTests(unittest.TestCase):
    def test_client_supplied_user_id_cannot_replace_session_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "api.db")
            db_modules = (
                api_main,
                auth,
                game_profile,
                insight_store,
                message_store,
                thread_title,
            )
            with ExitStack() as stack:
                for module in db_modules:
                    stack.enter_context(patch.object(module, "SQLITE_DB_PATH", db_path))

                with TestClient(app) as client:
                    self.assertEqual(client.get("/threads?user_id=alice").status_code, 401)
                    client.post(
                        "/auth/register",
                        json={"username": "alice", "password": "correct horse battery staple"},
                    )
                    client.post(
                        "/auth/login",
                        json={"username": "alice", "password": "correct horse battery staple"},
                    )
                    self.assertEqual(client.get("/threads?user_id=bob").status_code, 200)
                    self.assertEqual(
                        client.get("/auth/user-info").json()["user"]["username"],
                        "alice",
                    )


if __name__ == "__main__":
    unittest.main()
