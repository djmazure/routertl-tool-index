# `rr_rea` — RouteRTL Embedded Analyzer (REA), v0.2 Spec

## What it is
Vendor-neutral on-chip logic analyzer IP, JTAG-attached. **Drop-in compatible at the JTAG register interface with `fcapz_ela_xilinx7`** at the on-chip layer; on the host side, routertl ships its own first-party client (`REAClient`) that uses fcapz's transport for JTAG plumbing only. The seam is clear: routertl owns the capture protocol + register map; fcapz owns the JTAG transport layer.

## Why first-party
- **Sliding-window from day one**: the dpram records continuously from reset deassertion. fcapz gates the dpram write on `armed`, leaving uninit BRAM cells when the trigger fires before `pretrig_len` cycles have elapsed. We don't ship that bug.
- routertl-owned IP in the registry (`rr pkg add routertl/rea`).
- VHDL throughout, modular, contract-first per ROUTERTL conventions.

## Non-goals (v0.1)
- Trigger sequencer (multi-stage)
- Decimation
- External trigger input
- Segmented capture
- Storage qualification
- Edge-detect mode
- Multi-channel mux

These are parked on the version roadmap below.

---

## SW Interface Contract (frozen — fcapz-compatible)

JTAG register map at the burst slave (32-bit words). v0.1 implements the registers below; everything else reads as 0.

| Offset | R/W | Name        | Notes |
|-------:|:---:|:------------|:------|
| `0x00` | RO  | VERSION     | Magic `0x52454101` ('REA' + v0.1) |
| `0x04` | WO  | CTRL        | bit[0]=arm_toggle, bit[1]=reset_toggle |
| `0x08` | RO  | STATUS      | bit[0]=armed, [1]=triggered, [2]=done, [3]=overflow |
| `0x0C` | RO  | SAMPLE_W    | Synth-time generic |
| `0x10` | RO  | DEPTH       | Synth-time generic |
| `0x14` | RW  | PRETRIG     | Pretrigger sample count |
| `0x18` | RW  | POSTTRIG    | Posttrigger sample count |
| `0x1C` | RO  | CAPTURE_LEN | = PRETRIG + POSTTRIG + 1 (after `done`) |
| `0x20` | RW  | TRIG_MODE   | bit[0]=value_match (only mode in v0.1) |
| `0x24` | RW  | TRIG_VALUE  | Comparator value |
| `0x28` | RW  | TRIG_MASK   | Comparator bitmask |
| `0xA0` | RW  | CHAN_SEL    | Must be 0 in v0.1 |
| `0xA4` | RO  | NUM_CHAN    | = 1 in v0.1 |
| `0xC4` | RO  | TIMESTAMP_W | 0 if no timestamps, else width |
| `0xC8` | RO  | START_PTR   | Address of oldest sample after `done` |
| `0x100`+ | RO | DATA_BASE  | DPRAM, DEPTH × ⌈SAMPLE_W/32⌉ words |
| `0x100`+DEPTH·⌈SAMPLE_W/32⌉·4 | RO | TS_DATA_BASE | Timestamp DPRAM |

---

## Module hierarchy

```
rr_rea_top                       ← integration
├── rr_rea_jtag_xilinx7          ← BSCANE2 wrapper (HARD MACRO; mocked in sim)
├── rr_rea_jtag_iface            ← BSCAN → reg/burst protocol decoder
├── rr_rea_regbank               ← register map (jtag_clk domain)
├── rr_rea_cdc                   ← jtag_clk ↔ sample_clk syncs
├── rr_rea_capture_fsm           ← trigger detect + sliding-window pointer math
├── rr_rea_dpram (sample buffer) ← BRAM-inferred dual-port
└── rr_rea_dpram (ts buffer)     ← BRAM-inferred dual-port (gen by TIMESTAMP_W>0)
```

Each block has its own VHDL file, package-of-types, and cocotb testbench. None depend on a vendor primitive except `rr_rea_jtag_xilinx7`, which has a behavioral `_sim.vhd` mock for testbenches.

---

## Capture FSM contract (the core fix)

- `wr_ptr` increments every `sample_clk` cycle while `!done`. **Free-running from reset.** Not gated by `armed`.
- `arm_pulse` sets `armed <= 1`, clears `triggered/done`. Does **not** touch `wr_ptr`.
- On the cycle `trigger_hit` fires (and `armed && !triggered`): `trig_ptr <= wr_ptr` AND `triggered <= 1`.
- After trigger: count `posttrig_len` more cycles, then `done <= 1`, `start_ptr <= (trig_ptr - pretrig_len) mod DEPTH`.
- `dpram_we` is `!done` (always writes when capture is permitted).

This is where we explicitly diverge from fcapz.

---

## Test infrastructure

- Tests in `tb/`, runnable via `rr sim run <name>` (ROUTERTL-001 sanctioned engine).
- Each `tb/test_*.py` ends with `engine.simulation.run_simulation(...)` per ROUTERTL-001.
- All expected values hard-coded per ROUTERTL-002.
- `requirements.yml` ties every `@requires(REA-REQ-N)` tag to a one-line description; `rr sim coverage-map` enforces the mapping.

---

## Mocking BSCAN

`rr_rea_jtag_xilinx7_sim.vhd` exposes the same port signature as the real Xilinx wrapper but lets the cocotb testbench drive `capture/shift/update/tdi/tdo` directly. Only piece in the hierarchy that can't run untouched in sim — and it's a ~50-line behavioral mock. A future Intel `sld_virtual_jtag` wrapper gets the same `_sim.vhd` treatment.

---

## Migration path

1. Land `rr_rea` v0.1 in `routertl-tool-index/tools/routertl/rea/`. **Done — v0.1 shipped 2026-04-29.**
2. Verify on Zybo against the unmodified fcapz host SW. **Done.**
3. Switch `examples/zybo_fcapz_demo/` to instantiate `rr_rea_xilinx7`. **Done.**
4. Open a courtesy upstream PR to fcapz with the sliding-window RTL fix (the `mem_we_a = !done && store_enable` patch + `wr_ptr`-not-reset-on-arm). **Issue posted 2026-04-29; awaiting maintainer response.**
5. Register `routertl/rea` in the IP registry — first-party debug IP for SDK users. **Done.**

## v0.2 — Host-side ownership (shipped 2026-04-29)

The on-chip RTL is unchanged from v0.1. v0.2 ships:

- **`REAClient` (routertl.sdk.cli.rea)** — first-party SDK host client owning the capture protocol (configure / arm / wait_done / capture). Uses fcapz's transport for JTAG plumbing only. Replaces fcapz's `Analyzer.capture()` in the `rr ila capture` bridge.
- **Batched dpram readback** — single xsdb `jtag sequence` for all DEPTH cells with `delay 20` between scans (matches fcapz's single-reg `READ_IDLE_CYCLES`, which is the timing that works on rr_rea's regbank). One round-trip instead of N. Verified on Zybo Z7-20: capture+read in 1.9 s for DEPTH=4096, down from ~5 s with the v0.1 single-reg fallback.
- **Native `start_ptr`-based rotation** — REAClient reads `ADDR_START_PTR` (0xC8) from the chip and rotates the buffer in software so the trigger sample lands at index `pretrigger` by construction. No timestamp dependency.
- **Synthetic `sample_clk` anchor channel (host-side)** — the bridge appends a 1-bit `sample_clk` channel to the wave_stream_v1 HELO descriptor and emits two sub-samples per real sample so RouteWave displays the clock at the *true* sample frequency (not sample_freq/2). Zero RTL/JTAG cost.

10 unit tests pin REAClient's contract; the existing 34 ila bridge tests carry through with extended fakes for the new transport surface.

---

## Out of scope (parked)

| Version | Feature | Backlog | Status |
|--------:|:--------|:--------|:--|
| v0.2 | Host-side `REAClient` (capture protocol ownership) | RTL-P3.276 | **Shipped** |
| v0.2 | Synthetic `sample_clk` anchor (host-side) | RTL-P3.272 v0.1 promise | **Shipped** |
| v0.2 | Cross-domain trigger crossbar (`rr_rea_trig_xbar`) | RTL-P3.266 + (new) | Next up |
| v0.2 | On-chip sample-clock tick channel (RTL companion to host anchor) | (new) | Parked |
| v0.2 | Edge-detect trigger mode | RTL-P3.263 | Parked |
| v0.3 | Multi-stage trigger sequencer | RTL-P3.265 | Parked |
| v0.3 | Decimation | (new) | Parked |
| v0.4 | Segmented capture | (new) | Parked |
| v0.4 | Storage qualification | (new) | Parked |
| v0.5 | Multi-channel mux | (new) | Parked |
| v0.5 | Intel JTAG vendor wrapper (`sld_virtual_jtag`) | (new) | Parked |

### v0.1 (host-side) — Synthetic clock anchor channel

Each REA instance gets a virtual `clk_<corename>` channel (e.g.
`clk_ila1`, `clk_ila2`) that the routertl `rr ila capture` bridge
synthesizes on the producer side: the channel is added to the
wave_stream_v1 HELO descriptor and emitted as a 1/0 toggle pattern,
one bit per sample. Zero RTL/JTAG cost — every sample is exactly
one cycle apart by construction, so the pattern is honest. Gives
the user a visual anchor on the RouteWave canvas to see the
sample cadence alongside the captured probes.

### v0.2 — Cross-domain trigger crossbar (rr_rea_trig_xbar)

When a design has multiple clock domains (e.g., 125 MHz Ethernet,
250 MHz fabric, 100 MHz processor), the user often wants ONE event
in any domain to freeze the capture in ALL domains, so the captured
windows are time-coherent.

Design sketch:
- Each REA instance exposes a 1-cycle `trigger_out` pulse on its
  own sample_clk when its local trigger fires.
- Each REA instance accepts a `trigger_in` strobe (sync'd to its
  sample_clk via two-flop) — when high, behaves as if its local
  comparator fired.
- A small `rr_rea_trig_xbar` module sits between N instances and
  ORs each domain's `trigger_out` into every other domain's
  `trigger_in`, with the necessary CDC syncs.
- One CTRL.arm bit on the JTAG side fans out to all instances'
  arm_pulse — domains arm together.
- `done` reports per-instance; the host SW waits for all to go high.

This pairs naturally with the wave_stream_v1 nanosecond-based
timestamps already used by the routertl `rr ila capture` bridge —
multi-domain rendering on the consumer side falls out for free
once each window has its own (HELO sample_clk_hz, captured ts)
and a coherent trigger fire moment.

### v0.2 (RTL-side) — On-chip sample-clock tick channel

Optional companion to the host-side anchor: add a 1-bit register
that toggles every `sample_clk` cycle and prepend it to the probe
word inside the REA instance, so the *actual* on-chip sample-clock
state is captured per-sample. Costs +1 bit of `SAMPLE_W` and +1 cell
of dpram per entry. Only meaningful when paired with the cross-domain
trigger crossbar above — that's when an on-chip "this clk really did
tick at this moment" anchor starts to add information beyond the
deterministic host-side toggle.

Requirements catalog will land under REA-REQ-400 series in v0.2.
