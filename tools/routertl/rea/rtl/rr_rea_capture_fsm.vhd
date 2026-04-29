-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_capture_fsm — sliding-window capture state machine.
--
-- THIS IS WHERE WE EXPLICITLY DIVERGE FROM fcapz.
--
-- The dpram write path is FREE-RUNNING from sample_rst deassertion:
-- `dpram_we` is `!done && store_enable_in`, NOT gated by `armed`.
-- Combined with `wr_ptr` that increments every cycle (also not gated
-- by `armed`), this implements the textbook ILA sliding-window model
-- (Vivado ChipScope, Intel SignalTap, ARM ELA): the buffer always
-- holds the most-recent DEPTH samples, so a trigger that fires
-- immediately after `arm` still has the full pretrigger window of
-- context already in the buffer.
--
-- fcapz's `mem_we_a = armed && !done && store_enable` leaves uninit
-- BRAM cells in the captured window when the trigger fires before
-- pretrig_len cycles have elapsed since arm. We do not ship that.
--
-- v0.1 simplification: store_enable_in is unused (tied high by the
-- top-level for now). v0.3 brings decimation and storage
-- qualification, at which point this port carries the gate.
--
-- See requirements.yml REA-REQ-100..106 for the test contract.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_capture_fsm is
    generic (
        G_SAMPLE_W     : positive := 12;
        G_DEPTH        : positive := 4096;
        G_TRIG_STAGES  : positive := 1   -- v0.3 sequencer depth (REA-REQ-607)
    );
    port (
        sample_clk  : in  std_logic;
        sample_rst  : in  std_logic;

        -- ── Probe input (sync'd to sample_clk by the caller) ─────
        probe_in    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── Control pulses (sync'd to sample_clk by rr_rea_cdc) ──
        arm_pulse   : in  std_logic;   -- 1 cycle wide
        reset_pulse : in  std_logic;   -- 1 cycle wide; clears state

        -- ── External trigger input (REA-REQ-400) ─────────────────
        -- 1-cycle pulse on sample_clk from the cross-domain trigger
        -- crossbar (rr_rea_trig_xbar) — when armed, fires the
        -- capture as if the local comparator hit. Does NOT drive
        -- trigger_out (that would create a ping-pong loop with
        -- other REA instances on the bus). Tied low when the
        -- crossbar isn't connected.
        trigger_in  : in  std_logic := '0';

        -- ── Latched config (sample_clk domain) ───────────────────
        pretrig_len_in  : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        posttrig_len_in : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_value_in   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trig_mask_in    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        -- v0.3 decimation: capture every (decim_ratio + 1) samples.
        -- Tied 0 disables decimation (every sample stored). Latched
        -- on arm_pulse like the other config.
        decim_ratio_in  : in  std_logic_vector(23 downto 0)
                              := (others => '0');

        -- ── v0.3 multi-stage sequencer (REA-REQ-600..607) ────────
        -- seq_enable_in selects between the legacy single-comparator
        -- path (trig_value_in / trig_mask_in) and the per-stage
        -- sequencer below. Tied 0 → legacy path (REA-REQ-600).
        seq_enable_in     : in  std_logic := '0';

        -- Per-stage value/mask/count_target arrays packed into flat
        -- vectors so the entity stays VHDL-93-compatible. Each
        -- stage K occupies bits [(K+1)*W - 1 : K*W] in its respective
        -- vector. SAMPLE_W bits per stage for value/mask, 16 bits
        -- per stage for count_target.
        seq_values_in     : in  std_logic_vector(
            G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        seq_masks_in      : in  std_logic_vector(
            G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
        seq_counts_in     : in  std_logic_vector(
            G_TRIG_STAGES * 16 - 1 downto 0)
                              := (others => '0');

        -- ── Status flags (combinational from registers) ──────────
        armed       : out std_logic;
        triggered   : out std_logic;
        done        : out std_logic;
        overflow    : out std_logic;
        trigger_out : out std_logic;   -- 1-cycle pulse on local fire

        -- ── DPRAM port-A drive ───────────────────────────────────
        dpram_we    : out std_logic;
        dpram_addr  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        dpram_din   : out std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── Pointer outputs (regbank readback) ───────────────────
        wr_ptr_out    : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_ptr_out  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        start_ptr_out : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0)
    );
end entity;

architecture rtl of rr_rea_capture_fsm is

    constant C_PTR_W : positive := clog2(G_DEPTH);

    signal armed_r       : std_logic := '0';
    signal triggered_r   : std_logic := '0';
    signal done_r        : std_logic := '0';
    signal overflow_r    : std_logic := '0';
    signal wr_ptr_r      : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal trig_ptr_r    : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal start_ptr_r   : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal post_count_r  : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal pretrig_len_r : unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal posttrig_len_r: unsigned(C_PTR_W - 1 downto 0) := (others => '0');
    signal decim_ratio_r : unsigned(23 downto 0)         := (others => '0');
    signal decim_count_r : unsigned(23 downto 0)         := (others => '0');
    signal decim_tick    : std_logic;

    -- ── Sequencer state (v0.3, REA-REQ-600..607) ─────────────────
    -- seq_state_r tracks the current stage (0..G_TRIG_STAGES-1).
    -- seq_counters_r[K] counts cumulative matches for stage K and
    -- resets when seq_state advances past K (or on arm).
    -- Per-stage value/mask/count are LATCHED on arm_pulse just
    -- like the legacy comparator config; this keeps mid-capture
    -- changes from disturbing an in-flight sequence.
    constant C_SEQ_STATE_W : positive :=
        clog2(G_TRIG_STAGES + 1);    -- +1 so we can express FINAL+1
    -- Flat vectors for the per-stage config and counter state.
    -- Avoids array-of-vector slicing inside a clocked process loop,
    -- which nvc (1.18) handled inconsistently — the latch from the
    -- input port to the array element silently dropped to zero.
    -- Flat copies sidestep that and let the per-stage slice happen
    -- only in pure-combinational generate blocks below.
    signal seq_value_r_flat : std_logic_vector(
        G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0) := (others => '0');
    signal seq_mask_r_flat  : std_logic_vector(
        G_TRIG_STAGES * G_SAMPLE_W - 1 downto 0) := (others => '0');
    signal seq_count_target_r_flat : std_logic_vector(
        G_TRIG_STAGES * 16 - 1 downto 0) := (others => '0');
    signal seq_counter_r_flat : std_logic_vector(
        G_TRIG_STAGES * 16 - 1 downto 0) := (others => '0');

    -- Per-stage views via generate (combinational slices).
    type t_seq_count_array is array (0 to G_TRIG_STAGES - 1)
        of unsigned(15 downto 0);
    signal seq_count_target_view : t_seq_count_array;
    signal seq_counter_view      : t_seq_count_array;
    signal seq_state_r   : unsigned(C_SEQ_STATE_W - 1 downto 0)
                              := (others => '0');
    signal seq_enable_r  : std_logic := '0';

    -- Per-stage match (combinational from probe_in). When the
    -- corresponding seq_mask_r is 0 the comparator is "always
    -- match" — useful for unconditional advance after counting.
    signal stage_match : std_logic_vector(G_TRIG_STAGES - 1 downto 0);

    -- "We just hit the final-stage's required count" — drives
    -- triggered_r when seq_enable_r is on (REA-REQ-602).
    signal seq_final_fire : std_logic;
    signal trig_value_r  : std_logic_vector(G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
    signal trig_mask_r   : std_logic_vector(G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
    signal trigger_out_r : std_logic := '0';

    -- Combinational comparator. value_match mode (only mode in v0.1):
    --   trigger_hit = (probe_in & trig_mask) == (trig_value & trig_mask)
    signal trigger_hit : std_logic;

begin

    -- ── Per-stage views (combinational slices of flat vectors) ──
    g_seq_views : for k in 0 to G_TRIG_STAGES - 1 generate
        seq_count_target_view(k) <= unsigned(seq_count_target_r_flat(
            k * 16 + 15 downto k * 16));
        seq_counter_view(k) <= unsigned(seq_counter_r_flat(
            k * 16 + 15 downto k * 16));
    end generate;

    -- ── Per-stage comparators (REA-REQ-601) ─────────────────────
    -- Each stage K matches when (probe_in & mask_K) == (value_K
    -- & mask_K). Same convention as the legacy single-comparator
    -- path. Generated combinationally; G_TRIG_STAGES=1 collapses
    -- to one comparator with no overhead.
    g_stage_match : for k in 0 to G_TRIG_STAGES - 1 generate
        stage_match(k) <= '1' when (
            (probe_in and seq_mask_r_flat(k * G_SAMPLE_W + G_SAMPLE_W - 1
                                          downto k * G_SAMPLE_W)) =
            (seq_value_r_flat(k * G_SAMPLE_W + G_SAMPLE_W - 1
                              downto k * G_SAMPLE_W) and
             seq_mask_r_flat(k * G_SAMPLE_W + G_SAMPLE_W - 1
                             downto k * G_SAMPLE_W))
        ) else '0';
    end generate;

    -- ── Trigger-hit selection (REA-REQ-600 backward-compat) ─────
    -- When seq_enable_r=0, behavior matches v0.1/v0.2 exactly:
    -- the legacy trig_value_r / trig_mask_r drive trigger_hit.
    -- When seq_enable_r=1, only the FINAL stage's match (qualified
    -- by the count target — see process below) drives trigger_hit
    -- via seq_final_fire.
    trigger_hit <= '1' when (
        seq_enable_r = '0' and
        ((probe_in and trig_mask_r) = (trig_value_r and trig_mask_r))
    ) else seq_final_fire when seq_enable_r = '1'
      else '0';

    -- v0.3 decimation tick: '1' every (decim_ratio + 1) cycles.
    -- decim_ratio = 0 → tick always high (no decimation, store every
    -- cycle — matches v0.1/v0.2 behavior).
    decim_tick <= '1' when decim_count_r = 0 else '0';

    -- ── Sequencer: final-stage fire (REA-REQ-602) ──────────────
    -- Combinational: '1' when (a) sequencer enabled, (b) we're at
    -- the final stage, (c) the final stage's local comparator
    -- matches, AND (d) the cumulative match counter would reach
    -- the count_target on this same cycle. Drives trigger_hit via
    -- the selector above; the FSM block then captures wr_ptr_r
    -- into trig_ptr_r unchanged.
    seq_final_fire <= '1' when (
        seq_enable_r = '1' and
        seq_state_r = to_unsigned(G_TRIG_STAGES - 1, C_SEQ_STATE_W) and
        stage_match(G_TRIG_STAGES - 1) = '1' and
        seq_counter_view(G_TRIG_STAGES - 1) + 1
            >= seq_count_target_view(G_TRIG_STAGES - 1)
    ) else '0';

    -- ── Status outputs ───────────────────────────────────────────
    armed         <= armed_r;
    triggered     <= triggered_r;
    done          <= done_r;
    overflow      <= overflow_r;
    trigger_out   <= trigger_out_r;
    wr_ptr_out    <= std_logic_vector(wr_ptr_r);
    trig_ptr_out  <= std_logic_vector(trig_ptr_r);
    start_ptr_out <= std_logic_vector(start_ptr_r);

    -- ── DPRAM drive — sliding-window write enable. Note: NOT gated
    -- by `armed_r`. This is the architectural fix vs fcapz.
    -- v0.3: also gated by decim_tick so only every (decim_ratio+1)
    -- sample is stored. With decim_ratio=0 the tick is always 1 and
    -- behavior matches v0.1/v0.2 exactly. ───────────────────────
    dpram_we   <= '1' when (done_r = '0' and decim_tick = '1') else '0';
    dpram_addr <= std_logic_vector(wr_ptr_r);
    dpram_din  <= probe_in;

    -- ── Capture FSM ──────────────────────────────────────────────
    process (sample_clk, sample_rst)
    begin
        if sample_rst = '1' then
            armed_r        <= '0';
            triggered_r    <= '0';
            done_r         <= '0';
            overflow_r     <= '0';
            wr_ptr_r       <= (others => '0');
            trig_ptr_r     <= (others => '0');
            start_ptr_r    <= (others => '0');
            post_count_r   <= (others => '0');
            pretrig_len_r  <= (others => '0');
            posttrig_len_r <= (others => '0');
            trig_value_r   <= (others => '0');
            trig_mask_r    <= (others => '0');
            decim_ratio_r  <= (others => '0');
            decim_count_r  <= (others => '0');
            seq_enable_r   <= '0';
            seq_state_r    <= (others => '0');
            seq_value_r_flat        <= (others => '0');
            seq_mask_r_flat         <= (others => '0');
            seq_count_target_r_flat <= (others => '0');
            seq_counter_r_flat      <= (others => '0');
            trigger_out_r  <= '0';

        elsif rising_edge(sample_clk) then

            -- Default: trigger_out is a 1-cycle pulse.
            trigger_out_r <= '0';

            -- ── Free-running write pointer ─────────────────────
            -- REA-REQ-100/101: wr_ptr advances every cycle while
            -- !done, regardless of armed state. arm_pulse does NOT
            -- reset wr_ptr — pre-arm context is preserved.
            -- v0.3: also gated by decim_tick so wr_ptr only advances
            -- on stored samples (one per decim_ratio+1 cycles).
            if done_r = '0' and decim_tick = '1' then
                wr_ptr_r <= wr_ptr_r + 1;
            end if;

            -- ── v0.3 decimation counter ────────────────────────
            -- Down-counter that wraps at decim_ratio. When the counter
            -- hits 0, decim_tick fires for one cycle (storing this
            -- sample), then the counter reloads to decim_ratio.
            -- arm_pulse resets the counter so each capture session
            -- starts on a clean tick boundary.
            if done_r = '0' then
                if decim_count_r = 0 then
                    decim_count_r <= decim_ratio_r;
                else
                    decim_count_r <= decim_count_r - 1;
                end if;
            end if;

            -- ── reset_pulse: hard reset of capture state ───────
            if reset_pulse = '1' then
                armed_r       <= '0';
                triggered_r   <= '0';
                done_r        <= '0';
                overflow_r    <= '0';
                post_count_r  <= (others => '0');
                trigger_out_r <= '0';
                -- NOTE: wr_ptr_r is NOT reset on reset_pulse for v0.1
                -- — keeping the buffer state alive across soft resets
                -- is consistent with sliding-window semantics. Hard
                -- buffer-clearing only happens via sample_rst.
            end if;

            -- ── arm_pulse: enable trigger watching ─────────────
            -- Latches config, but does NOT reset wr_ptr_r.
            if arm_pulse = '1' then
                armed_r        <= '1';
                triggered_r    <= '0';
                done_r         <= '0';
                post_count_r   <= (others => '0');
                pretrig_len_r  <= unsigned(pretrig_len_in);
                posttrig_len_r <= unsigned(posttrig_len_in);
                trig_value_r   <= trig_value_in;
                trig_mask_r    <= trig_mask_in;
                decim_ratio_r  <= unsigned(decim_ratio_in);
                -- REA-REQ-606: arm_pulse resets seq_state to 0 and
                -- clears all counters; latches the per-stage config.
                seq_enable_r   <= seq_enable_in;
                seq_state_r    <= (others => '0');
                seq_value_r_flat        <= seq_values_in;
                seq_mask_r_flat         <= seq_masks_in;
                seq_count_target_r_flat <= seq_counts_in;
                seq_counter_r_flat      <= (others => '0');
                -- Load count to 0 so the FIRST cycle after arm ticks
                -- (stores) — and subsequent ticks happen every
                -- (decim_ratio + 1) cycles. With decim_ratio=0 the
                -- counter reloads to 0 every cycle → tick every
                -- cycle (no decimation, matches v0.1/v0.2).
                decim_count_r  <= (others => '0');
                -- Overflow check: window doesn't fit in DEPTH.
                if (unsigned('0' & pretrig_len_in) +
                    unsigned('0' & posttrig_len_in)) >= G_DEPTH then
                    overflow_r <= '1';
                else
                    overflow_r <= '0';
                end if;
            end if;

            -- ── Sequencer state machine (REA-REQ-601..605) ─────
            -- Only advances when the CURRENT stage matches.
            -- Out-of-order matches are ignored (REA-REQ-605).
            -- Non-final-stage matches advance seq_state but do NOT
            -- fire triggered_r (REA-REQ-604) — the trigger-hit
            -- selector above gates the final-stage fire onto
            -- triggered_r via seq_final_fire.
            if seq_enable_r = '1' and armed_r = '1'
               and triggered_r = '0' and done_r = '0' then
                for k in 0 to G_TRIG_STAGES - 1 loop
                    if seq_state_r = to_unsigned(k, C_SEQ_STATE_W)
                       and stage_match(k) = '1' then
                        if seq_counter_view(k) + 1
                           >= seq_count_target_view(k) then
                            -- Reached the count target on this match.
                            -- Final stage → drive seq_final_fire (the
                            -- combinational signal feeding trigger_hit
                            -- which the trigger-detect block below
                            -- still gates onto triggered_r/trig_ptr_r).
                            -- Non-final stage → just advance.
                            if k = G_TRIG_STAGES - 1 then
                                null;  -- final fire handled below
                            else
                                seq_state_r <=
                                    seq_state_r + 1;
                                -- Reset stage K's counter slice in
                                -- the flat vector.
                                seq_counter_r_flat(
                                    k * 16 + 15 downto k * 16
                                ) <= (others => '0');
                            end if;
                        else
                            seq_counter_r_flat(
                                k * 16 + 15 downto k * 16
                            ) <= std_logic_vector(seq_counter_view(k) + 1);
                        end if;
                    end if;
                end loop;
            end if;

            -- ── Trigger detection ──────────────────────────────
            -- Fires only when armed and not yet triggered.
            -- REA-REQ-400/401: an external trigger_in pulse fires
            -- the capture exactly like a local hit, but does NOT
            -- drive trigger_out (otherwise N coupled REA cores
            -- would ping-pong each other forever).
            -- REA-REQ-602: in seq_enable mode, trigger_hit is the
            -- final-stage match path (seq_final_fire).
            if armed_r = '1' and triggered_r = '0' and done_r = '0' then
                if trigger_hit = '1' or trigger_in = '1' then
                    triggered_r <= '1';
                    trig_ptr_r  <= wr_ptr_r;
                    if trigger_hit = '1' then
                        trigger_out_r <= '1';  -- LOCAL fire only
                    end if;
                end if;
            end if;

            -- ── Post-trigger countdown ─────────────────────────
            -- v0.3: counts STORED samples only (decim_tick gate),
            -- so the post-trigger window is `posttrig_len` cells
            -- regardless of decimation ratio.
            if armed_r = '1' and triggered_r = '1' and done_r = '0'
               and decim_tick = '1' then
                if post_count_r >= posttrig_len_r then
                    -- Done capturing the post-trigger window.
                    -- REA-REQ-104: start_ptr <= trig_ptr - pretrig_len
                    -- (mod DEPTH — natural wrap on PTR_W-bit subtract).
                    done_r      <= '1';
                    armed_r     <= '0';
                    start_ptr_r <= trig_ptr_r - pretrig_len_r;
                else
                    post_count_r <= post_count_r + 1;
                end if;
            end if;

        end if;
    end process;

end architecture;
