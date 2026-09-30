"""Small unit tests for search re-ranking and the active-downloads endpoint.

Run with:  python -m unittest discover -s tests
(or inside the image:  docker run --rm -v "$PWD/tests:/app/tests" requestarr python -m unittest discover -s tests)
"""

import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="requestarr-test-"))

from fastapi.testclient import TestClient  # noqa: E402

from app import auth_store, bot, job_store, main  # noqa: E402
from app.config import settings  # noqa: E402


def _item(title: str, anime: bool = False) -> SimpleNamespace:
    return SimpleNamespace(title=title, looks_like_anime=anime)


class RankingTests(unittest.TestCase):
    def test_closest_title_first(self):
        results = [_item("The Matrix Revolutions"), _item("The Matrix Reloaded"), _item("The Matrix")]
        ranked = bot.rank_by_similarity("the matrx", results)
        self.assertEqual(ranked[0].title, "The Matrix")

    def test_typo_still_ranks_first(self):
        results = [_item("Interstellar Wars"), _item("Interstellar")]
        self.assertEqual(bot.rank_by_similarity("intersteller", results)[0].title, "Interstellar")

    def test_anime_first_then_similarity(self):
        results = [_item("Frieren", anime=False), _item("Frieren: Beyond Journey's End", anime=True), _item("Frieren Special", anime=True)]
        ranked = bot.rank_by_similarity("frieren", results, anime_first=True)
        self.assertTrue(ranked[0].looks_like_anime and ranked[1].looks_like_anime)
        self.assertEqual(ranked[0].title, "Frieren Special")
        self.assertFalse(ranked[2].looks_like_anime)

    def test_input_not_mutated(self):
        results = [_item("B"), _item("A")]
        bot.rank_by_similarity("a", results)
        self.assertEqual([r.title for r in results], ["B", "A"])


class ActiveDownloadsEndpointTests(unittest.TestCase):
    URL = "/api/jobs/active-downloads"

    @classmethod
    def setUpClass(cls):
        auth_store.set_admin("admin", "testpass123")
        job_store.store.active_jobs = {
            "movie:1": job_store.Job(kind="movie", external_id=1, title="Film", thread_id=11, requester_id=22, status="downloading"),
            "tv:2": job_store.Job(kind="tv", external_id=2, title="Show", thread_id=33, requester_id=44, status="searching"),
        }
        # No lifespan: don't start the Discord bot or the poll loop.
        cls.client = TestClient(main.app)

    def setUp(self):
        self._saved = (settings.active_downloads_api_key, settings.active_downloads_allow_unauthenticated)

    def tearDown(self):
        settings.active_downloads_api_key, settings.active_downloads_allow_unauthenticated = self._saved

    def test_default_requires_login(self):
        settings.active_downloads_api_key, settings.active_downloads_allow_unauthenticated = "", False
        self.assertEqual(self.client.get(self.URL).status_code, 401)
        ok = self.client.get(self.URL, auth=("admin", "testpass123"))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json(), {"active_downloads": [{"kind": "movie", "external_id": 1, "title": "Film"}]})

    def test_api_key(self):
        settings.active_downloads_api_key, settings.active_downloads_allow_unauthenticated = "s3cret-key", False
        self.assertEqual(self.client.get(self.URL, headers={"X-Api-Key": "wrong"}).status_code, 401)
        self.assertEqual(self.client.get(self.URL, headers={"X-Api-Key": "s3cret-key"}).status_code, 200)

    def test_open_mode_for_trusted_lan(self):
        settings.active_downloads_api_key, settings.active_downloads_allow_unauthenticated = "", True
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("requester_id", resp.text)
        self.assertNotIn("thread_id", resp.text)

    def test_read_only(self):
        settings.active_downloads_allow_unauthenticated = True
        self.assertEqual(self.client.post(self.URL).status_code, 405)


if __name__ == "__main__":
    unittest.main()
