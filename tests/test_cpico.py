# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests de la couche du jeu (cpico) et du remplaçant de rpi_ws281x, par thème."""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import _orig
import _paths  # noqa: F401

sys.path.insert(0, os.path.join(str(_paths.ROOT), "pi", "raspydarts_layer", "include"))
sys.path.insert(0, os.path.join(str(_paths.ROOT), "pi", "raspydarts_layer", "shims"))
import cpico  # noqa: E402
import rpi_ws281x  # noqa: E402
from fake_mqtt import FakeMqttModule, Message  # noqa: E402


class Logs:
    def __init__(self):
        self.lines = []

    def warning(self, m):
        self.lines.append(m)

    info = debug = error = warning


class Base(unittest.TestCase):
    def setUp(self):
        FakeMqttModule.Client.instances = []
        self.link = cpico.PicoLink(Logs(), mqtt_module=FakeMqttModule)
        self.client = FakeMqttModule.Client.instances[-1]
        self.link._on_status('{"pico": "connected"}')
        self.ext = cpico.PicoExtender(Logs(), self.link, cpico.DEFAULTS)

    def sent(self):
        return [c[2] for c in self.client.calls if c[0] == "publish"]


class LinkSetup(Base):
    def test_keepalive_and_last_will(self):
        self.assertEqual(self.client.api, "v2")
        self.assertEqual([c for c in self.client.calls if c[0] == "connect_async"][0][3], 5)
        will = [c for c in self.client.calls if c[0] == "will_set"][0]
        self.assertEqual(will[1:3], ("raspydarts/hw/cmd", "OFF"))

    def test_mcp1_follows_status(self):
        self.assertIs(self.ext.mcp1, self.ext)
        self.link._on_status('{"pico": "absent"}')
        self.assertIsNone(self.ext.mcp1)


class ReadEntries(Base):
    def tick(self, seconds):
        self.ext.clock = lambda now=self.ext.clock() + seconds: now

    def test_held_button_counts_once(self):
        self.ext.clock = lambda: 1000.0
        self.link.on_event("B 7 1 0")                              # PIN_VALIDATE
        self.assertEqual(self.ext.read_entries(), "PIN_VALIDATE")
        for _ in range(20):
            self.tick(0.1)
            self.assertIsNone(self.ext.read_entries())             # un appui = une action

    def test_held_arrow_repeats(self):
        self.ext.clock = lambda: 1000.0
        self.link.on_event("B 1 1 0")                              # PIN_UP
        self.assertEqual(self.ext.read_entries(), "PIN_UP")
        self.tick(0.3)
        self.assertIsNone(self.ext.read_entries())                 # avant REPEAT_DELAY
        self.tick(0.3)
        self.assertEqual(self.ext.read_entries(), "PIN_UP")        # répétition
        self.tick(0.1)
        self.assertIsNone(self.ext.read_entries())                 # avant REPEAT_EVERY
        self.tick(0.1)
        self.assertEqual(self.ext.read_entries(), "PIN_UP")

    def test_stuck_button_banned_until_released(self):
        self.ext.clock = lambda: 1000.0
        self.link.on_event("B 1 1 0")
        self.assertEqual(self.ext.read_entries(), "PIN_UP")
        self.tick(cpico.STUCK + 1)
        self.assertIsNone(self.ext.read_entries())
        self.assertEqual(self.ext.banned_button, "PIN_UP")
        self.link.on_event("B 1 0 0")
        self.tick(0.1)
        self.assertIsNone(self.ext.read_entries())
        self.assertIsNone(self.ext.banned_button)                  # relâché : débanni

    def test_short_press_latched(self):
        self.link.on_event("B 15 1 0")
        self.link.on_event("B 15 0 10")                            # relâché avant lecture
        self.assertEqual(self.ext.read_entries(), "PIN_CPTPLAYER")  # vu une fois (R3)
        self.assertIsNone(self.ext.read_entries())

    def test_miss_is_missdart_once(self):
        self.link.on_event("MISS 123")
        self.assertEqual(self.ext.read_entries(), "PIN_MISSDART")
        self.assertIsNone(self.ext.read_entries())

    def test_test_entries(self):
        self.link.on_event("BS 0101")
        self.assertEqual(sorted(self.ext.test_entries()), ["BTN1", "BTN9"])


class Darts(Base):
    def test_dart_queue(self):
        q = queue.Queue()
        src = cpico.PicoTargetSource(self.link, q)
        self.assertIsNone(src.poll())
        self.link.clock = lambda: 12.5
        self.link.on_event("D R3C4 100 2000")
        self.assertEqual(q.get_nowait(), ("R3C4", 12.5))
        src.terminate()
        self.link.on_event("D R1C1 100 2000")
        self.assertTrue(q.empty())

    def test_dart_time_is_reception_not_pico_ticks(self):
        # Complément 19 : instant = time.monotonic() à la réception ; t_µs du Pico ignoré
        q = queue.Queue()
        cpico.PicoTargetSource(self.link, q)
        before = time.monotonic()
        self.link.on_event("D R2C5 999999999 2000")
        code, instant = q.get_nowait()
        self.assertEqual(code, "R2C5")
        self.assertGreaterEqual(instant, before)
        self.assertLessEqual(instant, time.monotonic())


def _base_methods(names):
    """Méthodes de Craspberry lues dans la copie de référence au commit BASE (git show,
    rien n'est écrit), compilées seules : la file des fléchettes telle que le jeu la lit."""
    import ast
    import textwrap
    source = subprocess.run(["git", "-C", _orig.ORIG, "show",
                             "%s:include/craspberry.py" % _orig.BASE_COMMIT],
                            capture_output=True, text=True, check=True).stdout
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Craspberry")
    namespace = {"queue": queue, "time": time}
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(textwrap.dedent(ast.get_source_segment(source, node)), namespace)
    return {n: namespace[n] for n in names}


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class DartsReadByTheGame(Base):
    """La file remplie par PicoTargetSource, lue par take_dart et wait_dart de la base :
    aurait échoué avec des codes seuls sur RaspyDarts 4.6.5 (code, self.dart_time = dart)."""

    def setUp(self):
        super().setUp()
        self.methods = _base_methods(("take_dart", "wait_dart"))

        class Rpi:
            pass
        self.rpi = Rpi()
        self.rpi.target_darts = queue.Queue()
        self.rpi.pending_dart = None
        self.rpi.dart_time = None
        self.source = cpico.PicoTargetSource(self.link, self.rpi.target_darts)

    def take(self):
        return self.methods["take_dart"](self.rpi)

    def test_take_dart_gives_code_and_dart_time(self):
        self.link.clock = lambda: 42.0
        self.link.on_event("D R3C4 100 2000")
        self.assertEqual(self.take(), "R3C4")
        self.assertEqual(self.rpi.dart_time, 42.0)
        self.assertIsNone(self.take())

    def test_wait_dart_then_take_dart(self):
        self.link.clock = lambda: 7.25
        self.link.on_event("D R11C6 100 2000")
        self.methods["wait_dart"](self.rpi, 0.01)
        self.assertEqual(self.take(), "R11C6")
        self.assertEqual(self.rpi.dart_time, 7.25)


class Outputs(Base):
    def test_lights_toys_and_absent_toys(self):
        self.ext.light_toys(["LIGHT_LIGHT", "TOY8", "TOY9", "TOY16"], True)
        self.assertEqual(self.sent(), ["LGT 10 1", "TOY 8 1"])      # TOY9..16 ignorés

    def test_strobe_witness_threads(self):
        start = time.monotonic()
        threads = self.ext.strobe_toys(["LIGHT_NAVIGATE", "TOY12"], iterations=2,
                                       delay_on=30, delay_off=40)
        self.assertEqual(self.sent(), ["LGTPULSE 3 30 40 2"])
        self.assertEqual(len(threads), 1)
        for t in threads:
            t.join()
        self.assertGreaterEqual(time.monotonic() - start, 0.095)    # 2 × 30 + 40 ms

    def test_no_coin_acceptor(self):
        self.assertEqual(self.ext.chips, [])                        # D4 : rien à lire
        self.assertFalse(getattr(self.ext, "chips", None))

    def test_defaults_match_pinout(self):
        import json
        with open(os.path.join(str(_paths.ROOT), "hardware", "pinout.json"), encoding="utf-8") as f:
            pin = json.load(f)
        for b in pin["buttons"]:
            self.assertEqual(cpico.DEFAULTS["PIN_" + b["function"]], b["id"])
        for name, v in pin["lights_default_mapping_raspydarts"].items():
            if not name.startswith("_"):
                self.assertEqual(cpico.DEFAULTS[name], v)


class Shim(unittest.TestCase):
    def test_frame_brightness_and_order(self):
        got = []
        rpi_ws281x.set_transport(lambda t, p: got.append((t, p)))
        s = rpi_ws281x.PixelStrip(2, 1, 800000, 9, False, 127, 0,
                                  strip_type=rpi_ws281x.WS2811_STRIP_GRB)
        s.begin()
        s.setPixelColor(0, rpi_ws281x.Color(255, 0, 0))
        s.show()
        self.assertEqual(got[0], ("raspydarts/hw/cmd", "SET led1_order GRB"))
        self.assertEqual(got[1], ("raspydarts/hw/cmd", "LEDB 1 127"))   # luminosité à part
        self.assertEqual(got[2], ("raspydarts/hw/led/1", bytes([255, 0, 0, 0, 0, 0])))  # sans atténuation
        s.setBrightness(64)
        self.assertEqual(got[3], ("raspydarts/hw/cmd", "LEDB 1 64"))
        import hwbus
        hwbus.parse_command("LEDB 1 64")                           # accepté par hwd



# ------------------------------------------------ 2 à 5 : réception et vie du lien

class LinkMessages(Base):
    def test_routing_status_and_events(self):
        q = queue.Queue()
        self.link.add_dart_queue(q)
        self.link._on_message(self.client, None, Message("raspydarts/hw/events", b"D R1C2 5 2000"))
        self.assertEqual(q.get_nowait()[0], "R1C2")
        self.link._on_message(self.client, None,
                              Message("raspydarts/hw/status", b'{"pico": "absent"}'))
        self.assertFalse(self.link.connected)

    def test_invalid_payloads_are_ignored(self):
        self.link._on_message(self.client, None, Message("raspydarts/hw/events", b"\xff\xfe"))
        self.link._on_message(self.client, None, Message("raspydarts/hw/status", b"{pas du json"))
        self.assertTrue(self.link.connected)                       # état inchangé
        self.link._on_message(self.client, None, Message("raspydarts/hw/events", b"D R3C9 1 1"))
        self.assertTrue(any("Invalid contact code" in m for m in self.link.logs.lines))
        self.link._on_message(self.client, None, Message("autre/sujet", b"D R1C1 1 1"))

    def test_subscriptions_on_connect(self):
        self.link._on_connect(self.client, None, {}, 0, None)
        subs = [c[1] for c in self.client.calls if c[0] == "subscribe"]
        self.assertEqual(subs, [[("raspydarts/hw/events", 0), ("raspydarts/hw/status", 0)]])

    def test_close_sends_off_then_disconnects(self):
        self.link.close()
        names = [c[0] for c in self.client.calls]
        off = names.index("publish")
        self.assertEqual(self.client.calls[off][1:3], ("raspydarts/hw/cmd", "OFF"))
        self.assertLess(off, names.index("disconnect"))
        self.assertLess(names.index("disconnect"), names.index("loop_stop"))

    def test_absent_clears_buttons_and_errors_are_logged(self):
        self.link.on_event("B 3 1 0")
        self.link._on_status(json.dumps({"pico": "absent"}))
        self.assertEqual(self.link.pressed, 0)
        self.link.on_event("E WDG timeout")
        self.assertTrue(any("WDG timeout" in m for m in self.link.logs.lines))


# ------------------------------------------------ 6, 7 : correspondance et configuration

class Mapping(Base):
    def test_decode(self):
        D = cpico.PicoExtender.decode
        self.assertEqual(D("BTN15"), ("BTN", 15))
        self.assertEqual(D("btn3"), ("BTN", 3))                     # casse ignorée, comme A0/a0
        self.assertEqual(D("T8"), ("TOY", 8))
        self.assertEqual(D("V12"), ("LGT", 12))
        self.assertEqual(D("MISS"), ("MISS", 0))
        for bad in ("T0", "T09", "T9", "BTN16", "BTN0", "V13", "", "X1", "A0", "TOY1", "V"):
            with self.subTest(bad=bad):
                self.assertEqual(D(bad), (None, None))

    def test_set_io_ignores_values_outside_the_board(self):
        ext = cpico.PicoExtender(Logs(), self.link, {
            "PIN_UP": "BTN99", "PIN_DOWN": "T1", "TOY1": "V1", "LIGHT_FLASH": "T2",
            "TOY9": "T9", "PIN_LEFT": "", "PIN_RIGHT": "BTN4", "TOY2": "t2"})
        self.assertEqual(list(ext.buttons_list), ["PIN_RIGHT"])
        self.assertEqual(list(ext.toys_list), ["TOY2"])

    def test_defaults(self):
        d = self.ext.get_defaults()
        self.assertIs(d, cpico.DEFAULTS)
        self.assertEqual(sum(v.startswith("BTN") for v in d.values()), 15)
        self.assertEqual(d["PIN_MISSDART"], "MISS")
        self.assertEqual([d["TOY%d" % i] for i in range(9, 17)], [""] * 8)
        self.assertTrue(all(d[k] == "" for k in d if k.startswith("PIN_MUSIC_")))
        ext = cpico.PicoExtender(Logs(), self.link)
        self.assertEqual(ext.buttons_list, {})
        ext.set_defaults()
        self.assertEqual(len(ext.buttons_list), 16)                 # 15 boutons + MISS
        self.assertEqual(len(ext.toys_list), 8 + 10)                # 8 Toys, 10 LIGHT_*


# ------------------------------------------------ 8, 9 : sorties

class CaptureMode(Base):
    """Mode capture (ccapture.py, RaspyDarts 4.6.4) : Valider maintenu 2 s = fin."""

    def at(self, seconds):
        self.ext.clock = lambda: 1000.0 + seconds

    def test_short_validate_given_on_release(self):
        self.ext.hold_validate = True
        self.at(0)
        self.link.on_event("B 7 1 0")                              # PIN_VALIDATE
        self.assertIsNone(self.ext.read_entries())                 # pas encore
        self.at(0.5)
        self.assertIsNone(self.ext.read_entries())
        self.link.on_event("B 7 0 0")
        self.at(0.6)
        self.assertEqual(self.ext.read_entries(), "PIN_VALIDATE")  # au relâchement

    def test_long_validate_gives_end_once(self):
        self.ext.hold_validate = True
        self.at(0)
        self.link.on_event("B 7 1 0")
        self.assertIsNone(self.ext.read_entries())
        self.at(cpico.HOLD_END)
        self.assertEqual(self.ext.read_entries(), "PIN_CAPTURE_END")
        self.at(cpico.HOLD_END + 1)
        self.assertIsNone(self.ext.read_entries())
        self.link.on_event("B 7 0 0")
        self.at(cpico.HOLD_END + 1.1)
        self.assertIsNone(self.ext.read_entries())                 # pas de Valider en plus

    def test_short_press_latched_in_capture(self):
        # Appui bref entre deux lectures (verrou R3) : vu appuyé, puis rendu au relâchement.
        self.ext.hold_validate = True
        self.at(0)
        self.link.on_event("B 7 1 0")
        self.link.on_event("B 7 0 10")
        self.assertIsNone(self.ext.read_entries())
        self.at(0.1)
        self.assertEqual(self.ext.read_entries(), "PIN_VALIDATE")


class BlinkToy(Base):
    def setUp(self):
        super().setUp()
        self.slept = []
        self.ext.sleep = self.slept.append

    def run_blink(self, *args):
        thread = self.ext.blink_toy(*args)
        if thread is not None:
            thread.join(5)
        return thread

    def test_from_off(self):
        self.run_blink("LIGHT_NAVIGATE", 2, 100, 50)               # V3
        self.assertEqual(self.sent(), ["LGT 3 1", "LGT 3 0", "LGT 3 1", "LGT 3 0"])
        self.assertEqual(self.slept, [0.1, 0.05, 0.1])

    def test_from_lit_ends_lit(self):
        self.ext.light_toys(["LIGHT_NAVIGATE"])
        self.run_blink("LIGHT_NAVIGATE", 2, 100, 50)
        self.assertEqual(self.sent(), ["LGT 3 1", "LGT 3 0", "LGT 3 1", "LGT 3 0", "LGT 3 1"])
        self.assertEqual(self.slept, [0.05, 0.1, 0.05])

    def test_all_off_forgets_state(self):
        self.ext.light_toys(["LIGHT_NAVIGATE"])
        self.ext.all_off()
        self.run_blink("LIGHT_NAVIGATE", 1, 100, 50)
        self.assertEqual(self.sent()[-2:], ["LGT 3 1", "LGT 3 0"])  # parti de l'état éteint

    def test_absent_or_no_cycle(self):
        self.assertIsNone(self.run_blink("TOY12", 2, 10, 10))      # Toy absent du Pico
        self.assertIsNone(self.run_blink("LIGHT_NAVIGATE", 0, 10, 10))
        self.assertEqual(self.sent(), [])


class OutputPinValue(Base):
    """toys_list[nom][1].value, lu par Paramétrage > LED des boutons (cscreen.button_led_try,
    RaspyDarts 4.6.4) comme la broche d'un MCP23017 : 1 allumée, 0 éteinte."""

    def value(self, name):
        return self.ext.toys_list[name][1].value

    def test_value_follows_light_toys(self):
        self.assertEqual(self.value("LIGHT_NAVIGATE"), 0)          # éteinte au départ
        self.ext.light_toys(["LIGHT_NAVIGATE"])
        self.assertEqual(self.value("LIGHT_NAVIGATE"), 1)
        self.assertEqual(self.value("LIGHT_BACK"), 0)
        self.ext.light_toys(["LIGHT_NAVIGATE"], False)
        self.assertEqual(self.value("LIGHT_NAVIGATE"), 0)

    def test_value_after_all_off(self):
        self.ext.light_toys(["TOY1", "LIGHT_FLASH"])
        self.ext.all_off()
        self.assertEqual((self.value("TOY1"), self.value("LIGHT_FLASH")), (0, 0))

    def test_value_written_sends_command(self):
        self.ext.toys_list["LIGHT_NAVIGATE"][1].value = 1
        self.assertEqual(self.sent()[-1], "LGT 3 1")
        self.assertEqual(self.value("LIGHT_NAVIGATE"), 1)
        self.ext.toys_list["TOY2"][1].value = 0
        self.assertEqual(self.sent()[-1], "TOY 2 0")

    def test_button_led_try_restores_state(self):
        # Même séquence que cscreen.button_led_try : état lu, essai, état remis.
        self.ext.light_toys(["LIGHT_VALIDATE"])
        before = bool(self.ext.toys_list["LIGHT_VALIDATE"][1].value)
        self.ext.light_toys(["LIGHT_VALIDATE"], False)
        self.ext.light_toys(["LIGHT_VALIDATE"], before)
        self.assertEqual(self.sent()[-1], "LGT 4 1")


class OutputDetails(Base):
    def test_light_off_and_tuples_ignored(self):
        self.ext.light_toys(["TOY1", "LIGHT_BACK"], False)
        # test_toys_menu (cscreen.py:5951) passe des tuples (valeur, nom) : ignorés, comme avant.
        self.ext.light_toys([("T1", "TOY1"), ("V2", "LIGHT_BACK")], True)
        self.assertEqual(self.sent(), ["TOY 1 0", "LGT 2 0"])

    def test_strobe_limits(self):
        self.assertEqual(self.ext.strobe_toys(["TOY1"], iterations=0), [])
        self.assertEqual(self.sent(), [])
        self.ext.strobe_toys(["TOY1"], iterations=1, delay_on=5, delay_off=0)
        self.assertEqual(self.sent()[-1], "TOYPULSE 1 5 1 1")       # off ramené à 1 ms
        self.ext.strobe_toys(["TOY2"], iterations=1, delay_on=0, delay_off=0)
        self.assertEqual(self.sent()[-1], "TOYPULSE 2 1 1 1")
        for line in self.sent():
            import pico_proto
            pico_proto.parse_command(line)                         # toujours valide (§ 5.3)

    def test_strobe_clamped_to_protocol(self):
        # Fils témoins de 70 à 90 s : leur attente est remplacée par une attente nulle.
        import types
        saved = cpico.time
        cpico.time = types.SimpleNamespace(sleep=lambda d: None)
        self.addCleanup(setattr, cpico, "time", saved)
        self.ext.strobe_toys(["TOY3"], iterations=70000, delay_on=1, delay_off=0)
        self.assertEqual(self.sent()[-1], "TOYPULSE 3 1 1 65535")
        self.ext.strobe_toys(["TOY4"], iterations=1, delay_on=0, delay_off=0)
        self.ext.strobe_toys(["TOY5"], iterations=1, delay_on=90000, delay_off=0)
        self.assertEqual(self.sent()[-1], "TOYPULSE 5 60000 1 1")


# ------------------------------------------------ 10, 11 : lien unique et installation

class SharedAndInstall(unittest.TestCase):
    def tearDown(self):
        cpico._reset_shared()

    def test_shared_returns_one_link(self):
        FakeMqttModule.Client.instances = []
        a = cpico.shared(Logs(), mqtt_module=FakeMqttModule)
        b = cpico.shared(Logs(), mqtt_module=FakeMqttModule)
        self.assertIs(a, b)
        self.assertEqual(len(FakeMqttModule.Client.instances), 1)  # un seul client MQTT

    def test_import_when_installed_in_include(self):
        # Installation prévue : cpico.py, hwbus.py et pico_proto.py copiés dans include/.
        root = str(_paths.ROOT)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        inc = os.path.join(tmp, "include")
        os.mkdir(inc)
        open(os.path.join(inc, "__init__.py"), "w").close()
        for src in ("pi/raspydarts_layer/include/cpico.py", "pi/hwd/hwbus.py", "shared/pico_proto.py"):
            shutil.copy(os.path.join(root, src), inc)
        code = ("import sys; sys.path.insert(0, %r); from include import cpico; "
                "print(cpico.hwbus.__file__); print(cpico.P.__file__)" % tmp)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                             env=env, cwd=tmp)
        self.assertEqual(out.returncode, 0, out.stderr)
        files = out.stdout.split()
        self.assertTrue(all(f.startswith(inc) for f in files), files)


# ------------------------------------------------ 13 : remplaçant de rpi_ws281x

class ShimDetails(unittest.TestCase):
    def setUp(self):
        self.got = []
        rpi_ws281x.set_transport(lambda t, p: self.got.append((t, p)))
        self.saved = os.environ.pop("DARTSPICO_BRIDGE_LED_CHANNEL", None)

    def tearDown(self):
        rpi_ws281x.set_transport(None)
        os.environ.pop("DARTSPICO_BRIDGE_LED_CHANNEL", None)
        if self.saved is not None:
            os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"] = self.saved

    def test_channel_from_environment(self):
        os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"] = "2"
        s = rpi_ws281x.PixelStrip(1, 18)                           # pin de la config du jeu
        s.show()
        self.assertEqual(self.got[-1][0], "raspydarts/hw/led/2")

    def test_refusals(self):
        with self.assertRaises(ValueError):
            rpi_ws281x.PixelStrip(1, 18)                           # ni variable ni pin 0 à 2
        os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"] = "3"
        with self.assertRaises(ValueError):
            rpi_ws281x.PixelStrip(1, 0)
        os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"] = "0"
        with self.assertRaises(ValueError):
            rpi_ws281x.PixelStrip(301, 0)
        rpi_ws281x.PixelStrip(300, 0)

    def test_pixel_api(self):
        s = rpi_ws281x.PixelStrip(3, 0, brightness=255)
        self.assertEqual(s.numPixels(), 3)
        s.setPixelColor(1, rpi_ws281x.Color(1, 2, 3))
        s.setPixelColor(7, rpi_ws281x.Color(9, 9, 9))               # hors bande : ignoré
        self.assertEqual(s.getPixelColor(1), 0x010203)
        s.setBrightness(0)
        self.assertEqual(s.getBrightness(), 0)
        self.assertEqual(s.frame(), bytes([0, 0, 0, 1, 2, 3, 0, 0, 0]))   # luminosité : par LEDB
        self.assertEqual(rpi_ws281x.Color(1, 2, 3, 4), 0x04010203)
        self.assertIs(rpi_ws281x.Adafruit_NeoPixel, rpi_ws281x.PixelStrip)

    def test_get_brightness_is_game_value_not_cap(self):
        # § 3.8 point 6 : le plafond (led_brightness de hwd, 128 sur le canal 0)
        # est appliqué par hwd ; le remplaçant garde et renvoie la valeur du jeu.
        os.environ["DARTSPICO_BRIDGE_LED_CHANNEL"] = "0"
        s = rpi_ws281x.PixelStrip(3, 18, brightness=200)
        s.begin()
        self.assertEqual(s.getBrightness(), 200)
        s.setBrightness(250)
        self.assertEqual(s.getBrightness(), 250)
        self.assertEqual(self.got[-1], ("raspydarts/hw/cmd", "LEDB 0 250"))

    def test_six_colour_orders(self):
        for const, name in (("WS2811_STRIP_RGB", "RGB"), ("WS2811_STRIP_RBG", "RBG"),
                            ("WS2811_STRIP_GRB", "GRB"), ("WS2811_STRIP_GBR", "GBR"),
                            ("WS2811_STRIP_BRG", "BRG"), ("WS2811_STRIP_BGR", "BGR")):
            with self.subTest(order=name):
                rpi_ws281x.PixelStrip(1, 1, strip_type=getattr(rpi_ws281x, const)).begin()
                self.assertEqual(self.got[-2], ("raspydarts/hw/cmd", "SET led1_order %s" % name))
                self.assertEqual(self.got[-1], ("raspydarts/hw/cmd", "LEDB 1 255"))
                import hwbus
                hwbus.parse_command(self.got[-2][1])               # accepté par hwd



# ------------------------------------------------ monnayeur absent (D4)

class NoCoinsBehaviour(unittest.TestCase):
    def test_always_free_like_mode_off(self):
        c = cpico.NoCoins()
        self.assertFalse(c.active())
        self.assertEqual((c.price("X01", "local"), c.cost("X01", "local", 4)), (0, 0))
        self.assertTrue(c.can_pay(5))
        c.pay(5)
        c.add(credits=3, seconds=60)
        c.tick(True)
        c.save_settings({"mode": "games"})
        self.assertEqual(c.settings["mode"], "off")              # jamais activé
        self.assertEqual(c.state, {"credits": 0, "seconds": 0, "coins": 0})
        self.assertFalse(c.time_up())
        self.assertIsNone(c.left())                              # pas de bandeau de crédits
        self.assertFalse(c.changed)
        self.assertFalse(c.check_password("x"))
        self.assertEqual(c.spend(10), 0)


if __name__ == "__main__":
    unittest.main()
