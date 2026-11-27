# Copyright (C) 2026 Gohy Yves
# Licence : GPL v3 (voir LICENSE à la racine du dépôt).
"""Module `machine` simulé, pour tester le firmware de la carte Pico sur PC.

Il simule ce que le firmware touche : broches (Pin), ADC, bus I2C avec des
MCP23017 simulés, accès aux registres (mem32 : SIO et PADS_BANK0 du RP2350),
identifiant, chien de garde matériel. `reset_sim()` remet tout à neuf.
"""

# Adresses du RP2350 utilisées par le firmware (fiche technique RP2350, § 2.2).
SIO_BASE = 0xD0000000
GPIO_IN = SIO_BASE + 0x004
GPIO_OUT = SIO_BASE + 0x010
GPIO_OUT_SET = SIO_BASE + 0x018
GPIO_OUT_CLR = SIO_BASE + 0x020
GPIO_OUT_XOR = SIO_BASE + 0x028
GPIO_OE = SIO_BASE + 0x030
PADS_BANK0 = 0x40038000
PAD_PDE = 1 << 2
PAD_PUE = 1 << 3
PAD_IE = 1 << 6
PAD_RESET = 0x116                   # ISO, DRIVE 4 mA, PDE, SCHMITT, IE à 0 (pico-sdk, pads_bank0.h)

EIO = 5
ETIMEDOUT = 110


class _Sim:
    def __init__(self):
        self.out = 0                     # GPIO_OUT
        self.oe = 0                      # GPIO_OE
        self.pads = {}                   # n° GPIO -> registre de pad
        self.pins = {}                   # n° GPIO -> Pin
        self.ext_in = {}                 # n° GPIO -> niveau imposé de l'extérieur
        self.adc = {}                    # n° GPIO -> valeur 16 bits
        self.i2c_devices = {}            # adresse -> objet simulé (MCP23017)
        self.i2c_fail = False            # bus en panne (délai dépassé)
        self.i2c_accesses = []           # adresses des accès tentés
        self.matrix = None               # fonction (colonne) -> masque des lignes à 0
        self.uid = bytes.fromhex("0123456789ABCDEF")
        self.resets = 0
        self.wdt = None
        self.mem_writes = []             # (adresse, valeur), pour vérifier les accès

    def pad(self, n):
        return self.pads.get(n, PAD_RESET)

    def level(self, n):
        """Niveau logique lu sur une broche d'entrée."""
        if self.oe >> n & 1:
            return self.out >> n & 1
        col_rows = None
        if self.matrix is not None and n < 12:
            col_rows = self.matrix(self.column())
            if col_rows >> n & 1:
                return 0
        if n in self.ext_in:
            return self.ext_in[n]
        p = self.pad(n)
        if p & PAD_PUE:
            return 1
        if p & PAD_PDE:
            return 0
        return 1                         # broche flottante : l'erratum E9 tire vers le haut

    def column(self):
        """Adresse présente sur A0 à A3 (GP15 à GP12)."""
        a = 0
        for i, pin in enumerate((15, 14, 13, 12)):
            if self.out >> pin & 1:
                a |= 1 << i
        return a

    def gpio_in(self):
        v = 0
        for n in range(30):
            if self.level(n):
                v |= 1 << n
        return v


sim = _Sim()


def reset_sim():
    global sim
    sim = _Sim()
    return sim


class _Mem32:
    def __getitem__(self, addr):
        if addr == GPIO_IN:
            return sim.gpio_in()
        if addr == GPIO_OUT:
            return sim.out
        if addr == GPIO_OE:
            return sim.oe
        if PADS_BANK0 + 4 <= addr < PADS_BANK0 + 4 + 30 * 4:
            return sim.pad((addr - PADS_BANK0 - 4) // 4)
        raise KeyError("mem32 lecture non simulée : 0x%08X" % addr)

    def __setitem__(self, addr, value):
        value &= 0xFFFFFFFF
        sim.mem_writes.append((addr, value))
        if addr == GPIO_OUT:
            sim.out = value
        elif addr == GPIO_OUT_SET:
            sim.out |= value
        elif addr == GPIO_OUT_CLR:
            sim.out &= ~value
        elif addr == GPIO_OUT_XOR:
            sim.out ^= value
        elif PADS_BANK0 + 4 <= addr < PADS_BANK0 + 4 + 30 * 4:
            sim.pads[(addr - PADS_BANK0 - 4) // 4] = value
        else:
            raise KeyError("mem32 écriture non simulée : 0x%08X" % addr)


mem32 = _Mem32()


class Pin:
    IN = 0
    OUT = 1
    OPEN_DRAIN = 2
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_FALLING = 4
    IRQ_RISING = 8

    def __init__(self, n, mode=-1, pull=-1, value=None):
        self.n = n
        self.handler = None
        self.mode = None
        sim.pins[n] = self
        self.init(mode, pull, value)

    def init(self, mode=-1, pull=-1, value=None):
        n = self.n
        if value is not None:
            self.value(value)
        if mode != -1:
            self.mode = mode
            if mode == Pin.OUT:
                sim.oe |= 1 << n
            else:
                sim.oe &= ~(1 << n)
        if pull != -1:                   # MicroPython rp2 : None = aucun tirage
            p = sim.pad(n) & ~(PAD_PUE | PAD_PDE)
            if pull == Pin.PULL_UP:
                p |= PAD_PUE
            elif pull == Pin.PULL_DOWN:
                p |= PAD_PDE
            sim.pads[n] = p

    def value(self, v=None):
        if v is None:
            return sim.level(self.n)
        if v:
            sim.out |= 1 << self.n
        else:
            sim.out &= ~(1 << self.n)
        return None

    def __call__(self, v=None):
        return self.value(v)

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)

    def irq(self, handler=None, trigger=IRQ_FALLING):
        self.handler = handler


class ADC:
    def __init__(self, src):
        self.n = src.n if isinstance(src, Pin) else 26 + src
        # Le vrai ADC coupe-t-il l'entrée numérique ? À vérifier au banc (§ 3.1.1) :
        # ici, non, pour que le firmware doive le faire lui-même.

    def read_u16(self):
        return sim.adc.get(self.n, 0)


class I2C:
    def __init__(self, ident, scl=None, sda=None, freq=400000, timeout=50000):
        self.ident = ident
        self.timeout = timeout

    def _dev(self, addr):
        if sim.i2c_fail:
            raise OSError(ETIMEDOUT)
        sim.i2c_accesses.append(addr)
        d = sim.i2c_devices.get(addr)
        if d is None or not d.present:
            raise OSError(EIO)
        if d.fail_next:
            d.fail_next -= 1
            raise OSError(EIO)
        return d

    def scan(self):
        if sim.i2c_fail:
            return []
        return sorted(a for a, d in sim.i2c_devices.items() if d.present)

    def writeto(self, addr, buf, stop=True):
        d = self._dev(addr)
        if len(buf) >= 1:
            d.write(buf[0], bytes(buf[1:]))
        return len(buf)

    def writeto_mem(self, addr, reg, buf):
        self._dev(addr).write(reg, bytes(buf))

    def readfrom_mem(self, addr, reg, n):
        return self._dev(addr).read(reg, n)

    def readfrom_mem_into(self, addr, reg, buf):
        data = self._dev(addr).read(reg, len(buf))
        buf[:] = data


class FakeMCP23017:
    """MCP23017 en mode IOCON.BANK = 0 (registres A et B alternés)."""
    IODIRA = 0x00
    GPPUA = 0x0C
    GPIOA = 0x12
    OLATA = 0x14
    IOCON = 0x0A

    def __init__(self, addr):
        self.addr = addr
        self.present = True
        self.regs = bytearray(0x16)
        self.regs[0] = self.regs[1] = 0xFF   # IODIR à la mise sous tension : entrées
        self.inputs = 0xFFFF                 # niveaux sur les broches (tirages : 1)
        self.writes = []
        self.fail_next = 0                   # nombre de prochains accès en échec

    def power_on_reset(self):
        self.regs = bytearray(0x16)
        self.regs[0] = self.regs[1] = 0xFF

    def write(self, reg, data):
        self.writes.append((reg, data))
        for i, b in enumerate(data):
            r = reg + i
            if r == 0x0B:
                r = 0x0A                     # IOCON est vu aux deux adresses
            if r < len(self.regs):
                self.regs[r] = b
                if r in (self.GPIOA, self.GPIOA + 1):
                    self.regs[r + 2] = b     # écrire GPIO écrit OLAT

    def read(self, reg, n):
        out = bytearray()
        for i in range(n):
            r = reg + i
            if r in (self.GPIOA, self.GPIOA + 1):
                port = r - self.GPIOA
                iodir = self.regs[port]
                pins = (self.inputs >> (8 * port)) & 0xFF
                olat = self.regs[self.OLATA + port]
                out.append((pins & iodir) | (olat & ~iodir & 0xFF))
            else:
                out.append(self.regs[r])
        return bytes(out)

    def word(self, reg):
        return self.regs[reg] | self.regs[reg + 1] << 8

    def outputs(self):
        """Niveaux des sorties (OLAT là où IODIR = 0)."""
        iodir = self.word(self.IODIRA)
        return self.word(self.OLATA) & ~iodir & 0xFFFF


class WDT:
    def __init__(self, id=0, timeout=5000):
        self.timeout = timeout
        self.feeds = 0
        sim.wdt = self

    def feed(self):
        self.feeds += 1


def unique_id():
    return sim.uid


def reset():
    sim.resets += 1
    raise SystemExit("machine.reset()")


def freq(*a):
    return 150000000
