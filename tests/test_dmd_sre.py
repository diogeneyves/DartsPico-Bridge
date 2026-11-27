# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests de la sous-phase SR-e du service de rendu (§ 9.9, 50e complément) : configuration
(dmd.toml), options, événement d'arrêt et journaux (démarrage, arrêt et sa cause, décodage
et sa durée, NTP), journal d'arrêt de hwd, unités systemd, script d'installation (lu, jamais
lancé : il utilise SSH), journal d'exemple du rejeu.

Tout code minuté est testé avec une horloge qui avance. Le rendu Pillow est sauté s'il manque.
"""

import configparser
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

import _paths
import dmd_config as K
import dmd_core as C
import dmd_replay as P
import dmd_service as S

try:
    import dmd_render
except ImportError:                         # Pillow absent
    dmd_render = None

ROOT = Path(_paths.ROOT)
SEC = 1_000_000


class Clock:
    def __init__(self, t=1_000_000):
        self.t = t

    def __call__(self):
        return self.t


def advancing_sleep(clk):
    """sleep() du service : fait avancer l'horloge simulée (au moins 1 ms par appel)."""
    def sleep(s):
        clk.t += max(1000, int(s * 1e6))
    return sleep


class FakeScene:
    animated = False

    def next_frame(self, now_us=None):
        return bytes(8192)


class FakeRenderer:
    def __init__(self):
        self.commands = []

    def scene(self, command):
        self.commands.append(command)
        return FakeScene()


def write(folder, name, text):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def toml(self, text):
        return write(self.tmp.name, "dmd.toml", text)

    def test_defaults(self):
        cfg = K.DmdConfig().validate()
        self.assertEqual((cfg.dmd_media_dir, cfg.dmd_special_moves, cfg.attract_delay_s,
                          cfg.attract_gif_max_s, cfg.attract_clock_every_s, cfg.web_port,
                          cfg.mqtt_host, cfg.mqtt_port),
                         ("~/dmd-medias", 1, 15, 60, 120, 8081, "127.0.0.1", 1883))

    def test_example_file_holds_the_defaults(self):
        cfg = K.load(ROOT / "pi" / "dmd" / "dmd.example.toml")
        self.assertEqual(cfg, K.DmdConfig())
        text = (ROOT / "pi" / "dmd" / "dmd.example.toml").read_text(encoding="utf-8")
        for key in K.KEYS:
            self.assertIn("\n%s = " % key, text)

    def test_unknown_key_refused(self):
        with self.assertRaisesRegex(ValueError, "clés inconnues.*attract_gifs_max_s"):
            K.load(self.toml("attract_gifs_max_s = 60\n"))

    def test_ranges_and_types(self):
        bad = ["dmd_special_moves = 2", "attract_delay_s = 0", "attract_gif_max_s = 4",
               "attract_gif_max_s = 601", "attract_clock_every_s = 9", "web_port = 65536",
               "mqtt_port = 0", 'mqtt_host = ""', "dmd_media_dir = 3", "attract_delay_s = 1.5",
               "attract_delay_s = true", 'web_port = "8081"']
        for line in bad:
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    K.load(self.toml(line + "\n"))

    def test_special_moves_accepts_booleans(self):
        self.assertEqual(K.load(self.toml("dmd_special_moves = false\n")).dmd_special_moves, False)
        self.assertEqual(K.load(self.toml("dmd_special_moves = 0\n")).dmd_special_moves, 0)

    def test_invalid_toml(self):
        with self.assertRaisesRegex(ValueError, "TOML invalide"):
            K.load(self.toml("web_port = \n"))


class ParseArgsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = write(self.tmp.name, "dmd.toml",
                         'dmd_media_dir = "/m"\ndmd_special_moves = 0\nattract_delay_s = 20\n'
                         'attract_gif_max_s = 45\nattract_clock_every_s = 300\nweb_port = 9001\n'
                         'mqtt_host = "broker"\nmqtt_port = 1884\n')

    def test_without_config(self):
        args = S.parse_args([])
        self.assertEqual((args.media_dir, args.special_moves, args.attract_delay_s,
                          args.attract_gif_max_s, args.attract_clock_every_s, args.web_port,
                          args.mqtt_host, args.mqtt_port),
                         (os.path.expanduser("~/dmd-medias"), True, 15, 60, 120, 8081, "127.0.0.1",
                          1883))

    def test_media_dir_tilde_expanded_for_the_service_user(self):
        """Complément 77 : ~ développé pour l'utilisateur qui lance le service (HOME)."""
        cfg = write(self.tmp.name, "tilde.toml", 'dmd_media_dir = "~/medias-du-fichier"\n')
        with unittest.mock.patch.dict(os.environ, {"HOME": "/home/joueur"}):
            self.assertEqual(S.parse_args([]).media_dir, "/home/joueur/dmd-medias")
            self.assertEqual(S.parse_args(["--config", cfg]).media_dir, "/home/joueur/medias-du-fichier")
            self.assertEqual(S.parse_args(["--media-dir", "~/m"]).media_dir, "/home/joueur/m")
            self.assertEqual(S.parse_args(["--media-dir", "/abs"]).media_dir, "/abs")

    def test_config_file(self):
        args = S.parse_args(["--config", self.cfg])
        self.assertEqual((args.media_dir, args.special_moves, args.no_special_moves,
                          args.attract_delay_s, args.attract_gif_max_s,
                          args.attract_clock_every_s, args.web_port, args.mqtt_host,
                          args.mqtt_port),
                         ("/m", False, True, 20, 45, 300, 9001, "broker", 1884))

    def test_command_line_wins(self):
        args = S.parse_args(["--config", self.cfg, "--media-dir", "/x", "--web-port", "0",
                             "--mqtt-host", "h", "--mqtt-port", "2000"])
        self.assertEqual((args.media_dir, args.web_port, args.mqtt_host, args.mqtt_port),
                         ("/x", 0, "h", 2000))

    def test_no_special_moves_option_wins(self):
        cfg = write(self.tmp.name, "on.toml", "dmd_special_moves = 1\n")
        self.assertFalse(S.parse_args(["--config", cfg, "--no-special-moves"]).special_moves)
        self.assertTrue(S.parse_args(["--config", cfg]).special_moves)

    def test_replay_keeps_its_port(self):
        self.assertEqual(S.parse_args(["--config", self.cfg, "--replay", os.devnull]).web_port,
                         S.REPLAY_WEB_PORT)

    def test_bad_config_stops_with_message(self):
        bad = write(self.tmp.name, "bad.toml", "unknown = 1\n")
        for argv in (["--config", bad], ["--config", os.path.join(self.tmp.name, "absent.toml")],
                     ["--mqtt-port", "0"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as cm, \
                        unittest.mock.patch("sys.stderr") as err:
                    S.parse_args(argv)
                self.assertEqual(cm.exception.code, 2)
                written = "".join(c.args[0] for c in err.write.call_args_list)
                self.assertTrue("configuration refusée" in written or "hors plage" in written)


class RunStopTest(unittest.TestCase):
    """Arrêt de run() : événement d'arrêt, cause journalisée ; horloge qui avance."""

    def args(self, *extra):
        return S.parse_args(["--replay", os.devnull, "--web-port", "0"] + list(extra))

    def test_started_and_stopped_logs(self):
        clk = Clock()
        with self.assertLogs("dmd", "INFO") as logs:
            S.run(self.args(), FakeRenderer(), clock=clk, sleep=advancing_sleep(clk),
                  time_synced=lambda: False, stop=lambda: clk.t >= 2 * SEC)
        text = "\n".join(logs.output)
        self.assertIn("DMD : started (configuration defaults, media %s, special "
                      % os.path.expanduser("~/dmd-medias") +
                      "moves on, Attract Mode after 15 s, GIF cut at 60 s, clock every 120 s, "
                      "web port off, replay)", text)
        self.assertTrue(logs.output[-1].endswith("DMD : stopped (stop requested)"))

    def test_shutdown_request_cause(self):
        clk = Clock()
        shutdown = S.ShutdownRequest()

        def sleep(s):
            clk.t += max(1000, int(s * 1e6))
            if clk.t >= 3 * SEC:
                shutdown.request("SIGTERM")
                shutdown.request("SIGINT")          # la première cause reste
        with self.assertLogs("dmd", "INFO") as logs:
            S.run(self.args(), FakeRenderer(), clock=clk, sleep=sleep, time_synced=lambda: False,
                  stop=shutdown)
        self.assertTrue(logs.output[-1].endswith("DMD : stopped (SIGTERM)"))
        self.assertLess(clk.t, 3 * SEC + 20_000)    # arrêt au pas suivant

    def test_keyboard_interrupt(self):
        clk = Clock()

        def sleep(s):
            clk.t += max(1000, int(s * 1e6))
            if clk.t >= 2 * SEC:
                raise KeyboardInterrupt
        with self.assertLogs("dmd", "INFO") as logs:
            S.run(self.args(), FakeRenderer(), clock=clk, sleep=sleep, time_synced=lambda: False)
        self.assertTrue(logs.output[-1].endswith("DMD : stopped (KeyboardInterrupt)"))

    def test_error_is_logged_and_raised(self):
        clk = Clock()

        def sleep(s):
            raise RuntimeError("panne")
        with self.assertLogs("dmd", "INFO") as logs, self.assertRaises(RuntimeError):
            S.run(self.args(), FakeRenderer(), clock=clk, sleep=sleep, time_synced=lambda: False)
        self.assertTrue(logs.output[-1].endswith("DMD : stopped (error RuntimeError)"))

    def test_real_sigterm_sets_the_event(self):
        shutdown = S.ShutdownRequest()
        old = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)}
        try:
            shutdown.install()
            clk = Clock()
            sent = []

            def sleep(s):
                clk.t += max(1000, int(s * 1e6))
                if clk.t >= 2 * SEC and not sent:
                    sent.append(1)
                    os.kill(os.getpid(), signal.SIGTERM)
            with self.assertLogs("dmd", "INFO") as logs:
                S.run(self.args(), FakeRenderer(), clock=clk, sleep=sleep,
                      time_synced=lambda: False, stop=shutdown)
        finally:
            for n, handler in old.items():
                signal.signal(n, handler)
        self.assertTrue(shutdown())
        self.assertEqual(shutdown.cause, "SIGTERM")
        self.assertTrue(logs.output[-1].endswith("DMD : stopped (SIGTERM)"))
        self.assertLess(clk.t, 2 * SEC + 20_000)

    def test_attract_settings_reach_the_core(self):
        args = self.args()
        args.attract_delay_s, args.attract_clock_every_s = 20, 300
        clk = Clock()
        with self.assertLogs("dmd", "INFO"):
            service = S.run(args, FakeRenderer(), clock=clk, sleep=advancing_sleep(clk),
                            time_synced=lambda: False, stop=lambda: clk.t >= 2 * SEC)
        self.assertEqual((service.core.attract_delay_us, service.core.clock_every_us),
                         (20 * SEC, 300 * SEC))


class LoadRendererTest(unittest.TestCase):
    def test_decoding_logged_with_duration(self):
        seen = {}
        times = iter([100.0, 107.5])                # horloge qui avance de 7,5 s

        def factory(media_dir, **kw):
            seen.update(kw, media_dir=media_dir)
            return "renderer"
        args = S.parse_args(["--media-dir", "/m"])
        args.attract_gif_max_s = 45
        stop = S.ShutdownRequest()
        with self.assertLogs("dmd", "INFO") as logs:
            r = S.load_renderer(factory, args, should_stop=stop, clock=lambda: next(times))
        self.assertEqual(r, "renderer")
        self.assertEqual(seen, {"media_dir": "/m", "special_moves": True, "attract_max_s": 45,
                                "should_stop": stop})
        self.assertIn("DMD : decoding media of /m (Scores, SpecialsMoves)...", logs.output[0])
        self.assertIn("DMD : decoding done in 7.5 s", logs.output[1])

    def test_without_special_moves(self):
        args = S.parse_args(["--no-special-moves"])
        with self.assertLogs("dmd", "INFO") as logs:
            S.load_renderer(lambda d, **kw: kw, args, clock=lambda: 0.0)
        self.assertIn("(Scores)...", logs.output[0])


class NtpLogTest(unittest.TestCase):
    def test_change_logged_first_read_included(self):
        clk = Clock(0)
        pending = []
        values = [False, False, True, True, False]
        ntp = S.NtpStatus(reader=lambda: values.pop(0), clock=clk, spawn=pending.append)
        logged = []
        for _ in range(5):
            with self.assertLogs("dmd", "INFO") as logs:
                S.log.info("marker")
                ntp()
                while pending:
                    pending.pop(0)()
            logged.append([m for m in logs.output if "NTP" in m])
            clk.t += S.NTP_PERIOD_US                # horloge qui avance : une lecture par minute
        self.assertEqual([len(x) for x in logged], [1, 0, 1, 0, 1])
        self.assertIn("DMD : NTP synchronized : no", logged[0][0])
        self.assertIn("DMD : NTP synchronized : yes", logged[2][0])
        self.assertIn("DMD : NTP synchronized : no", logged[4][0])


class EntryPointTest(unittest.TestCase):
    def test_shared_found_without_pythonpath(self):
        # Comme sur le Pi (/opt/dartspico-bridge) : python3 -I, autre dossier courant ;
        # dmd_proto vient de ../../shared (50e complément)
        script = ROOT / "pi" / "dmd" / "dmd_service.py"
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, "-I", str(script), "--help"], cwd=cwd,
                                    capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)

    def test_bad_config_exits_2(self):
        script = ROOT / "pi" / "dmd" / "dmd_service.py"
        with tempfile.TemporaryDirectory() as cwd:
            cfg = write(cwd, "dmd.toml", "unknown = 1\n")
            result = subprocess.run([sys.executable, "-I", str(script), "--config", cfg],
                                    cwd=cwd, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn("configuration refusée", result.stderr)
        self.assertIn("unknown", result.stderr)


class HwdStopLogTest(unittest.TestCase):
    def test_sigterm_logged(self):
        import hwd_daemon as H

        class FakeDaemon:
            def __init__(self, cfg, bus):
                self.closed = False

            def run(self, stop):
                os.kill(os.getpid(), signal.SIGTERM)
                self.stopped = stop.wait(5)

            def close(self):
                self.closed = True
        old = {n: signal.getsignal(n) for n in (signal.SIGTERM, signal.SIGINT)}
        saved = (H.Daemon, H.PahoBus)
        H.Daemon, H.PahoBus = FakeDaemon, lambda cfg: None
        try:
            with self.assertLogs("hwd", "INFO") as logs:
                self.assertEqual(H.main(["--dmd", ""]), 0)
        finally:
            H.Daemon, H.PahoBus = saved
            for n, handler in old.items():
                signal.signal(n, handler)
        self.assertTrue(logs.output[-1].endswith("# Stop : SIGTERM"))


class UnitsTest(unittest.TestCase):
    def read(self, name):
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        parser.read(ROOT / "pi" / "systemd" / name, encoding="utf-8")
        return parser

    def test_units(self):
        for name, script, config in (
                ("raspydarts-hwd.service", "pi/hwd/hwd_daemon.py", "/etc/raspydarts/hwd.toml"),
                ("raspydarts-dmd.service", "pi/dmd/dmd_service.py", "/etc/raspydarts/dmd.toml")):
            with self.subTest(unit=name):
                u = self.read(name)
                self.assertEqual(u["Unit"]["After"], "mosquitto.service")
                self.assertEqual(u["Unit"]["Wants"], "mosquitto.service")
                service = u["Service"]
                self.assertEqual(service["Type"], "simple")
                # Marqueurs remplis à l'installation (complément 77)
                self.assertEqual(service["User"], "@RASPYDARTS_USER@")
                self.assertEqual(service["Group"], "@RASPYDARTS_GROUP@")
                self.assertEqual(service["Restart"], "on-failure")
                self.assertEqual(service["RestartSec"], "2")
                self.assertEqual(service["TimeoutStopSec"], "10")
                self.assertEqual(service["ExecStart"], "/usr/bin/python3 /opt/dartspico-bridge/%s "
                                 "--config %s" % (script, config))
                self.assertEqual(service["WorkingDirectory"], "/opt/dartspico-bridge")
                self.assertTrue(u["Unit"]["Description"].startswith("DartsPico-Bridge : "))
                self.assertTrue((ROOT / script).is_file())
        self.assertEqual(self.read("raspydarts-hwd.service")["Service"]["SupplementaryGroups"],
                         "dialout")


class InstallScriptTest(unittest.TestCase):
    """Le script n'est jamais lancé par les tests (il utilise SSH) : syntaxe et règles lues."""

    def setUp(self):
        self.path = ROOT / "tools" / "install_services.sh"
        self.text = self.path.read_text(encoding="utf-8")

    def test_syntax(self):
        result = subprocess.run(["bash", "-n", str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.access(self.path, os.X_OK))

    def test_dry_run_before_any_ssh(self):
        dry_end = self.text.index('if [ "$APPLY" -eq 0 ]; then')
        self.assertIn("exit 0", self.text[dry_end:dry_end + 200])
        code = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))
        dry = code.index('if [ "$APPLY" -eq 0 ]; then')
        for call in ('ssh -4 "$HOST"', 'ssh -4 -t "$HOST"'):     # les deux appels de --apply
            self.assertGreater(code.index(call), dry)
        self.assertEqual(code.count('ssh -4 "$HOST"') + code.count('ssh -4 -t "$HOST"'), 2)
        self.assertIn('APPLY=0', code)

    def test_units_never_enabled_nor_started(self):
        code = "\n".join(l for l in self.text.splitlines() if not l.lstrip().startswith("#"))
        for forbidden in ("systemctl enable", "systemctl start", "systemctl restart", "--now"):
            self.assertNotIn(forbidden, code)
        self.assertIn("systemctl daemon-reload", code)
        self.assertIn('if [ -e "$ETC_DIR/\\$name" ]', code)     # configurations jamais écrasées


class ReplayExampleTest(unittest.TestCase):
    """Espacement de replay_example.txt (50e complément), avec la vraie machine d'états."""

    def setUp(self):
        self.entries = P.load(ROOT / "pi" / "dmd" / "replay_example.txt")

    def test_scrolling_texts_have_17_s(self):
        for i, entry in enumerate(self.entries[:-1]):
            item = C.parse(entry.topic, entry.payload)
            if isinstance(item, C.TextMessage) and item.scroll:
                with self.subTest(payload=entry.payload):
                    self.assertGreaterEqual(self.entries[i + 1].delay_s, 17)

    def test_special_move_then_pending_text(self):
        payloads = [e.payload for e in self.entries]
        i = payloads.index(b"score|T20 - T20 - T20||0;255;0|O")
        self.assertEqual(C.special_move(("T20", "T20", "T20")).folder, "MAXIMUM_TON_80")
        self.assertIsInstance(C.parse(self.entries[i + 1].topic, self.entries[i + 1].payload),
                              C.TextMessage)
        self.assertLess(self.entries[i + 1].delay_s, 1.9)      # pendant le GIF (1,9 s au moins)

    def test_long_final_pause_in_attract_mode(self):
        last = self.entries[-1]
        self.assertIsInstance(C.parse(last.topic, last.payload), C.Ignored)
        self.assertGreaterEqual(last.delay_s, 150)

    def test_clock_seen_during_final_pause(self):
        # Rejeu du journal dans le cœur, horloge qui avance, GIF de 60 s au plus (pire cas) :
        # l'heure paraît avant la fin de la pause finale
        clk = Clock(0)
        core = C.DmdCore(clock=clk, gifs=["a.gif", "b.gif"], time_synced=lambda: True)
        core.start()
        commands = []
        media = None
        for entry in self.entries:
            end = clk.t + int(entry.delay_s * SEC)
            while clk.t < end:
                clk.t = min(end, clk.t + 60 * SEC)
                out = core.tick()
                if media is not None and core.state == C.ATTRACT_GIF:
                    out += core.media_done(media.token)
                for command in out:
                    commands.append((entry.payload, command))
                    if isinstance(command, C.ShowMedia):
                        media = command
            out = core.message(entry.topic, entry.payload)
            commands += [(entry.payload, c) for c in out]
            media = next((c for c in out if isinstance(c, C.ShowMedia)), media)
        before_last = [c for p, c in commands if p == self.entries[-1].payload]
        self.assertIn(C.ShowClock(10), before_last)


@unittest.skipIf(dmd_render is None, "Pillow absent")
class RendererSreTest(unittest.TestCase):
    def test_attract_max_and_default(self):
        self.assertEqual(dmd_render.Renderer(None).attract_max_us, 60 * SEC)
        self.assertEqual(dmd_render.Renderer(None, attract_max_s=30).attract_max_us, 30 * SEC)

    def test_stop_between_two_files(self):
        with tempfile.TemporaryDirectory() as media:
            folder = os.path.join(media, C.SCORES_FOLDER, "S1")
            os.makedirs(folder)
            for name in ("a.ppm", "b.ppm", "c.ppm"):
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(b"P6\n2 2\n255\n" + bytes(12))
            calls = []

            def should_stop():
                calls.append(1)
                return len(calls) >= 2              # arrêt demandé après le premier fichier
            with self.assertLogs("dmd", "INFO") as logs:
                r = dmd_render.Renderer(media, should_stop=should_stop)
            self.assertEqual(len(r.scores.get("S1", [])), 0)    # dossier abandonné en cours
            self.assertEqual(r.specials, {})                    # SpecialsMoves non commencé
            self.assertTrue(any("interrupted by the stop request" in m for m in logs.output))
            r = dmd_render.Renderer(media)
            self.assertEqual(len(r.scores["S1"]), 3)


if __name__ == "__main__":
    unittest.main()
