# Relay

Relay is a Python web app for moving files between devices. It creates private transfer rooms, lets you upload and download files in a browser, and only allows access from the public IP address that created each room.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000` for local-only use. To transfer to another device on the same Wi-Fi, use the host computer's LAN address (for example, `http://192.168.1.20:5000`) from both the host browser and the receiving device. The host firewall must allow inbound connections on port 5000.

## How access works

The room link is a random, hard-to-guess identifier, not a user account. For public IP addresses, the server checks that every room visit, upload, and download comes from the exact address that created the room. When directly hosting on a private LAN, devices in the same local subnet are also allowed because each device has its own private IP. VPNs, cellular connections, guest networks, and some corporate networks can make addresses differ.

If deployed behind exactly one trusted reverse proxy, set `TRUST_PROXY=1` so the app reads the forwarded client address. Only enable this when the proxy overwrites forwarded-address headers; otherwise clients may spoof their IP. For direct deployments, leave it disabled.

Rooms expire after 24 hours and expired files are cleaned up periodically. Configure `SHARE_TTL_HOURS`, `MAX_CONTENT_LENGTH` (bytes), `UPLOAD_FOLDER`, `DATABASE`, `SECRET_KEY`, and `PORT` with environment variables as needed. The default maximum request size is 2 GiB.

## Tests

```bash
python -m unittest discover -v
```