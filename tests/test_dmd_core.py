# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests du cœur du service de rendu du DMD (§ 9.9, § 6.6, sous-phase SR-a).

Python pur, sans Pillow ni MQTT ; horloge simulée.
"""

import itertools
import random
import unittest

import _paths  # noqa: F401
import dmd_core as C

DMD = C.TOPIC_DMD
R2D = C.TOPIC_RASPY2DMD
SEC = 1000000


class FakeClock:
    def __init__(self):
        self.now = 1000

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += int(seconds * SEC)


class ParseText(unittest.TestCase):
    def test_msg(self):
        self.assertEqual(C.parse(DMD, "msg|STRIKE"), C.TextMessage("msg", "STRIKE"))

    def test_msg_tempo(self):
        self.assertEqual(C.parse(DMD, "msg|Hop|3"), C.TextMessage("msg", "Hop", tempo=3.0))

    def test_msg_empty_text_is_valid(self):
        self.assertEqual(C.parse(DMD, "msg|"), C.TextMessage("msg", ""))

    def test_msgmove(self):
        self.assertEqual(C.parse(DMD, "msgmove|Un texte bien plus long que 22|left"),
                         C.TextMessage("msgmove", "Un texte bien plus long que 22", scroll="left"))

    def test_msgmove_flip_tempo(self):
        self.assertEqual(C.parse(DMD, "msgmove|Goal|flip|2"),
                         C.TextMessage("msgmove", "Goal", scroll="flip", tempo=2.0))

    def test_msgmovebcl(self):
        self.assertEqual(C.parse(DMD, "msgmovebcl|Bravo|left|3"),
                         C.TextMessage("msgmovebcl", "Bravo", scroll="left", passes=3))

    def test_msgcolor_full(self):
        self.assertEqual(C.parse(DMD, "msgcolor|Bravo|255;0;0|0;0;0|left|2"),
                         C.TextMessage("msgcolor", "Bravo", color=(255, 0, 0), background=(0, 0, 0),
                                       scroll="left", passes=2))

    def test_msgcolor_fixed(self):
        # cdmd.send_text : sens et passages vides pour un texte fixe
        self.assertEqual(C.parse(DMD, "msgcolor|Bravo|0;255;0|0;0;0||"),
                         C.TextMessage("msgcolor", "Bravo", color=(0, 255, 0), background=(0, 0, 0)))

    def test_bytes_and_newline(self):
        self.assertEqual(C.parse(DMD, "msg|Été\n".encode("utf-8")), C.TextMessage("msg", "Été"))

    def test_invalid(self):
        for payload in ("msg", "msg|a|b|c", "msg|a|x", "msg|a|-1", "msgmove|a", "msgmove|a|",
                        "msgmove|a|left|1|2", "msgmovebcl|a|left", "msgmovebcl|a|left|0",
                        "msgmovebcl|a||2", "msgmovebcl|a|left|", "msgcolor|a|1;2;3|0;0;0|left",
                        "msgcolor|a|1;2|0;0;0||", "msgcolor|a|1;2;300|0;0;0||", "msgcolor|a|r;g;b|0;0;0||",
                        "msgcolor|a|1;2;3|0;0;0||0", "", "\n", "msg|a|nan"):
            with self.subTest(payload=payload):
                self.assertIsInstance(C.parse(DMD, payload), C.Invalid)

    def test_not_utf8(self):
        self.assertIsInstance(C.parse(DMD, b"msg|\xff"), C.Invalid)


class ParseScore(unittest.TestCase):
    def test_full_form(self):
        self.assertEqual(C.parse(DMD, "score|T20 - X - X||255;0;0|O"),
                         C.ScoreMessage("score", ("T20", "X", "X"), (255, 0, 0), True))

    def test_no_colour_no_gif(self):
        self.assertEqual(C.parse(DMD, "score|S1 - MISS - D5|||N"),
                         C.ScoreMessage("score", ("S1", "MISS", "D5"), None, False))

    def test_old_forms(self):
        # § 6.6 : score|X - X - X et forme 4.6.3 sans <O/N> ; <O/N> absent vaut O
        self.assertEqual(C.parse(DMD, "score|X - X - X"), C.ScoreMessage("score", ("X", "X", "X")))
        self.assertEqual(C.parse(DMD, "score|SB - X - X||1;2;3"),
                         C.ScoreMessage("score", ("SB", "X", "X"), (1, 2, 3), True))

    def test_other_prefixes(self):
        for prefix in ["score1", "score2"] + [f"score{n}" for n in range(3, 13)]:
            with self.subTest(prefix=prefix):
                item = C.parse(DMD, f"{prefix}|DB - X - X||0;0;255|O")
                self.assertEqual(item, C.ScoreMessage(prefix, ("DB", "X", "X"), (0, 0, 255), True))

    def test_unknown_prefixes(self):
        for prefix in ("score0", "score13", "scores"):
            with self.subTest(prefix=prefix):
                self.assertEqual(C.parse(DMD, f"{prefix}|X - X - X"), C.Ignored("unknown"))

    def test_invalid(self):
        for payload in ("score", "score|", "score|X - X", "score|X - X - X - X", "score|X -  - X",
                        "score|X - X - X|a|0;0;0|O", "score|X - X - X||0;0;0|Y",
                        "score|X - X - X||0;0;0|O|extra", "score|X - X - X||1;2|O"):
            with self.subTest(payload=payload):
                self.assertIsInstance(C.parse(DMD, payload), C.Invalid)

    def test_last_dart(self):
        self.assertEqual(C.ScoreMessage("score", ("T20", "S5", "X")).last_dart, "S5")
        self.assertEqual(C.ScoreMessage("score", ("T20", "X", "X")).last_dart, "T20")
        self.assertEqual(C.ScoreMessage("score", ("S1", "S2", "MISS")).last_dart, "MISS")
        self.assertIsNone(C.ScoreMessage("score", ("X", "X", "X")).last_dart)


class ParseOthers(unittest.TestCase):
    def test_menu_buttons(self):
        # include/cdmdmenu.py:265 (4.6.5) : boutons de test du menu Afficheur, sujet raspy2dmd
        self.assertEqual(C.parse(R2D, "msg|Bonjour"), C.TextMessage("msg", "Bonjour"))
        self.assertEqual(C.parse(R2D, "pico|test_rgb"), C.PatternMessage())
        self.assertEqual(C.parse(R2D, "demo|gif"), C.AttractMessage())
        self.assertEqual(C.parse(R2D, "time|start"), C.Ignored("not rendered"))

    def test_ignored(self):
        for payload in ("time|start", "shutdwn|", "fond1|start|O", "fond2|stop", "team501|x",
                        "zap501|x", "zapped|x", "bonus421|x", "demo|other", "pico|other", "img|x.png",
                        "gif|x.gif", "sound|x.wav", "conf|x"):
            with self.subTest(payload=payload):
                self.assertIsInstance(C.parse(DMD, payload), C.Ignored)

    def test_unknown_topic(self):
        self.assertEqual(C.parse("raspydarts/other", "msg|a"), C.Ignored("topic raspydarts/other"))


class CoreBase(unittest.TestCase):
    GIFS = ["a.gif", "b.gif", "c.gif"]

    def make(self, gifs=None, synced=False, delay=15):
        self.clock = FakeClock()
        self.synced = synced
        core = C.DmdCore(clock=self.clock, attract_delay_s=delay,
                         gifs=self.GIFS if gifs is None else gifs,
                         time_synced=lambda: self.synced, rng=random.Random(1))
        return core


class Welcome(CoreBase):
    def test_start(self):
        core = self.make()
        self.assertEqual(core.start(), [C.Welcome()])
        self.assertEqual(core.state, C.WELCOME)

    def test_welcome_then_attract(self):
        # § 9.9, ligne 738 (45e complément) : accueil au démarrage, puis Attract Mode après
        # attract_delay_s sans message (remplace test_welcome_stays_until_first_message)
        core = self.make()
        core.start()
        self.clock.advance(14.999)
        self.assertEqual(core.tick(), [])
        self.assertEqual(core.state, C.WELCOME)
        self.clock.advance(0.001)
        out = core.tick()
        self.assertIsInstance(out[0], C.ShowMedia)
        self.assertEqual(out[0].folder, C.ATTRACT_FOLDER)
        self.assertEqual(core.state, C.ATTRACT_GIF)

    def test_welcome_ignored_does_not_restart_delay(self):
        core = self.make()
        core.start()
        self.clock.advance(10)
        with self.assertLogs("dmd", "INFO"):
            self.assertEqual(core.message(DMD, "shutdwn|"), [])
        self.clock.advance(5)
        self.assertIsInstance(core.tick()[0], C.ShowMedia)

    def test_ignored_and_invalid_change_nothing(self):
        core = self.make()
        core.start()
        with self.assertLogs("dmd", "INFO") as logs:
            self.assertEqual(core.message(DMD, "time|start"), [])
            self.assertEqual(core.message(DMD, "score|X"), [])
        self.assertEqual(core.state, C.WELCOME)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("message ignored on raspydarts/dmd", logs.output[0])
        self.assertIn("invalid message on raspydarts/dmd", logs.output[1])

    def test_bad_delay(self):
        with self.assertRaises(ValueError):
            C.DmdCore(clock=FakeClock(), attract_delay_s=0)


class LogOncePerType(CoreBase):
    """§ 9.9, ligne 744 (45e complément) : une ligne la première fois pour chaque type."""

    def test_once_per_type(self):
        core = self.make()
        core.start()
        with self.assertLogs("dmd", "INFO") as logs:
            for payload in ("zap501|a", "zap501|b", "shutdwn|", "zap501|c", "shutdwn|", "fond2|stop"):
                self.assertEqual(core.message(DMD, payload), [])
        self.assertEqual(len(logs.output), 3)
        self.assertIn("'zap501|a'", logs.output[0])
        self.assertIn("'shutdwn|'", logs.output[1])
        self.assertIn("'fond2|stop'", logs.output[2])

    def test_backgrounds_of_4_6_5_not_rendered(self):
        # 52e complément : fond3| à fond12| (styles de la 4.6.5) ignorés comme fond1| et fond2|,
        # raison « not rendered », et non « unknown »
        for n in range(1, 13):
            with self.subTest(prefix=f"fond{n}"):
                self.assertEqual(C.parse(DMD, f"fond{n}|start|N"), C.Ignored("not rendered"))
        self.assertEqual(C.parse(DMD, "fond13|start|N"), C.Ignored("unknown"))
        core = self.make()
        core.start()
        with self.assertLogs("dmd", "INFO") as logs:
            self.assertEqual(core.message(DMD, "fond7|start|N"), [])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("(not rendered) : 'fond7|start|N'", logs.output[0])
        self.assertNotIn("unknown", logs.output[0])

    def test_invalid_once_per_type(self):
        core = self.make()
        with self.assertLogs("dmd", "INFO") as logs:
            core.message(DMD, "score|X")
            core.message(DMD, "score|X - X")
            core.message(DMD, "msgmove|a")
            core.message(R2D, "score9|X")          # autre type que score
        self.assertEqual(len(logs.output), 3)
        self.assertTrue(all("invalid message" in line for line in logs.output))

    def test_ignored_and_invalid_share_type(self):
        core = self.make()
        with self.assertLogs("dmd", "INFO") as logs:
            core.message(DMD, "pico|other")         # ignoré
            core.message(DMD, "pico")               # ignoré, même type
            core.message(DMD, "msg")                # invalide (type msg)
            core.message(DMD, "msg|a|b|c")          # invalide, même type
        self.assertEqual(len(logs.output), 2)

    def test_valid_message_not_logged_and_not_counted(self):
        core = self.make()
        with self.assertLogs("dmd", "INFO") as logs:
            core.message(DMD, "msg|a")
            core.message(DMD, "msg|a|b|c")          # premier msg invalide : journalisé
        self.assertEqual(len(logs.output), 1)

    def test_special_types(self):
        core = self.make()
        with self.assertLogs("dmd", "INFO") as logs:
            for topic, payload in ((DMD, ""), (DMD, "\n"), (DMD, b"\xff"), (DMD, b"msg|\xfe"),
                                   ("autre", "msg|a"), ("autre", "x"), ("encore", "x")):
                core.message(topic, payload)
        self.assertEqual(len(logs.output), 4)       # vide, pas UTF-8, autre, encore

    def test_message_type(self):
        self.assertEqual(C.message_type(DMD, "zap501|a|b"), "zap501")
        self.assertEqual(C.message_type(R2D, "shutdwn"), "shutdwn")
        self.assertEqual(C.message_type("x/y", "msg|a"), "topic x/y")
        self.assertEqual(C.message_type(DMD, "\r\n"), "empty")
        self.assertEqual(C.message_type(DMD, b"\xff"), "not UTF-8")

    def test_does_not_interrupt_attract(self):
        core = self.make()
        media = core.message(R2D, "demo|gif")[0]
        with self.assertLogs("dmd", "INFO"):
            self.assertEqual(core.message(DMD, "zapped|x"), [])
            self.assertEqual(core.message(DMD, "score|bad"), [])
        self.assertEqual(core.state, C.ATTRACT_GIF)
        self.assertIsInstance(core.media_done(media.token)[0], C.ShowMedia)


class Display(CoreBase):
    def test_text_default_colour(self):
        core = self.make()
        core.start()
        self.assertEqual(core.message(DMD, "msg|STRIKE"), [C.ShowText("STRIKE", (0, 0, 255))])
        self.assertEqual(core.state, C.DISPLAY)

    def test_text_scroll(self):
        core = self.make()
        self.assertEqual(core.message(DMD, "msgmovebcl|Bravo|left|2|5"),
                         [C.ShowText("Bravo", (0, 0, 255), None, "left", 2, 5.0)])

    def test_text_colour(self):
        core = self.make()
        self.assertEqual(core.message(DMD, "msgcolor|Bravo|255;0;0|0;0;0|left|"),
                         [C.ShowText("Bravo", (255, 0, 0), (0, 0, 0), "left", None, None)])

    def test_score_with_gif(self):
        core = self.make()
        out = core.message(DMD, "score|T20 - X - X||255;0;0|O")
        self.assertEqual(len(out), 1)
        media = out[0]
        self.assertEqual(media.folder, "Scores/T20")
        self.assertIsNone(media.name)
        self.assertEqual(media.fallback, C.ShowText("T20 - X - X", (255, 0, 0)))

    def test_score_without_gif(self):
        core = self.make()
        self.assertEqual(core.message(DMD, "score|T20 - X - X||255;0;0|N"),
                         [C.ShowText("T20 - X - X", (255, 0, 0))])

    def test_score_text_only(self):
        # X - X - X (début de volée) et MISS : pas de dossier, texte seul
        core = self.make()
        self.assertEqual(core.message(DMD, "score|X - X - X||"), [C.ShowText("X - X - X")])
        self.assertEqual(core.message(DMD, "score2|S1 - MISS - X||0;0;0|O"),
                         [C.ShowText("S1 - MISS - X", (0, 0, 0))])

    def test_score_last_dart(self):
        core = self.make()
        self.assertEqual(core.message(DMD, "score|T20 - DB - X|||O")[0].folder, "Scores/DB")

    def test_pattern(self):
        core = self.make()
        self.assertEqual(core.message(R2D, "pico|test_rgb"), [C.ShowPattern()])
        self.assertEqual(core.state, C.DISPLAY)


class Attract(CoreBase):
    def gif_of(self, out):
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], C.ShowMedia)
        self.assertEqual(out[0].folder, C.ATTRACT_FOLDER)
        return out[0]

    def test_after_delay(self):
        core = self.make()
        core.start()
        core.message(DMD, "msg|a")
        self.clock.advance(14.999)
        self.assertEqual(core.tick(), [])
        self.clock.advance(0.001)
        media = self.gif_of(core.tick())
        self.assertIn(media.name, self.GIFS)
        self.assertEqual(core.state, C.ATTRACT_GIF)
        self.assertEqual(core.tick(), [])

    def test_delay_setting(self):
        core = self.make(delay=2)
        core.message(DMD, "msg|a")
        self.clock.advance(2)
        self.gif_of(core.tick())

    def test_message_restarts_delay(self):
        core = self.make()
        core.message(DMD, "msg|a")
        self.clock.advance(10)
        core.message(DMD, "msg|b")
        self.clock.advance(10)
        self.assertEqual(core.tick(), [])
        self.clock.advance(5)
        self.gif_of(core.tick())

    def test_ignored_does_not_restart_delay(self):
        core = self.make()
        core.message(DMD, "msg|a")
        self.clock.advance(10)
        with self.assertLogs("dmd", "INFO"):
            core.message(DMD, "fond2|stop")
        self.clock.advance(5)
        self.gif_of(core.tick())

    def test_demo_gif_at_once(self):
        core = self.make()
        core.start()
        self.gif_of(core.message(R2D, "demo|gif"))
        self.assertEqual(core.state, C.ATTRACT_GIF)

    def test_message_interrupts(self):
        core = self.make()
        media = self.gif_of(core.message(R2D, "demo|gif"))
        self.assertEqual(core.message(DMD, "msg|Hop"), [C.ShowText("Hop")])
        self.assertEqual(core.state, C.DISPLAY)
        # Fin tardive du GIF interrompu : sans effet
        self.assertEqual(core.media_done(media.token), [])

    def test_score_media_then_fixed_text(self):
        # Le média, puis le texte fixe dans la couleur du joueur (48e complément)
        core = self.make()
        media = core.message(DMD, "score|T20 - X - X||255;0;0|O")[0]
        text = C.ShowText("T20 - X - X", (255, 0, 0))
        self.assertEqual(media.fallback, text)
        self.assertEqual(core.media_done(media.token), [text])
        self.assertEqual(core.state, C.DISPLAY)
        self.assertEqual(core.media_done(media.token), [])          # une seule fois

    def test_score_text_does_not_restart_attract_delay(self):
        core = self.make(gifs=["a.gif"])
        media = core.message(DMD, "score|SB - X - X|||O")[0]
        self.clock.advance(5)
        self.assertEqual(len(core.media_done(media.token)), 1)
        self.clock.advance(9.999)
        self.assertEqual(core.tick(), [])
        self.clock.advance(0.001)                                    # 15 s depuis le score
        self.assertEqual(core.tick()[0].folder, C.ATTRACT_FOLDER)

    def test_text_waits_for_score_media(self):
        # 49e complément : le texte n'interrompt plus le média, il s'affiche après
        core = self.make()
        media = core.message(DMD, "score|T20 - X - X|||O")[0]
        self.assertEqual(core.message(DMD, "msg|Hop"), [])
        other = core.message(DMD, "score|D20 - X - X|||O")[0]       # un score interrompt
        self.assertEqual(core.media_done(media.token), [])           # jeton du score précédent
        self.assertEqual(core.media_done(other.token), [other.fallback])   # « Hop » oublié

    def test_ignored_or_invalid_message_keeps_score_media(self):
        core = self.make()
        media = core.message(DMD, "score|T20 - X - X|||O")[0]
        with self.assertLogs("dmd", "INFO"):
            self.assertEqual(core.message(DMD, "zap501|x"), [])
            self.assertEqual(core.message(DMD, "msgcolor|x"), [])
        self.assertEqual(core.media_done(media.token), [media.fallback])

    def test_attract_cancels_score_media(self):
        core = self.make(gifs=["a.gif"])
        media = core.message(DMD, "score|T20 - X - X|||O")[0]
        core.message(R2D, "demo|gif")
        self.assertEqual(core.state, C.ATTRACT_GIF)
        self.assertEqual(core.media_done(media.token), [])

    def test_score_without_media_text_only(self):
        core = self.make()
        self.assertEqual(core.message(DMD, "score|T20 - X - X|||N"), [C.ShowText("T20 - X - X")])
        self.assertEqual(core.message(DMD, "score|X - X - X|||O"), [C.ShowText("X - X - X")])

    def test_stale_token(self):
        core = self.make()
        first = self.gif_of(core.message(R2D, "demo|gif"))
        second = self.gif_of(core.media_done(first.token))
        self.assertEqual(core.media_done(first.token), [])
        self.gif_of(core.media_done(second.token))

    def test_bag(self):
        gifs = [f"{n}.gif" for n in range(7)]
        core = self.make(gifs=gifs)
        media = self.gif_of(core.message(R2D, "demo|gif"))
        names = [media.name]
        for _ in range(7 * 6 - 1):
            media = self.gif_of(core.media_done(media.token))
            names.append(media.name)
        for start in range(0, len(names), 7):
            self.assertEqual(sorted(names[start:start + 7]), gifs)
        for a, b in zip(names, names[1:]):
            self.assertNotEqual(a, b)

    def test_bag_order_random(self):
        gifs = [f"{n:02}.gif" for n in range(20)]
        core = self.make(gifs=gifs)
        media = self.gif_of(core.message(R2D, "demo|gif"))
        names = [media.name]
        for _ in range(19):
            media = self.gif_of(core.media_done(media.token))
            names.append(media.name)
        self.assertNotEqual(names, gifs)
        self.assertNotEqual(names, gifs[::-1])

    def test_single_gif(self):
        core = self.make(gifs=["seul.gif"])
        media = self.gif_of(core.message(R2D, "demo|gif"))
        for _ in range(3):
            media = self.gif_of(core.media_done(media.token))
            self.assertEqual(media.name, "seul.gif")

    def test_set_gifs(self):
        core = self.make()
        core.set_gifs(["z.gif", "z.gif"])
        self.assertEqual(self.gif_of(core.message(R2D, "demo|gif")).name, "z.gif")

    def test_no_gif_welcome(self):
        core = self.make(gifs=[])
        self.assertEqual(core.message(R2D, "demo|gif"), [C.Welcome()])
        self.assertEqual(core.state, C.WELCOME)
        core.message(DMD, "msg|a")
        self.clock.advance(15)
        self.assertEqual(core.tick(), [C.Welcome()])
        # Pas de nouvelle commande à chaque tick ; nouvel essai après le délai
        self.assertEqual(core.tick(), [])
        core.set_gifs(["n.gif"])
        self.clock.advance(15)
        self.assertEqual(core.tick()[0].name, "n.gif")


class AttractClock(CoreBase):
    """Heure 10 s toutes les 2 min (50e complément, remplace « toutes les 10 GIF » du 48e) :
    échéance comptée depuis l'entrée en Attract Mode, puis depuis la fin de l'heure ; l'heure
    attend la fin du GIF en cours. Horloge qui avance : chaque GIF dure gif_s secondes."""

    def play(self, core, media, gif_s, until_s):
        """Fait passer des GIF de gif_s secondes jusqu'à ce que l'horloge ait avancé d'au moins
        until_s depuis l'appel, ou jusqu'à l'heure ; rend (dernière commande, GIF joués)."""
        start = self.clock.now
        count = 0
        while True:
            self.clock.advance(gif_s)
            count += 1
            out = core.media_done(media.token)
            if isinstance(out[0], C.ShowClock) or self.clock.now - start >= until_s * SEC:
                return out[0], count
            self.assertIsInstance(out[0], C.ShowMedia)
            media = out[0]

    def test_clock_every_two_minutes(self):
        core = self.make(synced=True)
        entry = self.clock.now
        media = core.message(R2D, "demo|gif")[0]
        out, count = self.play(core, media, 7, 1000)
        # GIF de 7 s : l'échéance (120 s) tombe pendant le 18e, l'heure attend sa fin (126 s)
        self.assertEqual(out, C.ShowClock(10))
        self.assertEqual(count, 18)
        self.assertEqual(self.clock.now - entry, 126 * SEC)
        self.assertEqual(core.state, C.ATTRACT_CLOCK)
        self.clock.advance(9.999)
        self.assertEqual(core.tick(), [])
        self.clock.advance(0.001)
        media = core.tick()[0]                      # fin de l'heure : 136 s
        self.assertIsInstance(media, C.ShowMedia)
        self.assertEqual(core.state, C.ATTRACT_GIF)
        end_of_clock = self.clock.now
        out, count = self.play(core, media, 7, 1000)
        self.assertEqual(out, C.ShowClock(10))      # 120 s après la fin de l'heure, à la fin d'un GIF
        self.assertEqual(self.clock.now - end_of_clock, 126 * SEC)

    def test_clock_on_time_when_gif_ends_on_due(self):
        core = self.make(synced=True)
        media = core.message(R2D, "demo|gif")[0]
        out, count = self.play(core, media, 10, 1000)
        self.assertEqual((out, count), (C.ShowClock(10), 12))  # 12 GIF de 10 s : 120 s pile

    def test_not_before_due(self):
        core = self.make(synced=True)
        media = core.message(R2D, "demo|gif")[0]
        out, count = self.play(core, media, 1, 119)
        self.assertIsInstance(out, C.ShowMedia)     # 119 s : pas encore
        self.assertEqual(count, 119)

    def test_long_gif_delays_clock(self):
        # GIF de 60 s (attract_gif_max_s) : l'heure attend sa fin, au plus 180 s après l'entrée
        core = self.make(synced=True)
        entry = self.clock.now
        media = core.message(R2D, "demo|gif")[0]
        self.clock.advance(119)
        out = core.media_done(media.token)[0]       # 1er GIF fini à 119 s : pas encore l'heure
        self.assertIsInstance(out, C.ShowMedia)
        self.clock.advance(1)
        self.assertEqual(core.tick(), [])           # échéance passée pendant le GIF : rien
        self.clock.advance(59)                      # 2e GIF de 60 s, fini à 179 s
        self.assertEqual(core.media_done(out.token), [C.ShowClock(10)])
        self.assertEqual(self.clock.now - entry, 179 * SEC)

    def test_no_clock_when_not_synced(self):
        core = self.make(synced=False)
        media = core.message(R2D, "demo|gif")[0]
        out, count = self.play(core, media, 7, 1200)
        self.assertIsInstance(out, C.ShowMedia)
        self.assertEqual(core.state, C.ATTRACT_GIF)

    def test_not_synced_at_due_retries_two_minutes_after_that_gif(self):
        core = self.make(synced=False)
        media = core.message(R2D, "demo|gif")[0]
        out, count = self.play(core, media, 7, 126)  # échéance manquée à la fin du 18e GIF (126 s)
        self.assertEqual(count, 18)
        media = out
        self.synced = True
        out, count = self.play(core, media, 7, 1000)
        # Nouvel essai 120 s après la fin de ce GIF : 126 + 120 = 246 s, fin de GIF à 252 s
        self.assertEqual(out, C.ShowClock(10))
        self.assertEqual(count, 18)

    def test_configurable_period(self):
        self.clock = FakeClock()
        core = C.DmdCore(clock=self.clock, gifs=self.GIFS, time_synced=lambda: True,
                         rng=random.Random(1), clock_every_s=30)
        media = core.message(R2D, "demo|gif")[0]
        out, count = self.play(core, media, 10, 1000)
        self.assertEqual((out, count), (C.ShowClock(10), 3))
        with self.assertRaises(ValueError):
            C.DmdCore(clock=self.clock, clock_every_s=0)

    def test_default_period_is_two_minutes(self):
        self.assertEqual(C.CLOCK_EVERY_S, 120)
        self.assertEqual(self.make().clock_every_us, 120 * SEC)

    def test_message_interrupts_clock(self):
        core = self.make(synced=True)
        media = core.message(R2D, "demo|gif")[0]
        out, _ = self.play(core, media, 7, 1000)
        self.assertEqual(out, C.ShowClock(10))
        self.assertEqual(core.message(DMD, "msg|a"), [C.ShowText("a")])
        self.clock.advance(10)
        self.assertEqual(core.tick(), [])
        self.assertEqual(core.state, C.DISPLAY)

    def test_ignored_message_does_not_touch_clock_or_delay(self):
        # 50e complément, Q8 : un message ignoré (fond1|) n'interrompt pas l'Attract Mode et
        # ne relance pas attract_delay_s ; l'heure vient toujours à son échéance
        core = self.make(synced=True)
        media = core.message(R2D, "demo|gif")[0]
        self.clock.advance(5)
        self.assertEqual(core.message(DMD, "fond1|"), [])
        self.assertEqual(core.state, C.ATTRACT_GIF)
        out, count = self.play(core, media, 5, 1000)
        self.assertEqual(out, C.ShowClock(10))
        self.assertEqual(count, 23)                 # 5 + 23 × 5 = 120 s

    def test_ignored_message_does_not_restart_attract_delay(self):
        core = self.make()
        core.start()
        self.clock.advance(10)
        core.message(DMD, "fond1|")
        self.clock.advance(5)
        self.assertIsInstance(core.tick()[0], C.ShowMedia)   # 15 s depuis le démarrage

    def test_count_restarts_on_entry(self):
        core = self.make(synced=True)
        media = core.message(R2D, "demo|gif")[0]
        self.play(core, media, 7, 100)              # 105 s d'Attract Mode
        core.message(DMD, "msg|a")
        self.clock.advance(15)
        media = core.tick()[0]                      # nouvelle entrée après attract_delay_s
        self.assertEqual(core.state, C.ATTRACT_GIF)
        out, count = self.play(core, media, 7, 1000)
        self.assertEqual(out, C.ShowClock(10))
        self.assertEqual(count, 18)                 # 120 s depuis la nouvelle entrée, pas avant


class AttractSource(CoreBase):
    """Liste des GIF relue à chaque nouveau sac et fichiers exclus (SR-c commit 4, 48e complément)."""

    def make_source(self, names):
        self.names = list(names)
        self.calls = 0

        def source():
            self.calls += 1
            return list(self.names)
        self.clock = FakeClock()
        return C.DmdCore(clock=self.clock, gif_source=source, rng=random.Random(2))

    def bag(self, core, media, count):
        seen = [media.name]
        for _ in range(count - 1):
            media = core.media_done(media.token)[0]
            seen.append(media.name)
        return media, seen

    def test_list_read_at_each_new_bag(self):
        core = self.make_source(["a.gif", "b.gif"])
        self.assertEqual(self.calls, 0)
        media = core.message(R2D, "demo|gif")[0]
        self.assertEqual(self.calls, 1)
        media, seen = self.bag(core, media, 2)
        self.assertEqual(sorted(seen), ["a.gif", "b.gif"])
        self.names = ["a.gif", "b.gif", "c.gif"]            # ajouté pendant le sac
        self.assertEqual(self.calls, 1)
        media = core.media_done(media.token)[0]             # nouveau sac : liste relue
        self.assertEqual(self.calls, 2)
        media, seen = self.bag(core, media, 3)
        self.assertEqual(sorted(seen), ["a.gif", "b.gif", "c.gif"])

    def test_removed_file_gone_next_bag(self):
        core = self.make_source(["a.gif", "b.gif", "c.gif"])
        media = core.message(R2D, "demo|gif")[0]
        media, _ = self.bag(core, media, 3)
        self.names = ["a.gif"]
        media = core.media_done(media.token)[0]
        self.assertEqual(media.name, "a.gif")
        self.assertEqual(core.media_done(media.token)[0].name, "a.gif")

    def test_empty_source_gives_welcome_then_retries(self):
        core = self.make_source([])
        self.assertEqual(core.message(R2D, "demo|gif"), [C.Welcome()])
        self.names = ["z.gif"]
        self.clock.advance(15)
        media = core.tick()[0]
        self.assertEqual(media.name, "z.gif")

    def test_source_error_logged_once(self):
        self.clock = FakeClock()

        def broken():
            raise OSError("illisible")
        core = C.DmdCore(clock=self.clock, gif_source=broken)
        with self.assertLogs("dmd", "WARNING") as logs:
            self.assertEqual(core.message(R2D, "demo|gif"), [C.Welcome()])
            self.clock.advance(15)
            self.assertEqual(core.tick(), [C.Welcome()])
        self.assertEqual(len(logs.records), 1)

    def test_exclude_from_current_bag(self):
        core = self.make_source(["a.gif", "b.gif", "c.gif"])
        media = core.message(R2D, "demo|gif")[0]
        rest = [g for g in ("a.gif", "b.gif", "c.gif") if g != media.name]
        core.exclude(rest[0])
        media = core.media_done(media.token)[0]
        self.assertEqual(media.name, rest[1])

    def test_exclude_survives_reload(self):
        core = self.make_source(["a.gif", "b.gif"])
        core.exclude("b.gif")
        media = core.message(R2D, "demo|gif")[0]
        for _ in range(6):
            self.assertEqual(media.name, "a.gif")
            media = core.media_done(media.token)[0]
        self.assertGreaterEqual(self.calls, 6)

    def test_all_excluded_gives_welcome(self):
        core = self.make_source(["a.gif"])
        core.exclude("a.gif")
        core.exclude("a.gif")                               # sans effet la seconde fois
        self.assertEqual(core.message(R2D, "demo|gif"), [C.Welcome()])

    def test_exclude_with_static_list(self):
        core = self.make(gifs=["a.gif", "b.gif"])
        core.exclude("a.gif")
        core.set_gifs(["a.gif", "b.gif"])
        media = core.message(R2D, "demo|gif")[0]
        self.assertEqual(media.name, "b.gif")


if __name__ == "__main__":
    unittest.main()


# --- Coups spéciaux et textes en attente (sous-phase SR-f, 49e complément) --------------------

def move_of(*darts):
    return C.special_move(darts)


class SpecialMoveTable(unittest.TestCase):
    """Une volée par ligne de la table du 49e complément."""

    def check(self, darts, rule, name, folder, file=None):
        move = C.special_move(darts)
        self.assertIsNotNone(move, darts)
        self.assertEqual((move.rule, move.name, move.folder, move.file), (rule, name, folder, file), darts)

    def test_rule_1_maximum(self):
        self.check(("T20", "T20", "T20"), 1, "MAXIMUM_TON_80", "MAXIMUM_TON_80")

    def test_rule_2_devil(self):
        self.check(("S6", "S6", "S6"), 2, "DEVIL", "DEVIL", "devil 2.gif")

    def test_rule_3_devil_triple(self):
        self.check(("T6", "T6", "T6"), 3, "DEVIL_TRIPLE", "DEVIL", "devil (triple) 2.gif")

    def test_rule_4_breakfast_any_order(self):
        for darts in itertools.permutations(("S1", "S5", "S20")):
            self.check(darts, 4, "BREAKFAST", "BREAKFAST")

    def test_rule_5_champagne_breakfast_any_order(self):
        for darts in itertools.permutations(("T1", "T5", "T20")):
            self.check(darts, 5, "CHAMPAGNE_BREAKFAST", "CHAMPAGNE_BREAKFAST")

    def test_rule_6_round_of_terms(self):
        self.check(("T1", "T2", "T3"), 6, "ROUND_OF_TERMS", "ROUND_OF_TERMS")

    def test_rule_7_bucket_of_nail(self):
        self.check(("S1", "S1", "S1"), 7, "BUCKET_OF_NAIL", "BUCKET_OF_NAIL")

    def test_rule_8_not_old_any_order(self):
        for darts in itertools.permutations(("S5", "S12", "S20")):
            self.check(darts, 8, "NOT_OLD", "NOT_OLD")

    def test_rule_9_black_hat(self):
        self.check(("DB", "DB", "DB"), 9, "BLACK_HAT_THREE_IN_THE_BLACK", "BLACK_HAT_THREE_IN_THE_BLACK")

    def test_rule_10_red_hat_no_folder(self):
        self.check(("SB", "SB", "SB"), 10, "RED_HAT", None)

    def test_rule_11_hat_trick(self):
        for darts in (("SB", "DB", "SB"), ("DB", "DB", "SB"), ("SB", "SB", "DB")):
            self.check(darts, 11, "HAT_TRICK", "HAT_TRICK")

    def test_rule_12_three_in_a_bed(self):
        for dart in ("S11", "D5", "S20"):
            self.check((dart,) * 3, 12, "THREE_IN_A_BED", "THREE_IN_A_BED")

    def test_rule_13_low_ton_100_to_150(self):
        for darts in (("T20", "D20", "MISS"),                           # 100
                      ("T20", "T20", "S20"),                            # 140
                      ("T20", "T20", "D15")):                           # 150
            self.check(darts, 13, "LOW_TON", "LOW_TON")
        self.assertIsNone(C.special_move(("T20", "D19", "MISS")))       # 98

    def test_rule_14_high_ton_151_to_179(self):
        for darts in (("T20", "T20", "D16"),                            # 152
                      ("T20", "T19", "DB"),                             # 167
                      ("T20", "T20", "DB")):                            # 170
            self.check(darts, 14, "HIGH_TON", "HIGH_TON")
        self.check(("T20", "T20", "S11"), 13, "LOW_TON", "LOW_TON")     # 131

    def test_rule_15_bail_out_no_folder(self):
        for third in ("T17", "T18", "T19", "T20"):
            self.check(("S1", "S2", third), 15, "BAIL_OUT", None)

    def test_rule_16_trophy_totals(self):
        volleys = {
            22: ("S10", "S10", "S2"), 33: ("S10", "S20", "S3"), 45: ("S20", "SB", "MISS"),
            66: ("D20", "D3", "S20"), 69: ("D20", "S9", "S20"), 77: ("DB", "S20", "S7"),
            88: ("DB", "S19", "S19"), 95: ("DB", "D20", "S5"),
        }
        for total, darts in volleys.items():
            self.assertEqual(sum(C.dart_points(d) for d in darts), total)
            name, folder = C.TROPHY_TOTALS[total]
            self.check(darts, 16, name, folder)
        self.assertEqual({folder for _, folder in C.TROPHY_TOTALS.values()},
                         {"DINKY_DOO", "FEATHERS", "BAG_O_NUT", "ROUTE_66", "HAPPY_MEAL",
                          "SUNSET_STRIP", "GARDEN_GATE_FAT_LADIES", "BABY_TON"})

    def test_lower_case_compared_upper(self):
        self.check(("t20", "T20", "t20"), 1, "MAXIMUM_TON_80", "MAXIMUM_TON_80")

    def test_no_rule(self):
        self.assertIsNone(C.special_move(("S1", "S2", "S3")))
        self.assertIsNone(C.special_move(("MISS", "S20", "S1")))       # 21

    def test_unused_folders_never_chosen(self):
        unused = {"WHITE_HORSE", "CIRCLE_IT", "STEADY", "TROMBONES", "VARIETIES", "WOODY"}
        self.assertFalse(unused & C.SPECIAL_FOLDERS)


class SpecialMovePriority(unittest.TestCase):
    def test_s11_three_times_three_in_a_bed_before_trophy(self):
        # 33 points, mais la ligne 12 passe avant les trophées (Q5)
        move = C.special_move(("S11", "S11", "S11"))
        self.assertEqual((move.rule, move.folder), (12, "THREE_IN_A_BED"))

    def test_sb_three_times_red_hat_stops_search(self):
        # Ligne 10 sans dossier : pas de HAT_TRICK ni de THREE_IN_A_BED (ligne 11, 12)
        move = C.special_move(("SB", "SB", "SB"))
        self.assertEqual((move.rule, move.folder), (10, None))

    def test_three_miss_nothing(self):
        self.assertIsNone(C.special_move(("MISS", "MISS", "MISS")))
        self.assertIsNone(C.special_move(("miss", "Miss", "MISS")))

    def test_unknown_value_nothing(self):
        for darts in (("T20", "T20", "T21"), ("S0", "S0", "S0"), ("T20", "T20", "BULL"),
                      ("T20", "T20", "")):
            self.assertIsNone(C.special_move(darts), darts)

    def test_not_thrown_nothing(self):
        self.assertIsNone(C.special_move(("T20", "T20", "X")))
        self.assertIsNone(C.special_move(("X", "X", "X")))

    def test_maximum_before_round_of_terms(self):
        self.assertEqual(C.special_move(("T20",) * 3).rule, 1)
        self.assertEqual(C.special_move(("T6",) * 3).rule, 3)
        self.assertEqual(C.special_move(("T5", "T1", "T20")).rule, 5)

    def test_db_three_times_black_hat_before_hat_trick(self):
        self.assertEqual(C.special_move(("DB",) * 3).rule, 9)

    def test_ton_before_bail_out_and_trophies(self):
        self.assertEqual(C.special_move(("S20", "D20", "T20")).rule, 13)      # 120, 3e T20
        self.assertEqual(C.special_move(("S1", "S1", "T20")).rule, 15)        # 62
        self.assertEqual(C.special_move(("S2", "S20", "T20")).rule, 15)       # 82, avant rien
        self.assertEqual(C.special_move(("S2", "D5", "T19")).rule, 15)        # 69, avant HAPPY_MEAL


class SpecialMoveCore(CoreBase):
    """Le cœur choisit le coup spécial, le score ordinaire ou le texte seul."""

    def make_av(self, available=None, special_moves=True):
        self.clock = FakeClock()
        self.asked = []

        def media_available(folder, name):
            self.asked.append((folder, name))
            return available is None or (folder, name) in available or folder in available
        return C.DmdCore(clock=self.clock, gifs=["a.gif"], rng=random.Random(1),
                         special_moves=special_moves, media_available=media_available)

    def score(self, core, darts, flag="O"):
        out = core.message(DMD, "score|%s||255;0;0|%s" % (darts, flag))
        self.assertEqual(len(out), 1)
        return out[0]

    def test_special_move_media(self):
        core = self.make_av()
        cmd = self.score(core, "T20 - T20 - T20")
        self.assertEqual((cmd.folder, cmd.name), ("SpecialsMoves/MAXIMUM_TON_80", None))
        self.assertEqual(cmd.fallback, C.ShowText("T20 - T20 - T20", (255, 0, 0)))
        self.assertEqual(core.media_done(cmd.token), [cmd.fallback])

    def test_devil_precise_file(self):
        core = self.make_av()
        self.assertEqual(self.score(core, "S6 - S6 - S6").name, "devil 2.gif")
        self.assertEqual(self.score(core, "T6 - T6 - T6").name, "devil (triple) 2.gif")
        self.assertIn(("SpecialsMoves/DEVIL", "devil 2.gif"), self.asked)

    def test_rule_without_folder_ordinary_score(self):
        core = self.make_av()
        self.assertEqual(self.score(core, "SB - SB - SB").folder, "Scores/SB")
        self.assertEqual(self.score(core, "S1 - S2 - T19").folder, "Scores/T19")
        self.assertFalse([a for a in self.asked if a[0].startswith("SpecialsMoves")])

    def test_empty_folder_ordinary_score_next_rules_not_tried(self):
        # MAXIMUM_TON_80 vide : score ordinaire, et non ROUND_OF_TERMS (ligne 6)
        core = self.make_av(available={"SpecialsMoves/ROUND_OF_TERMS", "Scores/T20"})
        self.assertEqual(self.score(core, "T20 - T20 - T20").folder, "Scores/T20")
        # DEVIL sans le bon fichier : score ordinaire
        core = self.make_av(available={("SpecialsMoves/DEVIL", "devil (triple) 2.gif"), "Scores/S6"})
        self.assertEqual(self.score(core, "S6 - S6 - S6").folder, "Scores/S6")

    def test_nothing_available_text_only_no_hold(self):
        core = self.make_av(available=set())
        self.assertEqual(self.score(core, "T20 - T20 - T20"), C.ShowText("T20 - T20 - T20", (255, 0, 0)))
        # Aucun média en cours : le texte suivant s'affiche tout de suite
        self.assertEqual(core.message(DMD, "msg|Hop"), [C.ShowText("Hop")])

    def test_unknown_value_ordinary_score(self):
        core = self.make_av()
        # Valeur inconnue : pas de coup spécial, et elle ne compte pas comme MISS (120 -> LOW_TON)
        self.assertEqual(self.score(core, "MISS - T20 - T20").folder, "SpecialsMoves/LOW_TON")
        self.assertEqual(self.score(core, "T21 - T20 - T20").folder, "Scores/T20")
        # Dernière fléchette inconnue : pas de dossier de Scores, texte seul
        self.assertEqual(self.score(core, "T20 - T20 - BULL"), C.ShowText("T20 - T20 - BULL", (255, 0, 0)))

    def test_three_miss_ordinary(self):
        core = self.make_av()
        self.assertEqual(self.score(core, "MISS - MISS - MISS"), C.ShowText("MISS - MISS - MISS", (255, 0, 0)))

    def test_flag_n_or_x_no_special(self):
        core = self.make_av()
        self.assertEqual(self.score(core, "T20 - T20 - T20", "N"), C.ShowText("T20 - T20 - T20", (255, 0, 0)))
        self.assertEqual(self.score(core, "T20 - T20 - X").folder, "Scores/T20")

    def test_switch_off(self):
        core = self.make_av(special_moves=False)
        cmd = self.score(core, "T20 - T20 - T20")
        self.assertEqual(cmd.folder, "Scores/T20")
        # Coupés : les textes restent mis en attente pendant le média de Scores
        self.assertEqual(core.message(DMD, "msg|Hop"), [])
        self.assertEqual(core.media_done(cmd.token), [C.ShowText("Hop")])

    def test_other_score_prefixes(self):
        core = self.make_av()
        out = core.message(DMD, "score2|DB - DB - DB||0;255;0|O")[0]
        self.assertEqual(out.folder, "SpecialsMoves/BLACK_HAT_THREE_IN_THE_BLACK")


class PendingTexts(CoreBase):
    """Textes en attente pendant le média d'un score (49e complément), horloge qui avance."""

    def setUp(self):
        self.core = self.make(gifs=["a.gif"])
        self.clock.advance(3)
        self.media = self.core.message(DMD, "score|T20 - T20 - T20||255;0;0|O")[0]
        self.assertEqual(self.media.folder, "SpecialsMoves/MAXIMUM_TON_80")

    def test_last_text_shown_after_media(self):
        self.clock.advance(0.2)
        self.assertEqual(self.core.message(DMD, "msg|Lâchez"), [])
        self.clock.advance(1.6)
        self.assertEqual(self.core.message(R2D, "msgmove|Joueur 2|left"), [])
        self.clock.advance(2.0)
        self.assertEqual(self.core.tick(), [])
        self.assertEqual(self.core.media_done(self.media.token), [C.ShowText("Joueur 2", scroll="left")])
        self.assertEqual(self.core.state, C.DISPLAY)
        self.assertEqual(self.core.media_done(self.media.token), [])

    def test_no_text_score_text(self):
        self.clock.advance(4)
        self.assertEqual(self.core.media_done(self.media.token), [self.media.fallback])

    def test_text_after_media_immediate(self):
        self.clock.advance(4)
        self.core.media_done(self.media.token)
        self.clock.advance(1)
        self.assertEqual(self.core.message(DMD, "msg|Hop"), [C.ShowText("Hop")])

    def test_colour_text_kept_with_its_rendering(self):
        self.core.message(DMD, "msgcolor|Bravo|255;255;0|0;0;0|left|2")
        self.assertEqual(self.core.media_done(self.media.token),
                         [C.ShowText("Bravo", (255, 255, 0), (0, 0, 0), "left", 2)])

    def test_new_score_interrupts_and_forgets(self):
        self.core.message(DMD, "msg|Hop")
        self.clock.advance(0.5)
        other = self.core.message(DMD, "score|X - X - X||255;0;0|O")
        self.assertEqual(other, [C.ShowText("X - X - X", (255, 0, 0))])
        self.assertEqual(self.core.media_done(self.media.token), [])
        self.assertEqual(self.core.message(DMD, "msg|Après"), [C.ShowText("Après")])   # plus de média

    def test_demo_gif_interrupts_and_forgets(self):
        self.core.message(DMD, "msg|Hop")
        out = self.core.message(R2D, "demo|gif")
        self.assertEqual(out[0].folder, C.ATTRACT_FOLDER)
        self.assertEqual(self.core.media_done(self.media.token), [])
        self.assertEqual(self.core.message(DMD, "msg|Hop"), [C.ShowText("Hop")])        # Attract interrompu

    def test_pattern_interrupts_and_forgets(self):
        self.core.message(DMD, "msg|Hop")
        self.assertEqual(self.core.message(R2D, "pico|test_rgb"), [C.ShowPattern()])
        self.assertEqual(self.core.media_done(self.media.token), [])

    def test_ignored_and_invalid_change_nothing(self):
        self.core.message(DMD, "msg|Hop")
        with self.assertLogs("dmd", "INFO"):
            self.assertEqual(self.core.message(DMD, "zap501|x"), [])
            self.assertEqual(self.core.message(DMD, "msgcolor|x"), [])
            self.assertEqual(self.core.message(R2D, "time|start"), [])
        self.assertEqual(self.core.media_done(self.media.token), [C.ShowText("Hop")])

    def test_attract_delay_from_last_message(self):
        self.clock.advance(4)
        self.core.message(DMD, "msg|Hop")                # mis en attente, relance le délai
        self.clock.advance(2)
        self.assertEqual(self.core.media_done(self.media.token), [C.ShowText("Hop")])
        self.clock.advance(12.999)                       # 14,999 s depuis « Hop », 18,999 depuis le score
        self.assertEqual(self.core.tick(), [])
        self.clock.advance(0.001)
        self.assertEqual(self.core.tick()[0].folder, C.ATTRACT_FOLDER)

    def test_ignored_does_not_restart_delay(self):
        self.clock.advance(5)
        self.core.media_done(self.media.token)
        with self.assertLogs("dmd", "INFO"):
            self.core.message(DMD, "zap501|x")
        self.clock.advance(10)                           # 15 s depuis le score
        self.assertEqual(self.core.tick()[0].folder, C.ATTRACT_FOLDER)

    def test_ordinary_score_media_holds_texts_too(self):
        core = self.make()
        media = core.message(DMD, "score|S1 - X - X|||O")[0]         # PPM ou GIF de Scores
        self.assertEqual(media.folder, "Scores/S1")
        self.assertEqual(core.message(DMD, "msg|Hop"), [])
        self.clock.advance(2)
        self.assertEqual(core.media_done(media.token), [C.ShowText("Hop")])
