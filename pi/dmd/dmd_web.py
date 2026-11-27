# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""DMD virtuel (spécification § 9.9, 47e complément, sous-phase SR-d).

Page servie sur 127.0.0.1 seulement, qui affiche exactement les images passées
à la publication du service (copie interne, sans abonnement MQTT) :

    view = WebView(port)        port 0 : choisi par le système (tests)
    view.start()
    view.frame(octets)          image RGB565 de 8 192 octets, comme publiée
    view.message(sujet, contenu) dernier message du jeu, pour la ligne d'état
    view.close()

Flux par SSE (GET /events) : événements « frame » (JSON {"n", "b"} : numéro
et 8 192 octets en base64) et « msg » (JSON {"topic", "text"}). Chaque client
ne reçoit que la dernière image ; un client lent ne bloque jamais l'appelant.
Bibliothèque standard seule : ni Pillow ni paho.
"""

import base64
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import dmd_proto as D

log = logging.getLogger("dmd")

HOST = "127.0.0.1"                  # jamais une autre adresse (§ 9.9)
MAX_CLIENTS = 4
KEEPALIVE_S = 15                    # commentaire SSE : détecte les clients partis


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class WebView:
    def __init__(self, port):
        self._cond = threading.Condition()
        self._frame = None
        self._frame_no = 0
        self._msg = None
        self._msg_no = 0
        self._clients = 0
        self._closed = False
        self._thread = None
        self.server = _Server((HOST, port), _Handler)
        self.server.view = self

    @property
    def address(self):
        return self.server.server_address[:2]

    @property
    def clients(self):
        with self._cond:
            return self._clients

    def start(self):
        self._thread = threading.Thread(target=self.server.serve_forever, args=(0.1,), name="dmd-web", daemon=True)
        self._thread.start()
        log.info("DMD : virtual DMD on http://%s:%d/", *self.address)

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self.server.shutdown()
        self.server.server_close()

    def frame(self, data):
        """Copie de l'image publiée ; ne bloque pas, n'encode rien."""
        if len(data) != D.PIXELS_BYTES:
            raise ValueError("image de %d octets" % len(data))
        with self._cond:
            self._frame = bytes(data)
            self._frame_no += 1
            if self._clients:
                self._cond.notify_all()

    def message(self, topic, payload):
        if isinstance(payload, (bytes, bytearray)):
            payload = bytes(payload).decode("utf-8", "replace")
        with self._cond:
            self._msg = (topic, payload)
            self._msg_no += 1
            if self._clients:
                self._cond.notify_all()

    # --- côté client (fils du serveur) --------------------------------------------------------

    def _acquire(self):
        with self._cond:
            if self._clients >= MAX_CLIENTS or self._closed:
                return False
            self._clients += 1
            return True

    def _release(self):
        with self._cond:
            self._clients -= 1

    def _next_events(self, seen_frame, seen_msg, timeout):
        """Attend du nouveau ; renvoie (événements, n° image vu, n° message vu), ou None si fermé."""
        with self._cond:
            self._cond.wait_for(lambda: self._closed or self._frame_no != seen_frame
                                or self._msg_no != seen_msg, timeout)
            if self._closed:
                return None
            events = []
            if self._msg_no != seen_msg and self._msg is not None:
                topic, text = self._msg
                events.append(("msg", json.dumps({"topic": topic, "text": text})))
            frame, number = self._frame, self._frame_no
            seen_msg = self._msg_no
        if number != seen_frame and frame is not None:
            # Encodage hors du verrou : la publication n'attend jamais un client
            events.append(("frame", json.dumps({"n": number, "b": base64.b64encode(frame).decode("ascii")})))
        return events, number, seen_msg


class _Handler(BaseHTTPRequestHandler):
    server_version = "raspydarts-dmd"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        log.debug("DMD web : " + fmt, *args)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
        elif path == "/events":
            self._events()
        else:
            self._send(404, "text/plain; charset=utf-8", "introuvable\n".encode("utf-8"))

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        view = self.server.view
        if not view._acquire():
            self._send(503, "text/plain; charset=utf-8", "trop de clients\n".encode("utf-8"))
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.flush()
            seen_frame = seen_msg = 0
            while True:
                result = view._next_events(seen_frame, seen_msg, KEEPALIVE_S)
                if result is None:
                    return
                events, seen_frame, seen_msg = result
                chunk = "".join("event: %s\ndata: %s\n\n" % e for e in events) if events else ": ping\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except OSError:                     # client parti
            pass
        finally:
            view._release()


# Les textes venant du jeu passent par textContent, jamais par innerHTML (47e complément).
PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>DMD virtuel RaspyDarts</title>
<style>
  body { background: #111; color: #ccc; font: 14px system-ui, sans-serif; margin: 16px; }
  canvas { background: #000; image-rendering: pixelated; image-rendering: crisp-edges; display: block; }
  #bar, #last { margin-top: 10px; }
  #last { font-family: ui-monospace, monospace; white-space: pre-wrap; word-break: break-all; }
  select { margin-left: 4px; }
</style>
</head>
<body>
<canvas id="dmd" width="128" height="32"></canvas>
<div id="bar">
  <label>Agrandissement
    <select id="scale">
      <option value="4">×4</option>
      <option value="6" selected>×6</option>
      <option value="8">×8</option>
    </select>
  </label>
  · <span id="conn">connexion…</span>
  · <span id="fps">0</span> images/s
  · <span id="count">0</span> images reçues
  · dernière image il y a <span id="age">–</span>
</div>
<div id="last">Dernier message du jeu : <span id="msg">aucun</span></div>
<script>
"use strict";
const canvas = document.getElementById("dmd");
const ctx = canvas.getContext("2d");
const img = ctx.createImageData(128, 32);
const scale = document.getElementById("scale");
const text = (id, value) => { document.getElementById(id).textContent = value; };

function applyScale() {
  const s = Number(scale.value);
  canvas.style.width = (128 * s) + "px";
  canvas.style.height = (32 * s) + "px";
}
scale.addEventListener("change", applyScale);
applyScale();

// RGB565 petit-boutiste -> RGBA (bits répliqués, 0 -> 0 et 31 -> 255)
function draw(b64) {
  const raw = atob(b64);
  const px = img.data;
  for (let i = 0, j = 0; i < raw.length; i += 2, j += 4) {
    const v = raw.charCodeAt(i) | (raw.charCodeAt(i + 1) << 8);
    const r = v >> 11, g = (v >> 5) & 63, b = v & 31;
    px[j] = (r << 3) | (r >> 2);
    px[j + 1] = (g << 2) | (g >> 4);
    px[j + 2] = (b << 3) | (b >> 2);
    px[j + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
}

let received = 0, last = null, stamps = [];
function connect() {
  const es = new EventSource("events");
  es.onopen = () => text("conn", "connecté");
  es.onerror = () => text("conn", "déconnecté, nouvel essai…");
  es.addEventListener("frame", (e) => {
    const d = JSON.parse(e.data);
    draw(d.b);
    received += 1;
    last = performance.now();
    stamps.push(last);
    text("count", String(received));
  });
  es.addEventListener("msg", (e) => {
    const d = JSON.parse(e.data);
    text("msg", d.topic + " : " + d.text);
  });
}
setInterval(() => {
  const now = performance.now();
  stamps = stamps.filter((t) => now - t < 1000);
  text("fps", String(stamps.length));
  text("age", last === null ? "–" : ((now - last) / 1000).toFixed(1) + " s");
}, 250);
connect();
</script>
</body>
</html>
"""
