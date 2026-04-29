# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_jtag_iface — BSCAN → register-bus bridge.
#
# Tests cover REA-REQ-001..003 (see ../requirements.yml).
#
# We mock the BSCAN hard macro by driving the TAP signals (tck, tdi,
# tdo, capture, shift_en, update, sel) directly from cocotb. This is
# the entire point of factoring the protocol decoder out of the
# vendor wrapper — it makes the protocol unit-testable without any
# Xilinx primitive.

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

_RTL_DIR = str(_Path(__file__).resolve().parent.parent / "rtl")


def main() -> None:
    run_simulation(
        top_level="rr_rea_jtag_iface",
        module="test_rea_jtag_iface",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_jtag_iface.vhd",
            ],
        },
        waves=True,
        simulator="nvc",
    )


# ── Helpers ──────────────────────────────────────────────────────────


TCK_NS = 25.0  # 40 MHz nominal


async def _start_tck(dut):
    cocotb.start_soon(Clock(dut.tck, TCK_NS, unit="ns").start())


async def _reset(dut):
    dut.arst.value = 1
    dut.tdi.value = 0
    dut.capture.value = 0
    dut.shift_en.value = 0
    dut.update.value = 0
    dut.sel.value = 0
    dut.reg_rdata.value = 0
    await ClockCycles(dut.tck, 4)
    dut.arst.value = 0
    await ClockCycles(dut.tck, 1)


async def _capture_phase(dut):
    """Pulse `capture` for 1 tck cycle while sel=1."""
    dut.sel.value = 1
    dut.capture.value = 1
    dut.shift_en.value = 0
    dut.update.value = 0
    await RisingEdge(dut.tck)
    dut.capture.value = 0


async def _shift_dr(dut, value: int, n_bits: int) -> int:
    """Shift n_bits of `value` (LSB-first) into TDI while sel=1.

    JTAG hardware convention: TDO reflects sr(0) (registered). External
    masters sample TDO between rising edges (e.g. on falling edge).
    For each cycle: read the CURRENT tdo (= the bit at sr(0)), then
    issue the rising edge that shifts a new value into sr(0).
    """
    from cocotb.triggers import ReadOnly, NextTimeStep
    dut.sel.value = 1
    dut.capture.value = 0
    dut.update.value = 0
    dut.shift_en.value = 1
    # Force one delta-cycle settle so the just-set inputs (and the
    # CAPTURE'd sr) propagate through the simulator before we sample.
    await ReadOnly()
    await NextTimeStep()
    tdo_bits = 0
    for i in range(n_bits):
        dut.tdi.value = (value >> i) & 1
        # Sample tdo BEFORE the edge — the bit currently at sr(0).
        await ReadOnly()
        bit = int(dut.tdo.value) & 1
        tdo_bits |= bit << i
        await RisingEdge(dut.tck)
    dut.shift_en.value = 0
    return tdo_bits


async def _update_phase(dut):
    """Pulse `update` for 1 tck cycle while sel=1."""
    dut.sel.value = 1
    dut.capture.value = 0
    dut.shift_en.value = 0
    dut.update.value = 1
    await RisingEdge(dut.tck)
    dut.update.value = 0
    dut.sel.value = 0


def _frame(addr: int, data: int, write: bool) -> int:
    """Pack a 49-bit DR frame: rnw[1] | addr[16] | data[32]."""
    return ((1 if write else 0) << 48) | ((addr & 0xFFFF) << 32) | (data & 0xFFFFFFFF)


async def _do_write(dut, addr: int, data: int):
    """Full JTAG write sequence: capture (no-op) + shift frame + update."""
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, data, write=True), 49)
    await _update_phase(dut)


async def _do_read(dut, addr: int, expected_rdata: int) -> int:
    """Full JTAG read sequence.

    First DR scan: shift in the read frame (we don't care about TDO).
    Then UPDATE pulses reg_rd_en — the regbank presents reg_rdata
    next cycle. Second DR scan: capture loads reg_rdata into sr,
    and shifting reads it out via TDO.
    """
    # Issue 1: send the read frame.
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, 0, write=False), 49)
    await _update_phase(dut)

    # Set reg_rdata BEFORE the second capture and let it settle one
    # cycle so the edge sees the new value (cocotb scheduled writes
    # apply at the next settle, but we want NO chance of a race).
    dut.reg_rdata.value = expected_rdata
    await ClockCycles(dut.tck, 1)

    await _capture_phase(dut)
    # Shift the full 49-bit DR; the lower 32 bits carry rdata.
    tdo = await _shift_dr(dut, 0, 49)
    return tdo & 0xFFFF_FFFF


async def _watch_for_pulse(dut, sig_name: str, max_cycles: int = 5) -> bool:
    """After an update, pulse fires within a few cycles. Watch for it."""
    sig = getattr(dut, sig_name)
    for _ in range(max_cycles):
        if int(sig.value) == 1:
            return True
        await RisingEdge(dut.tck)
    return False


# ── REA-REQ-001: single register write produces one wr_en pulse ─────


@cocotb.test()
@requires("REA-REQ-001")
async def test_rea_req_001_write_produces_one_wr_en(dut):
    """A single JTAG write opcode must produce exactly one reg_wr_en
    pulse with the correct reg_addr / reg_wdata held during the
    pulse cycle."""
    await _start_tck(dut)
    await _reset(dut)

    # Hard-coded values per ROUTERTL-002.
    TARGET_ADDR = 0x0014  # PRETRIG
    TARGET_DATA = 0x0000_0800

    # Snoop in the background — record (cycle, addr, data) on each
    # reg_wr_en assertion.
    writes: list[tuple[int, int]] = []

    async def _snoop():
        cycle = 0
        for _ in range(200):
            await RisingEdge(dut.tck)
            cycle += 1
            if int(dut.reg_wr_en.value) == 1:
                writes.append(
                    (int(dut.reg_addr.value), int(dut.reg_wdata.value))
                )

    snoop = cocotb.start_soon(_snoop())

    await _do_write(dut, TARGET_ADDR, TARGET_DATA)
    # Drain a few cycles after the update so the snoop sees the pulse.
    await ClockCycles(dut.tck, 5)

    snoop.kill()

    assert len(writes) == 1, (
        f"REA-REQ-001 failed: expected exactly 1 wr_en pulse, got "
        f"{len(writes)} (writes={writes})"
    )
    addr, data = writes[0]
    assert addr == TARGET_ADDR, (
        f"REA-REQ-001 failed: reg_addr=0x{addr:04X}, expected "
        f"0x{TARGET_ADDR:04X}"
    )
    assert data == TARGET_DATA, (
        f"REA-REQ-001 failed: reg_wdata=0x{data:08X}, expected "
        f"0x{TARGET_DATA:08X}"
    )

    dut._log.info("REA-REQ-001 PASS — one wr_en pulse, addr/data correct")


# ── REA-REQ-002: single register read shifts out the addressed value ─


@cocotb.test()
@requires("REA-REQ-002")
async def test_rea_req_002_read_shifts_out_rdata(dut):
    """A read opcode followed by a second DR scan must shift out the
    value present on reg_rdata at the second CAPTURE."""
    await _start_tck(dut)
    await _reset(dut)

    EXPECTED_RDATA = 0xDEAD_BEEF

    received = await _do_read(dut, addr=0x0000, expected_rdata=EXPECTED_RDATA)

    assert received == EXPECTED_RDATA, (
        f"REA-REQ-002 failed: received 0x{received:08X} from TDO, "
        f"expected 0x{EXPECTED_RDATA:08X}"
    )

    dut._log.info(
        f"REA-REQ-002 PASS — read shifted out 0x{received:08X} on TDO"
    )


# ── REA-REQ-003: per-read pipelined reads carry distinct rdata ──────


@cocotb.test()
@requires("REA-REQ-003")
async def test_rea_req_003_pipelined_reads_distinct_rdata(dut):
    """Successive read scans return distinct rdata values — proves
    the iface re-captures reg_rdata on each new CAPTURE phase, not
    a stale stash. (This is the prerequisite for the host's
    read_block USER1 fallback path — N consecutive reads each
    return their addressed value.)"""
    await _start_tck(dut)
    await _reset(dut)

    EXPECTED = [0x1111_1111, 0x2222_2222, 0x3333_3333]
    observed = []
    for i, exp in enumerate(EXPECTED):
        got = await _do_read(dut, addr=0x0100 + 4 * i, expected_rdata=exp)
        observed.append(got)

    assert observed == EXPECTED, (
        f"REA-REQ-003 failed: observed={observed}, expected={EXPECTED}"
    )

    dut._log.info(
        f"REA-REQ-003 PASS — 3 pipelined reads → distinct rdata"
    )


if __name__ == "__main__":
    main()
