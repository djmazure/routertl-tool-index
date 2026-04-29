-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_trig_xbar — cross-domain trigger crossbar for multi-instance
-- REA setups (RTL-P3.266 + REA-REQ-402, v0.2 architectural feature).
--
-- When a design has multiple clock domains and a separate REA
-- instance per domain, you usually want ONE trigger event in any
-- domain to freeze the capture in ALL of them. That way the
-- captured windows are time-coherent and you can reason across
-- domain boundaries from a single trigger moment.
--
-- This block sits between N REA instances and routes each
-- instance's local trigger pulse (`trigger_out`) to every OTHER
-- instance's `trigger_in` input, with the necessary CDC.
--
-- v0.2 ships with N=2 only. Extending to N=3,4 is a generic-N
-- pulse_xfer fan-out tree — left for a follow-up. Two domains
-- already covers the common case (e.g. processor + fabric, or
-- TSE-MAC vs PCS reference clock).
--
-- The CDC primitive (rr_rea_pulse_xfer) takes a TOGGLE LEVEL on
-- the source clock and emits a 1-cycle pulse on the destination
-- clock. Each REA instance already maintains a `trigger_sticky_r`
-- toggle in rr_rea_top (it flips on every local trigger fire),
-- so we wire that directly to the xbar inputs — no glue needed.
--
-- The destination side gates on its own reset; the source side
-- doesn't need a reset because it just samples the toggle.

library ieee;
    use ieee.std_logic_1164.all;

entity rr_rea_trig_xbar is
    port (
        -- Instance A
        clk_a         : in  std_logic;
        rst_a         : in  std_logic;
        toggle_a_in   : in  std_logic;   -- from REA A's trigger_sticky_r
        pulse_a_out   : out std_logic;   -- to   REA A's trigger_in

        -- Instance B
        clk_b         : in  std_logic;
        rst_b         : in  std_logic;
        toggle_b_in   : in  std_logic;
        pulse_b_out   : out std_logic
    );
end entity;

architecture rtl of rr_rea_trig_xbar is
    component rr_rea_pulse_xfer is
        port (
            src_toggle : in  std_logic;
            dst_clk    : in  std_logic;
            dst_rst    : in  std_logic;
            dst_pulse  : out std_logic
        );
    end component;
begin

    -- B's trigger event → synchronized pulse on clk_a → A.trigger_in
    u_b_to_a : rr_rea_pulse_xfer
        port map (
            src_toggle => toggle_b_in,
            dst_clk    => clk_a,
            dst_rst    => rst_a,
            dst_pulse  => pulse_a_out
        );

    -- A's trigger event → synchronized pulse on clk_b → B.trigger_in
    u_a_to_b : rr_rea_pulse_xfer
        port map (
            src_toggle => toggle_a_in,
            dst_clk    => clk_b,
            dst_rst    => rst_b,
            dst_pulse  => pulse_b_out
        );

end architecture;
