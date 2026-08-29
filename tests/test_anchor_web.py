import tempfile
import unittest

from anchor_web import create_app


class FakeDB:
    def count(self):
        return 7


class FakeMemory:
    db = FakeDB()

    def count(self):
        return 7


class CreateAppTests(unittest.TestCase):
    def test_health_uses_the_supplied_memory_instance(self):
        with tempfile.TemporaryDirectory() as db_path:
            app = create_app(db_path, secret_key="test", mem=FakeMemory())

        response = app.test_client().get("/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "status": "ok",
                "total_memories": 7,
                "index_count": 7,
                "synced": True,
            },
            response.get_json(),
        )


if __name__ == "__main__":
    unittest.main()
