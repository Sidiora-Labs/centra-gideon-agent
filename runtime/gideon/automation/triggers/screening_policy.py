"""Stable screening rules and provider capability classifications."""

import re

_INVISIBLE = dict.fromkeys(
    [173, 8203, 8204, 8205, 8206, 8207, 8234, 8235, 8236, 8237, 8238, 8288, 65279]
)

_HOMOGLYPHS = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a"}
)

_B64_MIN_LEN = 24

_B64_RE = re.compile("[A-Za-z0-9+/]{%d,}={0,2}" % _B64_MIN_LEN)

INJECTION_GROUPS: dict[str, tuple[str, ...]] = {
    "override": (
        "\\b(ignore|disregard|forget|discard)\\b[^.\\n]{0,30}\\b(previous|prior|above|earlier|all|your|any)\\b[^.\\n]{0,20}\\b(instruction|prompt|rule|direction|directive|command|guideline)s?\\b",
        "\\b(override|overrule|supersede)\\b[^.\\n]{0,24}\\b(instruction|prompt|rule|directive|system|safety|guardrail)s?\\b",
        "^\\s*(important|urgent|attention|note)\\s*[:!]\\s*(override|ignore|disregard|new\\s+instruction|your\\s+new)",
        "\\bnew\\s+(instruction|directive|rule)s?\\s*:",
        "\\byour\\s+new\\s+(instruction|directive|task|goal|purpose)s?\\b",
    ),
    "token_smuggling": (
        "\\bignore\\s*all\\s*previous\\s*instructions?\\b",
        "\\bdisregard\\s*(all\\s*)?(previous|prior)\\s*(instructions?|prompts?)\\b",
    ),
    "persona_hijack": (
        "\\byou\\s+are\\s+(now|from\\s+now\\s+on)\\b",
        "\\bfrom\\s+now\\s+on[^.\\n]{0,30}\\b(you|respond|reply|act|behave)\\b",
        "\\b(pretend|imagine|roleplay|role-play)\\b[^.\\n]{0,20}\\byou\\s+(are|were|have)\\b",
        "\\bact\\s+as\\s+(a|an|the)?\\s*(unrestricted|uncensored|jailbroken|evil|malicious|rogue)\\b",
        "\\b(adopt|assume|become|switch\\s+to|take\\s+on)\\b[^.\\n]{0,16}\\b(new|different|alternate)?\\s*persona\\b",
        "\\byour\\s+(new|real|true)\\s+persona\\s+is\\b",
        "\\byou\\s+will\\s+(now\\s+)?respond\\s+as\\b",
    ),
    "jailbreak": (
        "\\b(enable|activate|enter|switch\\s+to)\\b[^.\\n]{0,16}\\b(developer|debug|god|dan|unrestricted|unfiltered|jailbreak)\\s*mode\\b",
        "\\b(no|without|ignore)\\b[^.\\n]{0,16}\\b(restrictions?|limitations?|filters?|guardrails?|safety|rules?)\\b[^.\\n]{0,16}\\b(apply|now|anymore|here)\\b",
        "\\bthis\\s+is\\s+(a\\s+)?(hypothetical|fictional|simulation|test)\\b[^.\\n]{0,30}\\b(no\\s+rules?|nothing\\s+is\\s+forbidden|anything\\s+is\\s+allowed|no\\s+restrictions?)\\b",
        "\\b(for\\s+)?(educational|research|academic)\\s+purposes?\\s+only\\b[^.\\n]{0,40}\\b(bypass|ignore|disable|circumvent)\\b",
        "\\b(bypass|circumvent|disable|turn\\s+off)\\b[^.\\n]{0,24}\\b(safety|filter|guardrail|restriction|policy|protection)s?\\b",
    ),
    "prompt_leaking": (
        "\\b(repeat|print|show|reveal|output|display|echo|dump)\\b[^.\\n]{0,30}\\b(your|the)\\s+(system\\s+prompt|initial\\s+instructions?|original\\s+instructions?|prompt|instructions?)\\b",
        "\\bwhat\\s+(were|are)\\s+your\\s+(original|initial|system|actual)\\s+(instructions?|prompts?|rules?)\\b",
        "\\b(print|show|repeat|output|reveal)\\b[^.\\n]{0,20}\\beverything\\s+(above|before)\\b",
        "\\b(verbatim|word\\s+for\\s+word|exactly)\\b[^.\\n]{0,24}\\b(system\\s+prompt|your\\s+instructions?)\\b",
    ),
    "indirect": (
        "<!--[^>]{0,80}\\b(ai|assistant|agent|llm|system)\\b\\s*[:,]",
        "\\[\\[\\s*(system|assistant|ai|agent)\\s*[:|]",
        "<\\s*(system|assistant)\\s*>",
        "<\\s*/?\\s*(instruction|system_prompt)s?\\s*>",
        "\\bwhen\\s+(summariz|process|read|analyz)\\w*\\b[^.\\n]{0,40}\\b(also\\s+)?(run|execute|exec|eval|curl|wget|send|post|email|upload)\\b",
        "\\|\\s*(sh|bash|zsh|python)\\b",
    ),
}

BLOCKING_GROUPS: frozenset[str] = frozenset({"override", "token_smuggling", "indirect"})

_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    group: tuple((re.compile(p, re.IGNORECASE) for p in patterns))
    for group, patterns in INJECTION_GROUPS.items()
}

CAPABILITY_KEYS: frozenset[str] = frozenset(
    {"tools", "providers", "paths", "env", "network"}
)

EMPTY_MEANS = "deny"

READ_ONLY_PROVIDERS: frozenset[str] = frozenset(
    {
        "notify",
        "send-message",
        "create-task",
        "call-app-route",
        "knowledge-retrieve",
        "knowledge-health",
        "knowledge-gaps",
        "artifact_inspect",
        "selfqa-triage",
        "check-work",
    }
)

WRITE_CAPABLE_PROVIDERS: frozenset[str] = frozenset(
    {
        "bash",
        "run-script",
        "run-prompt",
        "invoke-agent",
        "run-workflow",
        "knowledge-persist",
        "knowledge-consolidate",
        "artifact-update",
        "render-report",
        "notification-digest",
        "usage-recap",
        "self-remediation",
        "source-digest",
        "identity-report",
        "knowledge-propose",
        "knowledge-report",
        "selfqa-file-finding",
        "selfqa-evidence",
        "selfqa-commit-watch",
        "triage-digest",
        "inbox-op",
        "browse",
        "net-fetch",
        "best-of-n",
        "second-opinion",
        "a2a-call",
    }
)

APP_DELIVERED_PROVIDERS: frozenset[str] = frozenset({"a2a-call"})

UNTRUSTED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "web_watch": ("new_items",),
    "file": ("changed", "paths", "added", "modified"),
    "event": ("value",),
    "webhook": ("body", "text", "payload"),
    "inbox": ("body", "text"),
}

_ALWAYS_UNTRUSTED: tuple[str, ...] = ("payload_text", "content", "message", "summary")
