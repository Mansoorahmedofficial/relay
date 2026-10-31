import io
import ipaddress
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import qrcode
from qrcode.image.svg import SvgPathImage
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
from werkzeug.middleware.proxy_fix import ProxyFix


def create_app(test_config=None):
    app = Flask(__name__)
    max_upload_bytes = int(os.environ.get(
        "MAX_UPLOAD_BYTES",
        os.environ.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024 * 1024),
    ))
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
        DATABASE=os.environ.get("DATABASE", str(
            Path(app.instance_path) / "relay.sqlite3")),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", str(
            Path(app.instance_path) / "uploads")),
        MAX_UPLOAD_BYTES=max_upload_bytes,
        MAX_CONTENT_LENGTH=int(os.environ.get(
            "MAX_CONTENT_LENGTH", max_upload_bytes + 1024 * 1024)),
        SHARE_TTL_HOURS=int(os.environ.get("SHARE_TTL_HOURS", 24)),
        TRUST_PROXY=os.environ.get("TRUST_PROXY", "0") == "1",
    )
    if test_config:
        app.config.update(test_config)
        if (
            "MAX_CONTENT_LENGTH" in test_config
            and "MAX_UPLOAD_BYTES" not in test_config
        ):
            app.config["MAX_UPLOAD_BYTES"] = test_config["MAX_CONTENT_LENGTH"]
    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect_db():
        connection = sqlite3.connect(app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_db():
        with connect_db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    owner_ip TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                    stored_name TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    uploaded_at INTEGER NOT NULL
                );
                """
            )

    init_db()

    def client_ip():
        address = request.remote_addr
        if not address:
            return None
        try:
            parsed = ipaddress.ip_address(address)
            if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
                return str(parsed.ipv4_mapped)
            return str(parsed)
        except ValueError:
            return None

    def same_access_network(owner_ip, visitor_ip):
        if owner_ip == visitor_ip:
            return True
        try:
            owner = ipaddress.ip_address(owner_ip)
            visitor = ipaddress.ip_address(visitor_ip)
        except ValueError:
            return False
        if owner.version != visitor.version or not (owner.is_private and visitor.is_private):
            return False
        prefix = 24 if owner.version == 4 else 64
        return ipaddress.ip_network(f"{owner}/{prefix}", strict=False) == ipaddress.ip_network(
            f"{visitor}/{prefix}", strict=False
        )

    def cleanup_expired():
        now = int(time.time())
        with connect_db() as db:
            expired_files = db.execute(
                "SELECT files.stored_name FROM files JOIN rooms ON rooms.id = files.room_id "
                "WHERE rooms.expires_at <= ?",
                (now,),
            ).fetchall()
            db.execute(
                "DELETE FROM files WHERE room_id IN "
                "(SELECT id FROM rooms WHERE expires_at <= ?)",
                (now,),
            )
            db.execute("DELETE FROM rooms WHERE expires_at <= ?", (now,))
        for file in expired_files:
            (Path(app.config["UPLOAD_FOLDER"]) /
             file["stored_name"]).unlink(missing_ok=True)

    cleanup_expired()
    cleanup_lock = threading.Lock()
    last_cleanup = time.monotonic()

    @app.before_request
    def remove_expired_rooms():
        nonlocal last_cleanup
        if time.monotonic() - last_cleanup < 300:
            return
        with cleanup_lock:
            if time.monotonic() - last_cleanup >= 300:
                cleanup_expired()
                last_cleanup = time.monotonic()

    def get_room(room_id):
        with connect_db() as db:
            room = db.execute(
                "SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if room is None:
            abort(404)
        if room["expires_at"] <= int(time.time()):
            abort(410, description="This transfer has expired.")
        if not same_access_network(room["owner_ip"], client_ip()):
            abort(
                403, description="This transfer is only available from the creator's network.")
        return room

    @app.get("/")
    def home():
        visitor_ip = client_ip()
        with connect_db() as db:
            rooms = db.execute(
                "SELECT id, created_at, expires_at, owner_ip FROM rooms "
                "WHERE expires_at > ? ORDER BY created_at DESC",
                (int(time.time()),),
            ).fetchall()
        available_rooms = [
            dict(room)
            for room in rooms
            if visitor_ip and same_access_network(room["owner_ip"], visitor_ip)
        ]
        return render_template(
            "index.html",
            max_upload_gb=app.config["MAX_UPLOAD_BYTES"] / (1024 ** 3),
            share_ttl_hours=app.config["SHARE_TTL_HOURS"],
            available_rooms=available_rooms,
        )

    @app.post("/share")
    def create_room():
        owner_ip = client_ip()
        if owner_ip is None:
            abort(400, description="Your network address could not be detected.")
        now = int(time.time())
        expires_at = now + app.config["SHARE_TTL_HOURS"] * 60 * 60
        with connect_db() as db:
            for _ in range(10):
                room_id = str(secrets.randbelow(900000) + 100000)
                try:
                    db.execute(
                        "INSERT INTO rooms (id, owner_ip, created_at, expires_at) "
                        "VALUES (?, ?, ?, ?)",
                        (room_id, owner_ip, now, expires_at),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                abort(503, description="A unique room code could not be generated.")
        return redirect(url_for("room", room_id=room_id))

    @app.post("/join")
    def join_room():
        room_id = request.form.get("code", "").strip()
        if not re.fullmatch(r"[0-9]{6}", room_id):
            return redirect(url_for("home", invalid=1))
        return redirect(url_for("room", room_id=room_id))

    @app.get("/r/<room_id>")
    def room(room_id):
        get_room(room_id)
        return render_template(
            "room.html",
            room_id=room_id,
            expires_at=room_expiry(room_id),
            max_upload_mb=app.config["MAX_UPLOAD_BYTES"] // (1024 * 1024),
            max_upload_gb=app.config["MAX_UPLOAD_BYTES"] / (1024 ** 3),
        )

    @app.get("/r/<room_id>/qr")
    def room_qr(room_id):
        get_room(room_id)
        room_url = request.url_root.rstrip("/") + url_for(
            "room", room_id=room_id
        )
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=4,
        )
        qr.add_data(room_url)
        qr.make(fit=True)
        image = qr.make_image(image_factory=SvgPathImage)
        output = io.BytesIO()
        image.save(output)
        output.seek(0)
        response = send_file(
            output,
            mimetype="image/svg+xml",
            download_name=f"room-{room_id}-qr.svg",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def room_expiry(room_id):
        with connect_db() as db:
            row = db.execute(
                "SELECT expires_at FROM rooms WHERE id = ?", (room_id,)).fetchone()
        return row["expires_at"]

    @app.get("/r/<room_id>/files")
    def list_files(room_id):
        get_room(room_id)
        with connect_db() as db:
            files = db.execute(
                "SELECT id, original_name, size, content_type, uploaded_at FROM files "
                "WHERE room_id = ? ORDER BY uploaded_at DESC",
                (room_id,),
            ).fetchall()
        return jsonify([dict(file) for file in files])

    @app.post("/r/<room_id>/upload")
    def upload_files(room_id):
        get_room(room_id)
        incoming = request.files.getlist("files")
        if not incoming or all(not item.filename for item in incoming):
            return jsonify(error="Choose at least one file."), 400
        if len(incoming) > 30:
            return jsonify(error="Upload up to 30 files at a time."), 400

        created = []
        try:
            for item in incoming:
                if not item.filename:
                    continue
                file_id = secrets.token_urlsafe(12)
                stored_name = secrets.token_hex(20)
                destination = Path(app.config["UPLOAD_FOLDER"]) / stored_name
                item.save(destination)
                size = destination.stat().st_size
                original_name = Path(item.filename).name[:255]
                content_type = item.mimetype or "application/octet-stream"
                created.append(
                    (file_id, stored_name, original_name, size, content_type))

            with connect_db() as db:
                db.executemany(
                    "INSERT INTO files "
                    "(id, room_id, stored_name, original_name, size, content_type, uploaded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (file_id, room_id, stored, name,
                         size, mime, int(time.time()))
                        for file_id, stored, name, size, mime in created
                    ],
                )
        except Exception:
            for _, stored_name, *_ in created:
                (Path(app.config["UPLOAD_FOLDER"]) /
                 stored_name).unlink(missing_ok=True)
            raise

        return jsonify({"uploaded": len(created)}), 201

    @app.get("/r/<room_id>/download/<file_id>")
    def download_file(room_id, file_id):
        get_room(room_id)
        with connect_db() as db:
            file = db.execute(
                "SELECT stored_name, original_name FROM files WHERE id = ? AND room_id = ?",
                (file_id, room_id),
            ).fetchone()
        if file is None:
            abort(404)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            file["stored_name"],
            as_attachment=True,
            download_name=file["original_name"],
        )

    @app.delete("/r/<room_id>/files/<file_id>")
    def delete_file(room_id, file_id):
        get_room(room_id)
        with connect_db() as db:
            file = db.execute(
                "SELECT stored_name FROM files WHERE id = ? AND room_id = ?",
                (file_id, room_id),
            ).fetchone()
        if file is None:
            abort(404)
        (Path(app.config["UPLOAD_FOLDER"]) / file["stored_name"]).unlink(
            missing_ok=True
        )
        with connect_db() as db:
            db.execute(
                "DELETE FROM files WHERE id = ? AND room_id = ?",
                (file_id, room_id),
            )
        return jsonify(deleted=True)

    @app.post("/r/<room_id>/delete")
    def delete_room(room_id):
        get_room(room_id)
        with connect_db() as db:
            files = db.execute(
                "SELECT stored_name FROM files WHERE room_id = ?", (room_id,)
            ).fetchall()
            db.execute("DELETE FROM files WHERE room_id = ?", (room_id,))
            db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        for file in files:
            (Path(app.config["UPLOAD_FOLDER"]) /
             file["stored_name"]).unlink(missing_ok=True)
        return redirect(url_for("home"))

    @app.errorhandler(413)
    def request_too_large(_error):
        return jsonify(error="That upload is larger than the allowed transfer size."), 413

    @app.errorhandler(403)
    def forbidden(error):
        if request.path.startswith("/r/"):
            return render_template("denied.html", message=error.description), 403
        return error

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(
        os.environ.get("PORT", "5000")), threaded=True)
