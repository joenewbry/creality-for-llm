# Stock firmware recovery candidate

Research updated October 27, 2026. **Manufacturer download provenance verified;
physical restoration remains unqualified.** No printer access, media preparation,
firmware installation or recovery test occurred during this research.

## Manufacturer channel and identifying match

The [main manufacturer page](https://www.creality.com/download/creality-ender-3-s1-pro-fdm-3d-printer)
still lists .26 and .24. The separate [Creality Cloud catalog](https://www.crealitycloud.com/downloads/firmware/ender-series/ender-3-s1-pro)
contains the matching **2.0.8.28F4 / HWv24S1_301 / F401** package. The public
catalog query for the S1 Pro returned six entries; selected entry
`64d47bc978708804f388cafd` links directly to the
[manufacturer-hosted RAR](https://file2-cdn.creality.com/file/a82fb260f26c12871551de3789b9ba1a/Ender-3S1_Pro_HWv24S1_301_SWV2.0.8.28F4_F401_FDM_LASER.rar).
The catalog timestamp is August 10, 2023 UTC. Its instructions specifically
restrict this package to **STM32F401**; this is not a universal S1 controller image.
A separate Japanese/Korean package is also listed and was not selected.

The mainboard binary embeds 2.0.8.28F4, June 19, 2023 and 18:06:53, consistent with
the actual development-only M115 observation and the owner About photo. The
screen reports V1.0.5. These are identifying matches, not a readback of the
installed binary, physical chip inspection or confirmation of its bootloader.

## Offline integrity and mirror comparison

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Manufacturer RAR | 23137236 | `1b68556722db3a875d52276152405c58f1b38a45c2138a32d2869614a515727d` |
| Earlier mirrored ZIP | 24388809 | `be8786e4d2e40479c164b28e4262e5ce234c89f49e9e13ebaf708e03bfe5b004` |
| Mainboard image in both | 181488 | `de6290525b7cd183f32dbae3b0f4e752374196eb90bf864beae637f2d045dcc2` |

The RAR passed an offline 7-Zip 26.05 integrity test: 216 files, eight folders,
33,432,387 uncompressed bytes. After normalizing the mirror's duplicated outer
folder, **all 216 file names and payloads match exactly**. No file differs or is
exclusive to either package. This independently establishes that the retained
[community mirror at its pinned commit](https://raw.githubusercontent.com/ThomasToka/MarlinFirmware/cd34c7556edac985879816594e2fc3700d3f3071/Ender-3S1_Pro_HWv24S1_301_SWV2.0.8.28F4_F401_FDM_LASER.zip)
has the same contents as this manufacturer delivery. The archive containers differ.

This resolves the earlier manufacturer-channel provenance gap. No vendor code
signature or signed checksum was verified; our SHA-256 values pin what was
retrieved. It does not establish that the installed bytes match, that this is a
full settings backup, or that restoration has been tested. The
[sanitized record and per-file manifest](../validation/manufacturer-recovery-stage133.json)
retain the catalog query, source links, hashes and qualification limits. Raw
archives and captured catalog/HTML remain private and are not redistributed.

macOS bsdtar could list the archive and extract its mainboard image but reported
`Parsing filters is unsupported` for several other files. Do not use that partial
extraction as a complete recovery set. A task-local tool from the
[official 7-Zip download page](https://www.7-zip.org/download.html) completed the
full integrity/extraction check without a global installation.

## Update components and remaining qualification

The vendor instructions use the `STM32F4_UPDATE` directory for the F401 mainboard
SD route. The archive separately contains `DWIN_SET`, `private` and `firmware.zlib`
for the display update route. Reviewing these instructions does not test the
bootloader or an update on this unit. Do not overwrite a bootloader or display
merely because files are present. Preserve the current screen unless a separate
screen update is justified and qualified.

1. Revalidate only the assigned development unit and keep the protected printer
   excluded even when its port is absent.
2. Qualify the actual target, bootloader and compatible update/recovery route
   before flashing. The owner deferred chip/mainboard inspection for source,
   simulation and read-only work; request markings only if necessary to resolve
   remaining uncertainty at the firmware decision. Source availability and F4
   reporting do not independently inspect installed silicon.
3. Diagnose the invalid nozzle reading (−15°C). The owner already confirmed
   idle/clear; that confirmation does not qualify a later physical operation or
   prove sensor repair. Heating and operational commissioning remain blocked.
4. Preserve current settings and validate their restoration. A historical M503
   transcript exists, but is not a current backup or a tested restore procedure.
5. Qualify any required mainboard-only update while preserving the existing screen,
   then exercise restoration on the development printer and verify identity,
   settings, sensors and required non-printing behavior. Printing remains a
   separate post-repair qualification.

The [feature-parity audit](firmware-parity.md) confirms that the older .24
candidate lacks the input shaping reported by stock .28. The official HighSpeed
branch contains prebuilt .27 firmware rather than matching controller source.
An authentic
matching stock delivery does not resolve that source-version mismatch or prove
our candidate preserves all stock .28 features. This is a manufacturer-sourced
recovery **candidate**, not a validated rollback or permission to flash now.
