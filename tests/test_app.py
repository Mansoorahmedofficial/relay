import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app


class LinkShareTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(root / "test.sqlite3"),
                "UPLOAD_FOLDER": str(root / "uploads"),
                "MAX_CONTENT_LENGTH": 1024 * 1024,
                "SHARE_TTL_HOURS": 1,
            }
        )
        self.sender = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_room_is_bound_to_creator_ip_and_files_can_be_received(self):
        response = self.sender.post(
            "/share", environ_base={"REMOTE_ADDR": "198.51.100.8"})
        room_path = response.headers["Location"]
        self.assertEqual(response.status_code, 302)

        denied = self.app.test_client().get(room_path, environ_base={
            "REMOTE_ADDR": "203.0.113.14"})
        self.assertEqual(denied.status_code, 403)

        receiver = self.app.test_client()
        allowed = receiver.get(room_path, environ_base={
                               "REMOTE_ADDR": "198.51.100.8"})
        self.assertEqual(allowed.status_code, 200)

        uploaded = receiver.post(
            f"{room_path}/upload",
            data={"files": (io.BytesIO(
                b"hello from another device"), "hello.txt")},
            content_type="multipart/form-data",
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )
        self.assertEqual(uploaded.status_code, 201)
        file_record = receiver.get(
            f"{room_path}/files", environ_base={"REMOTE_ADDR": "198.51.100.8"}).get_json()[0]
        downloaded = receiver.get(
            f"{room_path}/download/{file_record['id']}",
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )
        content = downloaded.get_data()
        downloaded.close()
        self.assertEqual(content, b"hello from another device")

        stored_files = list(Path(self.app.config["UPLOAD_FOLDER"]).iterdir())
        self.assertEqual(len(stored_files), 1)
        denied_delete = self.app.test_client().delete(
            f"{room_path}/files/{file_record['id']}",
            environ_base={"REMOTE_ADDR": "203.0.113.14"},
        )
        self.assertEqual(denied_delete.status_code, 403)

        deleted = receiver.delete(
            f"{room_path}/files/{file_record['id']}",
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(receiver.get(
            f"{room_path}/files", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        ).get_json(), [])
        self.assertEqual(
            list(Path(self.app.config["UPLOAD_FOLDER"]).iterdir()), [])

    def test_same_private_lan_subnet_can_open_room(self):
        room_path = self.sender.post(
            "/share", environ_base={"REMOTE_ADDR": "192.168.1.20"}
        ).headers["Location"]

        response = self.app.test_client().get(
            room_path, environ_base={"REMOTE_ADDR": "192.168.1.42"}
        )

        self.assertEqual(response.status_code, 200)

    def test_home_page_has_create_and_join_actions(self):
        response = self.sender.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create transfer room", response.data)
        self.assertIn(b"Join a transfer", response.data)

    def test_join_accepts_a_pasted_room_link(self):
        room_path = self.sender.post(
            "/share", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        ).headers["Location"]

        response = self.sender.post(
            "/join",
            data={"code": room_path},
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith(
            room_path.rsplit("/", 1)[-1]))

    def test_app_startup_removes_expired_room_files(self):
        root = Path(self.temp_dir.name)
        room_path = self.sender.post(
            "/share", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        ).headers["Location"]
        uploaded = self.sender.post(
            f"{room_path}/upload",
            data={"files": (io.BytesIO(b"temporary"), "temporary.txt")},
            content_type="multipart/form-data",
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )
        self.assertEqual(uploaded.status_code, 201)
        with sqlite3.connect(root / "test.sqlite3") as db:
            db.execute("UPDATE rooms SET expires_at = 0")

        create_app(
            {
                "TESTING": True,
                "DATABASE": str(root / "test.sqlite3"),
                "UPLOAD_FOLDER": str(root / "uploads"),
            }
        )

        self.assertEqual(list((root / "uploads").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
