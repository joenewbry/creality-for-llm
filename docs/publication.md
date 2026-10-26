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
`THIRD_PARTY_NOTICES.md` and the pinned dependency metadata inventory. Remaining
gates include artifact-specific attribution review, manual privacy inspection,
clean-export tests, publishing the pinned modified firmware, and buildable PR
boundaries with dependency links. This is not a finished open-source release.
Existing upstream licenses must remain with their respective source trees.
Neither stock recovery binaries nor screen assets are included in this export.

Historical local progress logs are excluded. Test commands may create new local
`evidence` files, but those files are not part of the publication allowlist.
Some documentation references historical evidence that remains local; final
release evidence must be separately sanitized and included explicitly.
