"""Minimal Feetech STS3215 bus wrapper and an in-memory fake.

Both the SO-101 arm (IDs 1-6) and the LeKiwi base (IDs 7-9) share one serial
bus, so ``FeetechBus`` guards every operation with a lock. The real SDK
(``feetech-servo-sdk``, import name ``scservo_sdk``) is imported lazily inside
``open()`` so the core package and tests never need it installed.

Registers are addressed by ``(address, size)`` tuples (the module constants) or
by name. Values for registers listed in ``SIGN_BITS`` are sign-magnitude on the
wire and are encoded/decoded transparently, so callers always see plain
Python ints (``-5`` rather than ``0x8005``).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping

Register = tuple[int, int]

# STS3215 control table: name -> (address, size in bytes).
REGISTERS: dict[str, Register] = {
    "MODEL_NUMBER": (3, 2),
    "MIN_POSITION_LIMIT": (9, 2),
    "MAX_POSITION_LIMIT": (11, 2),
    "MAX_TORQUE_LIMIT": (16, 2),
    "PROTECTION_CURRENT": (28, 2),
    "HOMING_OFFSET": (31, 2),
    "OPERATING_MODE": (33, 1),  # 0 position, 1 velocity
    "OVERLOAD_TORQUE": (36, 1),
    "TORQUE_ENABLE": (40, 1),
    "ACCELERATION": (41, 1),
    "GOAL_POSITION": (42, 2),
    "GOAL_VELOCITY": (46, 2),
    "LOCK": (55, 1),
    "PRESENT_POSITION": (56, 2),
    "PRESENT_VELOCITY": (58, 2),
    "PRESENT_LOAD": (60, 2),
    "PRESENT_VOLTAGE": (62, 1),  # 0.1 V units
    "PRESENT_TEMPERATURE": (63, 1),
    "MOVING": (66, 1),
    "PRESENT_CURRENT": (69, 2),  # x6.5 mA
    "MAXIMUM_ACCELERATION": (85, 1),
}

MODEL_NUMBER = REGISTERS["MODEL_NUMBER"]
MIN_POSITION_LIMIT = REGISTERS["MIN_POSITION_LIMIT"]
MAX_POSITION_LIMIT = REGISTERS["MAX_POSITION_LIMIT"]
MAX_TORQUE_LIMIT = REGISTERS["MAX_TORQUE_LIMIT"]
PROTECTION_CURRENT = REGISTERS["PROTECTION_CURRENT"]
HOMING_OFFSET = REGISTERS["HOMING_OFFSET"]
OPERATING_MODE = REGISTERS["OPERATING_MODE"]
OVERLOAD_TORQUE = REGISTERS["OVERLOAD_TORQUE"]
TORQUE_ENABLE = REGISTERS["TORQUE_ENABLE"]
ACCELERATION = REGISTERS["ACCELERATION"]
GOAL_POSITION = REGISTERS["GOAL_POSITION"]
GOAL_VELOCITY = REGISTERS["GOAL_VELOCITY"]
LOCK = REGISTERS["LOCK"]
PRESENT_POSITION = REGISTERS["PRESENT_POSITION"]
PRESENT_VELOCITY = REGISTERS["PRESENT_VELOCITY"]
PRESENT_LOAD = REGISTERS["PRESENT_LOAD"]
PRESENT_VOLTAGE = REGISTERS["PRESENT_VOLTAGE"]
PRESENT_TEMPERATURE = REGISTERS["PRESENT_TEMPERATURE"]
MOVING = REGISTERS["MOVING"]
PRESENT_CURRENT = REGISTERS["PRESENT_CURRENT"]
MAXIMUM_ACCELERATION = REGISTERS["MAXIMUM_ACCELERATION"]

# Registers whose value is sign-magnitude on the wire: name -> sign bit index.
SIGN_BITS: dict[str, int] = {
    "HOMING_OFFSET": 11,
    "GOAL_POSITION": 15,
    "GOAL_VELOCITY": 15,
    "PRESENT_POSITION": 15,
    "PRESENT_VELOCITY": 15,
    "PRESENT_LOAD": 10,
}

STS3215_MODEL = 777
DEFAULT_BAUDRATE = 1_000_000
DEFAULT_PROTOCOL = 0

_NAME_BY_REGISTER: dict[Register, str] = {reg: name for name, reg in REGISTERS.items()}


class BusError(RuntimeError):
    """Raised when the servo bus cannot be opened or a motor does not answer."""


def reg_name(reg: Register | str) -> str:
    """Return the register name for a ``(address, size)`` tuple or a name (validated)."""
    if isinstance(reg, str):
        if reg not in REGISTERS:
            raise KeyError(reg)
        return reg
    return _NAME_BY_REGISTER[tuple(reg)]


def _resolve(reg: Register | str) -> tuple[str, int, int]:
    name = reg_name(reg)
    addr, size = REGISTERS[name]
    return name, addr, size


def encode_sign_magnitude(value: int, sign_bit: int) -> int:
    """Encode a signed int as sign-magnitude, e.g. ``-5`` with bit 15 -> ``0x8005``."""
    magnitude = abs(int(value))
    if magnitude >= (1 << sign_bit):
        raise ValueError(f"{value} does not fit in {sign_bit} magnitude bits")
    return magnitude | (1 << sign_bit) if value < 0 else magnitude


def decode_sign_magnitude(raw: int, sign_bit: int) -> int:
    """Decode a sign-magnitude field; ``-0`` decodes to ``0``."""
    magnitude = int(raw) & ((1 << sign_bit) - 1)
    return -magnitude if int(raw) & (1 << sign_bit) else magnitude


def _encode(name: str, value: int) -> int:
    bit = SIGN_BITS.get(name)
    return encode_sign_magnitude(value, bit) if bit is not None else int(value)


def _decode(name: str, raw: int) -> int:
    bit = SIGN_BITS.get(name)
    return decode_sign_magnitude(raw, bit) if bit is not None else int(raw)


def _split_bytes(raw: int, size: int, protocol: int) -> list[int]:
    """Byte order for sync writes: protocol 0 (STS/SMS) is little-endian, protocol 1 (SCS) big-endian."""
    data = [(raw >> (8 * i)) & 0xFF for i in range(size)]
    return data if protocol == 0 else data[::-1]


def _ids(ids: int | Iterable[int]) -> list[int]:
    return [int(ids)] if isinstance(ids, int) else [int(i) for i in ids]


class FeetechBus:
    """Thread-safe wrapper over ``scservo_sdk`` for one serial bus of STS3215 servos."""

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, protocol: int = DEFAULT_PROTOCOL):
        self.port = port
        self.baudrate = baudrate
        self.protocol = protocol
        self.lock = threading.Lock()
        self._sdk = None
        self._port_handler = None
        self._packet_handler = None

    # -- lifecycle -----------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._port_handler is not None

    def open(self) -> None:
        """Open the serial port. Never energizes motors."""
        with self.lock:
            if self._port_handler is not None:
                return
            try:
                import scservo_sdk  # lazy: only the Jetson has the robot group installed
            except ImportError as exc:
                raise BusError(
                    "scservo_sdk is not installed; run `uv sync --group robot` (feetech-servo-sdk)"
                ) from exc
            port_handler = scservo_sdk.PortHandler(self.port)
            try:
                opened = port_handler.openPort()
            except Exception as exc:  # pyserial raises SerialException on a missing port
                raise BusError(f"could not open servo port {self.port}: {exc}") from exc
            if not opened:
                raise BusError(f"could not open servo port {self.port}")
            try:
                baud_ok = port_handler.setBaudRate(self.baudrate)
            except Exception as exc:
                port_handler.closePort()
                raise BusError(f"could not set baudrate {self.baudrate} on {self.port}: {exc}") from exc
            if not baud_ok:
                port_handler.closePort()
                raise BusError(f"could not set baudrate {self.baudrate} on {self.port}")
            self._sdk = scservo_sdk
            self._port_handler = port_handler
            self._packet_handler = scservo_sdk.PacketHandler(self.protocol)

    def close(self) -> None:
        """Close the port. Safe to call any number of times."""
        with self.lock:
            port_handler, self._port_handler = self._port_handler, None
            self._packet_handler = None
            if port_handler is not None:
                port_handler.closePort()

    def _require_open(self) -> None:
        if self._port_handler is None:
            raise BusError(f"servo bus {self.port} is not open")

    def _ok(self, comm: int) -> bool:
        return comm == self._sdk.COMM_SUCCESS

    # -- single-motor access -------------------------------------------------

    def read(self, motor: int, reg: Register | str) -> int:
        """Read one register; signed registers are decoded."""
        name, addr, size = _resolve(reg)
        with self.lock:
            self._require_open()
            reader = self._packet_handler.read1ByteTxRx if size == 1 else self._packet_handler.read2ByteTxRx
            raw, comm, err = reader(self._port_handler, motor, addr)
            if not self._ok(comm):
                raise BusError(f"read {name} from motor {motor} failed (comm={comm}, error={err})")
        return _decode(name, raw)

    def write(self, motor: int, reg: Register | str, value: int) -> None:
        """Write one register; signed registers are encoded."""
        name, addr, size = _resolve(reg)
        raw = _encode(name, value)
        with self.lock:
            self._require_open()
            writer = self._packet_handler.write1ByteTxRx if size == 1 else self._packet_handler.write2ByteTxRx
            comm, err = writer(self._port_handler, motor, addr, raw)
            if not self._ok(comm):
                raise BusError(f"write {name}={value} to motor {motor} failed (comm={comm}, error={err})")

    # -- group access --------------------------------------------------------

    def sync_write(self, reg: Register | str, values: Mapping[int, int]) -> None:
        """Write the same register on several motors in one packet (no per-motor ack)."""
        name, addr, size = _resolve(reg)
        if not values:
            return
        encoded = {
            int(motor): _split_bytes(_encode(name, v), size, self.protocol) for motor, v in values.items()
        }
        with self.lock:
            self._require_open()
            group = self._sdk.GroupSyncWrite(self._port_handler, self._packet_handler, addr, size)
            for motor, data in encoded.items():
                if not group.addParam(motor, data):
                    raise BusError(f"sync_write {name}: could not add motor {motor}")
            comm = group.txPacket()
            group.clearParam()
            if not self._ok(comm):
                raise BusError(f"sync_write {name} failed (comm={comm})")

    def sync_read(self, reg: Register | str, ids: Iterable[int]) -> dict[int, int]:
        """Read the same register from several motors; motors that don't answer are omitted."""
        name, addr, size = _resolve(reg)
        motors = _ids(ids)
        if not motors:
            return {}
        with self.lock:
            self._require_open()
            group = self._sdk.GroupSyncRead(self._port_handler, self._packet_handler, addr, size)
            for motor in motors:
                group.addParam(motor)
            comm = group.txRxPacket()
            if not self._ok(comm):
                group.clearParam()
                raise BusError(f"sync_read {name} failed (comm={comm})")
            out = {}
            for motor in motors:
                if group.isAvailable(motor, addr, size):
                    out[motor] = _decode(name, group.getData(motor, addr, size))
            group.clearParam()
        return out

    # -- convenience ---------------------------------------------------------

    def scan(self, ids: Iterable[int] = range(1, 21)) -> dict[int, dict]:
        """Ping each id; for those that answer return ``{"model", "position", "voltage"}`` (volts)."""
        found: dict[int, dict] = {}
        for motor in _ids(ids):
            with self.lock:
                self._require_open()
                model, comm, _err = self._packet_handler.ping(self._port_handler, motor)
            if not self._ok(comm):
                continue
            found[motor] = {
                "model": int(model),
                "position": self.read(motor, PRESENT_POSITION),
                "voltage": self.read(motor, PRESENT_VOLTAGE) / 10.0,
            }
        return found

    def torque(self, ids: int | Iterable[int], on: bool) -> None:
        """Enable/disable torque: writes TORQUE_ENABLE then LOCK (on -> 1/1, off -> 0/0) per motor."""
        value = 1 if on else 0
        for motor in _ids(ids):
            self.write(motor, TORQUE_ENABLE, value)
            self.write(motor, LOCK, value)


class FakeBus:
    """In-memory bus with the same API as ``FeetechBus``.

    ``registers[motor][name]`` holds logical (already signed) values; ``writes`` logs every
    ``write``/``sync_write``/``torque`` as ``(motor, name, value)``; ``tick()`` snaps present
    position/velocity to the goals so a "move" completes instantly.
    """

    def __init__(
        self, ids: Iterable[int] = range(1, 10), port: str = "fake", baudrate: int = DEFAULT_BAUDRATE
    ):
        self.port = port
        self.baudrate = baudrate
        self.protocol = DEFAULT_PROTOCOL
        self.lock = threading.Lock()
        self.registers: dict[int, dict[str, int]] = {int(i): self._defaults() for i in ids}
        self.writes: list[tuple[int, str, int]] = []
        self._open = False

    @staticmethod
    def _defaults() -> dict[str, int]:
        regs = {name: 0 for name in REGISTERS}
        regs.update(
            MODEL_NUMBER=STS3215_MODEL,
            MIN_POSITION_LIMIT=0,
            MAX_POSITION_LIMIT=4095,
            MAX_TORQUE_LIMIT=1000,
            PROTECTION_CURRENT=500,
            OVERLOAD_TORQUE=80,
            GOAL_POSITION=2048,
            PRESENT_POSITION=2048,
            PRESENT_VOLTAGE=120,
            PRESENT_TEMPERATURE=30,
            MAXIMUM_ACCELERATION=254,
        )
        return regs

    # -- lifecycle -----------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        with self.lock:
            self._open = True

    def close(self) -> None:
        with self.lock:
            self._open = False

    # -- test helpers --------------------------------------------------------

    def _motor(self, motor: int) -> dict[str, int]:
        try:
            return self.registers[int(motor)]
        except KeyError:
            raise BusError(f"motor {motor} is not on the fake bus") from None

    def set(self, motor: int, reg: Register | str, value: int) -> None:
        """Set a register without logging it (simulate a sensor reading)."""
        name = reg_name(reg)
        with self.lock:
            self._motor(motor)[name] = int(value)

    def tick(self) -> None:
        """Advance the simulation: present position/velocity become the goals, nothing is moving."""
        with self.lock:
            for regs in self.registers.values():
                regs["PRESENT_POSITION"] = regs["GOAL_POSITION"]
                regs["PRESENT_VELOCITY"] = regs["GOAL_VELOCITY"]
                regs["MOVING"] = 0

    # -- single-motor access -------------------------------------------------

    def read(self, motor: int, reg: Register | str) -> int:
        name = reg_name(reg)
        with self.lock:
            return self._motor(motor)[name]

    def write(self, motor: int, reg: Register | str, value: int) -> None:
        name = reg_name(reg)
        with self.lock:
            self._write_locked(motor, name, value)

    def _write_locked(self, motor: int, name: str, value: int) -> None:
        _encode(name, value)  # validate range the same way the real bus would
        self._motor(motor)[name] = int(value)
        self.writes.append((int(motor), name, int(value)))

    # -- group access --------------------------------------------------------

    def sync_write(self, reg: Register | str, values: Mapping[int, int]) -> None:
        name = reg_name(reg)
        if not values:
            return
        with self.lock:
            for motor in values:
                self._motor(motor)
            for motor, value in values.items():
                self._write_locked(motor, name, value)

    def sync_read(self, reg: Register | str, ids: Iterable[int]) -> dict[int, int]:
        name = reg_name(reg)
        with self.lock:
            return {m: self.registers[m][name] for m in _ids(ids) if m in self.registers}

    # -- convenience ---------------------------------------------------------

    def scan(self, ids: Iterable[int] = range(1, 21)) -> dict[int, dict]:
        with self.lock:
            return {
                m: {
                    "model": self.registers[m]["MODEL_NUMBER"],
                    "position": self.registers[m]["PRESENT_POSITION"],
                    "voltage": self.registers[m]["PRESENT_VOLTAGE"] / 10.0,
                }
                for m in _ids(ids)
                if m in self.registers
            }

    def torque(self, ids: int | Iterable[int], on: bool) -> None:
        value = 1 if on else 0
        for motor in _ids(ids):
            self.write(motor, TORQUE_ENABLE, value)
            self.write(motor, LOCK, value)
