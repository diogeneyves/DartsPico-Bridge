# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Patch 0010 : galerie (include/cgallery.py) en lecture seule sans mot de passe, et
protection CSRF sur tout POST (spécification, complément 70).

Vérifié ici, sur un clone temporaire de la copie de référence (lecture seule) où toute la
série est appliquée, avec le vrai Handler du cgallery.py patché, servi sur 127.0.0.1 (port
choisi par le système), sur un dossier de photos temporaire :
- sans mot de passe : les pages et les fichiers se lisent ; tout POST /delete/<partie>,
  /delete/<partie>/<fichier> et /delete-many renvoie 403 (texte gallery-error, lien
  gallery-back) sans rien supprimer ; aucun formulaire ni case de suppression dans les pages ;
- avec mot de passe : suppressions d'origine (fichier, partie, sélection), mauvais mot de
  passe 403 sans rien supprimer, formulaires affichés ;
- CSRF, sur /login et /delete : accepté si Origin, ou à défaut Referer, a le même hôte:port
  que Host ; refusé (403) si les deux manquent, si l'hôte ou le port diffère, ou si Origin
  vaut null (Origin faux : pas de repli sur Referer) ;
- textes gallery-warning et gallery-no-password : msgstr changés dans les 6 langues (clés et
  reste des .po inchangés), une ligne, gallery-no-password pas plus long que l'original, .mo
  égaux aux .po ;
- le patch ne touche que include/cgallery.py et les 12 fichiers de langue.
Aucun accès réseau hors 127.0.0.1. Sautés si la copie de référence est absente.
"""

import gettext
import html
import http.client
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.parse

import _orig
import _paths
from test_patches import fresh_clone, run_script

ROOT = str(_paths.ROOT)
PATCH_0010 = os.path.join(ROOT, "pi", "patches", "0010-galerie-lecture-seule-csrf.patch")
LANGUAGES = ("de_DE", "en_GB", "es_ES", "fr_FR", "it_IT", "nl_NL")
KEYS = ("gallery-warning", "gallery-no-password")
FRENCH = {
    "gallery-warning": "Sans mot de passe, la galerie est en lecture seule. Définissez un mot de "
                       "passe sur l'écran « Galerie » de la borne pour pouvoir supprimer.",
    "gallery-no-password": "Sans mot de passe : lecture seule, définissez-en un pour supprimer",
}
DATE = "2026_11_26"
GAMES = ("X01_202611261200", "Cricket_202611261230")
FILES = ("a.jpg", "b.jpg")
PASSWORD = "secret"


def po_lines(text):
    return text.split("\n")


def git_show(path, rev, rel):
    return subprocess.run(["git", "-C", path, "show", "%s:%s" % (rev, rel)], capture_output=True,
                          check=True).stdout


def catalog(data):
    return gettext.GNUTranslations(io.BytesIO(data))._catalog


def po_rel(language, ext):
    return "locales/%s/LC_MESSAGES/pydarts.%s" % (language, ext)


def msgstr(text, key):
    lines = po_lines(text)
    index = lines.index('msgid "%s"' % key)
    line = lines[index + 1]
    assert line.startswith('msgstr "') and line.endswith('"'), line
    return index + 1, line[len('msgstr "'):-1]


def unescape(text):
    """msgstr tel que gettext le rend : les \\" et \\\\ du .po décodés."""
    return re.sub(r'\\(["\\])', r"\1", text)


def load_gallery(copy):
    """cgallery.py du clone, chargé avec le paquet include du clone ; sys.modules et sys.path
    sont rendus tels quels."""
    saved = {n: m for n, m in sys.modules.items() if n == "include" or n.startswith("include.")}
    saved_path = list(sys.path)
    for name in saved:
        del sys.modules[name]
    package = types.ModuleType("include")
    package.__path__ = [os.path.join(copy, "include")]
    sys.modules["include"] = package
    try:
        spec = importlib.util.spec_from_file_location(
            "cgallery_patched_0010", os.path.join(copy, "include", "cgallery.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name in [n for n in sys.modules if n == "include" or n.startswith("include.")]:
            del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = saved_path
    return module


@unittest.skipUnless(_orig.available(), "copie de RaspyDarts absente (%s)" % _orig.ORIG)
class Patch0010(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert _orig.status() == ""
        sys.dont_write_bytecode = True
        cls.tmp = tempfile.mkdtemp()
        cls.copy = fresh_clone(cls.tmp)
        out = run_script(cls.copy)
        assert out.returncode == 0, out.stderr
        cls.gallery = load_gallery(cls.copy)
        cls.gallery.CACHE_DIR = os.path.join(cls.tmp, "cache")
        cls.pictures = os.path.join(cls.tmp, "Pictures")
        cls.videos = os.path.join(cls.tmp, "Videos")
        cls.password = ""
        cls.gallery.settings = lambda: {
            "pictures": cls.pictures, "videos": cls.videos, "order": "0",
            "password": cls.password, "_": cls.gallery.translator("fr_FR"), "lang": "fr"}
        cls.server = cls.gallery.ThreadingHTTPServer(("127.0.0.1", 0), cls.gallery.Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp)
        assert _orig.status() == "", "le test a écrit dans la copie de référence"

    def setUp(self):
        shutil.rmtree(self.pictures, ignore_errors=True)
        for game in GAMES:
            folder = os.path.join(self.pictures, DATE, game)
            os.makedirs(folder)
            for name in FILES:
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(b"photo " + name.encode())
        type(self).password = ""

    # --- outils ------------------------------------------------------------------------------

    def set_password(self):
        type(self).password = self.gallery.hash_password(PASSWORD)

    def origin(self, host="127.0.0.1", port=None):
        return "http://%s:%d" % (host, self.port if port is None else port)

    def request(self, method, path, form=None, headers=None, cookie=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        all_headers = dict(headers or {})
        if cookie and self.password:
            all_headers["Cookie"] = "%s=%s" % (self.gallery.COOKIE, self.gallery.token(self.password))
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form, doseq=True)
            all_headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        connection.close()
        return result

    def post(self, path, form=None, headers=None, **kwargs):
        if headers is None:
            headers = {"Origin": self.origin()}
        return self.request("POST", path, form or {}, headers, **kwargs)

    def ident(self, game=GAMES[0]):
        return self.gallery.partie_id(DATE, game)

    def path(self, game=GAMES[0], name=None):
        folder = os.path.join(self.pictures, DATE, game)
        return os.path.join(folder, name) if name else folder

    def assert_all_files_kept(self):
        for game in GAMES:
            for name in FILES:
                self.assertTrue(os.path.isfile(self.path(game, name)), (game, name))

    def assert_refused(self, status, data):
        self.assertEqual(status, 403)
        text = data.decode("utf-8")
        _ = self.gallery.translator("fr_FR")
        self.assertIn(_("gallery-error"), text)
        self.assertIn(_("gallery-back"), text)

    # --- sans mot de passe : lecture seule ---------------------------------------------------

    def test_read_only_pages_and_files_readable(self):
        status, _h, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("X01", data.decode("utf-8"))
        status, _h, data = self.request("GET", "/p/%s" % self.ident())
        self.assertEqual(status, 200)
        status, _h, data = self.request("GET", "/f/%s/a.jpg" % self.ident())
        self.assertEqual((status, data), (200, b"photo a.jpg"))
        status, _h, data = self.request("GET", "/d/%s/b.jpg" % self.ident())
        self.assertEqual((status, data), (200, b"photo b.jpg"))
        status, headers, _d = self.request("GET", "/zip/%s" % self.ident())
        self.assertEqual((status, headers["Content-Type"]), (200, "application/zip"))

    def test_read_only_no_delete_form(self):
        for path in ("/", "/p/%s" % self.ident()):
            with self.subTest(path=path):
                text = self.request("GET", path)[2].decode("utf-8")
                self.assertNotIn("/delete", text)
                self.assertNotIn('class="delete"', text)
                self.assertNotIn('name="p"', text)
                self.assertIn(html.escape(FRENCH["gallery-warning"]), text)

    def test_read_only_delete_refused(self):
        cases = (("/delete/%s/a.jpg" % self.ident(), {}),
                 ("/delete/%s" % self.ident(), {}),
                 ("/delete-many", {"p": ["%s/%s" % (DATE, g) for g in GAMES]}))
        for path, form in cases:
            with self.subTest(path=path):
                status, _h, data = self.post(path, form)
                self.assert_refused(status, data)
                self.assert_all_files_kept()

    # --- avec mot de passe : comportement d'origine ------------------------------------------

    def test_password_forms_shown(self):
        self.set_password()
        self.assertIn("/delete-many", self.request("GET", "/")[2].decode("utf-8"))
        text = self.request("GET", "/p/%s" % self.ident())[2].decode("utf-8")
        self.assertIn('action="/delete/%s/a.jpg"' % self.ident(), text)
        self.assertIn('action="/delete/%s"' % self.ident(), text)
        self.assertNotIn(html.escape(FRENCH["gallery-warning"]), text)

    def test_password_delete_file(self):
        self.set_password()
        status, headers, _d = self.post("/delete/%s/a.jpg" % self.ident(), {"password": PASSWORD})
        self.assertEqual((status, headers["Location"]), (303, "/p/%s" % self.ident()))
        self.assertFalse(os.path.exists(self.path(name="a.jpg")))
        self.assertTrue(os.path.isfile(self.path(name="b.jpg")))

    def test_password_delete_game_and_selection(self):
        self.set_password()
        status, headers, _d = self.post("/delete/%s" % self.ident(), {"password": PASSWORD})
        self.assertEqual((status, headers["Location"]), (303, "/"))
        self.assertFalse(os.path.exists(self.path()))
        status, _h, _d = self.post("/delete-many", {"password": PASSWORD,
                                                    "p": ["%s/%s" % (DATE, GAMES[1])]})
        self.assertEqual(status, 303)
        self.assertFalse(os.path.exists(os.path.join(self.pictures, DATE)))

    def test_password_wrong_refused(self):
        self.set_password()
        for path, form in (("/delete/%s/a.jpg" % self.ident(), {"password": "faux"}),
                           ("/delete/%s" % self.ident(), {}),
                           ("/delete-many", {"password": "faux", "p": ["%s/%s" % (DATE, GAMES[0])]})):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, form)[0], 403)
                self.assert_all_files_kept()

    # --- CSRF ---------------------------------------------------------------------------------

    def bad_headers(self):
        return {
            "aucun en-tête": {},
            "autre hôte": {"Origin": self.origin(host="192.0.2.1")},
            "autre port": {"Origin": self.origin(port=self.port + 1)},
            "Origin null": {"Origin": "null"},
            "Origin faux, Referer bon": {"Origin": self.origin(host="192.0.2.1"),
                                         "Referer": self.origin() + "/"},
            "Referer autre hôte": {"Referer": "http://192.0.2.1:%d/p/x" % self.port},
            "Referer autre port": {"Referer": "%s/p/x" % self.origin(port=self.port + 1)},
        }

    def good_headers(self):
        return {"Origin": {"Origin": self.origin()},
                "Referer seul": {"Referer": "%s/p/%s" % (self.origin(), self.ident())}}

    def test_csrf_login(self):
        self.set_password()
        for label, headers in self.bad_headers().items():
            with self.subTest(label=label):
                status, answer, data = self.post("/login?next=/", {"password": PASSWORD}, headers,
                                                 cookie=False)
                self.assert_refused(status, data)
                self.assertNotIn("Set-Cookie", answer)
        for label, headers in self.good_headers().items():
            with self.subTest(label=label):
                status, answer, _d = self.post("/login?next=/", {"password": PASSWORD}, headers,
                                               cookie=False)
                self.assertEqual(status, 303)
                self.assertIn("Set-Cookie", answer)

    def test_csrf_login_without_password(self):
        status, _h, data = self.post("/login?next=/", {"password": "x"}, {}, cookie=False)
        self.assert_refused(status, data)

    def test_csrf_delete(self):
        self.set_password()
        target = "/delete/%s/a.jpg" % self.ident()
        for label, headers in self.bad_headers().items():
            with self.subTest(label=label):
                status, _h, data = self.post(target, {"password": PASSWORD}, headers)
                self.assert_refused(status, data)
                self.assert_all_files_kept()
        for label, headers in self.good_headers().items():
            with self.subTest(label=label):
                status, _h, _d = self.post(target, {"password": PASSWORD}, headers)
                self.assertEqual(status, 303)
                self.assertFalse(os.path.exists(self.path(name="a.jpg")))
                with open(self.path(name="a.jpg"), "wb") as f:
                    f.write(b"photo a.jpg")

    def test_csrf_delete_many(self):
        self.set_password()
        status, _h, data = self.post("/delete-many", {"password": PASSWORD,
                                                      "p": ["%s/%s" % (DATE, GAMES[0])]}, {})
        self.assert_refused(status, data)
        self.assert_all_files_kept()

    # --- fichiers du patch et textes ---------------------------------------------------------

    def test_patch_touches_only_gallery_and_language_files(self):
        with open(PATCH_0010, encoding="utf-8") as f:
            text = f.read()
        files = sorted(l[len("diff --git a/"):].split(" b/")[0] for l in text.splitlines()
                       if l.startswith("diff --git a/"))
        expected = sorted(["include/cgallery.py"] + [po_rel(l, ext) for l in LANGUAGES
                                                     for ext in ("po", "mo")])
        self.assertEqual(files, expected)

    def test_po_only_two_msgstr_changed(self):
        # HEAD : fichiers nouveaux ; HEAD~1 : 0010 ; HEAD~2 : 0009
        for language in LANGUAGES:
            with self.subTest(language=language):
                before = git_show(self.copy, "HEAD~2", po_rel(language, "po")).decode("utf-8")
                with open(os.path.join(self.copy, po_rel(language, "po")), "rb") as f:
                    raw = f.read()
                self.assertNotIn(b"\r", raw)
                after = raw.decode("utf-8")
                old, new = po_lines(before), po_lines(after)
                self.assertEqual(len(old), len(new))
                changed = sorted(i for i, (a, b) in enumerate(zip(old, new)) if a != b)
                self.assertEqual(changed, sorted(msgstr(after, key)[0] for key in KEYS))
                for key in KEYS:
                    old_text, new_text = msgstr(before, key)[1], msgstr(after, key)[1]
                    self.assertNotEqual(new_text, old_text)
                    self.assertTrue(new_text.strip())
                    self.assertNotIn("\\n", new_text)
                    if key == "gallery-no-password":
                        self.assertLessEqual(len(new_text), len(old_text))
                if language == "fr_FR":
                    for key in KEYS:
                        self.assertEqual(msgstr(after, key)[1], FRENCH[key])

    def test_mo_equals_po(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                before = catalog(git_show(self.copy, "HEAD~2", po_rel(language, "mo")))
                with open(os.path.join(self.copy, po_rel(language, "mo")), "rb") as f:
                    after = catalog(f.read())
                with open(os.path.join(self.copy, po_rel(language, "po")), encoding="utf-8") as f:
                    po = f.read()
                expected = dict(before, **{key: unescape(msgstr(po, key)[1]) for key in KEYS})
                self.assertEqual(after, expected)


if __name__ == "__main__":
    unittest.main()
