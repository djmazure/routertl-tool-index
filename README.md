# routertl-tool-index

Curated index of **FPGA companion host-side tools** for use with the
[RouteRTL SDK](https://github.com/djmazure/routertl). Third registry
alongside [`routertl-ip-index`](https://github.com/djmazure/routertl-ip-index)
(HDL IP cores) and [`routertl-ref-index`](https://github.com/djmazure/routertl-ref-index)
(reference designs).

## What is a companion tool?

A companion tool is a program that runs on the **host** (the developer's
machine) and pairs with on-chip debug cores or IP cores from `routertl-ip-index`.
Examples:

- **`fcapz`** (lcapossio/fpgacapZero) — Python CLI + GUI for ELA/EIO/JTAG-AXI
  debug stacks. Talks to on-chip cores over USER1/USER2 JTAG DR.
- **Logic-protocol decoders** — UART/SPI/I²C/QSPI/CAN host-side decoders
  that consume captured samples.
- **JTAG-AXI bridges**, **SignalTap CSV converters**, **bitstream
  inspectors** — anything host-side that augments the RTL tooling.

Companion tools are distinct from:

| Category                      | Where they live                              | Example       |
|-------------------------------|----------------------------------------------|---------------|
| HDL IP cores                  | [`routertl-ip-index`](https://github.com/djmazure/routertl-ip-index) | `axi_lite_slave` |
| Reference designs             | [`routertl-ref-index`](https://github.com/djmazure/routertl-ref-index) | `routertl/linux_zybo` |
| Vendor synthesis toolchains   | discovered by `rr doctor` on `$PATH`         | Vivado, Quartus |

## How tools are resolved

```
rr tool search jtag                # find tools tagged 'jtag'
rr tool info lcapossio/fcapz        # show the canonical tool.yml
rr tool install lcapossio/fcapz     # pip-install or git+ref-install
```

`rr tool install <name>` resolution order:

1. Lookup `<name>` (or `<ns>/<name>`) in this registry's `catalog.json`.
2. Fall back to a matching `host_tools:` shim under
   `libs/<ns>/<name>/ip.yml` in the consuming project (legacy path —
   IPs that ship their own host stack).
3. Error with a "did you mean...?" hint if neither hits.

## Repo layout

```
routertl-tool-index/
  registry.yml              # human-curated list of tool slugs (this file)
  tools/<namespace>/<name>/
    tool.yml                # canonical per-tool manifest
  generate_catalog.py       # rolls every tool.yml into catalog.json
  catalog.json              # CI-generated rollup, consumed by rr / Fly API
```

`tool.yml` schema (full spec in
[`routertl/docs/internal/design/tool_index_registry.md`](https://github.com/djmazure/routertl/blob/main/docs/internal/design/tool_index_registry.md)
when published):

```yaml
name: fcapz
namespace: lcapossio
description: ELA + EIO + JTAG-AXI host stack.
license: Apache-2.0
homepage: https://github.com/lcapossio/fpgacapZero
version: 0.1.0
install:
  pypi: fcapz                 # or { git: <url>, ref: <branch/tag> }
  version: ^0.1.0             # caret/tilde semver supported
pairs_with: [ela, eio, axi]   # debug-core types this tool consumes
vendor_compat: [xilinx7, xilinxus, intel, ecp5, gowin, polarfire]
tags: [debug, jtag, waveform, host-cli]
```

## License gating

Allowlist (mirrors OSI-approved set, broader than the IP registry's
strict 4 because companion tools don't redistribute into user projects):

```
Apache-2.0  MIT  BSD-2-Clause  BSD-3-Clause
MPL-2.0     LGPL-2.1+         GPL-2.0+
OpenLogic-1.0
```

Proprietary tools may be listed for **discoverability** with
`status: non-redistributable` — `rr tool install` will refuse and print
the upstream install URL.

## Adding a tool

1. Author `tools/<namespace>/<name>/tool.yml` (validated against the
   `486b` jsonschema).
2. Add `<namespace>/<name>` to `registry.yml`.
3. Run `python generate_catalog.py` locally to verify.
4. Open a PR — the additive-only merge gate enforces no silent removals.

## Seeding plan

| Tool                     | Status   | Tracker      |
|--------------------------|----------|--------------|
| `lcapossio/fcapz`        | planned  | RTL-P2.482   |
| `djmazure/routewave`     | deferred | RTL-P3.235 (needs `release:` install channel) |

## License

This index itself is MIT-licensed. Each catalogued tool ships under its
own license (see each tool.yml's `license:` field).
