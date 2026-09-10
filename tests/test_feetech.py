"""Tests for the Feetech STS3215 bus wrapper and its fake."""

import sys
import threading
import types

import pytest

from bob.hardware import feetech
from bob.hardware.feetech import (
    GOAL_POSITION,
    GOAL_VELOCITY,
    HOMING_OFFSET,
    LOCK,
    MODEL_NUMBER,
    PRESENT_LOAD,
    PRESENT_POSITION,
    PRESENT_VOLTAGE,
    REGISTERS,
    SIGN_BITS,
    TORQUE_ENABLE,
    BusError,
    FakeBus,
    FeetechBus,
    decode_sign_magnitude,
    encode_sign_magnitude,
    reg_name,
)

# --- register table ---------------------------------------------------------


def test_register_table_matches_datasheet():
    assert REGISTERS["TORQUE_ENABLE"] == (40, 1)
    assert REGISTERS["ACCELERATION"] == (41, 1)
    assert REGISTERS["GOAL_POSITION"] == (42, 2)
    assert REGISTERS["GOAL_VELOCITY"] == (46, 2)
    assert REGISTERS["LOCK"] == (55, 1)
    assert REGISTERS["PRESENT_POSITION"] == (56, 2)
    assert REGISTERS["PRESENT_VELOCITY"] == (58, 2)
    assert REGISTERS["PRESENT_LOAD"] == (60, 2)
    assert REGISTERS["PRESENT_VOLTAGE"] == (62, 1)
    assert REGISTERS["PRESENT_TEMPERATURE"] == (63, 1)
    assert REGISTERS["MOVING"] == (66, 1)
    assert REGISTERS["PRESENT_CURRENT"] == (69, 2)
    assert REGISTERS["OPERATING_MODE"] == (33, 1)
    assert REGISTERS["HOMING_OFFSET"] == (31, 2)
    assert REGISTERS["MIN_POSITION_LIMIT"] == (9, 2)
    assert REGISTERS["MAX_POSITION_LIMIT"] == (11, 2)
    assert REGISTERS["MAX_TORQUE_LIMIT"] == (16, 2)
    assert REGISTERS["PROTECTION_CURRENT"] == (28, 2)
    assert REGISTERS["OVERLOAD_TORQUE"] == (36, 1)
    assert REGISTERS["MAXIMUM_ACCELERATION"] == (85, 1)
    assert REGISTERS["MODEL_NUMBER"] == (3, 2)
    # Module-level constants mirror the table.
    assert feetech.TORQUE_ENABLE == (40, 1)
    assert feetech.MAXIMUM_ACCELERATION == (85, 1)
    assert MODEL_NUMBER == (3, 2)
    # No two names share an address.
    addrs = [addr for addr, _ in REGISTERS.values()]
    assert len(addrs) == len(set(addrs))


def test_reg_name_lookup():
    assert reg_name(GOAL_POSITION) == "GOAL_POSITION"
    assert reg_name((42, 2)) == "GOAL_POSITION"
    assert reg_name("LOCK") == "LOCK"
    with pytest.raises(KeyError):
        reg_name((200, 1))
    with pytest.raises(KeyError):
        reg_name("NOT_A_REGISTER")


def test_sign_bits_table():
    assert SIGN_BITS == {
        "HOMING_OFFSET": 11,
        "GOAL_POSITION": 15,
        "GOAL_VELOCITY": 15,
        "PRESENT_POSITION": 15,
        "PRESENT_VELOCITY": 15,
        "PRESENT_LOAD": 10,
    }
    for name in SIGN_BITS:
        assert name in REGISTERS


# --- sign-magnitude helpers -------------------------------------------------


@pytest.mark.parametrize(
    "value, bit, raw",
    [
        (0, 15, 0),
        (5, 15, 5),
        (-5, 15, 0x8005),
        (32767, 15, 0x7FFF),
        (-32767, 15, 0xFFFF),
        (-3, 11, 0x803),
        (-1000, 10, 0x400 | 1000),
        (600, 10, 600),
    ],
)
def test_encode_sign_magnitude(value, bit, raw):
    assert encode_sign_magnitude(value, bit) == raw


@pytest.mark.parametrize("value", [0, 1, -1, 5, -5, 1023, -1023, 2048, -2048, 32767, -32767])
def test_sign_magnitude_round_trip_bit15(value):
    assert decode_sign_magnitude(encode_sign_magnitude(value, 15), 15) == value


@pytest.mark.parametrize("bit", [10, 11, 15])
def test_sign_magnitude_round_trip_other_bits(bit):
    for value in (0, 7, -7, (1 << bit) - 1, -((1 << bit) - 1)):
        assert decode_sign_magnitude(encode_sign_magnitude(value, bit), bit) == value


def test_negative_zero_decodes_to_zero():
    # A servo may report "-0" (sign bit set, magnitude 0); that must read as 0.
    assert decode_sign_magnitude(1 << 15, 15) == 0
    assert decode_sign_magnitude(1 << 10, 10) == 0
    # Encoding 0 never sets the sign bit.
    assert encode_sign_magnitude(0, 15) == 0
    assert encode_sign_magnitude(-0, 15) == 0


def test_decode_ignores_bits_above_sign_bit():
    # Bits above the sign bit are not part of the field.
    assert decode_sign_magnitude(0x8005, 15) == -5
    assert decode_sign_magnitude(0x0405, 10) == -5


def test_encode_rejects_out_of_range_magnitude():
    with pytest.raises(ValueError):
        encode_sign_magnitude(1 << 15, 15)
    with pytest.raises(ValueError):
        encode_sign_magnitude(-(1 << 10), 10)


# --- FakeBus ----------------------------------------------------------------


def test_fakebus_defaults_and_scan():
    bus = FakeBus(ids=[1, 2, 7])
    bus.open()
    found = bus.scan(range(1, 21))
    assert sorted(found) == [1, 2, 7]
    for entry in found.values():
        assert entry == {"model": 777, "position": 2048, "voltage": 12.0}
    # Scan honours the requested id range.
    assert list(bus.scan([7, 8])) == [7]
    assert bus.scan([]) == {}
    bus.close()


def test_fakebus_default_ids_cover_arm_and_base():
    bus = FakeBus()
    assert sorted(bus.scan()) == list(range(1, 10))


def test_fakebus_default_registers():
    bus = FakeBus(ids=[1])
    assert bus.read(1, PRESENT_POSITION) == 2048
    assert bus.read(1, GOAL_POSITION) == 2048
    assert bus.read(1, PRESENT_VOLTAGE) == 120
    assert bus.read(1, MODEL_NUMBER) == 777
    assert bus.read(1, TORQUE_ENABLE) == 0
    assert bus.read(1, LOCK) == 0
    assert bus.read(1, PRESENT_LOAD) == 0
    assert bus.read(1, "MOVING") == 0
    assert bus.read(1, "OPERATING_MODE") == 0
    # Every named register has a default so later tasks never hit KeyError.
    for name in REGISTERS:
        assert isinstance(bus.read(1, name), int)


def test_fakebus_read_write_and_log():
    bus = FakeBus(ids=[1, 2])
    bus.write(1, GOAL_POSITION, 1000)
    bus.write(2, "GOAL_VELOCITY", -300)
    assert bus.read(1, GOAL_POSITION) == 1000
    assert bus.read(2, GOAL_VELOCITY) == -300
    assert bus.registers[1]["GOAL_POSITION"] == 1000
    assert bus.writes == [(1, "GOAL_POSITION", 1000), (2, "GOAL_VELOCITY", -300)]
    # Reads don't log.
    assert len(bus.writes) == 2
    # Present position is untouched until tick().
    assert bus.read(1, PRESENT_POSITION) == 2048


def test_fakebus_tick_moves_present_to_goal():
    bus = FakeBus(ids=[1, 2])
    bus.write(1, GOAL_POSITION, 100)
    bus.write(2, GOAL_POSITION, 3000)
    bus.registers[1]["MOVING"] = 1
    bus.tick()
    assert bus.read(1, PRESENT_POSITION) == 100
    assert bus.read(2, PRESENT_POSITION) == 3000
    assert bus.read(1, "MOVING") == 0
    # Velocity mode mirrors goal velocity into present velocity.
    bus.write(1, GOAL_VELOCITY, -500)
    bus.tick()
    assert bus.read(1, "PRESENT_VELOCITY") == -500
    # tick() is not a write.
    assert all(name.startswith("GOAL_") for _, name, _ in bus.writes)


def test_fakebus_sync_write_and_sync_read():
    bus = FakeBus(ids=[7, 8, 9])
    bus.sync_write(GOAL_VELOCITY, {7: 100, 8: -100, 9: 0})
    assert bus.writes == [(7, "GOAL_VELOCITY", 100), (8, "GOAL_VELOCITY", -100), (9, "GOAL_VELOCITY", 0)]
    assert bus.sync_read(GOAL_VELOCITY, [7, 8, 9]) == {7: 100, 8: -100, 9: 0}
    bus.tick()
    assert bus.sync_read("PRESENT_VELOCITY", [9, 7]) == {9: 0, 7: 100}
    assert bus.sync_read(PRESENT_POSITION, []) == {}
    assert bus.sync_write(GOAL_POSITION, {}) is None


def test_fakebus_torque_off_writes_zero_then_lock_zero():
    bus = FakeBus(ids=[1, 2])
    bus.torque([1, 2], True)
    assert bus.writes == [
        (1, "TORQUE_ENABLE", 1),
        (1, "LOCK", 1),
        (2, "TORQUE_ENABLE", 1),
        (2, "LOCK", 1),
    ]
    assert bus.read(1, TORQUE_ENABLE) == 1 and bus.read(2, LOCK) == 1
    bus.writes.clear()
    bus.torque([2, 1], False)
    assert bus.writes == [
        (2, "TORQUE_ENABLE", 0),
        (2, "LOCK", 0),
        (1, "TORQUE_ENABLE", 0),
        (1, "LOCK", 0),
    ]
    assert bus.read(1, TORQUE_ENABLE) == 0 and bus.read(1, LOCK) == 0
    # A single int is accepted too.
    bus.writes.clear()
    bus.torque(1, True)
    assert bus.writes == [(1, "TORQUE_ENABLE", 1), (1, "LOCK", 1)]


def test_fakebus_unknown_motor_or_register_raises():
    bus = FakeBus(ids=[1])
    with pytest.raises(BusError):
        bus.read(42, PRESENT_POSITION)
    with pytest.raises(BusError):
        bus.write(42, GOAL_POSITION, 0)
    with pytest.raises(BusError):
        bus.sync_write(GOAL_POSITION, {1: 0, 42: 0})
    with pytest.raises(KeyError):
        bus.read(1, "BOGUS")
    # sync_read simply omits motors that are not on the bus (like real hardware).
    assert bus.sync_read(PRESENT_POSITION, [1, 42]) == {1: 2048}


def test_fakebus_set_helper_does_not_log():
    bus = FakeBus(ids=[6])
    bus.set(6, PRESENT_LOAD, -450)
    assert bus.read(6, PRESENT_LOAD) == -450
    bus.set(6, "PRESENT_POSITION", 1234)
    assert bus.read(6, PRESENT_POSITION) == 1234
    assert bus.writes == []


def test_fakebus_open_close_idempotent_and_never_energizes():
    bus = FakeBus(ids=[1])
    assert bus.is_open is False
    bus.open()
    bus.open()
    assert bus.is_open is True
    assert bus.writes == []
    bus.close()
    bus.close()
    assert bus.is_open is False


def test_fakebus_is_thread_safe():
    bus = FakeBus(ids=[1])
    errors = []

    def worker(n):
        try:
            for i in range(200):
                bus.write(1, GOAL_POSITION, n * 1000 + i)
                bus.read(1, GOAL_POSITION)
                bus.tick()
        except Exception as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(bus.writes) == 800
    assert isinstance(bus.lock, type(threading.Lock()))


# --- FeetechBus against a fake scservo_sdk ----------------------------------


class _Port:
    instances: list = []
    fail_open = False
    fail_baud = False

    def __init__(self, name):
        self.name = name
        self.opened = False
        self.baud = None
        self.closed = 0
        _Port.instances.append(self)

    def openPort(self):
        if self.fail_open:
            return False
        self.opened = True
        return True

    def setBaudRate(self, baud):
        if self.fail_baud:
            return False
        self.baud = baud
        return True

    def closePort(self):
        self.closed += 1
        self.opened = False


class _Handler:
    """Byte-accurate stand-in for scservo_sdk's protocol_packet_handler."""

    COMM_SUCCESS = 0

    def __init__(self, protocol_end):
        self.protocol_end = protocol_end
        self.memory: dict[int, dict[int, int]] = {}  # id -> address -> byte
        self.calls: list[tuple] = []

    # -- helpers for tests
    def load(self, scs_id, address, size, raw):
        mem = self.memory.setdefault(scs_id, {})
        for i in range(size):
            mem[address + i] = (raw >> (8 * i)) & 0xFF

    def raw(self, scs_id, address, size):
        mem = self.memory[scs_id]
        return sum(mem[address + i] << (8 * i) for i in range(size))

    # -- SDK API
    def ping(self, port, scs_id):
        self.calls.append(("ping", scs_id))
        if scs_id not in self.memory:
            return 0, -6, 0
        return self.raw(scs_id, 3, 2), 0, 0

    def read1ByteTxRx(self, port, scs_id, address):
        self.calls.append(("read1", scs_id, address))
        if scs_id not in self.memory:
            return 0, -6, 0
        return self.raw(scs_id, address, 1), 0, 0

    def read2ByteTxRx(self, port, scs_id, address):
        self.calls.append(("read2", scs_id, address))
        if scs_id not in self.memory:
            return 0, -6, 0
        return self.raw(scs_id, address, 2), 0, 0

    def write1ByteTxRx(self, port, scs_id, address, data):
        self.calls.append(("write1", scs_id, address, data))
        if scs_id not in self.memory:
            return -6, 0
        self.load(scs_id, address, 1, data)
        return 0, 0

    def write2ByteTxRx(self, port, scs_id, address, data):
        self.calls.append(("write2", scs_id, address, data))
        if scs_id not in self.memory:
            return -6, 0
        self.load(scs_id, address, 2, data)
        return 0, 0


class _SyncWrite:
    def __init__(self, port, ph, start_address, data_length):
        self.ph = ph
        self.start_address = start_address
        self.data_length = data_length
        self.params: dict[int, list[int]] = {}

    def addParam(self, scs_id, data):
        if scs_id in self.params or len(data) > self.data_length:
            return False
        self.params[scs_id] = list(data)
        return True

    def clearParam(self):
        self.params.clear()

    def txPacket(self):
        if not self.params:
            return -9
        for scs_id, data in self.params.items():
            for i, b in enumerate(data):
                self.ph.memory.setdefault(scs_id, {})[self.start_address + i] = b
        self.ph.calls.append(("sync_write", self.start_address, self.data_length, dict(self.params)))
        return 0


class _SyncRead:
    def __init__(self, port, ph, start_address, data_length):
        self.ph = ph
        self.start_address = start_address
        self.data_length = data_length
        self.ids: list[int] = []
        self.fail = False

    def addParam(self, scs_id):
        if scs_id in self.ids:
            return False
        self.ids.append(scs_id)
        return True

    def clearParam(self):
        self.ids.clear()

    def txRxPacket(self):
        if not self.ids:
            return -9
        self.ph.calls.append(("sync_read", self.start_address, self.data_length, list(self.ids)))
        return -3 if self.ph.__dict__.get("sync_read_fails") else 0

    def isAvailable(self, scs_id, address, data_length):
        return scs_id in self.ids and scs_id in self.ph.memory

    def getData(self, scs_id, address, data_length):
        return self.ph.raw(scs_id, address, data_length)


@pytest.fixture
def fake_sdk(monkeypatch):
    """Inject a fake `scservo_sdk` so FeetechBus.open() never touches a serial port."""
    _Port.instances = []
    handlers: list[_Handler] = []

    def packet_handler(protocol_end):
        h = _Handler(protocol_end)
        handlers.append(h)
        return h

    mod = types.ModuleType("scservo_sdk")
    mod.PortHandler = _Port
    mod.PacketHandler = packet_handler
    mod.GroupSyncWrite = _SyncWrite
    mod.GroupSyncRead = _SyncRead
    mod.COMM_SUCCESS = 0
    monkeypatch.setitem(sys.modules, "scservo_sdk", mod)
    mod.handlers = handlers
    return mod


def _open_bus(fake_sdk, ids=(1, 2), **kwargs) -> tuple[FeetechBus, _Handler]:
    bus = FeetechBus("/dev/ttyFAKE", **kwargs)
    bus.open()
    handler = fake_sdk.handlers[-1]
    for scs_id in ids:
        handler.load(scs_id, 3, 2, 777)
        handler.load(scs_id, 56, 2, 2048)
        handler.load(scs_id, 62, 1, 121)
        handler.load(scs_id, 40, 1, 0)
        handler.load(scs_id, 55, 1, 0)
    return bus, handler


def test_feetechbus_import_is_lazy(monkeypatch):
    # Constructing the bus must not import the SDK.
    monkeypatch.delitem(sys.modules, "scservo_sdk", raising=False)
    bus = FeetechBus("/dev/ttyACM0")
    assert "scservo_sdk" not in sys.modules
    assert bus.port == "/dev/ttyACM0"
    assert bus.baudrate == 1_000_000
    assert bus.protocol == 0
    assert bus.is_open is False


def test_feetechbus_open_configures_port(fake_sdk):
    bus = FeetechBus("/dev/ttyFAKE")
    bus.open()
    port = _Port.instances[-1]
    assert port.name == "/dev/ttyFAKE"
    assert port.opened is True
    assert port.baud == 1_000_000
    assert fake_sdk.handlers[-1].protocol_end == 0
    assert bus.is_open is True
    # Opening twice is a no-op.
    bus.open()
    assert len(_Port.instances) == 1
    bus.close()
    assert port.closed == 1
    assert bus.is_open is False
    bus.close()
    assert port.closed == 1


def test_feetechbus_custom_baud_and_protocol(fake_sdk):
    bus = FeetechBus("/dev/ttyFAKE", baudrate=115200, protocol=1)
    bus.open()
    assert _Port.instances[-1].baud == 115200
    assert fake_sdk.handlers[-1].protocol_end == 1


def test_feetechbus_open_failures_raise_and_release_port(fake_sdk, monkeypatch):
    monkeypatch.setattr(_Port, "fail_open", True, raising=False)
    bus = FeetechBus("/dev/ttyFAKE")
    with pytest.raises(BusError, match="open"):
        bus.open()
    assert bus.is_open is False
    monkeypatch.setattr(_Port, "fail_open", False, raising=False)
    monkeypatch.setattr(_Port, "fail_baud", True, raising=False)
    with pytest.raises(BusError, match="baud"):
        bus.open()
    assert bus.is_open is False
    assert _Port.instances[-1].closed == 1


def test_feetechbus_open_wraps_serial_exceptions(fake_sdk, monkeypatch):
    # The real PortHandler.openPort() raises (pyserial SerialException) on a missing device.
    def boom(self):
        raise OSError("[Errno 2] could not open port")

    monkeypatch.setattr(_Port, "openPort", boom)
    bus = FeetechBus("/dev/ttyMISSING")
    with pytest.raises(BusError, match="could not open"):
        bus.open()
    assert bus.is_open is False


def test_feetechbus_requires_open(fake_sdk):
    bus = FeetechBus("/dev/ttyFAKE")
    with pytest.raises(BusError, match="not open"):
        bus.read(1, PRESENT_POSITION)
    with pytest.raises(BusError, match="not open"):
        bus.write(1, GOAL_POSITION, 0)
    with pytest.raises(BusError, match="not open"):
        bus.sync_write(GOAL_POSITION, {1: 0})
    with pytest.raises(BusError, match="not open"):
        bus.sync_read(PRESENT_POSITION, [1])
    with pytest.raises(BusError, match="not open"):
        bus.scan()


def test_feetechbus_read_write_unsigned(fake_sdk):
    bus, handler = _open_bus(fake_sdk)
    assert bus.read(1, PRESENT_VOLTAGE) == 121
    assert bus.read(1, MODEL_NUMBER) == 777
    bus.write(1, TORQUE_ENABLE, 1)
    bus.write(1, "ACCELERATION", 30)
    bus.write(1, "MAX_TORQUE_LIMIT", 500)
    assert handler.raw(1, 40, 1) == 1
    assert handler.raw(1, 41, 1) == 30
    assert handler.raw(1, 16, 2) == 500
    assert ("write1", 1, 40, 1) in handler.calls
    assert ("write2", 1, 16, 500) in handler.calls
    assert bus.read(1, "MAX_TORQUE_LIMIT") == 500


def test_feetechbus_write_encodes_sign_magnitude(fake_sdk):
    bus, handler = _open_bus(fake_sdk)
    bus.write(1, GOAL_POSITION, -5)
    assert handler.raw(1, 42, 2) == 0x8005
    assert ("write2", 1, 42, 0x8005) in handler.calls
    bus.write(1, GOAL_VELOCITY, -600)
    assert handler.raw(1, 46, 2) == 0x8000 | 600
    bus.write(1, HOMING_OFFSET, -3)
    assert handler.raw(1, 31, 2) == 0x803
    bus.write(1, GOAL_POSITION, 1500)
    assert handler.raw(1, 42, 2) == 1500


def test_feetechbus_read_decodes_sign_magnitude(fake_sdk):
    bus, handler = _open_bus(fake_sdk)
    handler.load(1, 56, 2, 0x8005)
    assert bus.read(1, PRESENT_POSITION) == -5
    handler.load(1, 58, 2, 0x8000 | 250)
    assert bus.read(1, "PRESENT_VELOCITY") == -250
    handler.load(1, 60, 2, 0x400 | 300)
    assert bus.read(1, PRESENT_LOAD) == -300
    handler.load(1, 60, 2, 300)
    assert bus.read(1, PRESENT_LOAD) == 300
    handler.load(1, 31, 2, 0x800 | 12)
    assert bus.read(1, HOMING_OFFSET) == -12
    handler.load(1, 56, 2, 0x8000)  # "-0"
    assert bus.read(1, PRESENT_POSITION) == 0


def test_feetechbus_read_write_errors(fake_sdk):
    bus, handler = _open_bus(fake_sdk, ids=(1,))
    with pytest.raises(BusError):
        bus.read(9, PRESENT_POSITION)
    with pytest.raises(BusError):
        bus.write(9, GOAL_POSITION, 0)
    with pytest.raises(KeyError):
        bus.read(1, "BOGUS")


def test_feetechbus_sync_write_packs_little_endian_and_signs(fake_sdk):
    bus, handler = _open_bus(fake_sdk, ids=(7, 8, 9))
    bus.sync_write(GOAL_VELOCITY, {7: 0x1234, 8: -5, 9: 0})
    call = [c for c in handler.calls if c[0] == "sync_write"][-1]
    assert call[1:3] == (46, 2)
    assert call[3] == {7: [0x34, 0x12], 8: [0x05, 0x80], 9: [0x00, 0x00]}
    assert handler.raw(8, 46, 2) == 0x8005
    bus.sync_write("TORQUE_ENABLE", {7: 1, 8: 1})
    call = [c for c in handler.calls if c[0] == "sync_write"][-1]
    assert call[1:3] == (40, 1)
    assert call[3] == {7: [1], 8: [1]}
    # Empty writes are a no-op.
    before = len(handler.calls)
    bus.sync_write(GOAL_VELOCITY, {})
    assert len(handler.calls) == before


def test_feetechbus_sync_write_big_endian_for_protocol_1(fake_sdk):
    bus = FeetechBus("/dev/ttyFAKE", protocol=1)
    bus.open()
    handler = fake_sdk.handlers[-1]
    bus.sync_write(GOAL_POSITION, {1: 0x1234})
    call = [c for c in handler.calls if c[0] == "sync_write"][-1]
    assert call[3] == {1: [0x12, 0x34]}


def test_feetechbus_sync_read_decodes_and_skips_missing(fake_sdk):
    bus, handler = _open_bus(fake_sdk, ids=(1, 2))
    handler.load(1, 56, 2, 0x8000 | 10)
    handler.load(2, 56, 2, 2500)
    assert bus.sync_read(PRESENT_POSITION, [1, 2, 3]) == {1: -10, 2: 2500}
    call = [c for c in handler.calls if c[0] == "sync_read"][-1]
    assert call[1:] == (56, 2, [1, 2, 3])
    handler.load(1, 60, 2, 0x400 | 77)
    handler.load(2, 60, 2, 77)
    assert bus.sync_read("PRESENT_LOAD", [1, 2]) == {1: -77, 2: 77}
    assert bus.sync_read(PRESENT_POSITION, []) == {}


def test_feetechbus_sync_read_comm_failure_raises(fake_sdk):
    bus, handler = _open_bus(fake_sdk)
    handler.sync_read_fails = True
    with pytest.raises(BusError):
        bus.sync_read(PRESENT_POSITION, [1, 2])


def test_feetechbus_scan_reports_found_motors(fake_sdk):
    bus, handler = _open_bus(fake_sdk, ids=(1, 2, 7))
    handler.load(2, 56, 2, 0x8000 | 40)
    found = bus.scan(range(1, 21))
    assert found == {
        1: {"model": 777, "position": 2048, "voltage": 12.1},
        2: {"model": 777, "position": -40, "voltage": 12.1},
        7: {"model": 777, "position": 2048, "voltage": 12.1},
    }
    pinged = [c[1] for c in handler.calls if c[0] == "ping"]
    assert pinged == list(range(1, 21))
    # A missing motor is only pinged, never read.
    assert not any(c[0].startswith("read") and c[1] == 3 for c in handler.calls)
    assert bus.scan([7]) == {7: {"model": 777, "position": 2048, "voltage": 12.1}}


def test_feetechbus_torque_writes_enable_then_lock(fake_sdk):
    bus, handler = _open_bus(fake_sdk, ids=(1, 2))
    handler.calls.clear()
    bus.torque([1, 2], True)
    writes = [c for c in handler.calls if c[0] == "write1"]
    assert writes == [
        ("write1", 1, 40, 1),
        ("write1", 1, 55, 1),
        ("write1", 2, 40, 1),
        ("write1", 2, 55, 1),
    ]
    handler.calls.clear()
    bus.torque([1, 2], False)
    writes = [c for c in handler.calls if c[0] == "write1"]
    assert writes == [
        ("write1", 1, 40, 0),
        ("write1", 1, 55, 0),
        ("write1", 2, 40, 0),
        ("write1", 2, 55, 0),
    ]
    assert handler.raw(1, 40, 1) == 0 and handler.raw(2, 55, 1) == 0


def test_feetechbus_has_lock(fake_sdk):
    bus = FeetechBus("/dev/ttyFAKE")
    assert isinstance(bus.lock, type(threading.Lock()))


def test_feetechbus_open_without_sdk_gives_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "scservo_sdk", None)  # simulate "not installed"
    bus = FeetechBus("/dev/ttyFAKE")
    with pytest.raises(BusError, match="feetech-servo-sdk"):
        bus.open()
    assert bus.is_open is False


def test_fake_and_real_share_public_api():
    public = {n for n in dir(FeetechBus) if not n.startswith("_")}
    assert public <= {n for n in dir(FakeBus) if not n.startswith("_")}
