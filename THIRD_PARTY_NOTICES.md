# Third-party notices

Original host code, tests and documentation in this project are offered under
AGPL-3.0-or-later, except where an upstream notice specifies other terms.
This selection does not relicense dependencies, firmware, screen assets or
stock binary packages. No endorsement by the projects below is implied.

## OctoPrint 1.11.8

The virtual adapter subclasses OctoPrint's sender and its bundled virtual printer.
OctoPrint is by Gina Häußge and contributors; its distribution supplies the
GNU Affero General Public License version 3. The original license text is retained
in `licenses/OctoPrint-LICENSE.txt` and serves as this repository's `LICENSE` text.

Source and original terms:
https://github.com/OctoPrint/OctoPrint/tree/1.11.8
https://github.com/OctoPrint/OctoPrint/blob/1.11.8/LICENSE.txt

Our separate virtual-only adapter adjusts pause queue scheduling, adds shared
framing, receipt checks and bounded operations. These project changes were made
in October 2026; they are not an upstream OctoPrint release or accepted patch.
Installed OctoPrint code is not bundled in this host source tree. Contribution
policy restrictions remain separate from licensing; see `docs/contributions.md`.

## Marlin and Creality S1 Pro firmware

Native tests compile selected code from a separately fetched, pinned firmware
checkout. Marlin's source headers retain the MarlinFirmware, Camiel Gubbels and
Erik van der Zalm notices and identify GPL version 3 or later. Creality's source
and our firmware worktree retain the upstream license and notices. The host
license does not replace them. A copy of the checked controller LICENSE is
retained as `licenses/Marlin-GPL-3.0.txt` for reference.

Official controller source:
https://github.com/CrealityOfficial/Ender-3S1/tree/7fffa9270ff8fb5ee208a5b04f832e20bab19c60

`sources.json` and `firmware-test-source.json` identify upstream and modified
revisions. Firmware sources, compiled binaries and generated native harnesses
are not bundled in this host export. The separate firmware publication must
preserve source notices, the complete license and corresponding build sources.

The touchscreen branch's redistribution terms remain unqualified. Screen assets
and the mirrored stock recovery package are excluded; their availability online
does not establish redistribution permission.

## Direct test and model clients

| Dependency | Version | Declared license | Preserved text |
| --- | --- | --- | --- |
| Model Context Protocol Python SDK | 2.3.0 | MIT | `licenses/mcp-LICENSE.txt` |
| OpenAI Python client | 3.37.0 | Apache-2.0 | `licenses/openai-LICENSE.txt` |
| pytest | 8.3.5 | MIT | `licenses/pytest-LICENSE.txt` |

Their original copyright notices remain in those files. These packages are
installed separately using the lock files, not copied into the host source.
Project links are recorded in the inventory below.

## Pinned dependency inventory

`dependency-notices.json` records the installed metadata declarations, project
URLs, metadata digest and available notice-file hashes for all 99 packages in
`requirements-print-mcp.lock`. It is version-bound evidence, not a compatibility
opinion or certification for a bundled binary/container release. Missing fields
remain missing rather than receiving an inferred license. Long embedded license
fields are represented by a hash; the original distribution remains the source
of their complete text.

Regenerate in the exact locked environment, using a new destination:

```sh
python scripts/dependency_notices.py --output /path/to/new-dependency-notices.json
```

The tool refuses a version mismatch or non-exact lock entry and does not overwrite
existing output. Firmware build-tool dependencies are outside this Python host
inventory and must be reviewed with the separate firmware artifact. Any future
bundle must retain its own complete dependency notices and source obligations;
this source-only inventory does not replace that review.


The review patches `contributions/marlin-heater-watch-zero.patch` and
`contributions/marlin-heater-watch-native-tests.patch`, plus their refreshed
combined form `contributions/marlin-heater-watch-combined.patch`, target
MarlinFirmware/Marlin and its GPL-3.0-or-later source. They are distributed under
GPL-3.0-or-later; the host project's AGPL designation does not relicense that
firmware patch. Upstream attribution remains in the target source.
