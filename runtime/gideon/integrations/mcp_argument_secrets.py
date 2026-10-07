"""Where a credential sits in an MCP server's arguments and in its address.

A server's arguments and URL are free text, and a token goes there when it is not in an
environment variable or a header: ``--api-token=…``, ``--api-key …``, ``--header "Authorization:
Bearer …"``, ``?token=…``. Native MCP URLs continue to refuse userinfo and fragments. Three readers need to know where:

* the credential store (``config.secret_refs``) keeps each such value in the store and leaves a
  ``{{secret:…}}`` reference in its place, so ``mcp.json``, the agent config, a snapshot, an export
  and a sync carry the reference and never the value, and a start of the server puts it back;
* the owner's yes to a server (``mcp_grants``) is sealed to what it runs, not to the values it is
  handed, as it is for its environment: each spot reads as the mask in the seal;
* every page that shows a server masks it (``mcp_discovery.masked_args``), by a rule deliberately
  wider than this one.

A spot is found by its PLACE where the place says what it holds: the value of a flag named for a
credential (``is_credential_field_name``, the repository's one rule), a header's value, a query value whose name is a credential's. So the stored definition (a
reference there) and the started one (the value there) read alike. Where the place says nothing,
the VALUE must: an argument in a format only credentials have (``security.redact_credentials``),
or a part of an address shaped like a token (:func:`looks_secret`). A long word standing alone is
masked on every page but is not moved: a package or a project name can look like one, and a home
restored elsewhere would then ask for a value nobody was ever shown, and a change to what the
server runs would no longer be asked about.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from urllib.parse import unquote

SECRET_BINDING_RE = re.compile(r"\{\{\s*secret:([A-Za-z0-9_.\-]+)\s*\}\}")

SECRET_MASK = "[REDACTED: credential]"


def is_credential_field_name(name):
    # Keep import-package writers above this low-level slot scanner in dependency order.
    from gideon.cognition.onboarding_import.floors import credential_field

    return credential_field(name)


#: Flags whose NEXT argument is an HTTP header (``mcp-remote --header "Authorization: Bearer …"``,
#: curl's ``-H``): its name stays, its value is the credential.
HEADER_FLAGS = frozenset({"--header", "--headers", "-H"})
#: A scheme, as ``urlsplit`` reads one: so ``--url=https://…`` is not mistaken for a URL.
SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*")
#: A run of token characters. Long enough, and mixing letters and digits, it is a key in a format
#: no named rule knows (a hex or base64url secret). ``/`` and ``.`` are not in it: a path, a host
#: name and a version number are not tokens, and a JWT's parts are tested one by one.
_TOKEN_RUN_RE = re.compile(r"[A-Za-z0-9_\-+=~]{20,}")
_LETTER_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\d")
_WORD_RE = re.compile(r"\S+")


def looks_secret(text: str) -> bool:
    """Shaped like a credential: a reference to one in the credential store, a format the
    credential redactor knows (a provider key, a bearer token, ``api_key=…``), or a long run of
    token characters mixing letters and digits."""
    from gideon.security.security import redact_credentials

    if SECRET_BINDING_RE.search(text) or redact_credentials(text)[1]:
        return True
    return any(
        _TOKEN_RUN_RE.fullmatch(piece)
        and _LETTER_RE.search(piece)
        and _DIGIT_RE.search(piece)
        for piece in text.split(".")
    )


def flag_carries(arg: str) -> str | None:
    """What the argument AFTER ``arg`` holds: ``"header"``, a credential ``"value"``, or neither."""
    if arg in HEADER_FLAGS:
        return "header"
    if (
        arg.startswith("-")
        and "=" not in arg
        and is_credential_field_name(arg.lstrip("-"))
    ):
        return "value"
    return None


@dataclass(frozen=True)
class Spot:
    """Where one credential sits in a text: ``text[start:end]``. ``kind`` and ``name`` say what it
    is in words that hold no value: ``flag`` (``name`` is the flag, ``--api-token``), ``header``
    (``name`` is the header), ``login`` (an address's), ``query`` (``name`` is the query key),
    ``path`` (a part of an address's path) or ``key`` (an argument in a key's format).
    """

    start: int
    end: int
    kind: str
    name: str = ""

    def shifted(self, by: int) -> Spot:
        return replace(self, start=self.start + by, end=self.end + by)


def _is_reference(text: str) -> bool:
    return SECRET_BINDING_RE.fullmatch(text) is not None


def _is_key_format(word: str) -> bool:
    from gideon.security.security import redact_credentials

    return bool(redact_credentials(word)[1])


def _header_spots(text: str) -> list[Spot]:
    """A ``Name: value`` header's value, without the whitespace around it."""
    name, sep, value = text.partition(":")
    held = value.strip()
    if not sep or not name.strip() or not held:
        return []
    start = len(name) + 1 + (len(value) - len(value.lstrip()))
    return [Spot(start, start + len(held), "header", name.strip())]


def _spots_after(word: str, carried: tuple[str, str] | None) -> list[Spot]:
    """*word*'s spots, *carried* being what the word before it said this one holds."""
    if carried is None:
        return _word_spots(word)
    kind, flag = carried
    if kind == "header":
        return _header_spots(word)
    return [Spot(0, len(word), "flag", flag)] if word else []


def _carried(word: str) -> tuple[str, str] | None:
    carries = flag_carries(word)
    return (carries, word) if carries else None


def _word_spots(word: str) -> list[Spot]:
    scheme, sep, _rest = word.partition("://")
    if sep and SCHEME_RE.fullmatch(scheme):
        return list(address_spots(word))
    name, sep, value = word.partition("=")
    if sep and name and not any(ch.isspace() for ch in name):
        base = len(name) + 1
        if name in HEADER_FLAGS:
            return [s.shifted(base) for s in _header_spots(value)]
        if is_credential_field_name(name.lstrip("-")):
            return [Spot(base, len(word), "flag", name)] if value else []
        return [s.shifted(base) for s in _word_spots(value)]
    if any(ch.isspace() for ch in word):
        # A shell string (``sh -c "…"``), read word by word by the same rules.
        spots: list[Spot] = []
        carried: tuple[str, str] | None = None
        for match in _WORD_RE.finditer(word):
            part = match.group()
            spots.extend(s.shifted(match.start()) for s in _spots_after(part, carried))
            carried = _carried(part)
        return spots
    if word and (_is_reference(word) or _is_key_format(word)):
        return [Spot(0, len(word), "key")]
    return []


def argument_spots(args: Sequence[str]) -> list[tuple[Spot, ...]]:
    """The spots of each of a server's arguments, in order."""
    out: list[tuple[Spot, ...]] = []
    carried: tuple[str, str] | None = None
    for arg in args:
        out.append(tuple(_spots_after(arg, carried)))
        carried = _carried(arg)
    return out


def address_spots(url: str) -> tuple[Spot, ...]:
    """The spots of an address: its login, the value of each query key named for a credential or
    shaped like one, and each part of its path shaped like a token. Nothing of a text that is not
    an address with a scheme."""
    from urllib.parse import urlsplit

    scheme, sep, _rest = url.partition("://")
    if not sep or not SCHEME_RE.fullmatch(scheme):
        return ()
    spots: list[Spot] = []
    parsed = urlsplit(url)
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("MCP URL userinfo and fragments are not supported")
    host = len(scheme) + 3
    authority_end = min(
        [i for i in (url.find(c, host) for c in "/?#") if i != -1] or [len(url)]
    )
    at = url.rfind("@", host, authority_end)
    if at != -1 and _is_reference(url[host:at]):
        # A login kept in the credential store: the reference is the whole of it.
        spots.append(Spot(host, at, "login"))
        host = at + 1
    path_end = min(
        [i for i in (url.find(c, authority_end) for c in "?#") if i != -1] or [len(url)]
    )
    pos = authority_end
    for segment in url[authority_end:path_end].split("/"):
        if segment and looks_secret(unquote(segment)):
            spots.append(Spot(pos, pos + len(segment), "path"))
        pos += len(segment) + 1
    if path_end < len(url) and url[path_end] == "?":
        fragment = url.find("#", path_end)
        query_end = len(url) if fragment == -1 else fragment
        pos = path_end + 1
        for pair in url[pos:query_end].split("&"):
            key, eq, value = pair.partition("=")
            if (
                eq
                and value
                and (
                    is_credential_field_name(unquote(key))
                    or looks_secret(unquote(value))
                )
            ):
                spots.append(
                    Spot(pos + len(key) + 1, pos + len(pair), "query", unquote(key))
                )
            pos += len(pair) + 1
    return tuple(spots)


def replaced(
    text: str, spots: Iterable[Spot], value_for: Callable[[Spot, str], str]
) -> str:
    """*text* with each spot's content replaced by ``value_for(spot, content)``."""
    out: list[str] = []
    pos = 0
    for spot in sorted(spots, key=lambda s: s.start):
        out.append(text[pos : spot.start])
        out.append(value_for(spot, text[spot.start : spot.end]))
        pos = spot.end
    out.append(text[pos:])
    return "".join(out)


def sealed_arguments(args: Sequence[str]) -> list[str]:
    """*args* with every spot read as the mask: what a seal of what a server runs is taken of."""
    return [
        replaced(arg, spots, lambda _spot, _value: SECRET_MASK)
        for arg, spots in zip(args, argument_spots(args), strict=True)
    ]


def sealed_address(url: str) -> str:
    """*url* with every spot read as the mask (:func:`sealed_arguments`)."""
    return replaced(url, address_spots(url), lambda _spot, _value: SECRET_MASK)


def described(spot: Spot) -> str:
    """What *spot* holds, as the sentence that asks for it says it: where the value goes, never
    the value."""
    if spot.kind == "flag":
        return f"the value of {spot.name}"
    if spot.kind == "header":
        return f"the value of its {spot.name} header"
    if spot.kind == "login":
        return "the login in its address"
    if spot.kind == "query":
        return f"the {spot.name} value in its address"
    if spot.kind == "path":
        return "the token in its address"
    return "a key in its arguments"


def command_line(spec):
    """Yield executable texts and deterministic credential spots, retaining shape."""
    args = spec.get("args") or []
    if not isinstance(args, (list, tuple)) or any(
        not isinstance(arg, str) or "\0" in arg for arg in args
    ):
        raise ValueError("MCP args must be NUL-free strings")
    for index, (text, spots) in enumerate(zip(args, argument_spots(args), strict=True)):
        yield "args", index, text, spots
    url = spec.get("url") or ""
    if not isinstance(url, str) or "\0" in url:
        raise ValueError("MCP URL must be a NUL-free string")
    if url:
        yield "url", 0, url, address_spots(url)


def command_line_references(spec):
    """Flat owner reference values for exact store cleanup; no plaintext returned."""
    from gideon.core.config.secret_refs import _owned_reference

    refs = {}
    for field, index, text, spots in command_line(spec):
        for order, spot in enumerate(spots):
            value = text[spot.start : spot.end]
            if _owned_reference(value) is not None:
                refs[f"{field}_{index}_{order}"] = value
    return refs


def credential_values(spec):
    """Every reference this server retains, including embedded argument/address slots."""
    spec = spec if isinstance(spec, dict) else {}
    refs = {}
    for field in ("env", "headers", "oauth"):
        values = spec.get(field)
        if isinstance(values, dict):
            refs.update({f"{field}_{key}": value for key, value in values.items()})
    refs.update(command_line_references(spec))
    return refs


def _with_texts(spec, texts):
    result = dict(spec)
    args = list(spec.get("args") or [])
    for (field, index), text in texts.items():
        if field == "args":
            args[index] = text
        else:
            result[field] = text
    if "args" in spec:
        result["args"] = args
    return result


def _validate_spot_reference(name, spot, value):
    from gideon.core.config.secret_refs import ForeignSecretReference, _owned_reference
    from gideon.extensions.providers.mcp_instances import mcp_owner

    reference = _owned_reference(value)
    if "{{secret:" in value and reference is None:
        raise ValueError(f"MCP credential reference malformed at {described(spot)}")
    if reference is not None and not mcp_owner(name).owns(reference):
        raise ForeignSecretReference(
            f"MCP credential belongs to another server at {described(spot)}"
        )
    return reference


def validate_references(name, spec):
    """Validate owner identity in recognized slots without resolving secret values."""
    for _field, _index, text, spots in command_line(spec):
        for spot in spots:
            _validate_spot_reference(name, spot, text[spot.start : spot.end])
        remainder = replaced(text, spots, lambda _spot, _value: "")
        if SECRET_BINDING_RE.search(remainder) or "{{secret:" in remainder:
            raise ValueError("MCP credential reference is outside a credential slot")


def store_command_line(name, spec, previous=None):
    from gideon.core.config.secret_refs import store
    from gideon.extensions.providers.mcp_instances import mcp_owner

    validate_references(name, spec)
    old = {}
    for field, index, text, spots in command_line(previous or {}):
        for order, spot in enumerate(spots):
            old[f"{field}_{index}_{order}"] = text[spot.start : spot.end]
    texts = {}
    for field, index, text, spots in command_line(spec):
        order = 0

        def value_for(spot, value):
            nonlocal order
            key = f"{field}_{index}_{order}"
            order += 1
            try:
                return store(
                    {key: value}, owner=mcp_owner(name), declared={key}, previous=old
                )[key]
            except ValueError as exc:
                raise ValueError(
                    f"MCP credential unavailable at {described(spot)}"
                ) from exc

        texts[(field, index)] = replaced(text, spots, value_for)
    return _with_texts(spec, texts)


def resolve_command_line(name, spec):
    from gideon.core.config.secret_refs import resolve
    from gideon.extensions.providers.mcp_instances import mcp_owner

    validate_references(name, spec)
    texts = {}
    for field, index, text, spots in command_line(spec):

        def value_for(spot, value):
            reference = _validate_spot_reference(name, spot, value)
            if reference is None:
                return value
            try:
                return resolve({"value": value}, owner=mcp_owner(name))["value"]
            except ValueError as exc:
                raise ValueError(
                    f"MCP credential unavailable at {described(spot)}"
                ) from exc

        texts[(field, index)] = replaced(text, spots, value_for)
    return _with_texts(spec, texts)


def known_values(spec):
    """Resolved credential bytes eligible for diagnostic redaction, never for logging."""
    from gideon.core.config.secret_refs import _owned_reference

    values: set[str] = set()
    for _field, _index, text, spots in command_line(spec):
        values.update(text[spot.start : spot.end] for spot in spots)
    for field in ("env", "headers", "oauth"):
        fields = spec.get(field) or {}
        for name, value in fields.items() if isinstance(fields, dict) else ():
            if isinstance(value, str) and (
                field == "headers" or is_credential_field_name(name)
            ):
                values.add(value)
    for value in tuple(values):
        if value.lower().startswith("bearer "):
            values.add(value[7:])
    return tuple(
        sorted(
            (value for value in values if value and _owned_reference(value) is None),
            key=len,
            reverse=True,
        )
    )


def scrub_text(text, values):
    for value in values:
        text = text.replace(value, SECRET_MASK)
    return text
