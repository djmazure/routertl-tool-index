# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_trig_xbar — cross-domain trigger crossbar tests (REA-REQ-402).
#
# Two pulse_xfer instances inside, one per direction. Each takes a
# toggle level on its source clock and emits a 1-cycle pulse on its
# destination clock. The crossbar wiring is:
#   B's trigger_sticky → pulse on A's clock → A's trigger_in
#   A's trigger_sticky → pulse on B's clock → B's trigger_in
#
# Asymmetric clock periods (40 MHz / 125 MHz) exercise the CDC.

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
        top_level="rr_rea_trig_xbar",
        module="test_rea_trig_xbar",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_RTL_DIR}/rr_rea_cdc.vhd",
                f"{_RTL_DIR}/rr_rea_trig_xbar.vhd",
            ],
        },
        waves=True,
        simulator="nvc",
    )


# ── Helpers ──────────────────────────────────────────────────────────


async def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.clk_a, 25.0, unit="ns").start())  # 40 MHz
    cocotb.start_soon(Clock(dut.clk_b,  8.0, unit="ns").start())  # 125 MHz


async def _reset(dut):
    dut.rst_a.value = 1
    dut.rst_b.value = 1
    dut.toggle_a_in.value = 0
    dut.toggle_b_in.value = 0
    await ClockCycles(dut.clk_a, 5)
    dut.rst_a.value = 0
    dut.rst_b.value = 0
    await ClockCycles(dut.clk_a, 3)


@cocotb.test()
@requires("REA-REQ-402")
async def test_rea_req_402_xbar_routes_both_directions(dut):
    """A→B and B→A: each toggle on one side produces exactly one
    pulse on the other side, within a few destination-clock cycles."""
    await _start_clocks(dut)
    await _reset(dut)

    # Watch both pulse outputs over the test window.
    a_pulses = 0
    b_pulses = 0

    async def _count_a():
        nonlocal a_pulses
        for _ in range(200):
            await RisingEdge(dut.clk_a)
            if int(dut.pulse_a_out.value) == 1:
                a_pulses += 1

    async def _count_b():
        nonlocal b_pulses
        for _ in range(500):
            await RisingEdge(dut.clk_b)
            if int(dut.pulse_b_out.value) == 1:
                b_pulses += 1

    w_a = cocotb.start_soon(_count_a())
    w_b = cocotb.start_soon(_count_b())

    # A fires first — B's pulse_a_out must NOT fire (only B→A
    # routes through pulse_a_out, not A→A).
    cur_a = 0
    cur_b = 0
    cur_a ^= 1; dut.toggle_a_in.value = cur_a
    await ClockCycles(dut.clk_b, 20)
    # B fires twice over its own clock domain.
    cur_b ^= 1; dut.toggle_b_in.value = cur_b
    await ClockCycles(dut.clk_b, 20)
    cur_b ^= 1; dut.toggle_b_in.value = cur_b
    await ClockCycles(dut.clk_b, 20)
    # A fires once more.
    cur_a ^= 1; dut.toggle_a_in.value = cur_a
    await ClockCycles(dut.clk_a, 30)

    await w_a
    await w_b

    # A → B path: 2 toggles on A → expect 2 pulses on B.
    assert b_pulses == 2, (
        f"REA-REQ-402 failed: 2 toggles on A → expected 2 pulses on "
        f"clk_b, got {b_pulses}"
    )
    # B → A path: 2 toggles on B → expect 2 pulses on A.
    assert a_pulses == 2, (
        f"REA-REQ-402 failed: 2 toggles on B → expected 2 pulses on "
        f"clk_a, got {a_pulses}"
    )

    dut._log.info(
        f"REA-REQ-402 PASS — A→B: 2 pulses, B→A: 2 pulses (1:1 across CDC)"
    )


if __name__ == "__main__":
    main()
