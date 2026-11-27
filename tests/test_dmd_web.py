# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du DMD virtuel (§ 9.9, 47e complément, sous-phase SR-d).

Vraie socket sur 127.0.0.1, port choisi par le système ; ni Pillow ni paho.
Le JavaScript de la page n'est pas exécuté (pas de navigateur) : seule la
conversion RGB565 qu'il fait est vérifiée, en Python, sur un vecteur.
"""

import base64
import http.client
import inspect
import json
import socket
import time
import unittest

import _paths  # noqa: F401
import dmd_proto as D
import dmd_web as W


def frame_of(value):
    return bytes([value & 0xFF, (value >> 8) & 0xFF]) * (D.PIXELS_BYTES // 2)


class Client:
    """Lecteur SSE minimal."""

    def __init__(self, view, path="/events"):
        host, port = view.address
        self.conn = http.client.HTTPConnection(host, port, timeout=5)
        self.conn.request("GET", path)
        self.resp = self.conn.getresponse()

    def event(self):
        name, data = None, []
        while True:
            line = self.resp.readline().decode("utf-8")
            if line == "":
                raise EOFError
            line = line.rstrip("\n")
            if line == "":
                if name is not None:
                    return name, json.loads("\n".join(data))
                continue
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])

    def event_named(self, wanted):
        while True:
            name, data = self.event()
            if name == wanted:
                return data

    def close(self):
        self.resp.close()                   # la réponse garde la socket ouverte sinon
        self.conn.close()


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class WebTest(unittest.TestCase):
    def setUp(self):
        self.view = W.WebView(0)
        self.view.start()
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.view.close()

    def client(self, path="/events"):
        c = Client(self.view, path)
        self.clients.append(c)
        return c

    def test_listens_on_loopback_only(self):
        self.assertEqual(W.HOST, "127.0.0.1")
        self.assertEqual(self.view.server.socket.getsockname()[0], "127.0.0.1")
        self.assertEqual(self.view.address[0], "127.0.0.1")
        # Pas d'option pour changer l'adresse
        self.assertEqual(list(inspect.signature(W.WebView).parameters), ["port"])
        # Une autre adresse de la machine n'est pas servie
        try:
            others = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        except OSError:
            others = set()
        for addr in others - {"127.0.0.1"}:
            if addr.startswith("127."):
                continue
            with self.assertRaises(OSError):
                socket.create_connection((addr, self.view.address[1]), timeout=1).close()

    def test_page(self):
        c = self.client("/")
        self.assertEqual(c.resp.status, 200)
        body = c.resp.read().decode("utf-8")
        self.assertIn("<canvas", body)
        self.assertIn('value="6" selected', body)
        self.assertIn("pixelated", body)
        self.assertIn("textContent", body)
        self.assertNotIn("innerHTML", body)          # textes du jeu jamais en HTML
        self.assertNotIn("outerHTML", body)

    def test_unknown_path(self):
        self.assertEqual(self.client("/rien").resp.status, 404)

    def test_frame_bytes_identical(self):
        data = bytes(range(256)) * (D.PIXELS_BYTES // 256)
        c = self.client()
        self.assertEqual(c.resp.status, 200)
        self.assertEqual(c.resp.getheader("Content-Type"), "text/event-stream")
        self.assertTrue(wait_for(lambda: self.view.clients == 1))
        self.view.frame(data)
        event = c.event_named("frame")
        self.assertEqual(base64.b64decode(event["b"]), data)
        self.assertEqual(event["n"], 1)

    def test_bytearray_is_copied(self):
        data = bytearray(frame_of(0xF800))
        self.view.frame(data)
        data[:] = frame_of(0x001F)
        event = self.client().event_named("frame")
        self.assertEqual(base64.b64decode(event["b"]), frame_of(0xF800))

    def test_new_client_gets_last_frame_and_message(self):
        self.view.frame(frame_of(0x07E0))
        self.view.message("raspydarts/dmd", "msg|<b>Bonjour</b> é".encode("utf-8"))
        c = self.client()
        events = dict([c.event(), c.event()])
        self.assertEqual(events["msg"], {"topic": "raspydarts/dmd", "text": "msg|<b>Bonjour</b> é"})
        self.assertEqual(base64.b64decode(events["frame"]["b"]), frame_of(0x07E0))

    def test_message_not_utf8(self):
        self.view.message("raspy2dmd", b"\xff")
        self.assertEqual(self.client().event_named("msg")["text"], "�")

    def test_only_last_frame_per_client(self):
        c = self.client()
        self.assertTrue(wait_for(lambda: self.view.clients == 1))
        with self.view._cond:               # le client est bloqué : trois images arrivent
            for v in (1, 2, 3):
                self.view._frame = frame_of(v)
                self.view._frame_no += 1
            self.view._cond.notify_all()
        event = c.event_named("frame")
        self.assertEqual(event["n"], 3)
        self.assertEqual(base64.b64decode(event["b"]), frame_of(3))

    def test_frame_never_blocks(self):
        c = self.client()
        self.assertTrue(wait_for(lambda: self.view.clients == 1))
        start = time.monotonic()
        for i in range(2000):               # le client ne lit pas
            self.view.frame(frame_of(i))
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(self.view._frame_no, 2000)
        c.close()

    def test_no_client(self):
        self.assertEqual(self.view.clients, 0)
        self.view.frame(frame_of(1))
        self.assertEqual(self.view._frame_no, 1)

    def test_bad_frame_size(self):
        with self.assertRaises(ValueError):
            self.view.frame(b"\x00" * 10)

    def test_client_limit(self):
        for _ in range(W.MAX_CLIENTS):
            self.assertEqual(self.client().resp.status, 200)
        self.assertTrue(wait_for(lambda: self.view.clients == W.MAX_CLIENTS))
        self.assertEqual(self.client().resp.status, 503)

    def test_client_slot_released(self):
        c = self.client()
        self.assertTrue(wait_for(lambda: self.view.clients == 1))
        c.close()
        self.clients.remove(c)
        # Les écritures vers le client parti échouent : la place est rendue
        self.assertTrue(wait_for(lambda: self.view.frame(frame_of(1)) or self.view.clients == 0))


class ColourTest(unittest.TestCase):
    """Même conversion RGB565 -> RGB que le JavaScript de la page."""

    @staticmethod
    def expand(v):
        r, g, b = v >> 11, (v >> 5) & 63, v & 31
        return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)

    def test_vector(self):
        self.assertEqual(self.expand(D.rgb565(255, 255, 255)), (255, 255, 255))
        self.assertEqual(self.expand(D.rgb565(0, 0, 0)), (0, 0, 0))
        self.assertEqual(self.expand(D.rgb565(255, 0, 0)), (255, 0, 0))
        self.assertEqual(self.expand(D.rgb565(0, 255, 0)), (0, 255, 0))
        self.assertEqual(self.expand(D.rgb565(0, 0, 255)), (0, 0, 255))
        self.assertEqual(self.expand(D.rgb565(128, 128, 128)), (132, 130, 132))

    def test_page_uses_same_formula(self):
        for expr in ("(r << 3) | (r >> 2)", "(g << 2) | (g >> 4)", "(b << 3) | (b >> 2)",
                     "raw.charCodeAt(i) | (raw.charCodeAt(i + 1) << 8)"):
            self.assertIn(expr, W.PAGE)


if __name__ == "__main__":
    unittest.main()
