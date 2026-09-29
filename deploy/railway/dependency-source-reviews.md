# Python dependencies outside PyPI

`scripts/ci/audit_python_lock.py` checks every PyPI registry version in both `uv.lock`
and `pm/uv.lock` with pip-audit, including alternate platform versions. Three
application pins come from Git or a release wheel. Their version strings do not
prove equivalence to PyPI packages, so they have exact-source reviews in
`scripts/ci/python_source_reviews.json` instead.

For these pins, the gate requires an exact match of name, version, source URL or
Git commit, and all locked artifact hashes. It then queries OSV for the reviewed
source commit and **all versions** of the upstream package. It never substitutes
the declared version into a PyPI audit. An unknown source, failed/malformed API
response, commit finding, or unreviewed package advisory fails the gate. Results
are paginated. The two historical psutil records below are accepted only when
their complete canonical JSON hashes still match the reviewed records, so an
updated affected range also requires review. No advisory is globally suppressed.

## Review performed 2026-09-29

### KittenTTS

- Source: the [official 0.8.1 release](https://github.com/KittenML/KittenTTS/releases/tag/0.8.1),
  [tag commit `f0282f0`](https://github.com/KittenML/KittenTTS/tree/f0282f0198d497b7256535b755f9f3e339c1baa7).
- Downloaded wheel SHA256:
  `482a436c4f1f3192153710376e459ff3689517ebcda7c2b051e2fd4187b41851`.
  This matches both the lock and GitHub release asset digest. Every wheel RECORD
  hash/size validates, and all five installed Python files byte-match the tag.
  The wheel contains only that package and ordinary distribution metadata.
- Reviewed the installed code and metadata without importing or installing it.
  Inference uses ONNX Runtime, NumPy (without enabling pickle), and espeak;
  downloads use Hugging Face Hub. The PyPI 0.8.1 release endpoint returns 404.
  OSV commit/package queries and the repository's published security advisory
  endpoint returned no findings. This establishes provenance and the absence of
  currently recorded findings, not equivalence to a nonexistent PyPI release.

### Misaki

- Source: [NousResearch's fork at `f03fd2b`](https://github.com/NousResearch/misaki/tree/f03fd2be7346952a83d3d4845c217fc7667f322d).
  GitHub identifies its parent as `hexgrad/misaki`.
- Compared to the [PyPI 0.9.4 source distribution](https://pypi.org/project/misaki/0.9.4/),
  SHA256 `3960fa3e6de179a90ee8e628446a4a4f6b8c730b6e3410999cf396189f4d9c40`:
  57 of 58 runtime files are identical. `misaki/en.py` adds a BART fallback,
  originally introduced by [upstream commit `232a5e3`](https://github.com/hexgrad/misaki/commit/232a5e32b1f0a2396ac56cc0b498f94d6cd9eb18).
  Metadata also adds pip/torch/transformers and widens Python support to <3.15.
- Reviewed the actual delta: it loads a named Hugging Face BART model using
  `BartForConditionalGeneration.from_pretrained`, then performs tensor inference;
  it does not enable remote-code execution. These dependency pins are separately
  covered by the registry audit. The fork is **not byte-equivalent to PyPI 0.9.4**.
  OSV commit/package queries and both repositories' published advisory endpoints
  returned no findings.

### psutil

- Source: [official upstream commit `380bd2b`](https://github.com/giampaolo/psutil/commit/380bd2b59c67b0e1b04bbf3a90b11744f4f96644),
  adding Android support and its `/proc/self/mountinfo` fallback. The lock selects
  it only for Android; other platforms use a separately audited PyPI release.
  Its declared version 8.0.0 has no corresponding PyPI release.
- The all-version OSV package query returned two records for one vulnerability:
  [GHSA-qfc5-mcwq-26q8](https://github.com/advisories/GHSA-qfc5-mcwq-26q8) and
  [PYSEC-2019-41](https://osv.dev/vulnerability/PYSEC-2019-41), both CVE-2019-18874.
  They concern reference-count double frees fixed in 5.6.6.
- Reviewed the [fix commit `7d512c8`](https://github.com/giampaolo/psutil/commit/7d512c8e4442a896d56505be3e78f1156f443465).
  The [GitHub comparison](https://github.com/giampaolo/psutil/compare/7d512c8e4442a896d56505be3e78f1156f443465...380bd2b59c67b0e1b04bbf3a90b11744f4f96644)
  reports the fix as merge base, 1,805 commits ahead and zero behind. The pin also
  descends from v7.2.2 (568 commits ahead). Inspection of the pinned Linux disk
  and POSIX users implementations confirms that loop cleanup still uses
  `Py_CLEAR` before the error-path `Py_XDECREF`, preserving the relevant fix.
  The commit query returned no findings.
  The manifest records narrowly scoped, content-bound dispositions for these
  two historical advisory records.

## Updating a reviewed pin

Recheck provenance and the real source/artifact delta before changing a review.
Record the new exact identity and supporting links; never copy a version number
from a Git checkout into registry requirements to make an audit pass. For an
advisory, verify the fix against the exact source and record its full OSV record
hash only when the finding does not apply. A changed record needs a fresh review.
The [OSV API](https://google.github.io/osv.dev/api/) supports commit and package
queries; an empty result alone is not proof that an arbitrary fork is safe.

These reviews cover locked code and known advisory applicability. They do not
claim a complete security assessment of runtime model weights: KittenTTS and
Misaki can download Hugging Face model assets without a pinned model revision.
KittenTTS is an opt-in extra, and its current inference path uses espeak rather
than constructing Misaki's added BART fallback. No model assets were downloaded
or executed during this review.
