"""Build v2's pinned word list for the #882 probe (spike code, never merged).

    uv run python scripts/probe_gdelt_wordlist.py hunspell-en_US-2026.02.25.zip

Reads `en_US.dic` and `en_US.aff` from the SCOWL hunspell release zip
(https://github.com/en-wl/wordlist/releases/tag/rel-2026.02.25), expands every
stem by its affix flags (prefixes, suffixes and their cross products, as hunspell
does), keeps the forms made only of ASCII letters, and writes
`scripts/probe_gdelt_words.txt.gz` (gzip, mtime 0, so the bytes are reproducible).
Each line is one form, in its dictionary case: lowercase forms are ordinary English
words; forms of a Capitalised or ALL-CAPS stem are proper nouns or acronyms. The
probe pins the SHA-256 of the uncompressed text (`WORDS_SHA256` in probe_gdelt.py).
"""

from __future__ import annotations

import gzip
import hashlib
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

OUT = Path(__file__).with_name("probe_gdelt_words.txt.gz")
_LETTERS = re.compile(r"^[A-Za-z]+$")
HEADER = """\
# probe_gdelt_words.txt: the pinned word list of matching rule name-title-url+ticker-tag/v2 (#882).
# Built by scripts/probe_gdelt_wordlist.py from the SCOWL hunspell en_US dictionary
# (SCOWL size 60), release rel-2026.02.25:
#   https://github.com/en-wl/wordlist/releases/download/rel-2026.02.25/hunspell-en_US-2026.02.25.zip
#   zip sha256 {zip_sha}
# Every stem expanded by its affix flags; only forms of ASCII letters kept.
# Lowercase lines are ordinary English words. Lines with a capital letter are forms
# of a Capitalised or ALL-CAPS stem: proper nouns (people, places, some brands) and acronyms.
#
# Copyright 2000-2026 by Kevin Atkinson
#
# Permission to use, copy, modify, distribute, and sell any part of SCOWLv2, or
# word lists created from it, is hereby granted without fee, provided that the
# above copyright notice appears in all copies and that both the above
# copyright notice and this notice appear in supporting documentation.  Kevin
# Atkinson makes no representations about the suitability of this database for
# any purpose.  It is provided "as is" without express or implied warranty.
"""


@dataclass(frozen=True)
class Affix:
    """One PFX or SFX rule line."""

    kind: str  # "PFX" or "SFX"
    cross: bool
    strip: str
    add: str
    cond: re.Pattern[str]

    def apply(self, word: str) -> str | None:
        """The affixed form, or None when the condition does not hold."""
        if not self.cond.search(word):
            return None
        if self.kind == "SFX":
            if self.strip and not word.endswith(self.strip):
                return None
            base = word[: len(word) - len(self.strip)] if self.strip else word
            return base + self.add
        if self.strip and not word.startswith(self.strip):
            return None
        return self.add + word[len(self.strip) :]


def parse_aff(text: str) -> dict[str, list[Affix]]:
    """Affix rules by flag (single-character flags, as in en_US.aff)."""
    rules: dict[str, list[Affix]] = {}
    cross: dict[str, bool] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("PFX", "SFX"):
            continue
        kind, flag = parts[0], parts[1]
        if flag not in cross:  # header: PFX flag Y|N count
            cross[flag] = parts[2] == "Y"
            rules[flag] = []
            continue
        strip = "" if parts[2] == "0" else parts[2]
        add = "" if parts[3].split("/")[0] == "0" else parts[3].split("/")[0]
        cond = parts[4] if len(parts) > 4 else "."
        pattern = ("^" + cond) if kind == "PFX" else (cond + "$")
        rules[flag].append(Affix(kind, cross[flag], strip, add, re.compile(pattern)))
    return rules


def expand(stem: str, flags: str, rules: dict[str, list[Affix]]) -> set[str]:
    """The stem and every form its flags make (prefix x suffix where both cross)."""
    forms = {stem}
    pfx = [a for f in flags for a in rules.get(f, ()) if a.kind == "PFX"]
    sfx = [a for f in flags for a in rules.get(f, ()) if a.kind == "SFX"]
    suffixed = [(a, w) for a in sfx if (w := a.apply(stem)) is not None]
    forms.update(w for _a, w in suffixed)
    for p in pfx:
        w = p.apply(stem)
        if w is None:
            continue
        forms.add(w)
        if p.cross:
            for s, sw in suffixed:
                if s.cross and (pw := p.apply(sw)) is not None:
                    forms.add(pw)
    return forms


def build(zip_path: Path) -> bytes:
    """The word list text (header and sorted forms), as bytes."""
    with zipfile.ZipFile(zip_path) as zf:
        dic = zf.read("en_US.dic").decode("utf-8")
        aff = zf.read("en_US.aff").decode("utf-8")
    rules = parse_aff(aff)
    forms: set[str] = set()
    for line in dic.splitlines()[1:]:
        stem, _, flags = line.strip().partition("/")
        if not stem:
            continue
        forms.update(f for f in expand(stem, flags, rules) if _LETTERS.match(f))
    zip_sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    body = "\n".join(sorted(forms))
    return (HEADER.format(zip_sha=zip_sha) + body + "\n").encode("ascii")


def main(argv: list[str]) -> int:
    """Write probe_gdelt_words.txt.gz and print the SHA-256 of its text."""
    text = build(Path(argv[0]))
    OUT.write_bytes(gzip.compress(text, mtime=0))
    print(f"{OUT.name}: {text.count(b'\n')} lines, text sha256 {hashlib.sha256(text).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
