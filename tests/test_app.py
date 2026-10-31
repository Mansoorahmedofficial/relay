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
                "MAX_UPLOAD_BYTES": 1024 * 1024,
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
        self.assertRegex(room_path.rsplit("/", 1)[-1], r"^[0-9]{6}$")

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

    def test_room_page_can_generate_a_network_restricted_qr_code(self):
        room_path = self.sender.post(
            "/share", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        ).headers["Location"]

        page = self.sender.get(
            room_path, environ_base={"REMOTE_ADDR": "198.51.100.8"}
        )
        self.assertIn(b'id="copy-link"', page.data)
        self.assertIn(b"Copy link", page.data)
        self.assertIn(b"Generate QR code", page.data)
        self.assertIn(b"Download QR code", page.data)

        qr_response = self.sender.get(
            f"{room_path}/qr", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        )
        self.assertEqual(qr_response.status_code, 200)
        self.assertEqual(qr_response.mimetype, "image/svg+xml")
        self.assertEqual(qr_response.headers["Cache-Control"], "no-store")
        self.assertIn(b"<svg", qr_response.data)

        denied = self.app.test_client().get(
            f"{room_path}/qr", environ_base={"REMOTE_ADDR": "203.0.113.14"}
        )
        self.assertEqual(denied.status_code, 403)

    def test_home_page_has_create_and_join_actions(self):
        response = self.sender.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create transfer room", response.data)
        self.assertIn(b"Join a transfer", response.data)

    def test_join_accepts_a_six_digit_room_code(self):
        room_path = self.sender.post(
            "/share", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        ).headers["Location"]
        room_code = room_path.rsplit("/", 1)[-1]

        response = self.sender.post(
            "/join",
            data={"code": room_code},
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith(room_code))

        invalid = self.sender.post(
            "/join",
            data={"code": "12345"},
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )
        self.assertIn("invalid=1", invalid.headers["Location"])

    def test_home_lists_rooms_on_network_and_room_can_be_deleted(self):
        room_path = self.sender.post(
            "/share", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        ).headers["Location"]
        other_network_room = self.app.test_client().post(
            "/share", environ_base={"REMOTE_ADDR": "203.0.113.14"}
        ).headers["Location"]
        uploaded = self.sender.post(
            f"{room_path}/upload",
            data={"files": (io.BytesIO(b"temporary"), "temporary.txt")},
            content_type="multipart/form-data",
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )
        self.assertEqual(uploaded.status_code, 201)

        home = self.sender.get(
            "/", environ_base={"REMOTE_ADDR": "198.51.100.8"})
        self.assertIn(room_path.rsplit("/", 1)[-1].encode(), home.data)
        self.assertNotIn(
            other_network_room.rsplit("/", 1)[-1].encode(), home.data
        )
        self.assertIn(b"Delete", home.data)

        denied = self.app.test_client().post(
            f"{room_path}/delete",
            environ_base={"REMOTE_ADDR": "203.0.113.14"},
        )
        self.assertEqual(denied.status_code, 403)

        deleted = self.sender.post(
            f"{room_path}/delete",
            environ_base={"REMOTE_ADDR": "198.51.100.8"},
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertEqual(
            list(Path(self.app.config["UPLOAD_FOLDER"]).iterdir()), [])
        refreshed_home = self.sender.get(
            "/", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        )
        self.assertNotIn(room_path.rsplit("/", 1)[-1].encode(), refreshed_home.data)

    def test_default_max_upload_size_is_10_gib(self):
        root = Path(self.temp_dir.name)
        app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(root / "default.sqlite3"),
                "UPLOAD_FOLDER": str(root / "default-uploads"),
            }
        )

        self.assertEqual(app.config["MAX_UPLOAD_BYTES"], 10 * 1024 ** 3)
        self.assertEqual(
            app.config["MAX_CONTENT_LENGTH"], 10 * 1024 ** 3 + 1024 * 1024
        )
        response = app.test_client().get(
            "/", environ_base={"REMOTE_ADDR": "198.51.100.8"}
        )
        self.assertIn(b"Up to 10 GB", response.data)

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
