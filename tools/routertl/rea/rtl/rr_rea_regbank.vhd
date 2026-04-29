-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_regbank — memory-mapped register file for the REA IP.
--
-- Sits between the JTAG protocol decoder (rr_rea_jtag_iface) and the
-- capture FSM (rr_rea_capture_fsm). Implements the SW-interface
-- contract from SPEC.md (REA-REQ-010..012). Synchronous to jtag_clk;
-- CDC to/from sample_clk is the separate rr_rea_cdc block's job.
--
-- v0.1 register map (full table in SPEC.md):
--   0x00 RO  VERSION       0xC8: 0x52454101 ('REA' + v0.1)
--   0x04 WO  CTRL          arm_toggle/reset_toggle
--   0x08 RO  STATUS        armed/triggered/done/overflow
--   0x0C RO  SAMPLE_W      synth-time generic
--   0x10 RO  DEPTH         synth-time generic
--   0x14 RW  PRETRIG
--   0x18 RW  POSTTRIG
--   0x1C RO  CAPTURE_LEN   = pretrig + posttrig + 1
--   0x20 RW  TRIG_MODE     bit[0] = value_match
--   0x24 RW  TRIG_VALUE
--   0x28 RW  TRIG_MASK
--   0xA0 RW  CHAN_SEL      v0.1: must be 0
--   0xA4 RO  NUM_CHAN      v0.1: =1
--   0xC4 RO  TIMESTAMP_W   synth-time generic
--   0xC8 RO  START_PTR     captured address of oldest sample (post-done)
--
-- The CTRL register is "write-toggle": every write XORs the addressed
-- bit position into a sticky toggle register. Downstream rr_rea_cdc
-- edge-detects each toggle to produce a single sample_clk pulse.
-- This is the standard JTAG → fast-clock pulse-coupling pattern.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_regbank is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 4096;
        G_TIMESTAMP_W : natural  := 32;
        G_NUM_CHAN    : positive := 1
    );
    port (
        jtag_clk : in  std_logic;
        jtag_rst : in  std_logic;

        -- ── Register-port interface (from rr_rea_jtag_iface) ─────
        wr_en    : in  std_logic;
        wr_addr  : in  std_logic_vector(15 downto 0);
        wr_data  : in  std_logic_vector(31 downto 0);
        rd_addr  : in  std_logic_vector(15 downto 0);
        rd_data  : out std_logic_vector(31 downto 0);

        -- ── Status inputs (from sample-clk domain, sync'd) ───────
        armed_in     : in std_logic;
        triggered_in : in std_logic;
        done_in      : in std_logic;
        overflow_in  : in std_logic;
        start_ptr_in : in std_logic_vector(clog2(G_DEPTH) - 1 downto 0);

        -- ── Config outputs (to sample-clk domain, will be sync'd) ─
        pretrig_len_out  : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        posttrig_len_out : out std_logic_vector(clog2(G_DEPTH) - 1 downto 0);
        trig_value_out   : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trig_mask_out    : out std_logic_vector(G_SAMPLE_W - 1 downto 0);
        trig_mode_out    : out std_logic_vector(31 downto 0);
        chan_sel_out     : out std_logic_vector(7 downto 0);

        -- ── Pulse toggles (to rr_rea_cdc → sample_clk pulses) ────
        arm_toggle_out   : out std_logic;
        reset_toggle_out : out std_logic
    );
end entity;

architecture rtl of rr_rea_regbank is

    constant C_PTR_W : positive := clog2(G_DEPTH);

    -- ── Storage for RW registers ─────────────────────────────────
    signal pretrig_r    : std_logic_vector(31 downto 0) := (others => '0');
    signal posttrig_r   : std_logic_vector(31 downto 0) := (others => '0');
    signal trig_mode_r  : std_logic_vector(31 downto 0) := (others => '0');
    signal trig_value_r : std_logic_vector(31 downto 0) := (others => '0');
    signal trig_mask_r  : std_logic_vector(31 downto 0) := (others => '0');
    signal chan_sel_r   : std_logic_vector(31 downto 0) := (others => '0');

    -- ── Toggle bits — flipped on every write to CTRL.bit[N] ──────
    signal arm_toggle_r   : std_logic := '0';
    signal reset_toggle_r : std_logic := '0';

    -- ── RO computed: capture_len ─────────────────────────────────
    signal capture_len_w : std_logic_vector(31 downto 0);

    -- ── Sized constants for the RO informational regs ────────────
    function u32(v : natural) return std_logic_vector is
    begin
        return std_logic_vector(to_unsigned(v, 32));
    end function;

    constant C_REG_SAMPLE_W    : std_logic_vector(31 downto 0) := u32(G_SAMPLE_W);
    constant C_REG_DEPTH       : std_logic_vector(31 downto 0) := u32(G_DEPTH);
    constant C_REG_TIMESTAMP_W : std_logic_vector(31 downto 0) := u32(G_TIMESTAMP_W);
    constant C_REG_NUM_CHAN    : std_logic_vector(31 downto 0) := u32(G_NUM_CHAN);

begin

    -- capture_len = pretrig + posttrig + 1 (combinational)
    capture_len_w <= std_logic_vector(
        unsigned(pretrig_r) + unsigned(posttrig_r) + 1
    );

    -- Drive config outputs (lower bits sliced for the FSM's bus widths)
    pretrig_len_out  <= pretrig_r(C_PTR_W - 1 downto 0);
    posttrig_len_out <= posttrig_r(C_PTR_W - 1 downto 0);
    trig_value_out   <= trig_value_r(G_SAMPLE_W - 1 downto 0);
    trig_mask_out    <= trig_mask_r(G_SAMPLE_W - 1 downto 0);
    trig_mode_out    <= trig_mode_r;
    chan_sel_out     <= chan_sel_r(7 downto 0);
    arm_toggle_out   <= arm_toggle_r;
    reset_toggle_out <= reset_toggle_r;

    -- ── Write port (jtag_clk-synchronous) ────────────────────────
    process (jtag_clk, jtag_rst)
    begin
        if jtag_rst = '1' then
            pretrig_r      <= (others => '0');
            posttrig_r     <= (others => '0');
            trig_mode_r    <= (others => '0');
            trig_value_r   <= (others => '0');
            trig_mask_r    <= (others => '0');
            chan_sel_r     <= (others => '0');
            arm_toggle_r   <= '0';
            reset_toggle_r <= '0';

        elsif rising_edge(jtag_clk) then
            if wr_en = '1' then
                case unsigned(wr_addr) is
                    when C_ADDR_CTRL =>
                        -- Toggle bits — XOR each requested bit into
                        -- the sticky toggle register. The downstream
                        -- CDC edge-detects each toggle to make a
                        -- one-cycle sample_clk pulse.
                        if wr_data(C_CTRL_BIT_ARM) = '1' then
                            arm_toggle_r <= not arm_toggle_r;
                        end if;
                        if wr_data(C_CTRL_BIT_RESET) = '1' then
                            reset_toggle_r <= not reset_toggle_r;
                        end if;

                    when C_ADDR_PRETRIG =>
                        pretrig_r <= wr_data;
                    when C_ADDR_POSTTRIG =>
                        posttrig_r <= wr_data;
                    when C_ADDR_TRIG_MODE =>
                        trig_mode_r <= wr_data;
                    when C_ADDR_TRIG_VALUE =>
                        trig_value_r <= wr_data;
                    when C_ADDR_TRIG_MASK =>
                        trig_mask_r <= wr_data;
                    when C_ADDR_CHAN_SEL =>
                        chan_sel_r <= wr_data;

                    when others =>
                        -- REA-REQ-012: writes to RO/unmapped addrs
                        -- are dropped on the floor.
                        null;
                end case;
            end if;
        end if;
    end process;

    -- ── Read port (combinational decode → registered driver) ─────
    --
    -- Pure-combinational read keeps the protocol simple for the JTAG
    -- iface; rd_addr stable for one jtag_clk → rd_data presented same
    -- cycle. The iface registers it on its TDO output.
    process (rd_addr,
             pretrig_r, posttrig_r, trig_mode_r, trig_value_r,
             trig_mask_r, chan_sel_r,
             armed_in, triggered_in, done_in, overflow_in,
             start_ptr_in, capture_len_w)
        variable status : std_logic_vector(31 downto 0);
        variable spr    : std_logic_vector(31 downto 0);
    begin
        status := (others => '0');
        status(C_STATUS_BIT_ARMED)     := armed_in;
        status(C_STATUS_BIT_TRIGGERED) := triggered_in;
        status(C_STATUS_BIT_DONE)      := done_in;
        status(C_STATUS_BIT_OVERFLOW)  := overflow_in;

        spr := (others => '0');
        spr(C_PTR_W - 1 downto 0) := start_ptr_in;

        case unsigned(rd_addr) is
            when C_ADDR_VERSION     => rd_data <= C_REA_VERSION;
            when C_ADDR_STATUS      => rd_data <= status;
            when C_ADDR_SAMPLE_W    => rd_data <= C_REG_SAMPLE_W;
            when C_ADDR_DEPTH       => rd_data <= C_REG_DEPTH;
            when C_ADDR_PRETRIG     => rd_data <= pretrig_r;
            when C_ADDR_POSTTRIG    => rd_data <= posttrig_r;
            when C_ADDR_CAPTURE_LEN => rd_data <= capture_len_w;
            when C_ADDR_TRIG_MODE   => rd_data <= trig_mode_r;
            when C_ADDR_TRIG_VALUE  => rd_data <= trig_value_r;
            when C_ADDR_TRIG_MASK   => rd_data <= trig_mask_r;
            when C_ADDR_CHAN_SEL    => rd_data <= chan_sel_r;
            when C_ADDR_NUM_CHAN    => rd_data <= C_REG_NUM_CHAN;
            when C_ADDR_TIMESTAMP_W => rd_data <= C_REG_TIMESTAMP_W;
            when C_ADDR_START_PTR   => rd_data <= spr;
            when others             => rd_data <= (others => '0');
        end case;
    end process;

end architecture;
