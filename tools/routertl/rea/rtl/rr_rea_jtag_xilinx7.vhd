-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_jtag_xilinx7 — Xilinx 7-series BSCANE2 wrapper for rr_rea_top.
--
-- Instantiates UNISIM.BSCANE2 with the given JTAG_CHAIN parameter
-- (default 1 = USER1) and connects its TAP signals straight to
-- rr_rea_top. This is the ONLY block in the IP that depends on a
-- vendor primitive — its sim companion is rr_rea_jtag_xilinx7_sim.vhd
-- (behavioral mock; same port signature).

library ieee;
    use ieee.std_logic_1164.all;

library unisim;
    use unisim.vcomponents.BSCANE2;

entity rr_rea_jtag_xilinx7 is
    generic (
        G_SAMPLE_W    : positive := 12;
        G_DEPTH       : positive := 4096;
        G_TIMESTAMP_W : natural  := 32;
        G_NUM_CHAN    : positive := 1;
        G_CTRL_CHAIN  : integer  := 1   -- BSCANE2 USER1
    );
    port (
        sample_clk : in  std_logic;
        sample_rst : in  std_logic;
        probe_in   : in  std_logic_vector(G_SAMPLE_W - 1 downto 0)
    );
end entity;

architecture rtl of rr_rea_jtag_xilinx7 is
    signal tck     : std_logic;
    signal tdi     : std_logic;
    signal tdo     : std_logic;
    signal capture : std_logic;
    signal shift_en: std_logic;
    signal update  : std_logic;
    signal sel     : std_logic;
    -- Power-on tied-low reset for the JTAG domain — BSCANE2 does not
    -- expose a reset, so we rely on the iface FSM's natural init via
    -- `arst='0'` in normal operation.
    signal arst    : std_logic := '0';
begin

    u_bscane2 : BSCANE2
        generic map (
            JTAG_CHAIN => G_CTRL_CHAIN
        )
        port map (
            CAPTURE => capture,
            DRCK    => open,
            RESET   => open,
            RUNTEST => open,
            SEL     => sel,
            SHIFT   => shift_en,
            TCK     => tck,
            TDI     => tdi,
            TMS     => open,
            UPDATE  => update,
            TDO     => tdo
        );

    u_top : entity work.rr_rea_top
        generic map (
            G_SAMPLE_W    => G_SAMPLE_W,
            G_DEPTH       => G_DEPTH,
            G_TIMESTAMP_W => G_TIMESTAMP_W,
            G_NUM_CHAN    => G_NUM_CHAN
        )
        port map (
            sample_clk => sample_clk,
            sample_rst => sample_rst,
            probe_in   => probe_in,
            arst       => arst,
            tck        => tck,
            tdi        => tdi,
            tdo        => tdo,
            capture    => capture,
            shift_en   => shift_en,
            update     => update,
            sel        => sel
        );

end architecture;
