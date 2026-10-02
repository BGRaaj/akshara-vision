# Akshara Vision: Product Audit and Launch Strategy

**Review date:** 6 September 2026  
**Project version reviewed:** `1.1.0`  
**Purpose:** Explain what the project does today, what must be fixed, whether a training-data business is possible, and how to build an easier chat-style product.

---

## 1. Short answer

Akshara Vision has a strong and meaningful idea: help people recover, understand, verify, and preserve old documents while keeping a clear link to the original page.

The project already contains much more than a basic command-line OCR tool. It has:

- document restoration and translation;
- local and cloud model support;
- PDF, image, text, ZIP, and manifest inputs;
- multiple export formats;
- grounded document chat;
- resumable run concepts and checkpoints;
- a scan-batch verification engine;
- a local browser review screen.

However, **the current version is not ready for a public production launch**. Several serious bugs can produce false audit results, expose local files, lose trust in outputs, or report success when work actually failed.

### My recommendation

**Do not launch version 1.1.0 as a “tender-compliant”, “PDF/A-compliant”, or production acceptance system.**

Instead:

1. Fix the critical security and correctness issues.
2. Narrow claims to what the software can prove.
3. Run pilots with two or three archives or scanning companies.
4. Build a browser interface as the main interface while keeping the CLI for technical users.
5. Build a rights-cleared, human-reviewed dataset as a separate product asset.

The long-term opportunity is real, but the winning product should be positioned as:

> **A trusted restoration and quality-control workspace for rare and damaged documents, with every AI suggestion linked back to visible evidence.**

That is stronger and safer than promising that AI can automatically “fill in” lost history.

---

## 2. What the product actually is today

The current product has three main parts.

### A. Document restoration pipeline

It accepts scanned pages, PDFs, ZIP archives, OCR text, and folders. It can send text or page images to local or cloud models, save intermediate records, and export the result.

Important code areas:

- `src/akshara_vision/core/pipeline.py`
- `src/akshara_vision/providers/`
- `src/akshara_vision/exporters/`
- `src/akshara_vision/core/models.py`

### B. Document chat

A chat mode already exists. Users can chat generally, attach documents, search sources, focus on pages, and request citations.

Important code areas:

- `src/akshara_vision/core/chat.py`
- `src/akshara_vision/cli/workflows.py`
- `docs/chat.md`

This means the chat product idea is **not starting from zero**.

### C. Akshara Verify

The verification system tries to check scan batches for file integrity, naming, sequence gaps, image quality, metadata, duplicates, and PDF properties. It can generate reports and start a local review website.

Important code areas:

- `src/akshara_vision/verify/`
- `src/akshara_vision/verify/profiles/`
- `docs/verify.md`

This could become a valuable business product for scanning contractors and archives, but it currently has serious false-pass and false-fail problems.

---

## 3. Launch decision

### Current decision: No-go for a full public launch

A limited developer preview is reasonable if it is clearly marked as alpha. A production launch is not reasonable until the P0 issues below are fixed.

### Why

A preservation product must be more trustworthy than a normal AI demo. Users may rely on it for historical, legal, cultural, academic, or government records. The system must never:

- silently invent missing text;
- say a failed run is complete;
- approve a batch using the wrong profile;
- claim PDF/A compliance without performing real PDF/A validation;
- overwrite files through an unsafe archive;
- send an unrelated local file to a cloud model;
- hide which text was AI-generated and which text was actually visible.

Trust and provenance are not optional features here. They are the main product.

---

## 4. Confirmed issues

The issues below were found by tracing the source code and running focused tests. “Confirmed” means the behavior is visible in the code or was reproduced. Suggested features are listed later and are not mixed with bugs.

## P0: Must fix before any public launch

### 1. ZIP files can write outside the extraction folder

**Severity:** Critical security issue  
**Code:** `src/akshara_vision/core/pipeline.py:5911-5920`, `6249-6262`

A specially created ZIP can use a rooted path and make the program write outside its temporary extraction directory. This is a form of ZIP path traversal.

**Risk:** An untrusted archive could overwrite a file that the current user is allowed to modify.

**Fix:** Reject absolute paths, drive paths, UNC paths, `..`, and mixed path separators. Resolve every target and confirm it remains inside the extraction directory before writing.

### 2. Instruction preset names can read unrelated local text files

**Severity:** High security and privacy issue  
**Code:** `src/akshara_vision/instructions/__init__.py:9-14`

The preset name is joined directly to the instruction directory. A name containing parent-directory segments can read a `.txt` file outside that directory. That content may then be included in a cloud-model request.

**Risk:** Local information can be exposed to a model provider.

**Fix:** Allow only simple preset IDs, reject path separators, and verify that the resolved path is inside the approved instruction directory.

### 3. A project `.env` file can change any environment variable

**Severity:** High security issue  
**Code:** `src/akshara_vision/core/env.py:37-60`

The application automatically reads `.env` from the current folder and imports every key. It does not limit keys to the documented Akshara and provider settings.

**Risk:** An untrusted project can alter `PATH`, proxy settings, TLS behavior, or other process settings before Akshara runs external tools.

**Fix:** Load only an explicit allowlist. Do not automatically trust `.env` in any folder without clear user approval.

### 4. Valid PDFs are treated as images by Akshara Verify

**Severity:** High correctness issue  
**Code:** `src/akshara_vision/verify/integrity.py:37-39`, `224-243`; `src/akshara_vision/verify/engine.py:109-114`

Both built-in profiles allow PDFs, but the integrity checker first tries to open every allowed file with Pillow as an image. A valid PDF can therefore receive a critical unreadable-image error before the PDF checker runs.

**Risk:** Good delivery batches can be rejected.

**Fix:** Route images, PDFs, metadata, and other files to separate validators.

### 5. Metadata can cause both false failures and false passes

**Severity:** High data-integrity issue  
**Code:** `src/akshara_vision/verify/engine.py:116-120`; `src/akshara_vision/verify/metadata.py:51-77`, `155-180`

A valid metadata CSV or JSON inside a batch can be treated as an invalid image file. At the same time, missing, empty, or malformed metadata can pass because parse failures are not converted into acceptance failures.

**Risk:** Correct batches can fail and incomplete batches can pass.

**Fix:** Define whether metadata is required in each profile. Report missing, empty, and parse-error conditions. Exclude metadata from image checks.

### 6. PDF/A compliance is not actually validated

**Severity:** High trust and legal/procurement issue  
**Code:** `src/akshara_vision/verify/pdfa.py:26-139`

The current code checks basic PDF structure, encryption, page count, and some XMP text. It does not perform complete PDF/A-1b or PDF/A-2b conformance checks and does not inspect whether fonts are embedded.

**Risk:** The product can approve a non-compliant PDF while the documentation says it validates PDF/A compliance.

**Fix:** Integrate a standards validator such as veraPDF. Until then, rename the feature to “basic PDF structural inspection” and remove the PDF/A compliance claim.

### 7. Built-in verification profiles are missing from built packages

**Severity:** High packaging issue  
**Code:** `pyproject.toml:67-68`; `src/akshara_vision/verify/engine.py:45-65`

The package configuration includes instruction text files but not `verify/profiles/*.toml`. Source-tree tests see those files, but a built wheel does not include them.

**Risk:** Installed users who choose a tender profile can receive fallback defaults instead.

**Fix:** Package the TOML profiles and test an installed wheel in CI.

### 8. Unknown profiles silently use default rules

**Severity:** High correctness issue  
**Code:** `src/akshara_vision/verify/engine.py:45-65`; `src/akshara_vision/cli/workflows.py:4450-4457`

A misspelled profile name produces a warning and continues with a default profile.

**Risk:** A tender audit can run with unintended and weaker criteria.

**Fix:** If a user explicitly names an unknown profile, stop with a nonzero exit code.

### 9. Unknown model providers silently use the mock provider

**Severity:** High correctness issue  
**Code:** `src/akshara_vision/registries/providers.py:80-81`

A provider spelling mistake returns the mock provider rather than an error.

**Risk:** A production run may produce demonstration output while appearing to use the requested provider.

**Fix:** Fail closed and list valid provider IDs. Use the mock only when the user explicitly selects `mock`.

### 10. Failed operations often return exit code 0

**Severity:** High automation issue  
**Code:** `src/akshara_vision/cli/app.py:79-87`, `256-270`; `src/akshara_vision/cli/workflows.py:3098-3113`, `4438-4531`

A missing verification folder prints an error but can return success to the operating system. A rejected batch also does not reliably become a nonzero CLI exit status. Pipeline exceptions are caught and printed without reaching the command caller.

**Risk:** CI systems, scripts, and government acceptance gates can treat a failed audit as successful.

**Fix:** Define stable exit codes for accepted, rejected, invalid input, and operational failure.

### 11. A completely failed restoration run is marked `complete`

**Severity:** High data-integrity issue  
**Code:** `src/akshara_vision/core/pipeline.py:690-707`, `860-887`

Per-file errors are recorded, but the final run state is written as `complete` even when all inputs failed.

**Risk:** Users may publish or archive an empty failed result as a completed restoration.

**Fix:** Use `complete`, `partial`, and `failed` states based on actual file and export results.

### 12. `dry_run`, core `resume`, and workflow selection do not control execution

**Severity:** High behavior mismatch  
**Code:** `src/akshara_vision/core/models.py:21-36`, `203-209`; `src/akshara_vision/core/pipeline.py:564-588`, `784-788`

The core pipeline does not read `RunRequest.dry_run` or `RunRequest.resume`. The selected workflow is mainly stored as metadata rather than choosing a different execution path.

**Risk:** A “dry run” can create outputs and call providers. Users cannot rely on the advertised workflow behavior.

**Fix:** Implement these controls at the core service boundary or remove them until implemented.

### 13. hOCR, ALTO, and PAGE XML exports use incorrect page records

**Severity:** High interoperability issue  
**Code:** `src/akshara_vision/exporters/archive.py:14-26`; `src/akshara_vision/core/pipeline.py:781-820`, `930-936`, `1030-1041`

The sidecar exporters expect `_records`, while normal runs provide restoration metadata in another shape. This can create a fake `unknown.png` page or repeat the full combined text for each page.

**Risk:** Archive systems receive invalid or misleading OCR sidecars.

**Fix:** Create one tested page-record schema and validate outputs against hOCR, ALTO, and PAGE XML expectations.

### 14. The local review website has no authentication or request protection

**Severity:** High if network-exposed; medium on loopback only  
**Code:** `src/akshara_vision/verify/review_ui.py:38-123`, `153-175`, `435-469`

The endpoints can read audit data, save decisions, and trigger calibration without authentication, CSRF protection, or origin checks. The default is local loopback, which reduces but does not remove the risk.

**Risk:** A malicious web page or another local process may alter decisions. Network binding can expose batch files and paths.

**Fix:** Keep loopback-only by default, generate a random launch token, validate origin/host/content type, restrict served files, and add secure headers.

### 15. Verification can follow links outside the selected batch

**Severity:** High privacy issue for untrusted batches  
**Code:** `src/akshara_vision/verify/integrity.py:224-231`; `src/akshara_vision/verify/engine.py:24-35`

The batch scanner follows file links without confirming the resolved path remains inside the selected batch.

**Risk:** An untrusted batch can make the tool read and hash an unrelated local file.

**Fix:** Reject links by default or enforce resolved-path containment.

---

## P1: Fix before production pilots or contractual use

### 16. Verification can invalidate its own checksum report

If the output folder is inside the batch, old reports may be hashed and then overwritten. The resulting checksum manifest is already stale. The default sibling report name can also be reused by different batches.

**Code:** `src/akshara_vision/verify/engine.py:81-88`, `165-168`; `src/akshara_vision/verify/reports.py:24-93`

**Fix:** Reject output folders inside the batch and create unique, atomic report directories.

### 17. Empty batches pass

A folder with no deliverable files can receive a passing result.

**Code:** `src/akshara_vision/verify/engine.py:124-156`; `tests/test_verify.py:510-517`

**Fix:** “No files checked” must be failed or indeterminate, never passed.

### 18. Important profile rules are stored but not enforced

`required_directory_structure` and `max_flag_rate` exist but do not affect acceptance.

**Code:** `src/akshara_vision/verify/models.py:52-59`; `src/akshara_vision/verify/engine.py:156`

**Fix:** Validate the profile schema and enforce every accepted rule.

### 19. Human review decisions overwrite each other

Decisions are keyed only by file path. Multiple problems on the same page cannot keep separate decisions.

**Code:** `src/akshara_vision/verify/review_ui.py:167-171`, `344-349`

**Fix:** Use a stable exception ID containing file, check, page, and occurrence.

### 20. Profile calibration does not match the checks it is meant to tune

Calibration expects names such as `blur_detection`, but verification emits names such as `blur`. The web endpoint also calibrates a new default profile instead of the profile that produced the report.

**Code:** `src/akshara_vision/verify/classifier.py:102-123`; `src/akshara_vision/verify/quality.py:36-65`, `116-123`; `src/akshara_vision/verify/review_ui.py:98-108`

**Fix:** Standardize check IDs and load the actual base profile.

### 21. The advertised archival classifier is not connected to verification

The classifier is exported but the verification flow does not call it. `onnxruntime` is also listed as a dependency but is not used in this subsystem.

**Code:** `src/akshara_vision/verify/__init__.py:20-40`; `src/akshara_vision/verify/quality.py:249-255`

**Fix:** Integrate and validate it, or remove the claim and unused dependency.

### 22. Sequence checking mixes unrelated documents and can exhaust memory

The checker puts numeric suffixes from unrelated folders and filename patterns into one sequence. A huge numeric gap can create an enormous list of individual exceptions. Duplicate page numbers are not reported.

**Code:** `src/akshara_vision/verify/integrity.py:152-181`

**Fix:** Group by directory and filename pattern, report ranges, cap exceptions, and detect duplicate numbers.

### 23. Near-duplicate detection has false positives and quadratic cost

The average hash makes a uniform black page and a uniform white page identical. Every page is compared with every other page, which does not scale to large archives.

**Code:** `src/akshara_vision/verify/quality.py:141-202`

**Fix:** Use stronger hashes plus luminance/dimension checks and indexed duplicate grouping.

### 24. Minimum DPI can be downgraded

The expected-DPI check runs before the hard minimum check using `elif`, so a page below the minimum can receive only medium severity. Exact floating-point equality also creates false mismatches.

**Code:** `src/akshara_vision/verify/integrity.py:40-59`

**Fix:** Check the minimum independently and use a DPI tolerance.

### 25. Hidden-file discovery is inconsistent

One discovery function excludes hidden files while another checks them. Totals, checksums, and exceptions can disagree.

**Code:** `src/akshara_vision/verify/engine.py:24-35`, `81-88`; `src/akshara_vision/verify/integrity.py:224-246`

**Fix:** Use one canonical inventory for every stage.

### 26. Verification repeatedly decodes the same large image

One archival image may be read and decoded many times for checksum, format, blank, blur, skew, crop, and duplicate checks.

**Code:** `src/akshara_vision/verify/integrity.py:11-17`, `37-39`, `121-125`; `src/akshara_vision/verify/quality.py`

**Fix:** Decode once, reuse downsampled representations, and add controlled parallel workers.

### 27. The review website handles nested files and TIFF previews poorly

It reduces paths to basenames, so duplicate names in different folders can collide. TIFF files are put directly in browser image tags even though browser TIFF support is weak.

**Code:** `src/akshara_vision/verify/review_ui.py:344-388`

**Fix:** Use opaque file IDs and generate browser-safe preview images.

### 28. Combine can ignore human-edited final files

The combine flow can prefer older manifest chunks over files edited by a human under `items/`, contrary to the documentation.

**Code:** `src/akshara_vision/core/pipeline.py:891-910`, `958-970`

**Fix:** Make reviewed final item data the first source, then use stage and manifest fallbacks.

### 29. Run folders can collide

Run directory names use second-level timestamps and `exist_ok=True`. Two rapid or concurrent runs can share a directory.

**Code:** `src/akshara_vision/core/pipeline.py:573-577`

**Fix:** Atomically create a unique directory using a UUID or collision-safe suffix.

### 30. Run-state and chat-session files are not written atomically

An interruption can leave invalid JSON. Concurrent updates can overwrite each other. Chat session names can also collide within one second.

**Code:** `src/akshara_vision/core/pipeline.py:1308-1318`; `src/akshara_vision/core/chat.py:252-261`, `327-349`

**Fix:** Write temporary files followed by `os.replace`, add locking, and use unique IDs.

### 31. ZIP extraction has no resource limits

There is no limit for file count, expanded bytes, compression ratio, or disk budget.

**Code:** `src/akshara_vision/core/pipeline.py:5911-5920`

**Fix:** Add archive limits before and during extraction.

### 32. PDF rendering and external programs can hang or use too many resources

PDF pages can be rendered in one large operation, and Poppler, Chromium, and LibreOffice calls have no timeout.

**Code:** `src/akshara_vision/core/pipeline.py:5475-5483`, `5762-5883`; `src/akshara_vision/exporters/pdf.py:108-159`

**Fix:** Render pages lazily, add bounded queues, subprocess timeouts, and process-tree cleanup.

### 33. Manifests can recursively include themselves

Manifest discovery has no visited set or nesting limit.

**Code:** `src/akshara_vision/core/input_discovery.py:29-36`

**Fix:** Track resolved manifests and reject cycles.

### 34. Some manifest records contain absolute or temporary paths

Media paths can expose a user directory or point to a deleted temporary ZIP directory.

**Code:** `src/akshara_vision/core/pipeline.py:4910-4914`, `5646-5657`, `6208-6219`

**Fix:** Copy review media into the run directory and store run-relative paths.

### 35. Cloud keys and document content can be sent over plain HTTP

Custom and “local” remote endpoints can default to HTTP. This is dangerous when the endpoint is not loopback, especially if an API key and document images are included.

**Code:** `src/akshara_vision/providers/local.py:17-23`, `156-177`, `409-480`; `src/akshara_vision/providers/cloud.py:117-137`

**Fix:** Require HTTPS for non-loopback hosts or any request containing a key. Make insecure HTTP an explicit advanced option.

### 36. Chat retrieval can omit the evidence it selected

The ranker searches the full source, but the prompt sends only the first 1,200 characters of each selected source.

**Code:** `src/akshara_vision/core/chat.py:398-405`, `490-528`

**Fix:** Index smaller page/paragraph chunks and send the matching passage plus nearby context under a total token budget.

### 37. Folder chat selects unrelated files

A current test shows that a question about `newton` processes both `newton-page.pdf` and `other-page.pdf`.

**Code:** `src/akshara_vision/core/chat.py:685-715`, `893-912`; `tests/test_core.py:1064-1088`

**Fix:** Match meaningful filename/path tokens, remove common words, and use all files only when no useful match exists.

### 38. Document content can attempt prompt injection

Source text is inserted directly into the model prompt. A document may contain instructions aimed at the model.

**Code:** `src/akshara_vision/core/chat.py:393-405`

**Status:** Strong risk, but provider-by-provider impact needs adversarial testing.

**Fix:** Clearly delimit untrusted content, never allow document text to change tool permissions, and verify that citations support the final answer.

### 39. Export failures are not saved clearly

Exporter exceptions are shown only through progress messages and can disappear from the final manifest while the run is still called complete.

**Code:** `src/akshara_vision/core/pipeline.py:829-837`, `1266-1288`

**Fix:** Save an explicit result for every requested export and include failures in final run status.

### 40. DOCX line-break XML is invalid

The exporter inserts `<w:br/>` inside a text node instead of as a WordprocessingML run child.

**Code:** `src/akshara_vision/exporters/text.py:729-732`, `1069-1070`, `1124-1129`

**Fix:** Generate sibling text and break elements and validate sample DOCX files in CI.

### 41. Blank text can become a successful `[missing text]` document

An empty input is marked restored rather than blank.

**Code:** `src/akshara_vision/core/pipeline.py:1356`, `1444-1455`

**Fix:** Save an empty, explicitly `blank` record.

### 42. Model temperature settings are ignored

Provider payloads hardcode `0.1` rather than using the configured temperature.

**Code:** `src/akshara_vision/providers/local.py:343-350`, `463-468`; `src/akshara_vision/providers/cloud.py:244-249`, `355-359`

**Fix:** Use the validated setting or remove it from user configuration.

### 43. Translation modes are only partly implemented

`transliterate` uses a general translation contract, `metadata-only` does not perform a metadata-only model task, and `bilingual` does not request a distinct bilingual provider output.

**Code:** `src/akshara_vision/core/constants.py:49-57`; `src/akshara_vision/core/pipeline.py:5017-5067`, `5278-5306`

**Fix:** Implement and test each mode or hide unfinished modes.

---

## P2: Product quality and maintenance issues

### 44. Python 3.13 is advertised but not tested in CI

`pyproject.toml` lists Python 3.13, while CI tests 3.9 through 3.12. The current local 3.13 run has one failing test.

**Code:** `pyproject.toml:21-26`; `.github/workflows/ci.yml:11-15`

### 45. CI tests editable source, not the installed package

This allowed missing TOML profile files to go unnoticed.

**Code:** `.github/workflows/ci.yml:24-30`

### 46. CI installs Ruff and coverage tools but does not run them

**Code:** `pyproject.toml:37-42`; `.github/workflows/ci.yml:24-30`

### 47. CLI parser behavior differs when Typer is unavailable

The fallback parser does not support documented options such as `review-batch --port` and instruction `--preset`.

**Code:** `src/akshara_vision/cli/app.py:162-170`, `272-295`, `313-415`

### 48. `process --profile` accepts but ignores the profile

**Code:** `src/akshara_vision/cli/app.py:128-135`

### 49. `akv model` does not open the documented menu by default

The documentation says it opens a menu, but the command defaults to provider status checking.

**Code:** `src/akshara_vision/cli/app.py:152-155`; `docs/models.md:26-39`

### 50. Doctor does not check all documented dependencies

It does not check every tool and optional verification dependency claimed in the README.

**Code:** `src/akshara_vision/cli/workflows.py:3725-3789`

### 51. Local HTML reports import Google Fonts

Opening a supposedly local comparison report can make an external request.

**Code:** `src/akshara_vision/cli/workflows.py:2991`

**Fix:** Use local/system fonts and no remote assets.

### 52. Documentation overstates some features

Examples include complete PDF/A validation, embedded-font checks, an active archival classifier, one-page-at-a-time PDF rendering, combine priority, browser opening behavior, and `/ui` customization.

**Fix:** Treat documentation claims as testable product contracts. Every major claim should have an acceptance test.

### 53. Repository URLs disagree

Package metadata and README links point to different GitHub owners.

**Code:** `pyproject.toml:55-58`; `README.md:131-134`, `205-207`

### 54. The main CLI workflow file is too large

`src/akshara_vision/cli/workflows.py` is more than 4,600 lines and mixes UI, business logic, HTML, chat, verification, installation, and process control.

**Risk:** Every new interface becomes harder to add and easier to break.

**Fix:** Separate transport/UI code from reusable application services before building the browser product.

---

## 5. Important product truth: detecting missing material is not the same as recreating it

Your central idea is good, but this distinction must be visible in the product.

### What AI can detect with useful confidence

- a missing page number;
- a gap in scan filenames;
- duplicated or out-of-order pages;
- a blank or partly cropped scan;
- a missing paragraph region when layout and OCR disagree;
- a table-of-contents entry with no matching chapter;
- an interrupted sentence between two pages;
- a page signature, folio, or catchword mismatch;
- text that is unreadable or uncertain;
- a difference between two editions of the same work.

### What AI cannot safely know from no evidence

If a page is physically absent and no other copy exists, an AI cannot recover the exact original words. It can only make a guess based on surrounding text. A fluent guess is still a guess.

### Safe product design

Use three visible states:

1. **Visible and confirmed** — the text is supported by the page image.
2. **Suggested reconstruction** — proposed from another edition or surrounding evidence.
3. **Unknown/missing** — there is not enough evidence.

Never merge suggested text silently into the archival transcription. Keep the original scan, raw OCR, restored text, reviewer decision, model/version, and confidence together.

A stronger feature name would be **Missingness Detection and Evidence-Based Reconstruction**.

---

## 6. Idea 1: Can this become training data for LLM companies?

## Direct answer

**Yes, this can be implemented and it can become a valuable business. But the current run outputs are not automatically a licensable training dataset.**

The valuable asset is not merely “many old books.” Large public collections already exist. The valuable asset is a difficult, trusted, rights-cleared, well-documented dataset containing:

- original page images;
- page order and document structure;
- raw OCR;
- corrected human transcription;
- AI restoration proposals;
- accepted and rejected corrections;
- layout boxes and reading order;
- language and script labels;
- damage and missing-region labels;
- confidence values;
- rights and provenance for every item.

Paired data such as **damaged scan → human-approved transcription** is especially useful for OCR, vision-language models, handwriting recognition, document layout, translation, and evaluation.

## Evidence that companies license data

- [OpenAI Data Partnerships](https://openai.com/blog/data-partnerships) says it works with organizations to build public and private training datasets and is interested in large, non-public datasets across languages and formats.
- [Shutterstock’s OpenAI partnership](https://investor.shutterstock.com/news-releases/news-release-details/shutterstock-expands-partnership-openai-signs-new-six-year) is a clear example of licensed training data with metadata.
- The Associated Press also announced a deal licensing part of its archive to OpenAI.

So the business idea is real. The difficult part is proving that Akshara has the right to license the data and that the data is better than freely available alternatives.

## Very important: user uploads do not automatically belong to Akshara

Processing a document does not give Akshara the right to:

- publish it;
- train a model on it;
- sell it;
- license it to another company.

The software’s AGPL license covers the software code. It does **not** grant rights over user documents or generated datasets.

The default rule should be:

> **Private uploads are not used for training or data licensing unless the customer gives explicit, informed permission and has the necessary rights.**

This also matches current business expectations. OpenAI states that API and business data is not used to train its models by default unless the customer opts in. Anthropic states a similar default for commercial products.

## Copyright and legal position

This is not legal advice. Obtain advice in every market where data will be sold.

### India

- Under [Section 22 of the Indian Copyright Act](https://copyright.gov.in/Copyright_Act_1957/chapter_v.html), many literary works remain protected for the author’s life plus 60 years, calculated from the next calendar year. Anonymous, posthumous, government, and other works can have different rules.
- India’s December 2025 DPIIT working paper on generative AI and copyright discusses voluntary licensing, text-and-data-mining exceptions, collective licensing, and a proposed hybrid model. It is a policy proposal, not a simple permission to train on any work.
- Digitized documents can also contain personal data. The [Digital Personal Data Protection Act, 2023](https://www.indiacode.nic.in/handle/123456789/22037?view_type=browse) and its phased rules must be considered where living people are identifiable.

### United States and Europe

- The [US Copyright Office’s 2025 AI training report](https://www.copyright.gov/ai/) explains that training-data copying and fair use depend on facts such as source, purpose, market harm, and licensing. It does not say that all AI training is automatically lawful.
- EU general-purpose AI obligations have applied since 2 August 2025. The European Commission describes copyright policies and public summaries of training content as part of the GPAI framework.

### Public domain is helpful but still needs careful records

A book being old does not by itself prove that it is public domain. Check author death date, edition, translation, illustrations, annotations, jurisdiction, donor agreements, and access contracts.

Use machine-readable rights labels. [RightsStatements.org](https://rightsstatements.org/en/) provides standard statements for cultural-heritage material. Creative Commons recommends that faithful digital reproductions of public-domain works remain in the public domain, but local law and contracts still need review.

## Build a “rights ledger” before building a data marketplace

Every document should have:

- stable document and page IDs;
- source institution and collection;
- creator, publication date, and edition;
- country/jurisdiction used for the rights decision;
- rights status and evidence;
- scan-owner or donor agreement;
- allowed uses: processing, research, commercial training, redistribution;
- personal/sensitive-data review;
- cultural or community restrictions;
- consent and withdrawal records;
- file hashes and change history;
- exact transformations and model versions;
- human reviewer and review status.

No item should enter a commercial training package unless the rights ledger allows that exact use.

## What dataset should Akshara sell?

Do not begin by trying to sell a giant undifferentiated text dump. Build focused, measurable datasets.

### Recommended first dataset

**A benchmark and training set for damaged, multilingual South Asian print documents.**

Possible contents:

- 10,000–50,000 carefully selected pages;
- multiple scripts and document types;
- clean train, validation, and hidden test splits;
- line and region-level human transcription;
- reading-order labels;
- damage types;
- missing/cropped/blank/duplicate labels;
- character error rate and word error rate baselines;
- rights-cleared commercial and research editions.

Start smaller and trustworthy. One excellent 10,000-page dataset can be more useful than one million noisy pages.

## Recommended data formats

- Page images and thumbnails;
- JSONL or Parquet for training rows;
- ALTO XML, PAGE XML, or hOCR for geometry;
- IIIF manifests for cultural-heritage interoperability;
- a Hugging Face-style dataset card;
- MLCommons Croissant metadata for machine-readable provenance and permissions.

Useful standards:

- [IIIF APIs](https://iiif.io/api/) support interoperable images, compound documents, search, and annotations.
- [Hugging Face dataset cards](https://huggingface.co/docs/hub/en/datasets-cards) document license, language, size, bias, intended use, and limitations.
- [MLCommons Croissant](https://mlcommons.org/working-groups/data/croissant/) describes dataset structure, provenance, permissions, and loading in a machine-readable form.
- The [Library of Congress NDNP guidelines](https://www.loc.gov/ndnp/guidelines/) show how serious digitization programs define files, OCR, metadata, validation, and delivery structure.

## Data business models

Use more than one revenue source:

1. **Institutional software subscription** — restoration, review, search, and export.
2. **Per-page processing** — for digitization vendors and archives.
3. **Akshara Verify contracts** — pre-delivery QA after the engine becomes reliable.
4. **Private deployment and support** — for archives that cannot send data to a public cloud.
5. **Custom model or script adaptation** — trained only on authorized data.
6. **Dataset licensing** — rights-cleared data for OCR/VLM/LLM companies.
7. **Benchmark access** — evaluation service with a hidden, legally controlled test set.
8. **Public-interest grants** — preservation of endangered languages and collections.

My advice is to make institutional workflow revenue the first business. Treat data licensing as a high-upside second business after provenance and rights are proven.

---

## 7. Idea 2: Chat-like CLI or browser interface

## Direct answer

**Yes. It is technically feasible, and much of the backend behavior already exists.**

The repository already has:

- an interactive CLI chat loop;
- general and document-grounded chat;
- file/folder attachment;
- source search and focus;
- citations;
- saved sessions and notes;
- a local HTTP server pattern for batch review;
- browser-ready HTML report generation.

The main problem is not model support. The main problem is architecture: chat behavior and terminal presentation are tightly mixed inside a very large `cli/workflows.py` file.

## Which interface is better?

### Recommended product decision

**Make the browser interface the main interface for normal users. Keep the CLI for automation, batch jobs, and advanced users.**

Why browser first:

- Old-document work is visual. Users need the page image beside the text.
- Uploading and dragging files is easier.
- Reviewers need to click uncertain regions and correct text.
- Progress, page lists, cost, warnings, and exports are easier to understand.
- Libraries and scanning teams can use it without memorizing commands.
- A chat answer can show a citation and immediately open the exact page region.

The CLI chat should remain because it is valuable for developers, servers, and fast local work.

## Recommended user experience

### Home screen

- **Upload a document**
- **Open an existing project**
- **Verify a scan batch**
- **Chat with a document**

### After upload

The user should not have to choose twenty technical settings immediately. Ask simple questions:

1. What do you want?
   - Read and clean
   - Find missing or damaged material
   - Translate
   - Verify scan quality
   - Export/publish
2. Must the document stay fully local?
3. Which language/script is expected?
4. Prefer speed, balance, or best quality?

Then show a clear run plan:

- files and page count;
- selected model and location;
- whether data leaves the machine;
- expected cost or “local/no API charge”;
- expected outputs;
- warnings about unsupported features.

### Main workspace

Use three areas:

- **Left:** page thumbnails and issue filters;
- **Center:** original scan with zoom, boxes, and highlighted citations;
- **Right:** chat, restored text, confidence, and review actions.

Useful actions:

- “What is missing on this page?”
- “Compare page 12 with page 13.”
- “Show uncertain words.”
- “Find every mention of this person.”
- “Translate this paragraph.”
- “Mark as confirmed / suggested / unknown.”
- “Export approved pages only.”

Every answer should link to a page and, where possible, a bounding box.

## Recommended technical architecture

Do not build a second pipeline inside the web UI. Extract reusable services and let both CLI and web call the same services.

```text
CLI -------------------\
                        -> Application services -> Core pipeline/providers/exporters
Browser API -----------/             |
                                      -> Job store, review ledger, rights ledger
```

### Step 1: Extract transport-neutral services

Create small services for:

- project creation and file intake;
- run planning and provider readiness;
- restoration jobs;
- verification jobs;
- one chat turn;
- source retrieval and citations;
- human review decisions;
- export and download;
- cancellation and resume.

These services should return structured results, not print terminal messages.

### Step 2: Add a local web API

**Recommended:** FastAPI.

Why:

- it fits the existing Python project;
- it supports streaming uploads through `UploadFile`;
- it supports WebSockets or server-sent progress updates;
- it can call the existing Python services directly;
- it can start locally with a command such as `akv web`.

FastAPI documentation covers [file uploads](https://fastapi.tiangolo.com/tutorial/request-files/), [background work](https://fastapi.tiangolo.com/tutorial/background-tasks/), and [WebSockets](https://fastapi.tiangolo.com/advanced/websockets/).

For long OCR/model jobs, use a real internal job queue or worker process rather than relying only on request-level background tasks.

### Step 3: Build the browser frontend

**Recommended production choice:** React with TypeScript.  
**Recommended quick prototype:** Gradio only for an early internal demo.

Gradio can prove the flow quickly, but the required page viewer, region editing, job control, rights screens, and large-document review will need a custom product interface. Avoid making the long-term product depend on a demo framework.

### Step 4: Add a simple local store

Use SQLite first for:

- projects;
- jobs and status;
- documents and pages;
- chat sessions;
- review decisions;
- model and prompt versions;
- rights and consent records.

Keep original files and large outputs on disk or object storage. Store hashes and relative paths in the database.

### Step 5: Secure it before remote hosting

Minimum requirements:

- loopback-only for local mode;
- random per-launch token;
- authenticated users for hosted mode;
- project-level authorization;
- strict upload size, file-count, page, and archive limits;
- safe filenames and storage outside the public web root;
- antivirus/content scanning where appropriate;
- CSRF, origin, and host validation;
- HTML/Markdown sanitization;
- no arbitrary local path attachment through browser requests;
- encryption in transit and at rest for hosted deployments;
- explicit data-retention and deletion controls;
- audit logs;
- clear provider data-egress warnings.

## Suggested API shape

```text
POST   /api/projects
POST   /api/projects/{id}/uploads
POST   /api/projects/{id}/runs
GET    /api/jobs/{id}
POST   /api/jobs/{id}/cancel
POST   /api/projects/{id}/chat
GET    /api/projects/{id}/pages/{page_id}
POST   /api/projects/{id}/reviews
POST   /api/projects/{id}/exports
GET    /api/projects/{id}/downloads/{artifact_id}
```

Use WebSocket or server-sent events for progress. Never keep a long model job tied only to one browser request.

## Rough effort estimate

These are planning estimates, not guarantees. They assume a small experienced team and first fixing the core blockers.

- **Improved terminal chat:** 2–4 weeks.
- **Local browser MVP:** 6–10 weeks after service extraction.
- **Production multi-user hosted product:** another 3–6 months.
- **Archive-grade review, permissions, billing, and enterprise deployment:** 6–12 months total.

A solo developer can build a demo sooner, but should not call it production-ready.

---

## 8. Competitive position

Akshara is entering an existing market. That is good because it proves demand, but it means “we use AI for old documents” is not enough differentiation.

### Transkribus

[Transkribus](https://www.transkribus.org/for-researchers) offers browser-based handwriting and printed-text recognition, custom model training, layout tools, collaboration, search, exports, and institutional plans. Its site reports hundreds of models and large institutional usage.

### eScriptorium

[eScriptorium](https://escriptorium.eu/) is an open web application for document import, transcription, correction, training, and export, built around Kraken. It already demonstrates the value of a visual, human-in-the-loop workspace.

### Internet Archive and large digital libraries

The [Internet Archive OCR documentation](https://archive.org/developers/ocr.html) describes large-scale hOCR output with word-level bounding boxes and confidence. The Library of Congress offers structured OCR, image services, APIs, and strict digitization standards.

## Where Akshara can win

Do not try to win only on generic OCR. Focus on a clear combination:

1. **South Asian and underrepresented scripts.**
2. **Damaged and mixed-layout historical material.**
3. **Local/private operation.**
4. **Missingness detection with visible evidence.**
5. **Restoration + chat + verification in one workflow.**
6. **Tender and delivery QA for scanning contractors.**
7. **Rights and provenance built into every page.**
8. **Human corrections that create better, licensable training pairs.**

The best moat will not be the CLI or one model provider. It will be trusted correction data, customer workflow, language expertise, evaluation benchmarks, and institutional relationships.

---

## 9. Recommended product strategy

## Product promise

> Upload a rare or damaged document. Akshara helps you read it, identifies likely gaps or scan problems, shows exactly why it made each suggestion, and produces reviewed archival outputs without changing the original.

## First target customers

Choose customers with an urgent workflow and budget:

1. digitization and scanning contractors;
2. university libraries and manuscript centers;
3. state archives, museums, and cultural institutions;
4. publishers restoring out-of-print works;
5. researchers handling regional-language collections.

Do not begin with a broad consumer “chat with every book” product. Consumer chat is easy to copy. Reliable archival QA and reviewed restoration are harder and more valuable.

## Best first paid pilot

A scanning contractor or archive provides a rights-approved sample batch. Akshara:

- inventories every file;
- detects missing, duplicate, blank, blurred, skewed, or cropped pages;
- creates OCR/restoration drafts;
- sends uncertain pages to a human queue;
- produces a signed, reproducible report;
- exports corrected text and standard sidecars.

Charge for the pilot, measure time saved and defects caught, and do not claim standards compliance until validated.

---

## 10. Roadmap

## Phase 0: Stop-the-line fixes — 0 to 4 weeks

- Fix ZIP traversal and instruction-file traversal.
- Restrict `.env` keys.
- Make unknown profiles/providers hard errors.
- Correct exit codes and failed-run status.
- Package built-in profiles.
- Separate PDF, image, and metadata checks.
- Remove unsupported PDF/A and classifier claims.
- Add archive, file, image, and subprocess limits.
- Add Python 3.13 and Windows CI.
- Build and test the wheel in CI.

**Exit condition:** No known arbitrary-file issue; failures cannot look successful; installed package behaves like source.

## Phase 1: Trustworthy core — 1 to 3 months

- Create one canonical page and provenance schema.
- Fix sidecar exports and validate them.
- Make state writes atomic and run IDs unique.
- Implement real dry-run and resume behavior.
- Replace chat prefix truncation with chunk retrieval.
- Fix review IDs and profile calibration.
- Integrate veraPDF or narrow PDF claims.
- Create an adversarial security test set.
- Publish measured limitations.

**Exit condition:** A pilot customer can reproduce a run and audit every output back to a page.

## Phase 2: Browser MVP — 2 to 5 months

- Extract application services from CLI workflows.
- Add FastAPI and a worker/job model.
- Add React upload, page viewer, chat, progress, review, and export.
- Add local-only mode first.
- Add rights/consent selection at document intake.
- Run two or three supervised institutional pilots.

**Exit condition:** A non-technical reviewer can complete a document without typing commands.

## Phase 3: Dataset and benchmark — 3 to 8 months

- Sign clear data agreements with partner institutions.
- Build rights and provenance ledgers.
- Define annotation instructions and reviewer quality checks.
- Create train/validation/hidden-test splits.
- Publish a small open benchmark to build credibility.
- Keep the highest-value rights-cleared set private or commercially licensed.
- Produce dataset cards, Croissant metadata, and baseline metrics.

**Exit condition:** Every dataset row has source, rights, transformation, and quality evidence.

## Phase 4: Commercial scale — 6 to 12 months

- Add multi-user projects and roles.
- Add secure hosted and private/on-prem deployment.
- Add billing, quotas, support, and service-level objectives.
- Add large-batch workers and observability.
- Obtain security review and legal review.
- Approach model companies with benchmark results and data samples, not only a pitch deck.

---

## 11. Metrics that should decide whether the product is good

Avoid measuring only pages processed. Measure trust and customer value.

### Restoration quality

- Character Error Rate by language/script;
- Word Error Rate by language/script;
- layout reading-order accuracy;
- table and page-number accuracy;
- percentage of AI suggestions accepted by reviewers;
- unsupported/invented text rate.

### Missingness and verification

- precision and recall for missing pages;
- false rejection rate for valid batches;
- false acceptance rate for defective batches;
- duplicate-detection precision;
- metadata validation accuracy;
- PDF/A result agreement with veraPDF.

### Product value

- review minutes per page;
- processing cost per page;
- time to first useful page;
- recovery success after interruption;
- number of corrections reused safely;
- exports accepted without manual repair;
- pilot customer renewal rate.

### Chat quality

- percentage of factual claims with valid citations;
- citation opens the correct page/region;
- answer supported by cited evidence;
- “not enough evidence” accuracy;
- prompt-injection resistance.

---

## 12. Legal, privacy, and licensing actions

Before collecting customer data at scale:

1. Write plain-language Terms of Service and a Data Processing Agreement.
2. State clearly that private uploads are not used for training by default.
3. Add a separate, optional contribution agreement for training data.
4. Require the contributor to identify their rights, but also perform Akshara’s own review for commercial datasets.
5. Support deletion, withdrawal, retention limits, and dataset-version tracking.
6. Separate software licensing from dataset/content licensing.
7. Record cultural and community restrictions, not only copyright.
8. Add a process for takedowns and rights disputes.
9. Have counsel review the AGPL commercial-license statement in `README.md`. The current sentence saying a commercial user must open-source its “entire application code” is broader than a safe one-line explanation of AGPL obligations.
10. Confirm that Akshara owns or can commercially relicense all software contributions before offering a dual commercial license.

---

## 13. Test and validation results from this review

### Commands run

- `python -m pytest -q -p no:cacheprovider`
- `.venv/Scripts/python.exe -m compileall -q src tests`
- Git status and latest commit inspection
- project diagnostics refresh
- isolated and focused behavior probes during subsystem audits

### Results

- **Tests:** 170 passed, 1 failed, 23 warnings.
- **Failing test:** `tests/test_core.py::CoreTests::test_chat_raw_folder_fallback_focuses_matching_file_before_processing`.
- **Python tested locally:** 3.13.9.
- **Compilation:** completed without syntax errors.
- **Virtual environment warning:** the project `.venv` did not contain `pytest`; tests used the system Python installation.
- **Ruff:** not installed in the active Python environment, so a Ruff run was not completed.
- **Git working tree before this report:** clean at commit `ba7556f` (`v1.1.0`).

The passing tests do not prove production readiness. Several problematic behaviors are not tested, and a few current tests accept unsafe behavior such as an empty verification batch passing.

---

## 14. Final recommendation to the founder

The idea is worth pursuing.

But the biggest opportunity is not “AI will magically recover missing old text.” That promise is too risky and will damage trust when the source is truly gone.

The stronger company is:

> **Akshara turns difficult historical scans into evidence-linked, human-approved, reusable knowledge—and helps institutions prove the quality of every page.**

Build in this order:

1. **Trust and security.**
2. **Measured restoration and verification quality.**
3. **Browser-based human review.**
4. **Institutional pilots and recurring workflow revenue.**
5. **Rights-cleared training datasets and benchmarks.**

If Akshara owns a high-quality correction workflow and has permissioned data from underrepresented languages and damaged historical documents, model companies may care. More importantly, archives and scanning businesses can pay for the product even if a large LLM data deal never happens.

That creates a healthier company: useful software first, unique data second, strategic AI partnerships third.

---

## 15. Research sources

### AI training data and copyright

- [OpenAI Data Partnerships](https://openai.com/blog/data-partnerships)
- [OpenAI business data privacy](https://openai.com/business-data/)
- [Anthropic commercial data training policy](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training)
- [US Copyright Office: Copyright and Artificial Intelligence](https://www.copyright.gov/ai/)
- [European Commission GPAI guidelines](https://digital-strategy.ec.europa.eu/en/policies/guidelines-gpai-providers)
- [European Commission training-content summary template](https://digital-strategy.ec.europa.eu/en/library/explanatory-notice-and-template-public-summary-training-content-general-purpose-ai-models)
- [Indian Copyright Act, Chapter V](https://copyright.gov.in/Copyright_Act_1957/chapter_v.html)
- [DPIIT Working Paper on Generative AI and Copyright, December 2025](https://www.dpiit.gov.in/static/uploads/2025/12/ff266bbeed10c48e3479c941484f3525.pdf)
- [India Digital Personal Data Protection Act](https://www.indiacode.nic.in/handle/123456789/22037?view_type=browse)

### Cultural heritage rights and standards

- [RightsStatements.org](https://rightsstatements.org/en/)
- [Creative Commons cultural heritage recommendations](https://creativecommons.org/wp-content/uploads/2025/07/OC-Recommended-Licenses.pdf)
- [IIIF API specifications](https://iiif.io/api/)
- [Library of Congress NDNP guidelines](https://www.loc.gov/ndnp/guidelines/)
- [Library of Congress Chronicling America technical information](https://www.loc.gov/collections/chronicling-america/about-this-collection/technical-information/)
- [Internet Archive OCR documentation](https://archive.org/developers/ocr.html)
- [National Archives OCR transcription guidance](https://www.archives.gov/research/catalog/lcdrg/contribution/ocr-transcription)

### Dataset documentation

- [Hugging Face dataset cards](https://huggingface.co/docs/hub/en/datasets-cards)
- [MLCommons Croissant](https://mlcommons.org/working-groups/data/croissant/)
- [Google Research Data Cards](https://research.google/pubs/data-cards-purposeful-and-transparent-dataset-documentation-for-responsible-ai/)

### Interfaces and competitors

- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/)
- [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
- [Transkribus for researchers](https://www.transkribus.org/for-researchers)
- [Transkribus pricing](https://www.transkribus.org/pricing)
- [eScriptorium](https://escriptorium.eu/)
