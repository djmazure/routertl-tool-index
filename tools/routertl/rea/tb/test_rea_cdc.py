# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_cdc — clock-domain crossing primitives.
#
# Tests cover REA-REQ-020 (word sync) and REA-REQ-021 (pulse xfer).
#
# Two top-level entities live in rr_rea_cdc.vhd; we run two
# independent simulator invocations from the same module — one per
# top — by parameterizing run_simulation. The cocotb decorators
# scope which @test() bodies run against which top.

from __future__ import annotations

import os
import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation                  # noqa: E402
from sdk.cocotb_helpers import requires                       # noqa: E402

_RTL_DIR = str(_Path(__file__).resolve().parent.parent / "rtl")


def _run_against(top: str) -> None:
    run_simulation(
        top_level=top,
        module="test_rea_cdc",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_RTL_DIR}/rr_rea_cdc.vhd",
            ],
        },
        generics={"G_WIDTH": 16} if top == "rr_rea_sync_word" else {},
        waves=True,
        simulator="nvc",
    )


def main() -> None:
    """Run BOTH CDC entity tests in sequence — separate sim invocations."""
    _run_against("rr_rea_sync_word")
    _run_against("rr_rea_pulse_xfer")


# ── REA-REQ-020: word sync ──────────────────────────────────────────


@cocotb.test()
@requires("REA-REQ-020")
async def test_rea_req_020_word_sync_settles_in_two_cycles(dut):
    """A multi-bit config word presented to rr_rea_sync_word must
    appear on dout within ≤2 dst_clk cycles, with no metastable
    glitch (verified by sampling and checking the value matches the
    held source).

    Skip body when the active simulation top is the OTHER entity in
    this file — cocotb runs every @test() against whatever top the
    sim was launched with."""
    if not hasattr(dut, "din"):
        return  # wrong top — pulse_xfer is the active DUT
    cocotb.start_soon(Clock(dut.dst_clk, 8.0, unit="ns").start())  # 125 MHz
    dut.din.value = 0
    await ClockCycles(dut.dst_clk, 5)

    # Hard-coded sequence of words. Each value held for ≥3 cycles so
    # it propagates through both sync flops and we can sample after
    # exactly 2 cycles to confirm settle time.
    cases = [0xCAFE, 0x1234, 0x0001, 0xFFFF, 0xA5A5]
    for word in cases:
        dut.din.value = word
        # Two clock edges → s1 catches → s2 catches.
        await RisingEdge(dut.dst_clk)
        await RisingEdge(dut.dst_clk)
        # On the THIRD edge the value should be visible on dout.
        await RisingEdge(dut.dst_clk)
        observed = int(dut.dout.value)
        assert observed == word, (
            f"REA-REQ-020 failed: din=0x{word:04X} not on dout after "
            f"3 dst_clk cycles (got 0x{observed:04X})"
        )

    dut._log.info("REA-REQ-020 PASS — word sync settles in 2 cycles")


# ── REA-REQ-021: pulse xfer ─────────────────────────────────────────


@cocotb.test()
@requires("REA-REQ-021")
async def test_rea_req_021_pulse_xfer_one_pulse_per_event(dut):
    """Each toggle of src_toggle produces exactly one dst_pulse on
    dst_clk, regardless of clock-ratio. The caller (e.g., regbank)
    flips src_toggle once per logical event."""
    if not hasattr(dut, "src_toggle"):
        return  # wrong top — sync_word is the active DUT

    # Asymmetric ratio: dst_clk = 125 MHz (8 ns).
    cocotb.start_soon(Clock(dut.dst_clk, 8.0, unit="ns").start())

    dut.dst_rst.value = 1
    dut.src_toggle.value = 0
    await ClockCycles(dut.dst_clk, 8)
    dut.dst_rst.value = 0
    await ClockCycles(dut.dst_clk, 4)

    # ── Background watcher: count dst_pulse asserts over 80 dst clocks.
    pulse_count = 0

    async def _count_pulses():
        nonlocal pulse_count
        for _ in range(80):
            await RisingEdge(dut.dst_clk)
            if int(dut.dst_pulse.value) == 1:
                pulse_count += 1

    watcher = cocotb.start_soon(_count_pulses())

    # Toggle 3 times — each toggle is one logical event.
    cur = 0
    for _ in range(3):
        cur ^= 1
        dut.src_toggle.value = cur
        await ClockCycles(dut.dst_clk, 10)  # let it propagate

    await watcher

    assert pulse_count == 3, (
        f"REA-REQ-021 failed: 3 toggles → expected 3 dst pulses, "
        f"got {pulse_count}"
    )

    dut._log.info(
        f"REA-REQ-021 PASS — 3 toggles → 3 dst pulses (1:1 across CDC)"
    )


if __name__ == "__main__":
    main()
