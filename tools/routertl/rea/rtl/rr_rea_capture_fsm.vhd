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
        G_SAMPLE_W : positive := 12;
        G_DEPTH    : positive := 4096
    );
    port (
        sample_clk  : in  std_logic;
        sample_rst  : in  std_logic;

        -- ── Probe input (sync'd to sample_clk by the caller) ─────
        probe_in    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── Control pulses (sync'd to sample_clk by rr_rea_cdc) ──
        arm_pulse   : in  std_logic;   -- 1 cycle wide
        reset_pulse : in  std_logic;   -- 1 cycle wide; clears state

        -- ── Latched config (sample_clk domain) ───────────────────
        pretrig_len_in  : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        posttrig_len_in : in  std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_value_in   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trig_mask_in    : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);

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
    signal trig_value_r  : std_logic_vector(G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
    signal trig_mask_r   : std_logic_vector(G_SAMPLE_W - 1 downto 0)
                              := (others => '0');
    signal trigger_out_r : std_logic := '0';

    -- Combinational comparator. value_match mode (only mode in v0.1):
    --   trigger_hit = (probe_in & trig_mask) == (trig_value & trig_mask)
    signal trigger_hit : std_logic;

begin

    trigger_hit <= '1' when ((probe_in and trig_mask_r) =
                             (trig_value_r and trig_mask_r))
                          else '0';

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
    -- by `armed_r`. This is the architectural fix vs fcapz. ───────
    dpram_we   <= '1' when done_r = '0' else '0';
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
            trigger_out_r  <= '0';

        elsif rising_edge(sample_clk) then

            -- Default: trigger_out is a 1-cycle pulse.
            trigger_out_r <= '0';

            -- ── Free-running write pointer ─────────────────────
            -- REA-REQ-100/101: wr_ptr advances every cycle while
            -- !done, regardless of armed state. arm_pulse does NOT
            -- reset wr_ptr — pre-arm context is preserved.
            if done_r = '0' then
                wr_ptr_r <= wr_ptr_r + 1;
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
                -- Overflow check: window doesn't fit in DEPTH.
                if (unsigned('0' & pretrig_len_in) +
                    unsigned('0' & posttrig_len_in)) >= G_DEPTH then
                    overflow_r <= '1';
                else
                    overflow_r <= '0';
                end if;
            end if;

            -- ── Trigger detection ──────────────────────────────
            -- Fires only when armed and not yet triggered.
            if armed_r = '1' and triggered_r = '0' and done_r = '0' then
                if trigger_hit = '1' then
                    -- REA-REQ-102: capture wr_ptr at the cycle
                    -- trigger_hit asserts. Non-blocking → uses the
                    -- current value of wr_ptr_r, which is the
                    -- address being written THIS cycle.
                    triggered_r   <= '1';
                    trig_ptr_r    <= wr_ptr_r;
                    trigger_out_r <= '1';
                end if;
            end if;

            -- ── Post-trigger countdown ─────────────────────────
            if armed_r = '1' and triggered_r = '1' and done_r = '0' then
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
