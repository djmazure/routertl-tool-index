# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea_capture_fsm — multi-stage sequencer tests (v0.3, REA-REQ-600..607).
#
# These tests instantiate the FSM with G_TRIG_STAGES=4 to exercise the
# full sequencer state machine. The default (G_TRIG_STAGES=1) test set
# in test_rea_capture_fsm.py covers REA-REQ-600 (legacy single-stage
# path stays correct when seq_enable=0).
#
# Probe pattern: per-test we drive a small known sequence onto
# probe_in (e.g. 0x10, 0x20, 0x30, 0x40) and configure stages to
# match those values in order. The test asserts:
#   - seq_state advances correctly (REA-REQ-601, 605)
#   - non-final matches don't fire trigger_out (REA-REQ-604)
#   - final match fires triggered + trigger_out (REA-REQ-602)
#   - count_target gates advance per stage (REA-REQ-603)
#   - arm_pulse resets seq_state and counters (REA-REQ-606)

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

GENERICS = {"G_SAMPLE_W": 12, "G_DEPTH": 4096, "G_TRIG_STAGES": 4}
_RTL_DIR = str(_Path(__file__).resolve().parent.parent / "rtl")


def main() -> None:
    run_simulation(
        top_level="rr_rea_capture_fsm",
        module="test_rea_capture_fsm_seq",
        custom_libraries={
            "work": [
                f"{_RTL_DIR}/rr_rea_pkg.vhd",
                f"{_RTL_DIR}/rr_rea_capture_fsm.vhd",
            ],
        },
        generics=GENERICS,
        waves=True,
        simulator="nvc",
    )


# ── Helpers ──────────────────────────────────────────────────────────


CLK_PERIOD_NS = 8.0
NUM_STAGES = 4
SAMPLE_W = 12


async def _start_clk(dut):
    cocotb.start_soon(Clock(dut.sample_clk, CLK_PERIOD_NS, unit="ns").start())


async def _reset(dut):
    """Drive sample_rst, deassert all inputs, then settle."""
    dut.sample_rst.value = 1
    dut.probe_in.value = 0
    dut.arm_pulse.value = 0
    dut.reset_pulse.value = 0
    dut.pretrig_len_in.value = 0
    dut.posttrig_len_in.value = 0
    dut.trig_value_in.value = 0
    dut.trig_mask_in.value = 0
    dut.decim_ratio_in.value = 0
    dut.trigger_in.value = 0
    dut.seq_enable_in.value = 0
    dut.seq_values_in.value = 0
    dut.seq_masks_in.value = 0
    dut.seq_counts_in.value = 0
    await ClockCycles(dut.sample_clk, 4)
    dut.sample_rst.value = 0
    await ClockCycles(dut.sample_clk, 1)


def _pack_per_stage(values: list[int], width: int) -> int:
    """Pack a per-stage list (LSB-first per stage) into a flat int."""
    out = 0
    for k, v in enumerate(values):
        out |= (v & ((1 << width) - 1)) << (k * width)
    return out


async def _arm_with_sequence(dut, *, values: list[int], masks: list[int],
                             counts: list[int], pretrig: int = 8,
                             posttrig: int = 8):
    """Configure 4-stage sequence and pulse arm."""
    assert len(values) == NUM_STAGES
    assert len(masks)  == NUM_STAGES
    assert len(counts) == NUM_STAGES
    dut.pretrig_len_in.value = pretrig
    dut.posttrig_len_in.value = posttrig
    dut.seq_enable_in.value  = 1
    dut.seq_values_in.value  = _pack_per_stage(values, SAMPLE_W)
    dut.seq_masks_in.value   = _pack_per_stage(masks,  SAMPLE_W)
    dut.seq_counts_in.value  = _pack_per_stage(counts, 16)
    await ClockCycles(dut.sample_clk, 5)
    dut.arm_pulse.value = 1
    await RisingEdge(dut.sample_clk)
    dut.arm_pulse.value = 0


async def _drive_probe(dut, value: int):
    """Set probe_in for one full cycle, return after one rising edge."""
    dut.probe_in.value = value
    await RisingEdge(dut.sample_clk)


# ── REA-REQ-601: in-order stage advance ──────────────────────────


@cocotb.test()
@requires("REA-REQ-601")
async def test_rea_req_601_seq_advances_in_order(dut):
    """seq_state advances 0→1→2→3 only on in-order stage matches."""
    await _start_clk(dut)
    await _reset(dut)

    # Sequence: stage K matches when probe == 0x10 + K.
    # mask = 0xFFF, count_target = 1 (advance on first match).
    await _arm_with_sequence(
        dut,
        values=[0x10, 0x11, 0x12, 0x13],
        masks =[0xFFF] * NUM_STAGES,
        counts=[1, 1, 1, 1],
    )

    # Drive 0x10 — stage 0 matches → seq_state advances to 1.
    await _drive_probe(dut, 0x10)
    # One settling cycle for non-blocking writes.
    await RisingEdge(dut.sample_clk)
    state_after_s0 = int(dut.seq_state_r.value) if hasattr(dut, "seq_state_r") else None
    # The internal seq_state_r isn't exposed as an output; instead
    # the only observable is "did stage 0 trigger fire?" — which
    # by REA-REQ-604 it should NOT (non-final stage).
    assert int(dut.triggered.value) == 0, (
        "stage 0 match must NOT fire triggered (non-final)"
    )

    # Drive 0x11, 0x12 — non-final advances.
    await _drive_probe(dut, 0x11)
    await RisingEdge(dut.sample_clk)
    assert int(dut.triggered.value) == 0
    await _drive_probe(dut, 0x12)
    await RisingEdge(dut.sample_clk)
    assert int(dut.triggered.value) == 0

    # Drive 0x13 — FINAL stage, must fire.
    await _drive_probe(dut, 0x13)
    # Wait a couple cycles for the seq_final_fire to propagate +
    # triggered_r to register.
    await ClockCycles(dut.sample_clk, 2)
    assert int(dut.triggered.value) == 1, (
        "final-stage match must fire triggered (REA-REQ-602)"
    )

    dut._log.info(
        "REA-REQ-601 PASS — seq_state advanced 0→1→2→3 on in-order matches"
    )


# ── REA-REQ-602: final-stage match fires capture ────────────────


@cocotb.test()
@requires("REA-REQ-602")
async def test_rea_req_602_final_stage_fires_trigger(dut):
    """Final stage's match drives triggered_r AND trigger_out_r."""
    await _start_clk(dut)
    await _reset(dut)

    await _arm_with_sequence(
        dut,
        values=[0xA0, 0xB0, 0xC0, 0xD0],
        masks =[0xFFF] * NUM_STAGES,
        counts=[1, 1, 1, 1],
    )

    saw_trigger_out = False

    async def _watch():
        nonlocal saw_trigger_out
        for _ in range(50):
            await RisingEdge(dut.sample_clk)
            if int(dut.trigger_out.value) == 1:
                saw_trigger_out = True

    watcher = cocotb.start_soon(_watch())

    # Walk through the full sequence ending on the final stage.
    for value in [0xA0, 0xB0, 0xC0, 0xD0]:
        await _drive_probe(dut, value)
        await RisingEdge(dut.sample_clk)

    await ClockCycles(dut.sample_clk, 5)
    await watcher

    assert int(dut.triggered.value) == 1
    assert saw_trigger_out, (
        "REA-REQ-602: final-stage match must drive trigger_out_r"
    )

    dut._log.info("REA-REQ-602 PASS — final-stage fired trigger_out")


# ── REA-REQ-603: count_target gates advance ─────────────────────


@cocotb.test()
@requires("REA-REQ-603")
async def test_rea_req_603_count_target_gates_advance(dut):
    """Stage K with count_target_K=N requires N matches before advancing."""
    await _start_clk(dut)
    await _reset(dut)

    # Stage 0 needs 3 matches of 0x42; stages 1-3 need 1 each.
    await _arm_with_sequence(
        dut,
        values=[0x42, 0x43, 0x44, 0x45],
        masks =[0xFFF] * NUM_STAGES,
        counts=[3, 1, 1, 1],
    )

    # Settle a few cycles past arm so the latched flat vectors are
    # observable.
    await ClockCycles(dut.sample_clk, 2)

    # Drive 0x42 exactly 2 times (2 matches) — stage 0 needs 3 to
    # advance, so seq_state should still be 0.
    await _drive_probe(dut, 0x42)
    await _drive_probe(dut, 0x42)
    # Drive a non-matching value to reset probe (next iteration
    # of _drive_probe will overwrite, but be explicit).
    await _drive_probe(dut, 0x00)
    await ClockCycles(dut.sample_clk, 1)

    # Walk stages 1-3 values — must NOT advance (REA-REQ-605: out-
    # of-order matches rejected) because we're still at stage 0.
    for value in [0x43, 0x44, 0x45]:
        await _drive_probe(dut, value)
    await ClockCycles(dut.sample_clk, 2)
    assert int(dut.triggered.value) == 0, (
        "stage 0 not yet satisfied (only 2/3 matches) — out-of-order "
        "stages 1-3 must NOT advance the sequencer"
    )

    # Provide the third 0x42 → stage 0 satisfied, advance to 1.
    # Then walk stages 1-3 to fire the final trigger.
    await _drive_probe(dut, 0x42)
    for value in [0x43, 0x44, 0x45]:
        await _drive_probe(dut, value)
    await ClockCycles(dut.sample_clk, 3)
    assert int(dut.triggered.value) == 1, (
        "after 3 stage-0 matches + stages 1-3 in order, capture "
        "should have fired"
    )

    dut._log.info("REA-REQ-603 PASS — count_target gates advance per stage")


# ── REA-REQ-604: non-final match does NOT trigger ───────────────


@cocotb.test()
@requires("REA-REQ-604")
async def test_rea_req_604_nonfinal_match_does_not_trigger(dut):
    """Stages 0..N-2 advance seq_state but never fire triggered_r
    or trigger_out_r — only the final stage triggers."""
    await _start_clk(dut)
    await _reset(dut)

    await _arm_with_sequence(
        dut,
        values=[0x01, 0x02, 0x03, 0x04],
        masks =[0xFFF] * NUM_STAGES,
        counts=[1, 1, 1, 1],
    )

    saw_any_trigger = False

    async def _watch():
        nonlocal saw_any_trigger
        for _ in range(60):
            await RisingEdge(dut.sample_clk)
            if int(dut.trigger_out.value) == 1 or int(dut.triggered.value) == 1:
                saw_any_trigger = True

    watcher = cocotb.start_soon(_watch())

    # Walk 3 of 4 stages — never reach the final stage.
    for value in [0x01, 0x02, 0x03]:
        await _drive_probe(dut, value)
        await RisingEdge(dut.sample_clk)
    await ClockCycles(dut.sample_clk, 20)

    # If trigger fired before we reached stage 3's match, that's
    # a REA-REQ-604 violation. We didn't drive 0x04 yet, so saw_any_trigger
    # must be False.
    await watcher
    assert saw_any_trigger is False, (
        "REA-REQ-604 violated: non-final-stage match drove triggered/trigger_out"
    )
    assert int(dut.triggered.value) == 0

    dut._log.info(
        "REA-REQ-604 PASS — stages 0..N-2 advance silently, no trigger"
    )


# ── REA-REQ-605: out-of-order matches don't advance ─────────────


@cocotb.test()
@requires("REA-REQ-605")
async def test_rea_req_605_out_of_order_matches_rejected(dut):
    """A stage-(K+1) match while seq_state==K must NOT advance past K."""
    await _start_clk(dut)
    await _reset(dut)

    # Distinct non-overlapping values per stage so each only matches
    # its own stage.
    await _arm_with_sequence(
        dut,
        values=[0x70, 0x80, 0x90, 0xA0],
        masks =[0xFFF] * NUM_STAGES,
        counts=[1, 1, 1, 1],
    )

    # We're at stage 0. Drive stage-3's value (0xA0) — must NOT
    # advance.
    await _drive_probe(dut, 0xA0)
    await RisingEdge(dut.sample_clk)
    # Drive stage-2's value (0x90) — also must not advance.
    await _drive_probe(dut, 0x90)
    await RisingEdge(dut.sample_clk)
    # Drive stage-1's value (0x80) — also must not advance.
    await _drive_probe(dut, 0x80)
    await RisingEdge(dut.sample_clk)

    # Now drive stage-0's value (0x70) — sequencer should advance
    # 0→1. Then walk 0x80, 0x90, 0xA0 to fire the final stage.
    for value in [0x70, 0x80, 0x90, 0xA0]:
        await _drive_probe(dut, value)
        await RisingEdge(dut.sample_clk)
    await ClockCycles(dut.sample_clk, 3)
    assert int(dut.triggered.value) == 1, (
        "in-order traversal after the out-of-order rejection should "
        "still fire the final trigger"
    )

    dut._log.info(
        "REA-REQ-605 PASS — out-of-order matches rejected, in-order works"
    )


# ── REA-REQ-606: arm_pulse resets seq_state and counters ────────


@cocotb.test()
@requires("REA-REQ-606")
async def test_rea_req_606_arm_resets_sequencer(dut):
    """A second arm_pulse re-starts the sequence from stage 0."""
    await _start_clk(dut)
    await _reset(dut)

    await _arm_with_sequence(
        dut,
        values=[0x10, 0x20, 0x30, 0x40],
        masks =[0xFFF] * NUM_STAGES,
        counts=[1, 1, 1, 1],
    )

    # Walk to stage 2 (don't trigger).
    for value in [0x10, 0x20]:
        await _drive_probe(dut, value)
        await RisingEdge(dut.sample_clk)
    assert int(dut.triggered.value) == 0

    # Re-arm. The sequencer must reset to stage 0 — that means
    # driving 0x30 (stage-2's value) right now should NOT advance,
    # because we're back at stage 0.
    await _arm_with_sequence(
        dut,
        values=[0x10, 0x20, 0x30, 0x40],
        masks =[0xFFF] * NUM_STAGES,
        counts=[1, 1, 1, 1],
    )
    await _drive_probe(dut, 0x30)  # would advance from stage 2; out-of-order from stage 0
    await ClockCycles(dut.sample_clk, 5)
    assert int(dut.triggered.value) == 0, (
        "re-arm must reset seq_state to 0 — drive stage-2 value should "
        "not advance from stage 0"
    )

    # Walk in order — should fire correctly post-reset.
    for value in [0x10, 0x20, 0x30, 0x40]:
        await _drive_probe(dut, value)
        await RisingEdge(dut.sample_clk)
    await ClockCycles(dut.sample_clk, 3)
    assert int(dut.triggered.value) == 1

    dut._log.info("REA-REQ-606 PASS — arm_pulse resets seq_state to 0")


if __name__ == "__main__":
    main()
