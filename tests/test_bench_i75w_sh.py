# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Tests sur PC de tools/bench_i75w/i75w.sh : ciblage explicite de la 75 W, jamais du Pico 2.

Faux sysfs, faux /dev/serial/by-id, faux picotool, faux python3 et faux fuser (scripts qui
notent leurs arguments) : aucune carte, aucun accès USB. Firmware C++ (2e8a:0009, port
usb-RaspyDarts_RaspyDarts_DMD_<série>-if00) et retour au MicroPython (40e complément).
"""

import hashlib
import os
import stat
import subprocess
import tempfile
import unittest

import _paths

SCRIPT = os.path.join(str(_paths.ROOT), "tools", "bench_i75w", "i75w.sh")
# Séries fictives (complément 77) : les vraies ne sont pas dans le dépôt.
PICO = "0123456789ABCDEF"
I75W = "FEDCBA9876543210"

FAKE_PICOTOOL = """#!/bin/sh
echo "picotool $*" >> "$FAKE_LOG"
case "$1" in
  info) printf 'Program Information\n name:          %s\n version:       0.1.0\n target chip:   %s\n' "$FAKE_NAME" "$FAKE_TARGET"
        printf 'Device Information\n type:      %s\n package:   %s\n chipid:    0x1122\n' "$FAKE_CHIP" "$FAKE_PACKAGE" ;;
  load) echo "Loading" ;;
  reboot)
        # picotool 2.1.1 : type de redémarrage (-a, -u, -g, -c) avant la sélection de
        # l'appareil (--bus, --address, --vid, --pid, --ser, -f, -F). « reboot -f -u --ser … »
        # échoue au banc du 25/11/2026 : « ERROR: unexpected option: --ser », code 255.
        shift; sel=0
        for a in "$@"; do
          case "$a" in
            -a|-u|-g|-c|--application|--usb) [ "$sel" = 0 ] || { echo "ERROR: unexpected option" >&2; exit 255; } ;;
            -f|-F|--force|--force-no-reboot|--ser|--bus|--address|--vid|--pid) sel=1 ;;
          esac
        done ;;
esac
"""

FAKE_PYTHON = """#!/bin/sh
echo "python $*" >> "$FAKE_LOG"
exit "$FAKE_PY_RC"
"""

FAKE_FUSER = """#!/bin/sh
echo "fuser $*" >> "$FAKE_LOG"
exit "$FAKE_FUSER_RC"
"""


def write_exe(path, text):
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class Bench(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.sysfs = os.path.join(t, "sys")
        self.byid = os.path.join(t, "by-id")
        os.makedirs(self.sysfs)
        os.makedirs(self.byid)
        self.log = os.path.join(t, "log")
        self.dmd = os.path.join(t, "dmd")
        write_exe(os.path.join(t, "picotool"), FAKE_PICOTOOL)
        write_exe(os.path.join(t, "python3"), FAKE_PYTHON)
        write_exe(os.path.join(t, "fuser"), FAKE_FUSER)
        self.env = dict(os.environ, I75W_SYSFS=self.sysfs, I75W_BYID=self.byid,
                        I75W_DMD_LINK=self.dmd, PICOTOOL=os.path.join(t, "picotool"),
                        PYTHON=os.path.join(t, "python3"), FUSER=os.path.join(t, "fuser"),
                        FAKE_LOG=self.log, FAKE_CHIP="RP2350", FAKE_PACKAGE="QFN60",
                        FAKE_NAME="RaspyDarts DMD", FAKE_TARGET="RP2350", FAKE_PY_RC="0",
                        FAKE_FUSER_RC="1", PICO_SERIAL=PICO, I75W_SERIAL=I75W)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def usb(self, pid, serial, manufacturer="Raspberry Pi", product="RP2350 Boot"):
        self.n += 1
        d = os.path.join(self.sysfs, "1-1.%d" % self.n)
        os.makedirs(d)
        for name, value in (("idVendor", "2e8a"), ("idProduct", pid), ("serial", serial),
                            ("busnum", "1"), ("devnum", str(self.n + 3)),
                            ("manufacturer", manufacturer), ("product", product)):
            with open(os.path.join(d, name), "w") as f:
                f.write(value + "\n")

    def fw(self, serial=I75W, manufacturer="RaspyDarts", product="RaspyDarts DMD"):
        """La 75 W sous le firmware C++ : appareil 0009 et son port by-id."""
        self.usb("0009", serial, manufacturer, product)
        return self.port(serial)

    def port(self, serial):
        p = os.path.join(self.byid, "usb-RaspyDarts_RaspyDarts_DMD_%s-if00" % serial.upper())
        open(p, "w").close()
        return p

    def run_sh(self, *args):
        r = subprocess.run(["bash", SCRIPT] + list(args), env=self.env, capture_output=True,
                           text=True, timeout=30)
        return r.returncode, r.stdout, r.stderr

    def logged(self):
        if not os.path.exists(self.log):
            return ""
        with open(self.log) as f:
            return f.read()


class TestSerialsRequired(Bench):
    """Complément 77 : PICO_SERIAL et I75W_SERIAL sans défaut, fournis par l'environnement."""

    def test_missing_serial_refused(self):
        self.usb("000f", I75W)
        for name in ("PICO_SERIAL", "I75W_SERIAL"):
            with self.subTest(name=name):
                env = dict(self.env)
                del env[name]
                r = subprocess.run(["bash", SCRIPT, "bootsel"], env=env, capture_output=True,
                                   text=True, timeout=30)
                self.assertEqual(r.returncode, 64, r.stderr)
                self.assertIn(name, r.stderr)
        self.assertNotIn("picotool", self.logged())

    def test_list_without_serials(self):
        """list sert à relever les séries : il marche sans elles, et ne vise aucune carte."""
        self.usb("000f", I75W)
        del self.env["PICO_SERIAL"], self.env["I75W_SERIAL"]
        rc, out, err = self.run_sh("list")
        self.assertEqual(rc, 0, err)
        self.assertIn(I75W, out)
        self.assertIn("non défini", out)

    def test_malformed_serial_refused(self):
        for value in ("0123", "0123456789ABCDEG", "0123456789ABCDEF0", "<SERIE_I75W>"):
            with self.subTest(value=value):
                self.env["I75W_SERIAL"] = value
                self.assertEqual(self.run_sh("list")[0], 64)

    def test_no_real_serial_in_script(self):
        with open(SCRIPT, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('PICO_SERIAL="${PICO_SERIAL:-}"', text)
        self.assertIn('I75W_SERIAL="${I75W_SERIAL:-}"', text)
        self.assertNotRegex(text, r"(?i)\b[0-9a-f]{16}\b")


class TestBootsel(Bench):
    def test_single_i75w(self):
        self.usb("0005", PICO)                # Pico 2 sous MicroPython : ignoré
        self.usb("000f", I75W)
        rc, out, _ = self.run_sh("bootsel")
        self.assertEqual((rc, out.strip()), (0, I75W))

    def test_targets_serial_not_first_found(self):
        self.usb("000f", "1111222233334444")  # autre RP2350 en BOOTSEL, trouvé en premier
        self.usb("000f", I75W.lower())
        rc, out, _ = self.run_sh("bootsel")
        self.assertEqual((rc, out.strip()), (0, I75W))

    def test_absent_or_twice(self):
        self.usb("000f", "1111222233334444")
        rc, _, err = self.run_sh("bootsel")
        self.assertEqual(rc, 2)
        self.assertIn("trouvée 0 fois", err)
        self.usb("000f", I75W)
        self.usb("000f", I75W)
        rc, _, err = self.run_sh("bootsel")
        self.assertEqual(rc, 2)
        self.assertIn("trouvée 2 fois", err)

    def test_pico_in_bootsel_not_targeted(self):
        self.usb("000f", PICO)
        self.assertEqual(self.run_sh("bootsel")[0], 2)
        self.env["I75W_SERIAL"] = PICO
        rc, _, err = self.run_sh("bootsel")
        self.assertEqual(rc, 3)
        self.assertIn("Pico 2", err)

    def test_info_and_chip_target_serial(self):
        self.usb("000f", I75W)
        self.assertEqual(self.run_sh("info")[0], 0)
        rc, out, _ = self.run_sh("chip")
        self.assertEqual(rc, 0)
        self.assertIn("type RP2350, boîtier QFN60", out)
        log = self.logged()
        self.assertIn("picotool info -a --ser %s" % I75W, log)
        self.assertIn("picotool info -d --ser %s" % I75W, log)

    def test_chip_not_rp2350(self):
        self.usb("000f", I75W)
        self.env["FAKE_CHIP"] = "RP2040"
        rc, _, err = self.run_sh("chip")
        self.assertEqual(rc, 3)
        self.assertIn("pas un RP2350", err)

    def test_chip_not_qfn60(self):
        self.usb("000f", I75W)
        for package in ("QFN80", "unknown", ""):
            self.env["FAKE_PACKAGE"] = package
            rc, _, err = self.run_sh("chip")
            self.assertEqual(rc, 3, package)
            self.assertIn("pas un QFN60 (RP2350A)", err)


class TestFlash(Bench):
    def uf2(self, name="i75w_display.uf2", data=b"uf2"):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p, hashlib.sha256(data).hexdigest()

    def test_flash_cpp_ok(self):
        self.usb("000f", I75W)
        p, sha = self.uf2()
        rc, out, err = self.run_sh("flash", p, sha.upper())
        self.assertEqual(rc, 0, err)
        self.assertIn("programme RaspyDarts DMD, version 0.1.0, puce RP2350", out)
        log = self.logged()
        self.assertIn("picotool info -a %s\n" % p, log)              # fichier seul
        self.assertIn("picotool load -v -x %s --ser %s" % (p, I75W), log)

    def test_flash_cpp_refusals(self):
        self.usb("000f", I75W)
        p, sha = self.uf2()
        self.assertEqual(self.run_sh("flash", p, "0" * 64)[0], 3)          # empreinte
        q, sha2 = self.uf2("i75w_rp2350-v0.0.5-micropython.uf2")
        rc, _, err = self.run_sh("flash", q, sha2)                          # sans --micropython
        self.assertEqual(rc, 3)
        self.assertIn("flash --micropython", err)
        r, sha3 = self.uf2("autre.uf2")
        self.assertEqual(self.run_sh("flash", r, sha3)[0], 3)              # autre nom
        for name in ("blink", "i75w_display", "RaspyDarts", "RaspyDarts Autre",
                     "RaspyDarts DMD x"):
            self.env["FAKE_NAME"] = name
            self.assertEqual(self.run_sh("flash", p, sha)[0], 3, name)     # autre programme
        self.env["FAKE_NAME"] = "RaspyDarts DMD"
        self.env["FAKE_TARGET"] = "RP2040"
        self.assertEqual(self.run_sh("flash", p, sha)[0], 3)               # autre puce cible
        self.env["FAKE_TARGET"] = "RP2350"
        self.env["FAKE_CHIP"] = "RP2040"
        self.assertEqual(self.run_sh("flash", p, sha)[0], 3)               # mauvaise puce
        self.env["FAKE_CHIP"] = "RP2350"
        self.env["FAKE_PACKAGE"] = "QFN80"
        self.assertEqual(self.run_sh("flash", p, sha)[0], 3)               # RP2350B
        self.assertEqual(self.run_sh("flash", p)[0], 64)                   # empreinte absente
        self.assertNotIn("picotool load", self.logged())

    def test_flash_needs_bootsel(self):
        self.fw()                                                           # firmware lancé
        p, sha = self.uf2()
        self.assertEqual(self.run_sh("flash", p, sha)[0], 2)
        self.assertNotIn("picotool load", self.logged())

    def test_flash_micropython_ok(self):
        self.usb("000f", I75W)
        p, sha = self.uf2("i75w_rp2350-v0.0.5-micropython.uf2")
        rc, _, err = self.run_sh("flash", "--micropython", p, sha)
        self.assertEqual(rc, 0, err)
        self.assertIn("picotool load -v -x %s --ser %s" % (p, I75W), self.logged())

    def test_flash_micropython_refusals(self):
        self.usb("000f", I75W)
        p, sha = self.uf2()
        self.assertEqual(self.run_sh("flash", "--micropython", p, sha)[0], 3)
        q, sha2 = self.uf2("i75w_rp2040-v0.0.5-micropython.uf2")
        self.assertEqual(self.run_sh("flash", "--micropython", q, sha2)[0], 3)
        r, _ = self.uf2("i75w_rp2350-v0.0.5-micropython.uf2")
        self.assertEqual(self.run_sh("flash", "--micropython", r, "0" * 64)[0], 3)
        self.assertEqual(self.run_sh("flash", "--micropython", r)[0], 64)
        self.assertNotIn("picotool load", self.logged())


class TestFirmware(Bench):
    def test_port_present(self):
        self.port(PICO)
        self.port("1111222233334444")                 # autre carte : ignorée
        p = self.port(I75W)
        rc, out, _ = self.run_sh("port")
        self.assertEqual((rc, out.strip()), (0, p))
        self.assertTrue(p.endswith("usb-RaspyDarts_RaspyDarts_DMD_%s-if00" % I75W))

    def test_port_lowercase_serial_setting(self):
        self.env["I75W_SERIAL"] = I75W.lower()
        p = self.port(I75W)
        rc, out, _ = self.run_sh("port")
        self.assertEqual((rc, out.strip()), (0, p))

    def test_port_absent(self):
        rc, _, err = self.run_sh("port")
        self.assertEqual(rc, 2)
        self.assertIn("port de la 75 W absent", err)
        self.assertIn("usb-RaspyDarts_RaspyDarts_DMD_%s-if00" % I75W, err)

    def test_to_bootsel_and_run(self):
        self.usb("0005", PICO)
        self.fw()
        rc, _, err = self.run_sh("to-bootsel")
        self.assertEqual(rc, 0, err)
        self.assertIn("picotool reboot -u -f --ser %s" % I75W, self.logged())
        self.assertEqual(self.run_sh("run")[0], 2)    # pas en BOOTSEL
        self.usb("000f", I75W)
        rc, _, err = self.run_sh("run")
        self.assertEqual(rc, 0, err)
        self.assertIn("picotool reboot --ser %s\n" % I75W, self.logged())

    def test_firmware_device_refusals(self):
        self.assertEqual(self.run_sh("to-bootsel")[0], 2)       # absente
        self.usb("0009", PICO, "RaspyDarts", "RaspyDarts DMD")   # autre série : ignorée
        self.assertEqual(self.run_sh("to-bootsel")[0], 2)
        self.fw(manufacturer="Raspberry Pi")
        rc, _, err = self.run_sh("to-bootsel")
        self.assertEqual(rc, 3)
        self.assertIn("fabricant", err)
        self.assertNotIn("reboot", self.logged())

    def test_firmware_device_twice_or_wrong_product(self):
        self.fw(product="Pico")
        rc, _, err = self.run_sh("check")
        self.assertEqual(rc, 3)
        self.assertIn("produit", err)
        self.fw()
        rc, _, err = self.run_sh("check")
        self.assertEqual(rc, 2)
        self.assertIn("trouvée 2 fois", err)
        self.assertNotIn("python", self.logged())

    def test_pico_serial_refused(self):
        self.fw(PICO)
        self.env["I75W_SERIAL"] = PICO
        for cmd in ("to-bootsel", "check", "port", "tx"):
            rc, _, err = self.run_sh(cmd)
            self.assertEqual(rc, 3, cmd)
            self.assertIn("Pico 2", err)
        self.assertEqual(self.logged(), "")

    def test_check_runs_hello_and_stats(self):
        p = self.fw()
        rc, _, err = self.run_sh("check")
        self.assertEqual(rc, 0, err)
        self.assertIn("contrôle bon : %s" % p, err)
        self.assertRegex(self.logged(), r"python \S*tx_bench\.py %s --check" % p)
        self.env["FAKE_PY_RC"] = "3"                  # protocole ou taille inattendus
        rc, _, err = self.run_sh("check")
        self.assertEqual(rc, 3)
        self.assertNotIn("contrôle bon", err)

    def test_tx(self):
        p = self.fw()
        rc, _, err = self.run_sh("tx", "--duration", "60", "--no-max")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("attention", err)
        log = self.logged()
        self.assertIn("fuser -s %s" % p, log)
        self.assertRegex(log, r"python \S*tx_bench\.py %s --duration 60 --no-max" % p)

    def test_tx_refused_when_port_busy(self):
        self.fw()
        self.env["FAKE_FUSER_RC"] = "0"
        rc, _, err = self.run_sh("tx")
        self.assertEqual(rc, 3)
        self.assertIn("arrêter hwd", err)
        self.assertNotIn("python", self.logged())

    def test_tx_warns_on_dmd_link(self):
        self.fw()
        open(self.dmd, "w").close()
        rc, _, err = self.run_sh("tx")
        self.assertEqual(rc, 0, err)
        self.assertIn("attention, %s existe" % self.dmd, err)


class TestCode(unittest.TestCase):
    def code_lines(self, name):
        with open(os.path.join(os.path.dirname(SCRIPT), name), encoding="utf-8") as f:
            return [l for l in f if not l.lstrip().startswith("#")]

    def test_no_uhubctl_command(self):
        bench = os.path.dirname(SCRIPT)
        for name in os.listdir(bench):
            if name.endswith((".sh", ".py")):
                self.assertFalse(any("uhubctl" in l for l in self.code_lines(name)), name)

    def test_no_mpremote_in_i75w_sh(self):
        self.assertFalse(any("mpremote" in l for l in self.code_lines("i75w.sh")))

    def test_unknown_command(self):
        r = subprocess.run(["bash", SCRIPT, "mp", "ls"], capture_output=True, text=True,
                           timeout=30)
        self.assertEqual(r.returncode, 64)


if __name__ == "__main__":
    unittest.main()
