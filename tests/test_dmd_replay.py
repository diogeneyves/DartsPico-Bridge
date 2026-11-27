# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du mode rejeu et de l'enregistreur du DMD (§ 9.9, 47e complément, sous-phase SR-d).

Horloge simulée, faux rendu (sans Pillow), faux paho (fake_mqtt). Le rejeu avec
le vrai rendu est sauté si Pillow manque.
"""

import io
import os
import tempfile
import unittest
from pathlib import Path

import _paths  # noqa: F401
import dmd_core as C
import dmd_proto as D
import dmd_record as R
import dmd_replay as P
import dmd_service as S
import dmd_web as W
from fake_mqtt import FakeBroker, make_module
from test_dmd_service import Clock, FakeRenderer
from test_dmd_web import Client

try:
    import dmd_render
except ImportError:                         # Pillow absent
    dmd_render = None

EXAMPLE = Path(_paths.ROOT) / "pi" / "dmd" / "replay_example.txt"


class ParseTest(unittest.TestCase):
    def test_lines(self):
        entries = P.parse([
            "# commentaire\n",
            "\n",
            "   \n",
            "0 raspydarts/dmd msg|Bonjour tout le monde\n",
            "1.5 raspy2dmd pico|test_rgb\r\n",
            "2 raspydarts/dmd\n",
            "0.25 raspydarts/dmd msg|é \n",
        ])
        self.assertEqual(entries, [
            P.Entry(0.0, "raspydarts/dmd", b"msg|Bonjour tout le monde"),
            P.Entry(1.5, "raspy2dmd", b"pico|test_rgb"),
            P.Entry(2.0, "raspydarts/dmd", b""),
            P.Entry(0.25, "raspydarts/dmd", "msg|é ".encode("utf-8")),
        ])

    def test_invalid_lines_logged_once_and_skipped(self):
        with self.assertLogs("dmd", "WARNING") as logs:
            entries = P.parse(["x raspydarts/dmd msg|a", "-1 raspydarts/dmd msg|b", "nan t m",
                               "inf t m", "3", "1  msg|c", "1 raspydarts/dmd msg|ok"])
        self.assertEqual(entries, [P.Entry(1.0, "raspydarts/dmd", b"msg|ok")])
        self.assertEqual(len(logs.output), 6)
        self.assertIn("line 1 ", logs.output[0])

    def test_example_log(self):
        entries = P.load(EXAMPLE)
        self.assertGreaterEqual(len(entries), 8)
        kinds = [type(C.parse(e.topic, e.payload)).__name__ for e in entries]
        self.assertNotIn("Invalid", kinds)
        self.assertEqual(set(e.topic for e in entries), set(C.TOPICS))
        for kind in ("TextMessage", "ScoreMessage", "PatternMessage", "AttractMessage", "Ignored"):
            self.assertIn(kind, kinds)


class ReplayerTest(unittest.TestCase):
    def setUp(self):
        self.clk = Clock(1_000_000)
        self.entries = [P.Entry(0.5, "a", b"1"), P.Entry(1.0, "a", b"2"), P.Entry(0, "a", b"3")]

    def test_delays(self):
        r = P.Replayer(self.entries, self.clk)
        self.assertEqual(r.wait_us(), 500_000)
        self.assertEqual(r.due(), [])
        self.clk.t += 500_000
        self.assertEqual(r.due(), [("a", b"1")])
        self.assertEqual(r.wait_us(), 1_000_000)
        self.clk.t += 999_999
        self.assertEqual(r.due(), [])
        self.clk.t += 1
        self.assertEqual(r.due(), [("a", b"2"), ("a", b"3")])
        self.assertTrue(r.finished)
        self.assertIsNone(r.wait_us())
        self.clk.t += 10_000_000
        self.assertEqual(r.due(), [])

    def test_late_call_keeps_schedule(self):
        r = P.Replayer(self.entries, self.clk)
        self.clk.t += 1_600_000
        self.assertEqual(r.due(), [("a", b"1"), ("a", b"2"), ("a", b"3")])

    def test_speed(self):
        r = P.Replayer(self.entries, self.clk, speed=4)
        self.assertEqual(r.wait_us(), 125_000)
        self.clk.t += 125_000
        r.due()
        self.assertEqual(r.wait_us(), 250_000)
        with self.assertRaises(ValueError):
            P.Replayer(self.entries, self.clk, speed=0)

    def test_loop(self):
        r = P.Replayer(self.entries, self.clk, loop=True)
        self.clk.t += 1_500_000
        self.assertEqual(len(r.due()), 3)
        self.assertFalse(r.finished)
        self.assertEqual(r.wait_us(), 500_000)
        self.clk.t += 500_000
        self.assertEqual(r.due(), [("a", b"1")])
        self.assertEqual(r.played, 4)

    def test_loop_zero_delays_does_not_hang(self):
        r = P.Replayer([P.Entry(0, "a", b"x")] * 3, self.clk, loop=True)
        self.assertEqual(len(r.due()), 3)

    def test_empty(self):
        r = P.Replayer([], self.clk, loop=True)
        self.assertTrue(r.finished)
        self.assertEqual(r.due(), [])


class ArgsTest(unittest.TestCase):
    def test_ports(self):
        self.assertEqual(S.parse_args([]).web_port, 8081)
        self.assertEqual(S.parse_args(["--replay", "j.txt"]).web_port, 8082)
        self.assertEqual(S.parse_args(["--replay", "j.txt", "--web-port", "9000"]).web_port, 9000)
        self.assertEqual(S.parse_args(["--web-port", "0"]).web_port, 0)

    def test_errors(self):
        for argv in (["--replay", "j", "--speed", "0"], ["--loop"], ["--speed", "2"], ["--web-port", "70000"]):
            with self.assertRaises(SystemExit), _quiet():
                S.parse_args(argv)


class _quiet:
    def __enter__(self):
        import contextlib
        self._ctx = contextlib.redirect_stderr(io.StringIO())
        return self._ctx.__enter__()

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


class RunReplayTest(unittest.TestCase):
    """dmd_service.run en rejeu : jamais de MQTT, images copiées vers le DMD virtuel."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("0.2 raspydarts/dmd msg|Un\n0.5 raspy2dmd pico|test_rgb\n")

    def tearDown(self):
        os.unlink(self.path)

    def run_service(self, argv, renderer, until_us, web_factory=None):
        clk = Clock(1_000_000)
        end = clk.t + until_us

        def no_mqtt(*a, **k):
            raise AssertionError("MQTT ouvert en rejeu")

        def sleep(s):
            clk.t += max(1000, int(s * 1e6))

        args = S.parse_args(argv)
        return S.run(args, renderer, link_factory=no_mqtt, web_factory=web_factory, clock=clk,
                     sleep=sleep, stop=lambda: clk.t >= end, time_synced=lambda: False)

    def test_replay_without_mqtt(self):
        renderer = FakeRenderer()
        service = self.run_service(["--replay", self.path, "--web-port", "0"], renderer, 2_000_000)
        names = [s.name for s in renderer.scenes]
        self.assertEqual(names[:3], ["Welcome", "ShowText:Un", "ShowPattern"])
        self.assertGreater(service.published, 2)

    def test_replay_feeds_web_view(self):
        views = []

        class SpyView:
            def __init__(self, port):
                self.port, self.frames, self.messages, self.closed = port, [], [], False
                views.append(self)

            def start(self):
                pass

            def frame(self, data):
                self.frames.append(data)

            def message(self, topic, payload):
                self.messages.append((topic, payload))

            def close(self):
                self.closed = True

        service = self.run_service(["--replay", self.path], FakeRenderer(), 1_000_000, web_factory=SpyView)
        view = views[0]
        self.assertEqual(view.port, 8082)
        self.assertEqual(len(view.frames), service.published)
        self.assertEqual(view.messages, [("raspydarts/dmd", b"msg|Un"), ("raspy2dmd", b"pico|test_rgb")])
        self.assertTrue(view.closed)

    def test_live_mode_tees_frames(self):
        broker = FakeBroker()
        module = make_module(broker)
        views = []

        def view_factory(port):
            v = W.WebView(0)
            views.append(v)
            return v

        renderer = FakeRenderer()
        args = S.parse_args([])
        clk = Clock(1_000_000)
        published = []

        def link_factory(host, port):
            link = S.MqttLink(host, port, mqtt_module=module)
            original = link.publish_frame

            def spy(frame):
                published.append(frame)
                original(frame)
            link.publish_frame = spy
            broker.pump()
            return link

        game = module.Client(module.CallbackAPIVersion.VERSION2)
        game.connect_async("127.0.0.1")
        state = {"n": 0}

        def sleep(s):
            clk.t += max(1000, int(s * 1e6))
            state["n"] += 1
            if state["n"] == 5:
                game.publish("raspydarts/dmd", "msg|Direct")
            broker.pump()

        holder = {}

        def stop():
            if views and "client" not in holder:
                holder["client"] = Client(views[0])
            return clk.t >= 3_000_000

        S.run(args, renderer, link_factory=link_factory, web_factory=view_factory, clock=clk,
              sleep=sleep, stop=stop, time_synced=lambda: False)
        holder["client"].close()
        self.assertIn("ShowText:Direct", [s.name for s in renderer.scenes])
        self.assertTrue(published)
        self.assertEqual(views[0]._frame, published[-1])       # mêmes octets que publiés
        self.assertEqual(views[0]._msg, ("raspydarts/dmd", "msg|Direct"))

    @unittest.skipIf(dmd_render is None, "Pillow absent")
    def test_example_with_real_renderer(self):
        renderer = dmd_render.Renderer("/nonexistent")
        views = []

        def view_factory(port):
            v = W.WebView(0)
            views.append(v)
            return v

        service = self.run_service(["--replay", str(EXAMPLE), "--speed", "10"], renderer, 4_000_000,
                                   web_factory=view_factory)
        self.assertGreater(service.published, 10)
        self.assertEqual(len(views[0]._frame), D.PIXELS_BYTES)


class RecorderTest(unittest.TestCase):
    def test_same_topics_as_core(self):
        self.assertEqual(tuple(R.TOPICS), tuple(C.TOPICS))

    def test_lines(self):
        t = [10.0]
        out = io.StringIO()
        rec = R.Recorder(out, clock=lambda: t[0])
        rec.record("raspydarts/dmd", b"msg|Bonjour le monde")
        t[0] += 1.25
        rec.record("raspy2dmd", b"pico|test_rgb")
        t[0] += 0.0004
        rec.record("raspydarts/dmd", b"")
        self.assertEqual(out.getvalue(),
                         "0.000 raspydarts/dmd msg|Bonjour le monde\n"
                         "1.250 raspy2dmd pico|test_rgb\n"
                         "0.000 raspydarts/dmd \n")
        self.assertEqual(rec.count, 3)

    def test_bad_content_logged_once(self):
        out = io.StringIO()
        rec = R.Recorder(out, clock=lambda: 0.0)
        with self.assertLogs("dmd_record", "WARNING") as logs:
            rec.record("raspydarts/dmd", b"msg|a\xff")
            rec.record("raspydarts/dmd", b"msg|b\xfe")
            rec.record("raspydarts/dmd", b"msg|deux\nlignes\r\nici")
            rec.record("raspydarts/dmd", b"msg|x\ny")
        self.assertEqual(len(logs.output), 2)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "0.000 raspydarts/dmd msg|a�")
        self.assertEqual(lines[2], "0.000 raspydarts/dmd msg|deux lignes ici")

    def test_round_trip(self):
        broker = FakeBroker()
        module = make_module(broker)
        t = [0.0]
        out = io.StringIO()
        client = R.connect(R.Recorder(out, clock=lambda: t[0]), mqtt_module=module)
        broker.pump()
        self.assertIn(("subscribe", [("raspydarts/dmd", 0), ("raspy2dmd", 0)]), client.calls)
        game = module.Client(module.CallbackAPIVersion.VERSION2)
        game.connect_async("127.0.0.1")
        broker.pump()
        game.publish("raspydarts/dmd", "msg|Partie à 501")
        broker.pump()
        t[0] = 2.0
        game.publish("raspy2dmd", "demo|gif")
        game.publish("raspydarts/hw/cmd", "OFF")          # autre sujet : pas enregistré
        broker.pump()
        entries = P.parse(io.StringIO(out.getvalue()))
        self.assertEqual(entries, [P.Entry(0.0, "raspydarts/dmd", "msg|Partie à 501".encode("utf-8")),
                                   P.Entry(2.0, "raspy2dmd", b"demo|gif")])


if __name__ == "__main__":
    unittest.main()
