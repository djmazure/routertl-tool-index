# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_top — full-stack integration test (REA-REQ-300).
#
# Mimics what the fcapz host SW does over JTAG:
#   1. configure: write PRETRIG / POSTTRIG / TRIG_VALUE / TRIG_MASK
#   2. arm:       toggle CTRL.bit[0] (arm)
#   3. wait_done: poll STATUS until bit[2] (done) goes high
#   4. read:      shift out DEPTH dpram cells from address window 0x100+
#   5. verify:    captured buffer contains a known counter pattern,
#                 with the trigger sample at start_ptr + pretrig.
#
# The TAP signals are driven directly (no BSCANE2). Same wire format
# as fcapz, so a future host-SW integration test can use the real
# Analyzer untouched against a separate Verilog/Vivado smoke shim.

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from engine.simulation import run_simulation                  # noqa: E402
from sdk.cocotb_helpers import requires                       # noqa: E402

GENERICS = {
    "G_SAMPLE_W": 12, "G_DEPTH": 256,    # smaller depth for fast sim
    "G_TIMESTAMP_W": 0, "G_NUM_CHAN": 1,
}
_RTL_DIR = str(_Path(__file__).resolve().parent.parent / "rtl")

# Register addresses (same as test_rea_regbank.py — frozen contract)
ADDR_CTRL        = 0x04
ADDR_STATUS      = 0x08
ADDR_PRETRIG     = 0x14
ADDR_POSTTRIG    = 0x18
ADDR_TRIG_MODE   = 0x20
ADDR_TRIG_VALUE  = 0x24
ADDR_TRIG_MASK   = 0x28
ADDR_START_PTR   = 0xC8
ADDR_DATA_BASE   = 0x100

CTRL_BIT_ARM   = 0x01
STATUS_BIT_DONE = 0x04


def main() -> None:
    run_simulation(
        top_level="rr_rea_top",
        module="test_rea_top",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_RTL_DIR}/rr_rea_dpram.vhd",
                f"{_RTL_DIR}/rr_rea_capture_fsm.vhd",
                f"{_RTL_DIR}/rr_rea_regbank.vhd",
                f"{_RTL_DIR}/rr_rea_cdc.vhd",
                f"{_RTL_DIR}/rr_rea_jtag_iface.vhd",
                f"{_RTL_DIR}/rr_rea_top.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── Helpers (JTAG protocol) ──────────────────────────────────────────


SAMPLE_PERIOD_NS = 8.0    # 125 MHz sample clock
TCK_PERIOD_NS    = 25.0   # 40 MHz JTAG clock


async def _start_clocks(dut):
    cocotb.start_soon(Clock(dut.sample_clk, SAMPLE_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.tck, TCK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    dut.sample_rst.value = 1
    dut.arst.value = 1
    dut.tdi.value = 0
    dut.capture.value = 0
    dut.shift_en.value = 0
    dut.update.value = 0
    dut.sel.value = 0
    dut.probe_in.value = 0
    await ClockCycles(dut.tck, 4)
    dut.sample_rst.value = 0
    dut.arst.value = 0
    await ClockCycles(dut.tck, 1)


async def _capture_phase(dut):
    dut.sel.value = 1
    dut.capture.value = 1
    dut.shift_en.value = 0
    dut.update.value = 0
    await RisingEdge(dut.tck)
    dut.capture.value = 0


async def _shift_dr(dut, value: int, n_bits: int) -> int:
    """Shift n_bits LSB-first; returns the n-bit value shifted out via TDO."""
    from cocotb.triggers import NextTimeStep
    dut.sel.value = 1
    dut.capture.value = 0
    dut.update.value = 0
    dut.shift_en.value = 1
    await ReadOnly()
    await NextTimeStep()
    out = 0
    for i in range(n_bits):
        dut.tdi.value = (value >> i) & 1
        await ReadOnly()
        out |= (int(dut.tdo.value) & 1) << i
        await RisingEdge(dut.tck)
    dut.shift_en.value = 0
    return out


async def _update_phase(dut):
    dut.sel.value = 1
    dut.capture.value = 0
    dut.shift_en.value = 0
    dut.update.value = 1
    await RisingEdge(dut.tck)
    dut.update.value = 0
    dut.sel.value = 0


def _frame(addr: int, data: int, write: bool) -> int:
    return ((1 if write else 0) << 48) | ((addr & 0xFFFF) << 32) | (data & 0xFFFFFFFF)


async def _jtag_write(dut, addr: int, data: int):
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, data, write=True), 49)
    await _update_phase(dut)


async def _jtag_read(dut, addr: int) -> int:
    """Issue a read via UPDATE, then a second DR scan to fetch rdata.

    Returns the lower 32 bits of the shifted-out frame."""
    await _capture_phase(dut)
    await _shift_dr(dut, _frame(addr, 0, write=False), 49)
    await _update_phase(dut)
    # Let regbank's combinational decoder + dpram BRAM settle.
    await ClockCycles(dut.tck, 2)
    await _capture_phase(dut)
    out = await _shift_dr(dut, 0, 49)
    return out & 0xFFFF_FFFF


# ── Probe stimulus driver (sample_clk domain) ────────────────────────


async def _drive_probe_counter(dut, cycles: int):
    """Free-run a counter on probe_in. Probe value = (cnt & 0xFF) | 0x100
    so it's NEVER zero — any zero in the captured buffer must be uninit
    BRAM (the bug we're explicitly NOT shipping)."""
    cnt = int(dut.probe_in.value) & 0xFF if int(dut.probe_in.value) else 0
    for _ in range(cycles):
        dut.probe_in.value = (cnt & 0xFF) | 0x100
        await RisingEdge(dut.sample_clk)
        cnt = (cnt + 1) & 0xFF


# ── REA-REQ-300: full-stack integration ─────────────────────────────


@cocotb.test()
@requires("REA-REQ-300")
async def test_rea_req_300_full_stack_capture(dut):
    """End-to-end: configure → arm → wait_done → read dpram via JTAG.
    Verifies the wire-format / register-map contract AND the
    sliding-window architectural fix (no uninit cells) at the
    integration level."""
    DEPTH = 256
    PRETRIG = DEPTH // 2 - 1  # 127
    POSTTRIG = DEPTH // 2 - 1  # 127

    await _start_clocks(dut)
    await _reset(dut)

    # ── Background: free-run the probe counter forever ──────────
    cocotb.start_soon(_drive_probe_counter(dut, 100_000))

    # Let the buffer warm up well past DEPTH cycles.
    await ClockCycles(dut.sample_clk, 2 * DEPTH)

    # ── Configure: pretrig, posttrig, trigger mode/value/mask ──
    await _jtag_write(dut, ADDR_PRETRIG,    PRETRIG)
    await _jtag_write(dut, ADDR_POSTTRIG,   POSTTRIG)
    await _jtag_write(dut, ADDR_TRIG_MODE,  0x0000_0001)  # value_match
    await _jtag_write(dut, ADDR_TRIG_VALUE, 0x0000_01FF)  # bit[8] | counter==0xFF
    await _jtag_write(dut, ADDR_TRIG_MASK,  0x0000_0FFF)  # full SAMPLE_W

    # ── Arm via CTRL register ────────────────────────────────────
    await _jtag_write(dut, ADDR_CTRL, CTRL_BIT_ARM)

    # ── Wait for done by polling STATUS ──────────────────────────
    timeout_iter = 200
    done = False
    for i in range(timeout_iter):
        status = await _jtag_read(dut, ADDR_STATUS)
        if (status & STATUS_BIT_DONE) != 0:
            done = True
            dut._log.info(
                f"STATUS=0x{status:02X} done after {i+1} polls"
            )
            break
        await ClockCycles(dut.tck, 4)
    assert done, (
        f"REA-REQ-300 failed: capture never completed (last STATUS="
        f"0x{status:02X}, polled {timeout_iter} times)"
    )

    # ── Read start_ptr and the full DPRAM window ─────────────────
    start_ptr = await _jtag_read(dut, ADDR_START_PTR)
    start_ptr &= (DEPTH - 1)

    samples = []
    for i in range(DEPTH):
        addr = ADDR_DATA_BASE + 4 * ((start_ptr + i) & (DEPTH - 1))
        cell = await _jtag_read(dut, addr)
        samples.append(cell & 0xFFF)

    # ── Check 1: zero uninit cells (sliding-window contract) ─────
    zero_cells = sum(1 for s in samples if s == 0)
    assert zero_cells == 0, (
        f"REA-REQ-300 failed: captured window has {zero_cells}/{DEPTH} "
        f"uninit zero cells — sliding-window contract violated"
    )

    # ── Check 2: time-monotonic counter (counter & 0xFF increments
    #            by 1 from sample to sample, mod 256) ─────────────
    for i in range(1, DEPTH):
        prev = samples[i - 1] & 0xFF
        curr = samples[i] & 0xFF
        delta = (curr - prev) & 0xFF
        assert delta == 1, (
            f"REA-REQ-300 failed: gap at window offset {i} — "
            f"prev=0x{prev:02X} curr=0x{curr:02X} delta={delta}"
        )

    # ── Check 3: trigger sample at idx PRETRIG matches the value
    #            we configured (counter==0xFF, bit[8]=1, total 0x1FF) ──
    trigger_cell = samples[PRETRIG]
    assert trigger_cell == 0x1FF, (
        f"REA-REQ-300 failed: trigger sample at idx {PRETRIG} = "
        f"0x{trigger_cell:03X}, expected 0x1FF (the configured trigger value)"
    )

    dut._log.info(
        f"REA-REQ-300 PASS — full stack: configured + armed + captured + "
        f"read {DEPTH} samples; trigger at idx {PRETRIG} = 0x{trigger_cell:03X}"
    )


if __name__ == "__main__":
    main()
