# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Les trois MCP23017 (§ 3.2), sur le cœur 0.

Registres en IOCON.BANK = 0 (A et B alternés) : un mot de 16 bits s'écrit
en une transaction (port A puis port B), bit 0 à 7 = GPA0 à GPA7, bit 8 à
15 = GPB0 à GPB7.

Règles (§ 3.2, v0.3.14) :
- au démarrage, impulsion basse d'environ 1 ms sur GP28, puis configuration,
  sorties (OLAT) à 0 **avant** de fixer le sens des broches ;
- GPA7 et GPB7 jamais en entrée ; broches libres en sorties à 0 (board.IODIR) ;
- U3 : IOCON.MIRROR = 1, tirages internes et interruption sur changement
  pour les boutons seulement ;
- un échec d'accès est retenté aussitôt ; 2 échecs consécutifs : puce
  absente, `E MCP 0x.. absent` une seule fois ;
- nouvelle recherche : une puce absente par seconde au plus, une tentative
  par tour de boucle ; puce retrouvée : reconfigurée, `E MCP 0x.. back`.
"""

import proto as P
import board

IODIR = 0x00
GPINTEN = 0x04
DEFVAL = 0x06
INTCON = 0x08
IOCON = 0x0A
GPPU = 0x0C
GPIO = 0x12
OLAT = 0x14

IOCON_MIRROR = 0x40
I2C_FREQ = 400000
I2C_TIMEOUT_US = 5000
PROBE_PERIOD_MS = 1000
RESET_PULSE_MS = 1


class McpBus:
    def __init__(self, i2c, clock, reset_pin, emit, on_change=None, sleep_ms=None):
        self.i2c = i2c
        self.clock = clock
        self.reset_pin = reset_pin
        self.emit = emit                      # ligne de protocole à émettre
        self.on_change = on_change            # fonction (adresse, présente)
        self.sleep_ms = sleep_ms
        self.present = {}
        for a in board.MCP_ADDRS:
            self.present[a] = False
        self.reported = {}                    # adresse -> "absent" déjà émis
        self.next_probe = 0                   # indice de la prochaine puce à rechercher
        self.last_probe = clock.ticks_ms()
        self._buf2 = bytearray(2)

    # ------------------------------------------------------------ accès

    def _try(self, fn):
        """Deux essais ; renvoie (True, résultat) ou (False, None)."""
        for _ in range(2):
            try:
                return True, fn()
            except OSError:
                pass
        return False, None

    def write_word(self, addr, reg, value):
        """Écrit un mot ; False si la puce est absente ou vient de disparaître."""
        if not self.present[addr]:
            return False
        buf = bytes((value & 0xFF, (value >> 8) & 0xFF))
        ok, _ = self._try(lambda: self.i2c.writeto_mem(addr, reg, buf))
        if not ok:
            self._lost(addr)
        return ok

    def read_word(self, addr, reg):
        """Lit un mot ; None si la puce est absente ou vient de disparaître."""
        if not self.present[addr]:
            return None
        ok, data = self._try(lambda: self.i2c.readfrom_mem(addr, reg, 2))
        if not ok:
            self._lost(addr)
            return None
        return data[0] | data[1] << 8

    # ------------------------------------------------------------ présence

    def _lost(self, addr):
        self.present[addr] = False
        if self.reported.get(addr) != "absent":
            self.reported[addr] = "absent"
            self.emit(P.msg_error(P.E_MCP, "0x%02X absent" % addr))
        if self.on_change is not None:
            self.on_change(addr, False)

    def _found(self, addr):
        self.present[addr] = True
        if self.reported.get(addr) == "absent":
            self.emit(P.msg_error(P.E_MCP, "0x%02X back" % addr))
        self.reported[addr] = "present"
        if self.on_change is not None:
            self.on_change(addr, True)

    def configure(self, addr):
        """Configuration complète d'une puce ; True si elle répond."""
        w = self.i2c.writeto_mem
        iodir = board.IODIR[addr]
        iocon = IOCON_MIRROR if addr == board.MCP_BUTTONS else 0
        seq = [
            # IOCON est vu aux adresses 0x0A et 0x0B : même valeur dans les deux octets,
            # sinon le second octet écrase le premier.
            (IOCON, iocon | iocon << 8),
            (OLAT, 0),                         # sorties à 0 avant le sens des broches
            (GPPU, iodir),                     # tirages sur les entrées seulement
            (GPINTEN, iodir if addr == board.MCP_BUTTONS else 0),
            (INTCON, 0),                       # interruption sur tout changement
            (IODIR, iodir),
        ]

        def run():
            for reg, v in seq:
                w(addr, reg, bytes((v & 0xFF, (v >> 8) & 0xFF)))
            back = self.i2c.readfrom_mem(addr, IODIR, 2)
            if (back[0] | back[1] << 8) != iodir:
                raise OSError(5)               # autre chose répond à cette adresse
            if addr == board.MCP_BUTTONS:
                self.i2c.readfrom_mem(addr, GPIO, 2)   # efface l'interruption
        ok, _ = self._try(run)
        return ok

    def reset_pulse(self):
        if self.reset_pin is None:
            return
        self.reset_pin.value(0)
        if self.sleep_ms is not None:
            self.sleep_ms(RESET_PULSE_MS)
        self.reset_pin.value(1)

    def boot(self):
        """Impulsion de remise à zéro, puis configuration des trois puces."""
        self.reset_pulse()
        for a in board.MCP_ADDRS:
            if self.configure(a):
                self._found(a)
            else:
                self._lost(a)
        self.last_probe = self.clock.ticks_ms()

    def any_absent(self):
        for a in board.MCP_ADDRS:
            if not self.present[a]:
                return True
        return False

    def step(self):
        """Nouvelle recherche : une puce absente au plus par PROBE_PERIOD_MS."""
        if not self.any_absent():
            return
        now = self.clock.ticks_ms()
        if self.clock.ticks_diff(now, self.last_probe) < PROBE_PERIOD_MS:
            return
        self.last_probe = now
        addrs = board.MCP_ADDRS
        for k in range(len(addrs)):
            i = (self.next_probe + k) % len(addrs)
            a = addrs[i]
            if not self.present[a]:
                self.next_probe = i + 1
                if self.configure(a):
                    self._found(a)
                return


def _word(bits, mask):
    w = 0
    for i in range(len(bits)):
        if mask >> i & 1:
            w |= 1 << bits[i]
    return w


def output_writers(bus):
    """Fonctions d'écriture des Toys (U4) et des voyants (U5) pour outputs.Outputs."""
    def toys(mask):
        return bus.write_word(board.MCP_TOYS, OLAT, _word(board.TOY_BITS, mask))

    def lights(mask):
        return bus.write_word(board.MCP_LIGHTS, OLAT, _word(board.LIGHT_BITS, mask))
    return toys, lights
