# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_dpram — true dual-port BRAM for the REA capture buffer.
#
# Tests cover REA-REQ-200 / 201 / 202 (see ../requirements.yml). Run via
# the sanctioned engine entry point:
#   rr sim run rr_rea_dpram   (or: python test_rea_dpram.py)

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

# ── Path guard so `from engine.*` resolves both under pytest and via
#    direct `python test_rea_dpram.py`. Mirrors the SDK convention. ───
_sim_cocotb = str(_Path(__file__).resolve().parent)  # tb/
if _sim_cocotb not in _sys.path:
    _sys.path.insert(0, _sim_cocotb)
del _sim_cocotb, _Path, _sys

from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

# Default generics — overridable per-test by instantiating with new values.
GENERICS = {"G_WIDTH": 12, "G_DEPTH": 4096}

_RTL_DIR = str((__import__("pathlib").Path(__file__).resolve().parent.parent / "rtl"))


def main() -> None:
    """Run rr_rea_dpram tests via the sanctioned engine entry point."""
    run_simulation(
        top_level="rr_rea_dpram",
        module=_Path(__file__).stem if False else "test_rea_dpram",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_RTL_DIR}/rr_rea_dpram.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── Helpers ──────────────────────────────────────────────────────────


async def _start_clocks(dut, period_a_ns: float = 8.0, period_b_ns: float = 12.0):
    """Start independent A/B clocks (different periods to exercise CDC)."""
    cocotb.start_soon(Clock(dut.clk_a, period_a_ns, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_b, period_b_ns, unit="ns").start())
    await ClockCycles(dut.clk_a, 2)


async def _write_a(dut, addr: int, data: int):
    dut.addr_a.value = addr
    dut.din_a.value = data
    dut.we_a.value = 1
    await RisingEdge(dut.clk_a)
    dut.we_a.value = 0


async def _read_b(dut, addr: int) -> int:
    """Issue a read on port B and return the value latched after one clk_b."""
    dut.addr_b.value = addr
    await RisingEdge(dut.clk_b)
    # dout_b updates on the SAME edge that latched addr_b → next edge it's stable
    await RisingEdge(dut.clk_b)
    return int(dut.dout_b.value)


# ── REA-REQ-200: write A → read B at same address ───────────────────


@cocotb.test()
@requires("REA-REQ-200")
async def test_rea_req_200_write_a_read_b_same_addr(dut):
    """REA-REQ-200: write port A, read port B at SAME address — port B
    must observe the written value (next clk_b edge after the write
    settled).

    Hard-coded expected values per ROUTERTL-002 — no derived
    expectations.
    """
    await _start_clocks(dut)

    # Three known (addr, data) pairs that touch low/high address + bit
    # positions across the WIDTH window. Hard-coded; do NOT compute from
    # the input under test.
    cases = [
        (0,    0x000),
        (1,    0xAAA),
        (2047, 0x555),
    ]
    for addr, data in cases:
        await _write_a(dut, addr, data)
        # Wait one extra clk_b cycle so the write has settled across the
        # async clock boundary before the read latches.
        await ClockCycles(dut.clk_b, 2)
        observed = await _read_b(dut, addr)
        assert observed == data, (
            f"REA-REQ-200 failed: write addr=0x{addr:03x} data=0x{data:03x} "
            f"→ port B read got 0x{observed:03x}"
        )

    dut._log.info("REA-REQ-200 PASS")


# ── REA-REQ-201: concurrent A-write / B-read at DIFFERENT addrs ─────


@cocotb.test()
@requires("REA-REQ-201")
async def test_rea_req_201_concurrent_different_addrs(dut):
    """REA-REQ-201: writing port A at addr X and reading port B at
    addr Y (X != Y) in the same cycle must NOT corrupt either side
    — port B sees the pre-existing value at Y."""
    await _start_clocks(dut)

    # Pre-seed two cells.
    await _write_a(dut, 100, 0x123)
    await _write_a(dut, 200, 0x456)
    await ClockCycles(dut.clk_b, 3)

    # Now write addr 100 (with new value) while concurrently reading
    # addr 200 — port B should still see 0x456.
    dut.addr_a.value = 100
    dut.din_a.value = 0xFFF
    dut.we_a.value = 1
    dut.addr_b.value = 200
    await RisingEdge(dut.clk_a)
    dut.we_a.value = 0
    await RisingEdge(dut.clk_b)
    await RisingEdge(dut.clk_b)
    observed = int(dut.dout_b.value)
    assert observed == 0x456, (
        f"REA-REQ-201 failed: concurrent write@100 + read@200 should "
        f"yield 0x456 on port B, got 0x{observed:03x}"
    )

    dut._log.info("REA-REQ-201 PASS")


# ── REA-REQ-202: WIDTH/DEPTH generics drive storage shape ───────────


@cocotb.test()
@requires("REA-REQ-202")
async def test_rea_req_202_generic_extents(dut):
    """REA-REQ-202: writing addr=DEPTH-1 with value=2^WIDTH-1 round-
    trips correctly. Catches off-by-one in addr decoding and bit
    truncation in the storage array."""
    await _start_clocks(dut)

    DEPTH = 4096
    WIDTH = 12
    last_addr = DEPTH - 1
    full_value = (1 << WIDTH) - 1  # 0xFFF

    await _write_a(dut, last_addr, full_value)
    await ClockCycles(dut.clk_b, 2)
    observed = await _read_b(dut, last_addr)
    assert observed == full_value, (
        f"REA-REQ-202 failed: write addr=0x{last_addr:03x} "
        f"value=0x{full_value:03x} round-tripped as 0x{observed:03x}"
    )

    dut._log.info("REA-REQ-202 PASS")


if __name__ == "__main__":
    main()
