-- SPDX-FileCopyrightText: 2026 Daniel J. Mazure
-- SPDX-License-Identifier: MIT
--
-- rr_rea_pkg — shared types and constants for the RouteRTL Embedded
-- Analyzer (REA) IP family. Pulled in by every block in the hierarchy.
--
-- The JTAG register addresses below are FROZEN — they form the SW-
-- interface contract with the fcapz host library (subset of the
-- fcapz_ela register map). Adding registers is fine; renumbering
-- existing ones breaks the host SW.

library ieee;
    use ieee.std_logic_1164.all;
    use ieee.numeric_std.all;

package rr_rea_pkg is

    -- ── Magic + version ──────────────────────────────────────────
    -- 'REA' (0x524541) in the upper 24 bits + minor version in low 8.
    constant C_REA_VERSION : std_logic_vector(31 downto 0) := x"52454101";

    -- ── JTAG register map (host SW contract — DO NOT renumber) ───
    constant C_ADDR_VERSION     : unsigned(15 downto 0) := x"0000";  -- RO
    constant C_ADDR_CTRL        : unsigned(15 downto 0) := x"0004";  -- WO
    constant C_ADDR_STATUS      : unsigned(15 downto 0) := x"0008";  -- RO
    constant C_ADDR_SAMPLE_W    : unsigned(15 downto 0) := x"000C";  -- RO
    constant C_ADDR_DEPTH       : unsigned(15 downto 0) := x"0010";  -- RO
    constant C_ADDR_PRETRIG     : unsigned(15 downto 0) := x"0014";  -- RW
    constant C_ADDR_POSTTRIG    : unsigned(15 downto 0) := x"0018";  -- RW
    constant C_ADDR_CAPTURE_LEN : unsigned(15 downto 0) := x"001C";  -- RO
    constant C_ADDR_TRIG_MODE   : unsigned(15 downto 0) := x"0020";  -- RW
    constant C_ADDR_TRIG_VALUE  : unsigned(15 downto 0) := x"0024";  -- RW
    constant C_ADDR_TRIG_MASK   : unsigned(15 downto 0) := x"0028";  -- RW
    constant C_ADDR_CHAN_SEL    : unsigned(15 downto 0) := x"00A0";  -- RW (=0 v0.1)
    constant C_ADDR_NUM_CHAN    : unsigned(15 downto 0) := x"00A4";  -- RO (=1 v0.1)
    constant C_ADDR_DECIM       : unsigned(15 downto 0) := x"00B0";  -- RW v0.3
    constant C_ADDR_TIMESTAMP_W : unsigned(15 downto 0) := x"00C4";  -- RO
    constant C_ADDR_START_PTR   : unsigned(15 downto 0) := x"00C8";  -- RO
    constant C_ADDR_DATA_BASE   : unsigned(15 downto 0) := x"0100";  -- RO

    -- ── CTRL register bit assignments ────────────────────────────
    constant C_CTRL_BIT_ARM     : natural := 0;
    constant C_CTRL_BIT_RESET   : natural := 1;

    -- ── STATUS register bit assignments ──────────────────────────
    constant C_STATUS_BIT_ARMED      : natural := 0;
    constant C_STATUS_BIT_TRIGGERED  : natural := 1;
    constant C_STATUS_BIT_DONE       : natural := 2;
    constant C_STATUS_BIT_OVERFLOW   : natural := 3;

    -- ── TRIG_MODE values ─────────────────────────────────────────
    constant C_TRIG_MODE_VALUE_MATCH : std_logic_vector(31 downto 0) := x"00000001";

    -- ── Helpers ──────────────────────────────────────────────────
    function clog2(n : natural) return natural;

end package;

package body rr_rea_pkg is

    function clog2(n : natural) return natural is
        variable r : natural := 0;
        variable v : natural := 1;
    begin
        while v < n loop
            v := v * 2;
            r := r + 1;
        end loop;
        return r;
    end function;

end package body;
