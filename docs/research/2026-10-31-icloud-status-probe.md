# Research Report: iCloud upload, eviction and download status on this Mac

**Brief:** TradePartner T354 (#2749, ADR 0028 first step #2714) · **Date:** 2026-11-01 (file name kept from the plan line) · **Status:** COMPLETE · **Source:** script `~/tradepartner-probes/icloud/spike.sh`, output `~/tradepartner-probes/icloud/spike-out.txt` (run 2026-11-01 05:37 UTC by the orchestrator at the owner's word)

## Answer

**The status probe is the Foundation URL resource keys, read with `osascript -l JavaScript`. No pyobjc is needed** (open question 4 of the #2714 amendment in `docs/specs/data-foundation.md`: answered, not needed). On macOS 27.0.1 (build 26A434):

- `NSURLUbiquitousItemIsUploadedKey` reports `true` once a file is in iCloud. A 20 MB file reported `true` within **11 s** of being written (an upper bound: the poll ran every 5 s). A 4 KB file was already `true` at the same check.
- `NSURLUbiquitousItemDownloadingStatusKey` reports `...StatusCurrent` for a local copy and `...StatusNotDownloaded` after eviction.
- `stat -f %Xf` agrees as a second signal: flags `0x40` while local, `0x40000060` (`compressed,dataless`) with `blocks=0` after eviction.
- `brctl evict <file>` evicted the file. After `brctl download <file>` and a read, the SHA-256 of both files matched the values taken before upload.

**Recommendations for T346:**

1. **Upload confirmation:** poll `NSURLUbiquitousItemIsUploadedKey` every 5 s, with a bounded wait of **300 s** per file (about 27 times the measured 11 s for 20 MB, leaving room for larger zips and a slow link). On timeout, leave the file local and counted against the floor and report it pending, as the spec says.
2. **Eviction as a deferred pass:** a separate pass, after ingest, that takes archived raw bodies (never a provenance manifest, which stays local) whose upload key is `true`, re-hashes the local copy, runs `brctl evict`, then requires `dataless` in `stat -f %Xf` and `StatusNotDownloaded`. Any other result leaves the file local and reported. **Do not count an eviction as freed space** until free space is measured again (see Disk space below).
3. **Download for re-derive:** run `brctl download` on every named file, then wait until each reports `StatusCurrent` and is not `dataless`, then re-hash. Refuse on a mismatch or a timeout.
4. **Do not use** `mdls` (every `kMDItemIsUploaded`, `kMDItemIsDownloaded` and `kMDItemIsUploading` key was `(null)`) or `brctl status <folder>`, which failed with `BRCloudDocsErrorDomain:30, "Client zone not found"` for a plain iCloud Drive folder.

## Evidence

Recorded states, trimmed (the home path is shown as `~`):

| State | `ls -lO` flags | `stat -f %Xf`, blocks | URL keys |
|---|---|---|---|
| Written, uploaded | `-` | `40`, 40960 | `uploaded=true uploading=false dlstatus=...Current ubiq=true` |
| After `brctl evict` | `compressed,dataless` | `40000060`, 0 | `uploaded=true dlstatus=...NotDownloaded` |
| After `brctl download` | `-` | `40`, 40960 | `uploaded=true dlstatus=...Current` |

- **Upload lag:** 11 s for 20,971,520 random bytes, polled every 5 s.
- **Download:** the script ran `brctl download` and then read the file with `shasum`, and the read itself triggers a download, so this run cannot separate the two, and its "0 s" is not a measured download time. The bytes matched. Reading a dataless file downloads it silently, **so nothing stops a reader from pulling an evicted file back.** The reader boundary T346 adds (only the archive helper opens archive paths) is therefore the guard, not the file system.
- **Disk space:** `/` and `/System/Volumes/Data` share one APFS free-space pool, so `df /` measured the right space. **Free space did not rise after the evict: it was 196 KB lower** (9,565,744 KB before, 9,565,548 KB 10 s after), although the file showed `blocks=0`. APFS may release the space later, or count it as purgeable. Until a longer check shows the space coming back, T346 and #2743 must not treat an eviction as freed space, and must re-measure with `df -k` after each eviction pass. T345's `check_archive_write` measures the disk under the archive root, which is right.
- **Other:** `xattr` showed only `com.apple.provenance`, so it carries no iCloud state. No `.icloud` placeholder file appeared; this macOS uses dataless files in place.
- **Scope:** only the scratch folder `iCloud Drive/tradepartner-spike/` was touched. Its two files and the folder were removed at the end. No archive file was evicted.

## Caveats

- One run, on one link, with files of 20 MB and less. Larger insider zips (up to about 100 MB) and the 1.5 GB `submissions.zip` from #2743 were not timed; a 1.5 GB file will likely exceed 300 s, which fails safe (the file stays local and is reported). The 300 s wait is a starting value: T346 should make it a config key and report the lags it sees.
- The JXA call goes through `osascript`, which costs about 0.1–0.3 s per file. A deferred pass over thousands of files should batch the paths into one script call.
- The eviction run showed no rise in free space within 10 s. This is the open question that decides how much disk the archive actually frees: re-measure over minutes, and after a larger file, the first time T346 evicts.
