"""MIME in both directions: parse an inbound RFC822 message, build an outbound one.

**Inbound** (:func:`parse_inbound`) follows the same extraction contract the sibling
mail-inbox app proved:

- **prefer ``text/plain``** — a ``multipart/alternative`` carries the same content as
  plain and HTML; the plain part needs no sanitization;
- **HTML-only mail is stripped to visible text** with a small stdlib parser
  (``<script>``/``<style>`` bodies dropped wholesale), never rendered or executed;
- **headers are RFC-2047 decoded** (``=?utf-8?B?…?=``) so a non-ASCII subject reads as
  text, not as an encoded word.

Two things this module does that a read-only inbox source does not have to:

* **The sender address is taken from ``parseaddr`` ONLY.** ``From: "allowed@example.com"
  <evil@attacker.test>`` parses to display name ``allowed@example.com`` and address
  ``evil@attacker.test`` — a trust check against the display name would hand the channel
  to anyone who can set one. The display name is carried for UI only and is NEVER the
  trust key. Measured: an RFC-2047-encoded display name decodes to arbitrary text
  (including a full address) under ``policy.default``.
* **Quoted reply history is trimmed** (:func:`strip_quoted_reply`) before the text becomes
  a conversational turn. Every mail client quotes the whole previous message; feeding that
  back each round would re-inject the entire thread — including our own earlier output —
  into every turn.

**Outbound** (:func:`build_outbound`) sets the threading headers the channel's thread
identity depends on: a fresh ``Message-ID``, ``In-Reply-To`` = the parent's id, and
``References`` = the parent's ``References`` chain plus the parent's id, in order. That
chain is what makes a mail client show one conversation instead of N loose messages.
"""

from __future__ import annotations

import email.header
import email.message
import email.policy
import email.utils
import logging
import re
from email.message import EmailMessage, Message
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# Tags whose *content* is program/style text, not human-visible prose — drop the body.
_DROP_CONTENT_TAGS = frozenset({"script", "style", "head", "title"})
# Block-level tags that should force a line break so stripped text stays readable.
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
)
#: Cap extracted body text so one pathological mail can't blow up a session turn.
MAX_BODY_CHARS = 100_000

# Quoted-history markers, in the order a client emits them. Everything from the first
# match onward is previous-message quotation, not new user text.
_QUOTE_MARKERS = (
    # "On Mon, 9 Aug 2026 at 10:00, Someone <a@b> wrote:" (Gmail/Apple/Outlook)
    re.compile(r"^\s*On .*wrote:\s*$", re.IGNORECASE),
    # "-----Original Message-----" (Outlook)
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    # "From: someone@example.com" starting a forwarded/quoted block
    re.compile(r"^\s*_{5,}\s*$"),
    # Standard signature delimiter (RFC 3676 §4.3): "-- " on its own line.
    re.compile(r"^-- $"),
)


class _HTMLToText(HTMLParser):
    """Collapse HTML to visible text: drop script/style bodies, break on block tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _DROP_CONTENT_TAGS:
            self._suppress_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT_TAGS and self._suppress_depth > 0:
            self._suppress_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._suppress_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        # The tag-breaks leave runs of blank lines and trailing whitespace; collapse them.
        raw = "".join(self._parts)
        lines = [ln.strip() for ln in raw.splitlines()]
        out: list[str] = []
        for ln in lines:
            if ln or (out and out[-1]):
                out.append(ln)
        return "\n".join(out).strip()


def html_to_text(html: str) -> str:
    """Strip HTML to visible text — no tags, no script/style bodies, never rendered."""
    parser = _HTMLToText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # a malformed fragment must not break the poll loop
        logger.debug("mail-desk: HTML parse failed; returning best-effort text", exc_info=True)
    return parser.text()


def decode_header_value(raw: object) -> str:
    """Decode an RFC-2047 header to text; best-effort, never raises.

    ``policy.default`` already decodes most headers, but a message parsed under
    ``compat32`` (or a header whose charset the codec knows nothing about) arrives as a
    literal ``=?utf-8?B?…?=``. Running the decode unconditionally is safe: it is
    idempotent on already-decoded text. An unknown charset makes ``make_header`` raise
    ``LookupError`` — measured, and the reason this is wrapped rather than trusted."""
    if raw is None:
        return ""
    text = str(raw)
    if "=?" not in text:
        return text
    try:
        return str(email.header.make_header(email.header.decode_header(text)))
    except (LookupError, UnicodeDecodeError, ValueError):
        logger.debug("mail-desk: header decode failed; using the raw value", exc_info=True)
        return text


def sender_address(from_header: object) -> str:
    """The sender's bare address, lowercased — the ONLY value trust may key on.

    ``parseaddr`` returns ``(display_name, address)``; this returns the address half and
    discards the display name completely. A ``From`` with two addresses, or a malformed
    one, yields ``""`` (fail-closed: no address ⇒ no trust match).

    The ``local@domain`` shape is verified rather than assumed: ``parseaddr`` happily
    returns a bare token like ``not-an-address`` as the *address* half (measured), and a
    trust key that isn't an address is a key an attacker can collide with a non-address
    string."""
    _, addr = email.utils.parseaddr(str(from_header or ""))
    addr = addr.strip().lower()
    local, at, domain = addr.partition("@")
    if not at or not local or not domain or "@" in domain:
        return ""
    return addr


def sender_display_name(from_header: object) -> str:
    """The sender's display name, decoded, for UI/notification text ONLY.

    Never pass this to a trust check. It is attacker-controlled and may itself look
    exactly like an allowed address."""
    display, _ = email.utils.parseaddr(str(from_header or ""))
    return decode_header_value(display).strip()


def _decode_part(part: Message) -> str:
    """Decode one part's payload to str, honoring its declared charset (utf-8 fallback)."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, ValueError):
        return payload.decode("utf-8", errors="replace")


def extract_body(msg: Message) -> str:
    """The readable body text: prefer ``text/plain``, else stripped ``text/html``.

    Attachments are skipped here — :func:`parse_inbound` reports their names separately so
    the transport can mention them without this app growing a document-extraction path a
    channel does not need (the mail-inbox app owns that concern)."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename() or ""
        disposition = str(part.get("Content-Disposition", "")).lower()
        if "attachment" in disposition or filename:
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain_parts.append(_decode_part(part))
        elif ctype == "text/html":
            html_parts.append(_decode_part(part))

    if plain_parts:
        body = "\n".join(p for p in plain_parts if p.strip()).strip()
    elif html_parts:
        body = html_to_text("\n".join(html_parts))
    else:
        body = ""
    return body[:MAX_BODY_CHARS]


def attachment_names(msg: Message) -> list[str]:
    """Filenames of the message's attachment parts (decoded), in order."""
    names: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename() or ""
        disposition = str(part.get("Content-Disposition", "")).lower()
        if filename or "attachment" in disposition:
            names.append(decode_header_value(filename) or "(unnamed)")
    return names


def strip_quoted_reply(body: str) -> str:
    """Drop quoted previous-message history from a reply body.

    Cuts at the first attribution line ("On … wrote:", "-----Original Message-----",
    a bare ``-- `` signature delimiter) or at the first run of ``>``-quoted lines that
    continues to the end. Returns the original text when nothing looks quoted, so a plain
    first message is untouched."""
    lines = body.splitlines()
    cut = len(lines)
    for idx, line in enumerate(lines):
        if any(marker.match(line) for marker in _QUOTE_MARKERS):
            cut = idx
            break
    else:
        # No attribution line: find the first '>' block that runs to the end (a client
        # that quotes without an attribution header, e.g. some mobile clients).
        first_quote = None
        for idx, line in enumerate(lines):
            if line.lstrip().startswith(">"):
                if first_quote is None:
                    first_quote = idx
            elif line.strip():
                first_quote = None  # real text after the quote — not a trailing block
        if first_quote is not None:
            cut = first_quote

    trimmed = "\n".join(lines[:cut]).strip()
    # Never return empty when there WAS text: a reply that is only quotation still carries
    # intent ("+1" stripped by an over-eager rule would be a silent drop), so fall back to
    # the untrimmed body rather than dropping the turn.
    return trimmed or body.strip()


class InboundMail:
    """One parsed inbound message — the fields the transport actually needs.

    A plain class (not a dataclass) with ``__slots__``: it is constructed once per message
    on the poll path and never serialized."""

    __slots__ = (
        "uid", "message_id", "from_addr", "from_name", "subject", "body",
        "in_reply_to", "references", "to_addrs", "attachments", "ts",
    )

    def __init__(
        self, *, uid: int = 0, message_id: str = "", from_addr: str = "", from_name: str = "",
        subject: str = "", body: str = "", in_reply_to: str = "", references: str = "",
        to_addrs: list[str] | None = None, attachments: list[str] | None = None, ts: float = 0.0,
    ) -> None:
        self.uid = uid
        self.message_id = message_id
        self.from_addr = from_addr
        self.from_name = from_name
        self.subject = subject
        self.body = body
        self.in_reply_to = in_reply_to
        self.references = references
        self.to_addrs = to_addrs or []
        self.attachments = attachments or []
        self.ts = ts

    @property
    def thread_root(self) -> str:
        """The stable thread key: the FIRST id in the chain, else this message's id.

        Every message in one conversation shares the first ``References`` entry (the
        root), so keying the session on it survives a mid-thread reply arriving before its
        parent — which keying on ``In-Reply-To`` would not."""
        refs = self.references.split()
        if refs:
            return refs[0]
        if self.in_reply_to:
            return self.in_reply_to
        return self.message_id


def parse_inbound(raw: bytes, uid: int = 0) -> InboundMail | None:
    """Parse raw RFC822 bytes into an :class:`InboundMail`, or ``None`` if unusable.

    Fail-closed: an unparseable message, or one with no usable ``From`` address, returns
    ``None`` so no content reaches a session. The caller logs and continues — one bad
    message never stops the poll loop."""
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:
        logger.debug("mail-desk: message parse failed for uid %s", uid, exc_info=True)
        return None

    try:
        from_addr = sender_address(msg.get("From"))
        if not from_addr:
            logger.debug("mail-desk: uid %s has no usable From address — dropped", uid)
            return None
        to_addrs = [
            addr.strip().lower()
            for _, addr in email.utils.getaddresses(
                [str(msg.get("To", "")), str(msg.get("Cc", ""))]
            )
            if addr.strip()
        ]
        return InboundMail(
            uid=uid,
            message_id=str(msg.get("Message-ID", "")).strip(),
            from_addr=from_addr,
            from_name=sender_display_name(msg.get("From")),
            subject=decode_header_value(msg.get("Subject")),
            body=extract_body(msg),
            in_reply_to=str(msg.get("In-Reply-To", "")).strip(),
            references=str(msg.get("References", "")).strip(),
            to_addrs=to_addrs,
            attachments=attachment_names(msg),
            ts=_parse_date(msg),
        )
    except Exception:
        logger.debug("mail-desk: message mapping failed for uid %s", uid, exc_info=True)
        return None


def _parse_date(msg: Message) -> float:
    raw = msg.get("Date", "")
    if raw:
        try:
            return email.utils.parsedate_to_datetime(str(raw)).timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    return 0.0


def reply_subject(subject: str) -> str:
    """``Re:``-prefix a subject once (never ``Re: Re: Re:``)."""
    subj = (subject or "").strip()
    if not subj:
        return "Re:"
    return subj if subj.lower().startswith("re:") else f"Re: {subj}"


def build_references(parent_references: str, parent_message_id: str) -> str:
    """The child's ``References``: the parent's chain plus the parent's own id.

    Order matters and duplicates must not accumulate — a client walks this list to build
    the thread tree, and the root (first entry) is what our own ``thread_root`` keys on.
    RFC 5322 §3.6.4 is exactly this rule."""
    chain = [ref for ref in (parent_references or "").split() if ref]
    parent = (parent_message_id or "").strip()
    if parent and parent not in chain:
        chain.append(parent)
    return " ".join(chain)


def build_outbound(
    *,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    html_body: str = "",
    message_id: str = "",
    in_reply_to: str = "",
    references: str = "",
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> EmailMessage:
    """Build one outbound message with correct threading headers.

    ``attachments`` are ``(filename, mimetype, payload)`` triples. ``html_body``, when
    given, is added as a ``multipart/alternative`` sibling so ``deliver_rich`` can send
    HTML while the plain part stays the readable fallback."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or email.utils.make_msgid(domain=_domain_of(from_addr))
    msg["Date"] = email.utils.formatdate(localtime=True)
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    # Mark our own mail so a filter/rule can find it, and so a human reading raw headers
    # knows which agent sent it.
    msg["Auto-Submitted"] = "auto-replied"

    msg.set_content(body or "")
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    for filename, mimetype, payload in attachments or []:
        maintype, _, subtype = mimetype.partition("/")
        msg.add_attachment(
            payload, maintype=maintype or "application",
            subtype=subtype or "octet-stream", filename=filename,
        )
    return msg


def _domain_of(address: str) -> str:
    """The domain half of an address, for ``make_msgid``; ``None``-safe default."""
    _, addr = email.utils.parseaddr(address or "")
    _, _, domain = addr.partition("@")
    return domain or "localhost"
