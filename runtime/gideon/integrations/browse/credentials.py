"""The credential screen — BA-4's *credentials-never-transit-the-agent* invariant, structurally.

BROWSE-AUTOMATION §5.2 states the invariant as a promise: "the LLM never sees a password field's
value, never receives a 2FA code, never handles an OAuth token". A promise is enforced by whoever
remembers it. This module is the mechanism that makes it hold whether anyone remembers or not, and
the shape of that mechanism is the whole point of the file, so it is worth stating plainly.

**Why redaction alone cannot be the answer.** :func:`gideon.security.security.redact_credentials` is
SHAPE- and NAME-based: it knows `sk-ant-…`, `ghp_…` and `name = value` assignments. A human password
(`hunter2`) matches none of them, and a 2FA code (`418290`) matches nothing that is not also a
line number. So a design where the credential travels and a redactor tries to catch it fails for
exactly the inputs this invariant is about. It is also **not idempotent over a composed
`field: value` line** — a second application garbles the field name — which forbids the tempting
"redact everything on the way out" posture: composition happens many times, so the screen must
happen ONCE, at the boundary, BEFORE composition.

**So the invariant is three refusals, not a filter.**

1. **A credential field's value is never READ.** :func:`is_credential_input` decides at the single
   point where ``extraction._handle_input`` turns an ``<input>`` into a record. For a credential
   input the record carries :data:`WITHHELD` unconditionally — not the value, and not "the value if
   non-empty", because whether a password box is prefilled is itself a fact about the user's
   credential store. The value therefore does not exist anywhere in the browse process's state, so
   there is no downstream consumer to audit, no ordering to get right, and no second pass to
   garble. This is the difference between "we remembered not to log it" and "there is nothing to
   log".

2. **The agent cannot WRITE one either.** The loop refuses ``TYPE <ref>(…)`` into a credential
   field and escalates to the ``request_login`` handoff instead (see
   :mod:`gideon.integrations.browse.handoff`). That refusal is what makes the human handoff the ONLY
   authentication path rather than the polite one: a model that hallucinated a password could not
   spend it, and a caller that supplied one on the action config could not either. It also closes
   the echo: :func:`screen_action_render` replaces the value in the rendered action line, which is
   the string that reaches the stuck-detector, the next prompt's WARNINGS block, the step ledger,
   the SEL row and the parked run's user-facing sentence. Screening it at the render site is the
   "once, before composition" rule applied to the model's own output.

3. **A credential in a URL is never composed in.** The post-login redirect is where an OAuth
   ``code``/``access_token``/``id_token`` actually shows up, and a URL reaches the model through
   three separate paths (the outline's ``# <url>`` header, the fence's ``source``/``source_id``, and
   a link's rendered target) plus three user/audit paths (the park sentence, the run payload, the
   SEL ``resources`` field). :func:`screen_url` replaces the VALUE and keeps the KEY, so
   ``?code=[withheld]&country=US`` stays diagnosable — a URL redacted to nothing turns "the login
   redirect landed somewhere unexpected" into an unanswerable question.

**Deliberately value-replacing, never whole-string.** Every function here removes a value and
leaves its surroundings intact, for the reason `security.redact_url_userinfo` records: taking out
the secret must not take out the ability to read the line. And every replacement is a literal
containing characters the thing it replaces cannot contain, so re-running any of these over its own
output is a no-op — idempotence by construction, not by a guard a later session could delete.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

WITHHELD = "[withheld]"

CREDENTIAL_INPUT_TYPES: frozenset[str] = frozenset({"password"})

CREDENTIAL_AUTOCOMPLETE: frozenset[str] = frozenset(
    {"current-password", "new-password", "one-time-code"}
)

CREDENTIAL_NAME_TOKENS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "passcode",
    "otp",
    "totp",
    "mfa",
    "2fa",
    "twofactor",
    "two_factor",
    "onetimecode",
    "one_time_code",
    "verification_code",
    "verificationcode",
    "security_code",
    "securitycode",
    "authcode",
    "auth_code",
    "secret",
    "api_key",
    "apikey",
    "token",
)

CREDENTIAL_URL_PARAMS: frozenset[str] = frozenset(
    {
        "code",
        "access_token",
        "id_token",
        "refresh_token",
        "token",
        "auth_token",
        "authtoken",
        "id_assertion",
        "assertion",
        "session",
        "sessionid",
        "session_id",
        "sid",
        "sso",
        "samlresponse",
        "saml_response",
        "password",
        "passwd",
        "pwd",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "key",
        "otp",
        "ticket",
        "signature",
        "sig",
    }
)


def is_credential_input(
    input_type: str, *, name: str = "", autocomplete: str = "", placeholder: str = ""
) -> bool:
    """Whether this ``<input>`` holds a credential, so its value must never be read.

    Three signals, strongest first, ORed — a field is a credential if ANY of them says so, because
    the failure directions are not symmetric. A false positive costs one field the agent cannot
    fill (and the handoff exists precisely to fill it); a false negative puts a password in a
    prompt.

    ``placeholder`` is screened alongside ``name``/``id`` because a placeholder is the label a
    site shows on a field it did not bother to name, and ``extraction`` already falls back to it
    for the accessible name.
    """
    itype = (input_type or "").strip().lower()
    if itype in CREDENTIAL_INPUT_TYPES:
        return True
    tokens = {t.strip().lower() for t in (autocomplete or "").replace(",", " ").split()}
    if tokens & CREDENTIAL_AUTOCOMPLETE:
        return True
    haystack = f"{name or ''} {placeholder or ''}".lower()
    haystack = haystack.replace("-", "_").replace(".", "_")
    return any(token in haystack for token in CREDENTIAL_NAME_TOKENS)


def screen_url(url: str) -> str:
    """Replace credential-bearing query/fragment VALUES with :data:`WITHHELD`, keeping the keys.

    Applied where a URL is READ from the browser rather than where it is displayed, so one call
    covers every downstream consumer at once — the outline header, the fence's ``source_id``, the
    Links DSL, the run payload, the park sentence and the SEL row. That is the "screen at the
    boundary, once, before composition" rule: the alternative is six display sites each remembering
    to screen, which is five chances to forget.

    Both the query AND the fragment are screened. The fragment matters more, not less: the OAuth
    *implicit* flow returns ``#access_token=…``, and a fragment never reaches a server, so it is the
    one place a token is guaranteed to be sitting in the URL bar of the page the agent just read.

    Non-URL input, an unparseable URL, or a URL with no credential parameter is returned unchanged
    — this is a screen, not a validator, and refusing to hand back a string the agent needs would
    turn a privacy control into an outage.
    """
    raw = url or ""
    if not raw or ("?" not in raw and "#" not in raw):
        return raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    query = _screen_query(parts.query)
    fragment = _screen_query(parts.fragment)
    if query == parts.query and fragment == parts.fragment:
        return raw
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))


def _screen_query(blob: str) -> str:
    """Screen one ``a=1&b=2`` blob. Returns it unchanged when it holds no credential parameter.

    ``keep_blank_values=True`` so ``?code=`` survives as ``?code=`` rather than vanishing: a
    parameter disappearing changes what the URL means, and this function's whole contract is that
    only VALUES move.
    """
    if not blob or "=" not in blob:
        return blob
    try:
        pairs = parse_qsl(blob, keep_blank_values=True)
    except ValueError:
        return blob
    if not pairs:
        return blob
    hit = False
    screened: list[tuple[str, str]] = []
    for key, value in pairs:
        if (
            key.strip().lower().replace("-", "_") in CREDENTIAL_URL_PARAMS
            and value != WITHHELD
        ):
            screened.append((key, WITHHELD))
            hit = True
        else:
            screened.append((key, value))
    if not hit:
        return blob
    return urlencode(screened, safe="[]")


def screen_action_render(rendered: str, *, credential: bool) -> str:
    """Screen one rendered sentinel line before it is recorded or re-composed.

    ``credential`` is the caller's answer to "does this action address a credential field", which
    only the caller can know (it holds the ref→element index). Passing the verdict in rather than
    re-deriving it here keeps ONE place deciding what a credential field is.

    Only the ``TYPE`` argument moves, and the ref does not: the ref is what makes the refusal
    legible ("browse refused to type into field 4f2a1c"), and a line screened to
    ``TYPE [withheld]`` would name neither the field nor the action.
    """
    if not credential:
        return rendered
    if not rendered.startswith("TYPE "):
        return rendered
    head, sep, _ = rendered.partition("(")
    if not sep:
        return rendered
    return f"{head}({WITHHELD})"
