# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_regbank — register file unit tests.
#
# Tests cover REA-REQ-010..012 (see ../requirements.yml).
#
# Run via: rr sim run --ip <rea-dir> test_rea_regbank
#     or:  python test_rea_regbank.py  (with PYTHONPATH set)

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation                  # noqa: E402
from sdk.cocotb_helpers import requires                       # noqa: E402

GENERICS = {
    "G_SAMPLE_W": 12, "G_DEPTH": 4096,
    "G_TIMESTAMP_W": 32, "G_NUM_CHAN": 1,
}
_RTL_DIR = str(_Path(__file__).resolve().parent.parent / "rtl")

# Register addresses — must match rr_rea_pkg.vhd (which is the SW
# interface contract). Hard-coded here per ROUTERTL-002.
ADDR_VERSION     = 0x00
ADDR_CTRL        = 0x04
ADDR_STATUS      = 0x08
ADDR_SAMPLE_W    = 0x0C
ADDR_DEPTH       = 0x10
ADDR_PRETRIG     = 0x14
ADDR_POSTTRIG    = 0x18
ADDR_CAPTURE_LEN = 0x1C
ADDR_TRIG_MODE   = 0x20
ADDR_TRIG_VALUE  = 0x24
ADDR_TRIG_MASK   = 0x28
ADDR_CHAN_SEL    = 0xA0
ADDR_NUM_CHAN    = 0xA4
ADDR_TIMESTAMP_W = 0xC4
ADDR_START_PTR   = 0xC8

EXPECTED_VERSION = 0x52454101  # 'REA' + v0.1


def main() -> None:
    run_simulation(
        top_level="rr_rea_regbank",
        module="test_rea_regbank",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_RTL_DIR}/rr_rea_regbank.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── Helpers ──────────────────────────────────────────────────────────


CLK_NS = 25.0  # 40 MHz JTAG clock — slow on purpose


async def _start_clk(dut):
    cocotb.start_soon(Clock(dut.jtag_clk, CLK_NS, unit="ns").start())


async def _reset(dut):
    dut.jtag_rst.value = 1
    dut.wr_en.value = 0
    dut.wr_addr.value = 0
    dut.wr_data.value = 0
    dut.rd_addr.value = 0
    dut.armed_in.value = 0
    dut.triggered_in.value = 0
    dut.done_in.value = 0
    dut.overflow_in.value = 0
    dut.start_ptr_in.value = 0
    await ClockCycles(dut.jtag_clk, 4)
    dut.jtag_rst.value = 0
    await ClockCycles(dut.jtag_clk, 1)


async def _write(dut, addr: int, data: int):
    dut.wr_addr.value = addr
    dut.wr_data.value = data
    dut.wr_en.value = 1
    await RisingEdge(dut.jtag_clk)
    dut.wr_en.value = 0


async def _read(dut, addr: int) -> int:
    """Combinational read: drive rd_addr, wait one cycle for the
    decoder to settle, sample rd_data."""
    dut.rd_addr.value = addr
    await ClockCycles(dut.jtag_clk, 2)
    return int(dut.rd_data.value)


# ── REA-REQ-010: every RW addr round-trips arbitrary 32-bit values ──


@cocotb.test()
@requires("REA-REQ-010")
async def test_rea_req_010_rw_round_trip(dut):
    """Write each RW register, read back, expect bit-exact match.
    Hard-coded values per ROUTERTL-002 — no derived expectations."""
    await _start_clk(dut)
    await _reset(dut)

    # Hard-coded (addr, value) pairs covering each RW slot. Values
    # are chosen with non-trivial bit patterns (no all-zero, no
    # all-one) to catch bit-aliasing bugs in the storage decoder.
    cases = [
        (ADDR_PRETRIG,    0x0000_0800),  # 2048 — typical config
        (ADDR_POSTTRIG,   0x0000_07FF),  # 2047
        (ADDR_TRIG_MODE,  0x0000_0001),  # value_match
        (ADDR_TRIG_VALUE, 0x0000_0042),  # counter==0x42
        (ADDR_TRIG_MASK,  0x0000_00FF),  # low byte mask
        (ADDR_CHAN_SEL,   0x0000_0000),  # v0.1: must be 0
    ]
    for addr, value in cases:
        await _write(dut, addr, value)
        observed = await _read(dut, addr)
        assert observed == value, (
            f"REA-REQ-010 failed at addr=0x{addr:02X}: "
            f"wrote 0x{value:08X}, read 0x{observed:08X}"
        )

    # Bonus: change PRETRIG/POSTTRIG and verify CAPTURE_LEN updates.
    await _write(dut, ADDR_PRETRIG,  100)
    await _write(dut, ADDR_POSTTRIG, 50)
    cap_len = await _read(dut, ADDR_CAPTURE_LEN)
    assert cap_len == 100 + 50 + 1, (
        f"CAPTURE_LEN should equal pretrig+posttrig+1 = 151, got {cap_len}"
    )

    dut._log.info("REA-REQ-010 PASS — all RW slots round-trip cleanly")


# ── REA-REQ-011: STATUS reflects input wires combinationally ────────


@cocotb.test()
@requires("REA-REQ-011")
async def test_rea_req_011_status_reflects_inputs(dut):
    """STATUS reads must mirror the input wires (armed/triggered/done/
    overflow) combinationally — no latching, no separate writes."""
    await _start_clk(dut)
    await _reset(dut)

    # All-zero baseline.
    s0 = await _read(dut, ADDR_STATUS)
    assert s0 == 0, f"STATUS at reset should be 0, got 0x{s0:08X}"

    # Pulse each status bit individually; read STATUS while held.
    test_pattern = [
        # (signal, bit_position)
        (dut.armed_in,     0),
        (dut.triggered_in, 1),
        (dut.done_in,      2),
        (dut.overflow_in,  3),
    ]
    for signal, bit in test_pattern:
        signal.value = 1
        await ClockCycles(dut.jtag_clk, 1)  # let combinational settle
        observed = await _read(dut, ADDR_STATUS)
        expected = 1 << bit
        assert observed == expected, (
            f"REA-REQ-011 failed: status bit {bit} on → "
            f"expected 0x{expected:02X}, got 0x{observed:08X}"
        )
        signal.value = 0

    # All four together → low-nibble = 0xF.
    dut.armed_in.value = 1
    dut.triggered_in.value = 1
    dut.done_in.value = 1
    dut.overflow_in.value = 1
    await ClockCycles(dut.jtag_clk, 1)
    observed = await _read(dut, ADDR_STATUS)
    assert observed == 0x0F, (
        f"REA-REQ-011 failed: all status bits on → "
        f"expected 0x0F, got 0x{observed:08X}"
    )

    dut._log.info("REA-REQ-011 PASS — STATUS mirrors input wires")


# ── REA-REQ-012: write to RO addr is a no-op ────────────────────────


@cocotb.test()
@requires("REA-REQ-012")
async def test_rea_req_012_ro_writes_are_dropped(dut):
    """Writes to RO addresses (VERSION, SAMPLE_W, DEPTH, etc.) must
    NOT bleed into adjacent RW slots and must NOT change the RO
    value (hard-coded constants stay constant)."""
    await _start_clk(dut)
    await _reset(dut)

    # Pre-load a known RW value so we can detect aliasing.
    await _write(dut, ADDR_PRETRIG, 0x0000_DEAD)

    # Hammer every RO slot with a poison value. Hard-coded list of
    # all RO addresses in the v0.1 contract.
    poison = 0xCAFEBABE
    ro_addrs = [
        ADDR_VERSION, ADDR_STATUS, ADDR_SAMPLE_W, ADDR_DEPTH,
        ADDR_CAPTURE_LEN, ADDR_NUM_CHAN, ADDR_TIMESTAMP_W,
        ADDR_START_PTR,
    ]
    for addr in ro_addrs:
        await _write(dut, addr, poison)

    # PRETRIG must still hold its pre-load value — proves no
    # write-port aliasing into a RW slot.
    pretrig_after = await _read(dut, ADDR_PRETRIG)
    assert pretrig_after == 0x0000_DEAD, (
        f"REA-REQ-012 failed: PRETRIG was clobbered by an RO write "
        f"(0x{pretrig_after:08X} != 0xDEAD)"
    )

    # VERSION must still read its compiled-in magic.
    ver = await _read(dut, ADDR_VERSION)
    assert ver == EXPECTED_VERSION, (
        f"REA-REQ-012 failed: VERSION mutated to 0x{ver:08X} "
        f"(expected 0x{EXPECTED_VERSION:08X})"
    )

    # SAMPLE_W must still report the synth-time generic.
    sw = await _read(dut, ADDR_SAMPLE_W)
    assert sw == GENERICS["G_SAMPLE_W"], (
        f"REA-REQ-012 failed: SAMPLE_W mutated to {sw} "
        f"(expected {GENERICS['G_SAMPLE_W']})"
    )

    dut._log.info("REA-REQ-012 PASS — RO writes are no-ops")


if __name__ == "__main__":
    main()
