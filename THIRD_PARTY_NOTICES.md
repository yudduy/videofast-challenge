# Third-Party Notices

- **SVT-AV1** (`submission/`, `anchor/svt-av1-v4.2.0.tar.gz`) — Copyright
  Alliance for Open Media contributors. BSD-3-Clause-Clear license with the
  Alliance for Open Media Patent License 1.0. See `submission/LICENSE.md` and
  `submission/PATENTS.md`.
- **dav1d** (built into the toolchain image) — VideoLAN & dav1d authors,
  BSD-2-Clause.
- **VMAF / libvmaf** (built into the toolchain image) — Netflix, Inc.,
  BSD-2-Clause-Plus-Patent.
- **Test sequences** (`corpus-epoch1` release assets; fetched at setup, never
  committed to git) — 64-frame excerpts of clips from the **YouTube UGC
  Dataset** (Wang, Inguva & Adsumilli, "YouTube UGC Dataset for Video
  Compression Research", IEEE MMSP 2019), which comprises user uploads
  published under the Creative Commons Attribution license. Each entry in
  `corpus/manifest.json` records the canonical source object in the
  `ugc-dataset` GCS bucket and its attribution. Excerpts were produced by
  `scripts/prepare_corpus.py` without pixel modification.

Harness code in this repository (`harness/`, `.yukon/`, `toolchain/`,
workflows) is licensed under the repository LICENSE (MIT).
