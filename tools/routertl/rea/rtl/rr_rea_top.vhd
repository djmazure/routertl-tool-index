-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_top — RouteRTL Embedded Analyzer top-level integration.
--
-- Vendor-neutral: takes JTAG TAP signals as ports rather than
-- instantiating BSCANE2 / sld_virtual_jtag / etc. The Xilinx wrapper
-- (rr_rea_jtag_xilinx7.vhd) is a separate thin shim that connects
-- BSCANE2 to this top. In simulation, the cocotb testbench drives
-- the TAP signals directly — zero vendor primitive needed.
--
-- Wiring:
--   JTAG iface     ◄──► regbank          (reg-bus)
--   regbank        ──── CDCs ────►       capture_fsm config inputs
--   capture_fsm    ──── CDCs ────►       regbank status mirror
--   capture_fsm    ──────────────►       dpram (port A: write)
--   dpram (port B: read) ────────►       JTAG iface (when reg_addr in DPRAM window)
--
-- See SPEC.md and requirements.yml REA-REQ-300 for the contract.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

library work;
    use work.rr_rea_pkg.all;

entity rr_rea_top is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 4096;
        G_TIMESTAMP_W : natural  := 32;
        G_NUM_CHAN    : positive := 1
    );
    port (
        -- ── Sample-clock domain ──────────────────────────────────
        sample_clk : in  std_logic;
        sample_rst : in  std_logic;
        probe_in   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0);

        -- ── JTAG TAP (jtag_clk domain) — driven by external wrapper
        --    in synth, driven by testbench in sim ─────────────────
        arst       : in  std_logic;
        tck        : in  std_logic;
        tdi        : in  std_logic;
        tdo        : out std_logic;
        capture    : in  std_logic;
        shift_en   : in  std_logic;
        update     : in  std_logic;
        sel        : in  std_logic
    );
end entity;

architecture rtl of rr_rea_top is

    constant C_PTR_W : positive := clog2(G_DEPTH);

    -- ── Reg-bus wires (jtag_clk domain) ──────────────────────────
    signal reg_clk    : std_logic;
    signal reg_rst    : std_logic;
    signal reg_wr_en  : std_logic;
    signal reg_rd_en  : std_logic;
    signal reg_addr   : std_logic_vector(15 downto 0);
    signal reg_wdata  : std_logic_vector(31 downto 0);
    signal reg_rdata  : std_logic_vector(31 downto 0);

    -- ── Regbank → CDC → FSM config (jtag_clk → sample_clk) ──────
    signal pretrig_jclk  : std_logic_vector(C_PTR_W - 1 downto 0);
    signal posttrig_jclk : std_logic_vector(C_PTR_W - 1 downto 0);
    signal trig_value_jclk : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal trig_mask_jclk  : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal arm_toggle_jclk   : std_logic;
    signal reset_toggle_jclk : std_logic;
    signal trig_mode_jclk    : std_logic_vector(31 downto 0);
    signal chan_sel_jclk     : std_logic_vector(7 downto 0);

    signal pretrig_sclk    : std_logic_vector(C_PTR_W - 1 downto 0);
    signal posttrig_sclk   : std_logic_vector(C_PTR_W - 1 downto 0);
    signal trig_value_sclk : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal trig_mask_sclk  : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal arm_pulse_sclk   : std_logic;
    signal reset_pulse_sclk : std_logic;

    -- ── FSM outputs (sample_clk domain) ──────────────────────────
    signal armed_sclk     : std_logic;
    signal triggered_sclk : std_logic;
    signal done_sclk      : std_logic;
    signal overflow_sclk  : std_logic;
    signal trigger_out_sclk : std_logic;
    signal dpram_we_sclk    : std_logic;
    signal dpram_addr_sclk  : std_logic_vector(C_PTR_W - 1 downto 0);
    signal dpram_din_sclk   : std_logic_vector(G_SAMPLE_W - 1 downto 0);
    signal wr_ptr_sclk      : std_logic_vector(C_PTR_W - 1 downto 0);
    signal trig_ptr_sclk    : std_logic_vector(C_PTR_W - 1 downto 0);
    signal start_ptr_sclk   : std_logic_vector(C_PTR_W - 1 downto 0);

    -- ── CDC: sample_clk → jtag_clk (status mirror) ───────────────
    signal armed_jclk     : std_logic_vector(0 downto 0);
    signal triggered_jclk : std_logic_vector(0 downto 0);
    signal done_jclk      : std_logic_vector(0 downto 0);
    signal overflow_jclk  : std_logic_vector(0 downto 0);
    signal start_ptr_jclk : std_logic_vector(C_PTR_W - 1 downto 0);

    -- ── DPRAM read-port (jtag_clk domain) ────────────────────────
    signal dpram_addr_b : std_logic_vector(C_PTR_W - 1 downto 0);
    signal dpram_dout_b : std_logic_vector(G_SAMPLE_W - 1 downto 0);

    -- ── reg_rdata mux: regbank vs dpram window ───────────────────
    signal regbank_rdata : std_logic_vector(31 downto 0);
    signal dpram_rdata   : std_logic_vector(31 downto 0);
    signal in_dpram_window : std_logic;

    -- Forward declarations of CDC entities are not strictly needed in
    -- VHDL-93+ (entity instantiation works directly), but we keep
    -- them here for clarity.
    component rr_rea_sync_word is
        generic (G_WIDTH : positive);
        port (
            dst_clk : in  std_logic;
            din     : in  std_logic_vector(G_WIDTH - 1 downto 0);
            dout    : out std_logic_vector(G_WIDTH - 1 downto 0)
        );
    end component;

    component rr_rea_pulse_xfer is
        port (
            src_toggle : in  std_logic;
            dst_clk    : in  std_logic;
            dst_rst    : in  std_logic;
            dst_pulse  : out std_logic
        );
    end component;

begin

    -- ── JTAG protocol decoder ────────────────────────────────────
    u_jtag : entity work.rr_rea_jtag_iface
        port map (
            arst      => arst,
            tck       => tck,
            tdi       => tdi,
            tdo       => tdo,
            capture   => capture,
            shift_en  => shift_en,
            update    => update,
            sel       => sel,
            reg_clk   => reg_clk,
            reg_rst   => reg_rst,
            reg_wr_en => reg_wr_en,
            reg_rd_en => reg_rd_en,
            reg_addr  => reg_addr,
            reg_wdata => reg_wdata,
            reg_rdata => reg_rdata
        );

    -- ── Register file ────────────────────────────────────────────
    u_regbank : entity work.rr_rea_regbank
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W,
            G_NUM_CHAN    => G_NUM_CHAN
        )
        port map (
            jtag_clk => reg_clk,
            jtag_rst => reg_rst,
            wr_en    => reg_wr_en,
            wr_addr  => reg_addr,
            wr_data  => reg_wdata,
            rd_addr  => reg_addr,
            rd_data  => regbank_rdata,
            armed_in     => armed_jclk(0),
            triggered_in => triggered_jclk(0),
            done_in      => done_jclk(0),
            overflow_in  => overflow_jclk(0),
            start_ptr_in => start_ptr_jclk,
            pretrig_len_out  => pretrig_jclk,
            posttrig_len_out => posttrig_jclk,
            trig_value_out   => trig_value_jclk,
            trig_mask_out    => trig_mask_jclk,
            trig_mode_out    => trig_mode_jclk,
            chan_sel_out     => chan_sel_jclk,
            arm_toggle_out   => arm_toggle_jclk,
            reset_toggle_out => reset_toggle_jclk
        );

    -- ── reg_rdata mux: dpram window vs regbank ───────────────────
    -- dpram_window: addr in [0x0100 .. 0x0100 + DEPTH*4)
    -- (each dpram cell occupies 4 bytes / 1 word in the JTAG map)
    in_dpram_window <= '1' when
        unsigned(reg_addr) >= unsigned(C_ADDR_DATA_BASE) and
        unsigned(reg_addr) < (unsigned(C_ADDR_DATA_BASE) +
                              to_unsigned(G_DEPTH * 4, 16))
        else '0';

    -- Address into dpram: (reg_addr - DATA_BASE) >> 2
    dpram_addr_b <= std_logic_vector(resize(
        shift_right(unsigned(reg_addr) - unsigned(C_ADDR_DATA_BASE), 2),
        C_PTR_W));

    -- Zero-extend dpram cell to 32 bits.
    dpram_rdata <= std_logic_vector(resize(unsigned(dpram_dout_b), 32));

    reg_rdata <= dpram_rdata when in_dpram_window = '1' else regbank_rdata;

    -- ── CDC: jtag_clk config words → sample_clk ──────────────────
    u_cdc_pretrig : rr_rea_sync_word
        generic map (G_WIDTH => C_PTR_W)
        port map (dst_clk => sample_clk, din => pretrig_jclk,
                  dout => pretrig_sclk);

    u_cdc_posttrig : rr_rea_sync_word
        generic map (G_WIDTH => C_PTR_W)
        port map (dst_clk => sample_clk, din => posttrig_jclk,
                  dout => posttrig_sclk);

    u_cdc_trig_value : rr_rea_sync_word
        generic map (G_WIDTH => G_SAMPLE_W)
        port map (dst_clk => sample_clk, din => trig_value_jclk,
                  dout => trig_value_sclk);

    u_cdc_trig_mask : rr_rea_sync_word
        generic map (G_WIDTH => G_SAMPLE_W)
        port map (dst_clk => sample_clk, din => trig_mask_jclk,
                  dout => trig_mask_sclk);

    -- ── CDC: jtag_clk pulse toggles → sample_clk pulses ─────────
    u_cdc_arm : rr_rea_pulse_xfer
        port map (
            src_toggle => arm_toggle_jclk,
            dst_clk => sample_clk, dst_rst => sample_rst,
            dst_pulse => arm_pulse_sclk
        );

    u_cdc_reset : rr_rea_pulse_xfer
        port map (
            src_toggle => reset_toggle_jclk,
            dst_clk => sample_clk, dst_rst => sample_rst,
            dst_pulse => reset_pulse_sclk
        );

    -- ── Capture FSM ──────────────────────────────────────────────
    u_fsm : entity work.rr_rea_capture_fsm
        generic map (G_SAMPLE_W => G_SAMPLE_W, G_DEPTH => G_DEPTH)
        port map (
            sample_clk    => sample_clk,
            sample_rst    => sample_rst,
            probe_in      => probe_in,
            arm_pulse     => arm_pulse_sclk,
            reset_pulse   => reset_pulse_sclk,
            pretrig_len_in  => pretrig_sclk,
            posttrig_len_in => posttrig_sclk,
            trig_value_in   => trig_value_sclk,
            trig_mask_in    => trig_mask_sclk,
            armed       => armed_sclk,
            triggered   => triggered_sclk,
            done        => done_sclk,
            overflow    => overflow_sclk,
            trigger_out => trigger_out_sclk,
            dpram_we    => dpram_we_sclk,
            dpram_addr  => dpram_addr_sclk,
            dpram_din   => dpram_din_sclk,
            wr_ptr_out    => wr_ptr_sclk,
            trig_ptr_out  => trig_ptr_sclk,
            start_ptr_out => start_ptr_sclk
        );

    -- ── CDC: sample_clk status → jtag_clk ────────────────────────
    u_cdc_armed : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk => reg_clk,
                  din(0) => armed_sclk,
                  dout => armed_jclk);

    u_cdc_triggered : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk => reg_clk,
                  din(0) => triggered_sclk,
                  dout => triggered_jclk);

    u_cdc_done : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk => reg_clk,
                  din(0) => done_sclk,
                  dout => done_jclk);

    u_cdc_overflow : rr_rea_sync_word
        generic map (G_WIDTH => 1)
        port map (dst_clk => reg_clk,
                  din(0) => overflow_sclk,
                  dout => overflow_jclk);

    u_cdc_start_ptr : rr_rea_sync_word
        generic map (G_WIDTH => C_PTR_W)
        port map (dst_clk => reg_clk,
                  din => start_ptr_sclk,
                  dout => start_ptr_jclk);

    -- ── Sample DPRAM ─────────────────────────────────────────────
    u_dpram : entity work.rr_rea_dpram
        generic map (G_WIDTH => G_SAMPLE_W, G_DEPTH => G_DEPTH)
        port map (
            clk_a  => sample_clk,
            we_a   => dpram_we_sclk,
            addr_a => dpram_addr_sclk,
            din_a  => dpram_din_sclk,
            dout_a => open,
            clk_b  => reg_clk,
            addr_b => dpram_addr_b,
            dout_b => dpram_dout_b
        );

end architecture;
