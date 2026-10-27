# Stock firmware recovery candidate

Research date: 2026-10-25. Status: downloaded and inspected only; no printer access,
media preparation, firmware installation or recovery test.

The [manufacturer download page](https://www.creality.com/download/creality-ender-3-s1-pro-fdm-3d-printer)
currently embeds 2.0.8.26F4 and 2.0.8.24F4 packages. Neither matches the development
printer's previously reported 2.0.8.28F4 version. This does not establish that the
manufacturer has no other download channel.

A [community mirror at a pinned commit](https://raw.githubusercontent.com/ThomasToka/MarlinFirmware/cd34c7556edac985879816594e2fc3700d3f3071/Ender-3S1_Pro_HWv24S1_301_SWV2.0.8.28F4_F401_FDM_LASER.zip)
contains a package named for 2.0.8.28F4, F401 and HWv24S1_301.
Its mainboard binary embeds version 2.0.8.28F4 and a June 19, 2023 build date,
consistent with the earlier development-printer report. Matching identifying
strings is not a byte-for-byte backup of the installed firmware or proof of
manufacturer authenticity. No manufacturer signature or checksum was established.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Mirrored ZIP | 24388809 | `be8786e4d2e40479c164b28e4262e5ce234c89f49e9e13ebaf708e03bfe5b004` |
| Mainboard binary | 181488 | `de6290525b7cd183f32dbae3b0f4e752374196eb90bf864beae637f2d045dcc2` |

The ZIP passed its CRC integrity check. The ignored local manifest
`evidence/recovery-research/mirror-package.json` records every file's size and
SHA-256; the original archive and manufacturer-page HTML are retained alongside it.
CRC and our hashes establish local integrity and repeatability, not authenticity.
Do not redistribute these binaries with the host project pending license review.

The package README identifies the Ender-3 S1 Pro and specifically requires an
STM32F401 controller. The archive includes a mainboard image under
`STM32F4_UPDATE`, DWIN screen assets, a `private` screen directory and
`firmware.zlib`. These are distinct update components; the screen assets are not
proof that the physical display has been identified or a screen update is needed.

## Owner About evidence

The October 26 [owner photo intake](development-hardware.md) reports
2.0.8.28F4, screen V1.0.5 and hardware label CR-FDM-v24S1_301. This strengthens the
identifying-string match to this candidate. The physical processor, bootloader,
authenticity and restoration behavior remain unverified; this is still a recovery
candidate. The current display should be preserved unless a separate screen
update is justified and qualified.

## Qualification before using this candidate

1. Confirm the development printer's assignment independently of serial-port name;
   the protected printer remains excluded from all access.
2. Confirm the actual controller chip, board revision, display variant/version,
   existing bootloader and supported update route. Firmware-reported F4 alone is
   insufficient physical identification.
3. Resolve the negative nozzle-temperature reading and confirm the movement area
   is clear before any operational commissioning.
4. Establish package provenance and compatibility; retain the exact candidate,
   firmware/settings evidence and a tested recovery procedure for this hardware.
   A stock archive alone does not preserve calibration or settings.
5. Review whether a mainboard-only development update can preserve the existing
   screen. Do not overwrite bootloader or screen merely because files are present.
6. Qualify restoration on the development printer, then verify identity, settings,
   sensors and required non-printing behavior. Printing remains unqualified while
   its nozzle is clogged.

Until those checks pass, this is a recovery **candidate**, not a validated rollback.
Our custom source baseline is older than the installed firmware; finding a matching
stock binary does not resolve that source-version mismatch.
