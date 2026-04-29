# rr_rea — Changelog

## v0.3.1 — 2026-04-29

### Added
- **Multi-stage trigger sequencer** (REA-REQ-600..607). New `G_TRIG_STAGES` generic on `rr_rea_capture_fsm` (default 1, no synth overhead). When `seq_enable_in=1`, the FSM advances stage-by-stage through up to N comparators with optional per-stage match-count gating; only the final stage's match fires `triggered`. Out-of-order matches are rejected. arm_pulse resets the sequencer state.
- New `seq_enable_in`, `seq_values_in`, `seq_masks_in`, `seq_counts_in` ports on `rr_rea_capture_fsm` (flat per-stage packed vectors). Internally uses flat-vector storage with combinational generate-block per-stage views — sidesteps an nvc 1.18 quirk with array-of-vector latching inside clocked-process loops.
- Register layout follows fcapz convention: `ADDR_SEQ_BASE = 0x0040`, stride 20 bytes per stage. SEQ-window writes do not alias into the v0.1/v0.2 RW slots.
- 6 new RTL-side tests (`test_rea_capture_fsm_seq.py`) + 1 backward-compat test (`test_rea_req_600_legacy_path_unchanged` in `test_rea_capture_fsm.py`) + 1 regbank addr-layout test (`test_rea_req_607_seq_register_layout`). 32/32 REQs covered.

### Architectural backstory
nvc 1.18 silently dropped values written through `array_of_vector_signal(k) <= input_vec(slice)` inside a clocked process loop. Switching to flat `std_logic_vector` storage with generate-block combinational views (for the per-stage type-narrow signals) resolved it. Documented in the FSM source for future-you.

---

## v0.3.0 — 2026-04-29

### Added
- **Decimation** (REA-REQ-500/501). Capture every (decim_ratio + 1)-th sample-clock cycle. Trades time-resolution for time-window. `ADDR_DECIM = 0x00B0`, 24-bit ratio. Verified on Zybo Z7-20 with `--decim 7`: counter values step by 8.

---

## v0.2.1 — 2026-04-29 (later same day)

### Added
- **Cross-domain trigger crossbar (`rr_rea_trig_xbar`)** — N=2 crossbar that routes one REA instance's local trigger fire to the other instance's `trigger_in` through CDC pulse-xfer. Fires both captures together so the resulting windows are time-coherent across clock domains. New REA-REQ-400/401 (FSM trigger_in semantics, no-loopback) + REA-REQ-402 (xbar 1:1 fanout). 3 new tests; 22/22 REQs covered.
- `rr_rea_capture_fsm.trigger_in` input — when armed, fires the capture as if local; does NOT drive trigger_out (avoids ping-pong).

### Changed
- `rr_rea_capture_fsm`: trigger detection block now considers `trigger_hit OR trigger_in`; `trigger_out` only fires on local `trigger_hit`.

---

## v0.2.0 — 2026-04-29

### Added
- **`REAClient` (routertl.sdk.cli.rea)** — first-party SDK host client owning configure / arm / wait_done / capture. Uses fcapz's transport for JTAG plumbing only.
- **Batched dpram readback** via a single xsdb `jtag sequence` with `delay 20` between scans. One round-trip for the entire DEPTH-cell buffer instead of N. Capture+read on Zybo Z7-20 dropped from ~5 s to **1.9 s** for DEPTH=4096.
- **Native `start_ptr`-based rotation** — REAClient reads `ADDR_START_PTR` from the chip and rotates the captured buffer in software so the trigger sample lands at index `pretrigger` by construction. No timestamp dependency.
- **Synthetic `sample_clk` anchor channel** — the routertl `rr ila capture` bridge appends a 1-bit `sample_clk` channel to the wave_stream_v1 HELO descriptor and emits two sub-samples per real sample so RouteWave displays the clock at the true sample frequency.
- 10 unit tests pinning REAClient's contract.

### Changed
- The `rr ila capture` bridge no longer uses `fcapz.Analyzer.capture()` for rr_rea. The bridge keeps fcapz's transport (`XilinxHwServerTransport`, `OpenOcdTransport`, `VendorStubTransport`) but the capture protocol is first-party.

### Fixed
- The single-reg fallback shipped in v0.1 is no longer needed; users get the fast batched path by default.

### Architectural seam (clarified)
- **routertl owns**: capture protocol, register map, on-chip RTL, host client.
- **fcapz owns**: JTAG transport (xsdb / openocd / vendor stub).

### Verified
- Zybo Z7-20: capture+read 1.9 s, sliding-window contract intact, trigger marker at idx pretrigger, sample_clk anchor at true sample frequency.

---

## v0.1.0 — 2026-04-29

### Added
- 8 VHDL RTL modules: `rr_rea_pkg`, `rr_rea_dpram`, `rr_rea_capture_fsm`, `rr_rea_regbank`, `rr_rea_cdc`, `rr_rea_jtag_iface`, `rr_rea_top`, `rr_rea_jtag_xilinx7`.
- 6 cocotb testbenches, 19 functional requirements (REA-REQ-001..300), 19/19 covered.
- Sliding-window-from-day-one capture: `mem_we_a = !done && store_enable`, `wr_ptr` not reset on arm. dpram records continuously from reset deassertion, so an early-firing trigger still has the full pretrigger window already in the buffer.
- JTAG register layout deliberately compatible with `fcapz_ela_xilinx7` so the existing fcapz host SW connects unmodified at the wire level.
- BSCANE2 wrapper (`rr_rea_xilinx7`) with explicit `DONT_TOUCH` + `KEEP_HIERARCHY` to survive Vivado's optimizer; companion `_sim.vhd` mock for testbenches.
- Routertl bridge (`rr ila capture`) integration with `start_ptr`-based rotation, sliding-window contract upheld at every layer.

### Verified
- Zybo Z7-20: VERSION=0x52454101, 4096-cell sliding-window dpram fully populated (no uninit zeros), trigger marker at idx pretrigger.
