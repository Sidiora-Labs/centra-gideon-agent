"""MIME parsing/building: header decoding, parseaddr-only trust keys, HTML→text,
quoted-reply trimming, and the References chain rule.

The security-weight-carrying assertions here are the parseaddr ones: the transport keys
trust on :func:`sender_address`, so if a display name could ever leak into that value the
channel is handed to anyone who can set one.
"""

from __future__ import annotations

import base64

from mail_desk_runtime.mime import (
    MAX_BODY_CHARS,
    InboundMail,
    build_outbound,
    build_references,
    decode_header_value,
    extract_body,
    html_to_text,
    parse_inbound,
    reply_subject,
    sender_address,
    sender_display_name,
    strip_quoted_reply,
)
from mail_desk_fakes import build_message, raw_message


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class TestHeaderDecoding:
    def test_decodes_rfc2047_base64(self):
        assert decode_header_value(f"=?utf-8?B?{_b64('frühstück')}?=") == "frühstück"

    def test_decodes_quoted_printable(self):
        assert decode_header_value("=?utf-8?Q?caf=C3=A9?=") == "café"

    def test_plain_text_passes_through_unchanged(self):
        assert decode_header_value("plain subject") == "plain subject"

    def test_already_decoded_unicode_is_idempotent(self):
        assert decode_header_value("frühstück") == "frühstück"

    def test_unknown_charset_degrades_to_the_raw_value(self):
        """``make_header`` raises LookupError on a charset the codec table lacks —
        measured. Degrading beats letting one bad header break the poll loop."""
        raw = "=?bogus-charset-xyz?B?Wg==?="
        assert decode_header_value(raw) == raw

    def test_none_is_empty(self):
        assert decode_header_value(None) == ""

    def test_non_ascii_subject_survives_a_full_parse(self):
        raw = raw_message(
            f"From: bob@example.com\r\nSubject: =?utf-8?B?{_b64('frühstück ünïcode')}?="
        )
        mail = parse_inbound(raw, 1)
        assert mail is not None
        assert mail.subject == "frühstück ünïcode"


class TestSenderAddressIsTheOnlyTrustKey:
    """``From`` display names are attacker-controlled; only the address may key trust."""

    def test_display_name_plus_address_yields_the_address(self):
        assert sender_address('Bob Smith <bob@example.com>') == "bob@example.com"
        assert sender_display_name('Bob Smith <bob@example.com>') == "Bob Smith"

    def test_bare_address(self):
        assert sender_address("bob@example.com") == "bob@example.com"

    def test_address_is_lowercased(self):
        assert sender_address("Bob@Example.COM") == "bob@example.com"

    def test_display_name_that_looks_like_an_address_is_not_the_address(self):
        """The spoofing face: a display name containing an allowed address must NOT
        become the trust key."""
        header = '"allowed@example.com" <evil@attacker.test>'
        assert sender_address(header) == "evil@attacker.test"
        assert sender_display_name(header) == "allowed@example.com"

    def test_encoded_display_name_carrying_an_address_is_not_the_address(self):
        """Measured: an RFC-2047 display name decodes to arbitrary text under
        policy.default, so the encoded form is the sharper version of the same attack."""
        raw = raw_message(
            f"From: =?utf-8?B?{_b64('allowed@example.com')}?= <evil@attacker.test>\r\n"
            "Subject: hi"
        )
        mail = parse_inbound(raw, 7)
        assert mail is not None
        assert mail.from_addr == "evil@attacker.test"
        assert mail.from_name == "allowed@example.com"

    def test_display_name_with_nested_brackets_still_yields_the_real_address(self):
        header = '"Bob <evil@attacker.test>" <bob@example.com>'
        assert sender_address(header) == "bob@example.com"

    def test_two_addresses_in_from_fail_closed(self):
        """A ``From`` with two addresses is malformed; ``parseaddr`` yields nothing and we
        surfacing nothing beats guessing which one to trust."""
        assert sender_address("allowed@example.com, evil@attacker.test") == ""

    def test_missing_from_is_empty(self):
        assert sender_address("") == ""
        assert sender_address(None) == ""

    def test_a_bare_non_address_token_is_rejected(self):
        """Measured: ``parseaddr("not-an-address")`` returns that token as the ADDRESS
        half. A trust key that isn't ``local@domain`` is a key a non-address string can
        collide with, so the shape is verified, not assumed."""
        assert sender_address("not-an-address") == ""
        assert sender_address("@nolocal.test") == ""
        assert sender_address("nodomain@") == ""
        assert sender_address("two@at@signs.test") == ""


class TestParseInboundFailsClosed:
    def test_no_from_header_returns_none(self):
        assert parse_inbound(raw_message("Subject: no sender"), 1) is None

    def test_malformed_from_returns_none(self):
        assert parse_inbound(raw_message("From: not-an-address\r\nSubject: x"), 1) is None

    def test_unparseable_bytes_return_none_or_no_sender(self):
        """Garbage must never raise out of the parse — it either yields None (no usable
        From) or a mail whose fields are empty; both are safe, neither crashes."""
        assert parse_inbound(b"\x00\xff\xfe not a message at all", 1) is None

    def test_valid_message_maps_every_field(self):
        raw = build_message(
            from_addr="Bob <bob@example.com>", subject="Hi there",
            message_id="<m1@example.com>", plain="the body",
            in_reply_to="<p1@example.com>", references="<r0@example.com> <p1@example.com>",
        )
        mail = parse_inbound(raw, 42)
        assert mail is not None
        assert mail.uid == 42
        assert mail.from_addr == "bob@example.com"
        assert mail.from_name == "Bob"
        assert mail.subject == "Hi there"
        assert "the body" in mail.body
        assert mail.message_id == "<m1@example.com>"
        assert mail.in_reply_to == "<p1@example.com>"
        assert mail.references == "<r0@example.com> <p1@example.com>"
        assert mail.to_addrs == ["agent@example.com"]
        assert mail.ts > 0


class TestBodyExtraction:
    def test_prefers_plain_over_html(self):
        raw = build_message(plain="PLAIN VERSION", html="<p>HTML VERSION</p>")
        mail = parse_inbound(raw, 1)
        assert mail is not None
        assert "PLAIN VERSION" in mail.body
        assert "HTML VERSION" not in mail.body

    def test_html_only_falls_back_to_stripped_text(self):
        raw = build_message(plain=None, html="<p>Hello <b>world</b></p>")
        mail = parse_inbound(raw, 1)
        assert mail is not None
        assert "Hello" in mail.body and "world" in mail.body
        assert "<b>" not in mail.body

    def test_html_script_and_style_bodies_are_dropped(self):
        html = "<style>.a{color:red}</style><script>alert(1)</script><p>visible</p>"
        text = html_to_text(html)
        assert "visible" in text
        assert "alert" not in text and "color:red" not in text

    def test_malformed_html_does_not_raise(self):
        assert isinstance(html_to_text("<p>unclosed <b>bold"), str)

    def test_attachment_parts_are_not_body_text(self):
        raw = build_message(
            plain="just this", attachments=[("notes.txt", "text/plain", b"attached text")]
        )
        mail = parse_inbound(raw, 1)
        assert mail is not None
        assert "attached text" not in mail.body
        assert mail.attachments == ["notes.txt"]

    def test_body_is_capped(self):
        import email

        msg = email.message.EmailMessage()
        msg["From"] = "bob@example.com"
        msg.set_content("x" * (MAX_BODY_CHARS + 5000))
        assert len(extract_body(msg)) == MAX_BODY_CHARS


class TestQuotedReplyTrimming:
    def test_strips_gmail_attribution_block(self):
        body = (
            "Yes, do it.\n\n"
            "On Mon, 9 Aug 2026 at 10:00, Agent <agent@example.com> wrote:\n"
            "> Should I proceed?\n"
        )
        assert strip_quoted_reply(body) == "Yes, do it."

    def test_strips_outlook_original_message_block(self):
        body = "Approved.\n\n-----Original Message-----\nFrom: agent@example.com\nblah\n"
        assert strip_quoted_reply(body) == "Approved."

    def test_strips_signature_delimiter(self):
        body = "Go ahead.\n\n-- \nBob\nSent from a phone\n"
        assert strip_quoted_reply(body) == "Go ahead."

    def test_strips_a_trailing_quote_block_without_attribution(self):
        body = "sure\n> earlier line\n> another\n"
        assert strip_quoted_reply(body) == "sure"

    def test_keeps_a_quote_that_is_followed_by_new_text(self):
        body = "> you asked this\nand here is my answer"
        assert "my answer" in strip_quoted_reply(body)

    def test_plain_first_message_is_untouched(self):
        assert strip_quoted_reply("Just a normal message.") == "Just a normal message."

    def test_quote_only_body_is_not_dropped_to_empty(self):
        """A reply that is ONLY quotation still carried intent — never return empty."""
        assert strip_quoted_reply("> just the quote\n") != ""


class TestThreadRoot:
    def test_root_is_the_first_reference(self):
        mail = InboundMail(
            message_id="<m3@x>", in_reply_to="<m2@x>", references="<m1@x> <m2@x>"
        )
        assert mail.thread_root == "<m1@x>"

    def test_root_falls_back_to_in_reply_to(self):
        assert InboundMail(message_id="<m2@x>", in_reply_to="<m1@x>").thread_root == "<m1@x>"

    def test_root_of_a_first_message_is_its_own_id(self):
        assert InboundMail(message_id="<m1@x>").thread_root == "<m1@x>"


class TestReferencesChain:
    def test_appends_the_parent_id_to_the_parents_chain(self):
        assert build_references("<r1@x> <r2@x>", "<r3@x>") == "<r1@x> <r2@x> <r3@x>"

    def test_first_reply_has_just_the_parent(self):
        assert build_references("", "<r1@x>") == "<r1@x>"

    def test_does_not_duplicate_an_id_already_in_the_chain(self):
        assert build_references("<r1@x> <r2@x>", "<r2@x>") == "<r1@x> <r2@x>"

    def test_order_is_preserved(self):
        chain = build_references(build_references("", "<a@x>"), "<b@x>")
        assert chain.split() == ["<a@x>", "<b@x>"]

    def test_empty_parent_yields_the_chain_unchanged(self):
        assert build_references("<r1@x>", "") == "<r1@x>"


class TestReplySubject:
    def test_prefixes_once(self):
        assert reply_subject("Hello") == "Re: Hello"

    def test_does_not_double_prefix(self):
        assert reply_subject("Re: Hello") == "Re: Hello"
        assert reply_subject("re: hello") == "re: hello"

    def test_empty_subject(self):
        assert reply_subject("") == "Re:"


class TestBuildOutbound:
    def test_sets_the_threading_headers(self):
        msg = build_outbound(
            from_addr="agent@example.com", to_addr="bob@example.com", subject="Re: hi",
            body="reply text", in_reply_to="<p@x>", references="<r@x> <p@x>",
        )
        assert msg["To"] == "bob@example.com"
        assert msg["In-Reply-To"] == "<p@x>"
        assert msg["References"] == "<r@x> <p@x>"
        assert str(msg["Message-ID"]).startswith("<")
        assert msg["Auto-Submitted"] == "auto-replied"

    def test_message_id_domain_comes_from_the_sender(self):
        msg = build_outbound(
            from_addr="agent@example.com", to_addr="b@x.com", subject="s", body="b"
        )
        assert str(msg["Message-ID"]).endswith("@example.com>")

    def test_html_alternative_when_requested(self):
        msg = build_outbound(
            from_addr="a@x.com", to_addr="b@x.com", subject="s", body="plain",
            html_body="<p>rich</p>",
        )
        types = {p.get_content_type() for p in msg.walk() if not p.is_multipart()}
        assert types == {"text/plain", "text/html"}

    def test_plain_only_when_no_html(self):
        msg = build_outbound(from_addr="a@x.com", to_addr="b@x.com", subject="s", body="p")
        assert msg.get_content_type() == "text/plain"

    def test_attachment_becomes_a_mime_part(self):
        msg = build_outbound(
            from_addr="a@x.com", to_addr="b@x.com", subject="s", body="p",
            attachments=[("report.pdf", "application/pdf", b"%PDF-1.4")],
        )
        names = [p.get_filename() for p in msg.walk() if p.get_filename()]
        assert names == ["report.pdf"]

    def test_no_threading_headers_on_a_fresh_message(self):
        msg = build_outbound(from_addr="a@x.com", to_addr="b@x.com", subject="s", body="p")
        assert msg["In-Reply-To"] is None
        assert msg["References"] is None

    def test_non_ascii_subject_round_trips(self):
        import email
        import email.policy

        msg = build_outbound(
            from_addr="a@x.com", to_addr="b@x.com", subject="frühstück", body="p"
        )
        reparsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
        assert str(reparsed["Subject"]) == "frühstück"
