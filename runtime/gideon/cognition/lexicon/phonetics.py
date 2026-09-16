"""Double Metaphone — a compact, vendored, pure-Python implementation (core LEX).

Deterministic phonetic keying (the locked decision) with NO third-party dependency —
heavy phonetics libs stay out of core. ``double_metaphone(word) -> (primary, secondary)``
returns up to two 4-char phonetic keys; the secondary is "" when there's no alternate
pronunciation. Two words "sound alike" when any of their keys match.

This is the standard Lawrence Philips Double Metaphone algorithm, trimmed to the subset
that matters for matching English + common tech/proper-noun vocabulary. It is not a full
i18n phonetics engine; it is a fast, dependency-free same-sound key good enough to drive
the Lexicon's post-decode correction (which is further gated by edit-distance + a
confidence filter, so an occasional coarse key is harmless).
"""

from __future__ import annotations

from collections.abc import Callable

_VOWELS = frozenset("AEIOUY")
_MAX_KEY_LEN = 4
_SILENT_PREFIXES = frozenset(("GN", "KN", "PN", "WR", "PS"))


def _is_vowel(s: str, pos: int) -> bool:
    return 0 <= pos < len(s) and s[pos] in _VOWELS


class _MetaphoneEncoder:
    """Stateful encoder that accumulates primary/secondary key fragments."""

    __slots__ = ("_src", "_len", "_pos", "_primary", "_secondary")

    def __init__(self, src: str) -> None:
        self._src = src
        self._len = len(src)
        self._pos = 0
        self._primary: list[str] = []
        self._secondary: list[str] = []

    # -- helpers ---------------------------------------------------------------

    def _at(self, i: int) -> str:
        return self._src[i] if 0 <= i < self._len else ""

    def _sub(self, i: int, j: int) -> str:
        return self._src[max(i, 0) : j]

    def _add(self, p: str, sec: str | None = None) -> None:
        self._primary.append(p)
        self._secondary.append(p if sec is None else sec)

    def _advance(self, n: int = 1) -> None:
        self._pos += n

    def _is_done(self) -> bool:
        return self._pos >= self._len or (
            len("".join(self._primary)) >= _MAX_KEY_LEN
            and len("".join(self._secondary)) >= _MAX_KEY_LEN
        )

    # -- prefix skipping -------------------------------------------------------

    def _skip_silent_prefix(self) -> None:
        if self._sub(0, 2) in _SILENT_PREFIXES:
            self._pos = 1
        if self._at(0) == "X":
            self._add("S")
            self._pos = 1

    # -- consonant dispatch table ----------------------------------------------

    def _handle_b(self) -> None:
        self._add("P")
        self._advance(2 if self._at(self._pos + 1) == "B" else 1)

    def _handle_c(self) -> None:
        nxt = self._at(self._pos + 1)
        if nxt == "H":
            self._encode_ch()
        elif nxt == "C" and not (self._pos == 1 and self._at(0) == "M"):
            self._encode_cc()
        elif nxt in ("I", "E", "Y"):
            self._add("S")
            self._advance(2)
        else:
            self._add("K")
            self._advance()

    def _encode_ch(self) -> None:
        seg = self._sub(self._pos, self._pos + 4)
        if seg == "CHAE" or self._sub(self._pos, self._pos + 2) == "CH":
            if self._pos == 0 and self._sub(self._pos + 1, self._pos + 4) in (
                "HAR",
                "HOR",
            ):
                self._add("K", "X")
            else:
                self._add("X", "K")
        else:
            self._add("X", "K")
        self._advance(2)

    def _encode_cc(self) -> None:
        if (
            self._at(self._pos + 2) in "IEH"
            and self._sub(self._pos + 2, self._pos + 4) != "HU"
        ):
            if self._sub(self._pos + 1, self._pos + 3) in ("CI", "CE"):
                self._add("KS")
            else:
                self._add("X")
            self._advance(3)
        else:
            self._add("K")
            self._advance(2)

    def _handle_d(self) -> None:
        if self._at(self._pos + 1) == "G" and self._at(self._pos + 2) in "IEY":
            self._add("J")
            self._advance(3)
        else:
            self._add("T")
            self._advance(2 if self._at(self._pos + 1) == "D" else 1)

    def _handle_f(self) -> None:
        self._add("F")
        self._advance(2 if self._at(self._pos + 1) == "F" else 1)

    def _handle_g(self) -> None:
        nxt = self._at(self._pos + 1)
        if nxt == "H":
            self._encode_gh()
        elif nxt == "N":
            self._add("KN", "N")
            self._advance(2)
        elif nxt in ("I", "E", "Y"):
            self._add("J")
            self._advance(2)
        else:
            self._add("K")
            self._advance(2 if nxt == "G" else 1)

    def _encode_gh(self) -> None:
        if self._pos > 0 and not _is_vowel(self._src, self._pos - 1):
            self._add("K")
            self._advance(2)
        elif self._pos == 0:
            self._add("K" if self._at(self._pos + 2) != "I" else "J")
            self._advance(2)
        else:
            self._advance(2)  # silent gh

    def _handle_h(self) -> None:
        if (self._pos == 0 or _is_vowel(self._src, self._pos - 1)) and _is_vowel(
            self._src, self._pos + 1
        ):
            self._add("H")
        self._advance()

    def _handle_j(self) -> None:
        self._add("J", "H" if self._pos == 0 else "J")
        self._advance(2 if self._at(self._pos + 1) == "J" else 1)

    def _handle_k(self) -> None:
        self._add("K")
        self._advance(2 if self._at(self._pos + 1) == "K" else 1)

    def _handle_l(self) -> None:
        self._add("L")
        self._advance(2 if self._at(self._pos + 1) == "L" else 1)

    def _handle_m(self) -> None:
        self._add("M")
        self._advance(2 if self._at(self._pos + 1) == "M" else 1)

    def _handle_n(self) -> None:
        self._add("N")
        self._advance(2 if self._at(self._pos + 1) == "N" else 1)

    def _handle_p(self) -> None:
        if self._at(self._pos + 1) == "H":
            self._add("F")
            self._advance(2)
        else:
            self._add("P")
            self._advance(2 if self._at(self._pos + 1) == "P" else 1)

    def _handle_q(self) -> None:
        self._add("K")
        self._advance(2 if self._at(self._pos + 1) == "Q" else 1)

    def _handle_r(self) -> None:
        self._add("R")
        self._advance(2 if self._at(self._pos + 1) == "R" else 1)

    def _handle_s(self) -> None:
        nxt = self._at(self._pos + 1)
        if nxt == "H":
            self._add("X")
            self._advance(2)
        elif nxt in ("I", "Y") and self._at(self._pos + 2) == "O":
            self._add("S", "X")
            self._advance()
        else:
            self._add("S")
            self._advance(2 if nxt == "S" else 1)

    def _handle_t(self) -> None:
        seg2 = self._sub(self._pos, self._pos + 2)
        if seg2 == "TH":
            self._add("0", "T")
            self._advance(2)
        elif self._at(self._pos + 1) in ("I", "Y") and self._at(self._pos + 2) == "O":
            self._add("X")
            self._advance()
        else:
            self._add("T")
            self._advance(2 if self._at(self._pos + 1) == "T" else 1)

    def _handle_v(self) -> None:
        self._add("F")
        self._advance(2 if self._at(self._pos + 1) == "V" else 1)

    def _handle_w(self) -> None:
        if self._at(self._pos + 1) == "H":
            self._add("A")
            self._advance(2)
        elif _is_vowel(self._src, self._pos + 1):
            self._add("A", "F")
            self._advance()
        else:
            self._advance()

    def _handle_x(self) -> None:
        self._add("KS")
        self._advance(2 if self._at(self._pos + 1) in ("C", "X") else 1)

    def _handle_z(self) -> None:
        self._add("S")
        self._advance(2 if self._at(self._pos + 1) == "Z" else 1)

    def _build_dispatch(self) -> dict[str, Callable[[_MetaphoneEncoder], None]]:
        return {
            "B": type(self)._handle_b,
            "C": type(self)._handle_c,
            "\u00c7": type(self)._handle_c,
            "D": type(self)._handle_d,
            "F": type(self)._handle_f,
            "G": type(self)._handle_g,
            "H": type(self)._handle_h,
            "J": type(self)._handle_j,
            "K": type(self)._handle_k,
            "L": type(self)._handle_l,
            "M": type(self)._handle_m,
            "N": type(self)._handle_n,
            "P": type(self)._handle_p,
            "Q": type(self)._handle_q,
            "R": type(self)._handle_r,
            "S": type(self)._handle_s,
            "T": type(self)._handle_t,
            "V": type(self)._handle_v,
            "W": type(self)._handle_w,
            "X": type(self)._handle_x,
            "Z": type(self)._handle_z,
        }

    # -- main loop -------------------------------------------------------------

    def encode(self) -> tuple[str, str]:
        dispatch = self._build_dispatch()

        while not self._is_done():
            ch = self._at(self._pos)
            if ch in _VOWELS:
                if self._pos == 0:
                    self._add("A")
                self._advance()
                continue
            handler = dispatch.get(ch)
            if handler is not None:
                handler(self)
            else:
                self._advance()

        p = "".join(self._primary)[:_MAX_KEY_LEN]
        sec = "".join(self._secondary)[:_MAX_KEY_LEN]
        return p, (sec if sec != p else "")


def double_metaphone(word: str) -> tuple[str, str]:
    """Return (primary, secondary) phonetic keys (≤4 chars each) for *word*."""
    if not word:
        return "", ""
    s = "".join(ch for ch in word.upper() if ch.isalpha())
    if not s:
        return "", ""
    enc = _MetaphoneEncoder(s)
    enc._skip_silent_prefix()
    return enc.encode()


def phonetic_keys(word: str) -> list[str]:
    """Return the distinct non-empty Double Metaphone keys for *word* (0, 1, or 2)."""
    p, s = double_metaphone(word)
    seen: set[str] = set()
    out: list[str] = []
    for k in (p, s):
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def sounds_like(a: str, b: str) -> bool:
    """True if *a* and *b* share any phonetic key (a cheap same-sound test)."""
    ka, kb = set(phonetic_keys(a)), set(phonetic_keys(b))
    return bool(ka and kb and ka & kb)
