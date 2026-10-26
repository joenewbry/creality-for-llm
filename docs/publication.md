# Source export for contribution review

`scripts/export_review.py` prepares a new source directory from one committed
revision. `publication-files.json` lists each included file explicitly. Adding a
file to Git does not automatically publish it. The exporter does not copy Git
history, ignored artifacts, firmware/screen trees or private progress logs.
It reads blobs from the selected commit, so local working edits cannot silently
change the exported snapshot.

```sh
python scripts/export_review.py --revision HEAD --destination /path/to/new-review-directory
```

The destination's parent must already exist. Existing destinations and symlinks
are refused. Only committed regular UTF-8 files are allowed. Known local-path,
private-network, serial-device and credential patterns cause a refusal naming
only the file/category, without printing matched contents. This is a limited
review aid, not proof that all private information or credentials are absent.
Review every file before publication.

`EXPORT-REVIEW.json` records the source commit and each file's hash and byte count.
It always sets `publication_ready: false`. Export success does not authorize a
push, create a GitHub repository, license the project or claim a tested release.

The host uses AGPL-3.0-or-later; original upstream terms are recorded in
`THIRD_PARTY_NOTICES.md` and the pinned dependency metadata inventory. The October 26 delivery publishes the allowlisted host tree and pinned firmware in separate owner repositories with dependent draft PRs. Pattern scans, clean-export checks, attribution/dependency inventory and review of the exposed I/O, model, documentation and evidence paths informed publication; these are not a formal privacy or legal certification. Physical commissioning, production sender integration and upstream acceptance remain open.
Existing upstream licenses must remain with their respective source trees.
Neither stock recovery binaries nor screen assets are included in this export.

Historical local progress logs are excluded. Test commands may create new local
`evidence` files, but those files are not part of the publication allowlist.
Some documentation references historical evidence that remains local; final
release evidence must be separately sanitized and included explicitly.

## Python source archives

`MANIFEST.in` first excludes automatic file discovery, then explicitly includes
the publication allowlist. Keep its include entries synchronized with
`publication-files.json`; the exporter tests check this contract. This preserves
test helpers, dependency locks, firmware-fetch tooling and review documentation
in a source distribution. Setuptools also creates its package metadata files.
The wheel contains the host package and retained license notices; it does not
contain the complete qualification suite or firmware checkout.

Build distribution artifacts from a sanitized review checkout, not the private
working tree. Inspect archive members and compare their bytes to the reviewed
source. Firmware remains separately fetched and pinned; an archive does not
qualify physical operation. The pinned modified firmware is now public in the linked owner fork.
