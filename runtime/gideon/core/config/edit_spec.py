"""Shared policy dispatch for configuration edits from HTTP and command-line clients."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

__all__ = ["ConfigValueError", "coerce_edit_value"]


class ConfigValueError(ValueError):
    def __init__(self, message: str, resources: str = "", status: int = 400):
        super().__init__(message)
        self.resources = resources
        self.status = status


@dataclass
class EditCandidate:
    path: str
    value: Any
    spec: dict

    def deny(self, message: str, resource: str | None = None, status: int = 400):
        raise ConfigValueError(
            message,
            f"{self.path}={self.value}" if resource is None else resource,
            status,
        )

    def choices(self):
        if self.value not in self.spec["values"]:
            self.deny(f"invalid value, must be one of {self.spec['values']}")
        return self.value

    def numeric(self, integral: bool):
        constructor, description = (
            (int, "an integer") if integral else (float, "a number")
        )
        if self.value is None or isinstance(self.value, bool):
            self.deny(f"must be {description}")
        try:
            self.value = constructor(self.value)
        except (TypeError, ValueError):
            self.deny(f"must be {description}")
        if not integral and not math.isfinite(self.value):
            self.deny("must be a finite number")
        lower = self.spec.get("min", constructor(0))
        upper = self.spec.get("max", constructor(999999))
        if not lower <= self.value <= upper:
            self.deny(f"must be between {lower} and {upper}")
        return self.value

    def boolean(self):
        if not isinstance(self.value, bool):
            self.deny("must be a boolean")
        return self.value

    def duration(self):
        if not isinstance(self.value, str):
            self.deny("must be a duration string like 30d, 12h or 15m")
        if re.fullmatch(r"\d+[mhd]", self.value.strip()) is None:
            self.deny("must be a duration like 30d, 12h or 15m (integer + m/h/d)")
        self.value = self.value.strip()
        if int(self.value[:-1]) <= 0:
            self.deny("must be greater than zero")
        return self.value

    def strings(self):
        if not isinstance(self.value, list) or any(
            not isinstance(item, str) for item in self.value
        ):
            self.deny("must be a list of strings")
        maximum = self.spec.get("max_items", 20)
        if len(self.value) > maximum:
            self.deny(f"must have at most {maximum} items")
        if self.spec.get("each_regex"):
            for pattern in self.value:
                self.regex(pattern)
        return self.value

    def regex(self, pattern: str, resource: str | None = None, operation: str = ""):
        try:
            re.compile(pattern)
        except re.error as failure:
            self.deny(
                f"invalid {operation + ' ' if operation else ''}regex {pattern!r}: {failure}",
                resource,
            )

    def text(self):
        if not isinstance(self.value, str):
            self.deny("must be a string")
        limit = self.spec.get("max_len", 256)
        if len(self.value) > limit:
            self.deny(f"must be at most {limit} characters")
        if "values" in self.spec:
            self.choices()
        choices = self.spec.get("values_fn")
        if choices and self.value not in choices():
            self.deny(f"invalid value for {self.path}")
        normalize = self.spec.get("sanitize")
        return normalize(self.value) if normalize else self.value

    def egress(self):
        if not isinstance(self.value, dict):
            self.deny("must be an object")
        result = {}
        for key in ("allow_hosts", "deny_hosts"):
            hosts = self.value.get(key, [])
            resource = f"{self.path}.{key}"
            if not isinstance(hosts, list) or any(
                not isinstance(host, str) for host in hosts
            ):
                self.deny(f"{key} must be a list of strings", resource)
            if len(hosts) > 100:
                self.deny(f"{key} must have at most 100 items", resource)
            invalid = next(
                (
                    host
                    for host in hosts
                    if any(character in host for character in ("/", ":", " "))
                    or len(host) > 253
                ),
                None,
            )
            if invalid is not None:
                self.deny(
                    f"invalid host {invalid!r} (bare domain/hostname only)", resource
                )
            result[key] = hosts
        private = self.value.get("allow_private", False)
        if not isinstance(private, bool):
            self.deny("allow_private must be a boolean", f"{self.path}.allow_private")
        return {**result, "allow_private": private}

    def records(self, noun: str):
        if not isinstance(self.value, list):
            self.deny("must be a list")
        if len(self.value) > 50:
            self.deny(f"must have at most 50 {noun}s", self.path)
        for index, row in enumerate(self.value):
            resource = f"{self.path}[{index}]"
            if not isinstance(row, dict):
                self.deny(f"each {noun} must be an object", resource)
            yield row, resource

    def projection(self):
        from gideon.integrations.tool_providers.projection import _PROJECTORS

        strategies = set(_PROJECTORS)
        result = []
        for row, resource in self.records("rule"):
            pattern = str(row.get("match_regex", "")).strip()
            strategy = str(row.get("strategy", "")).strip().lower()
            if not pattern:
                self.deny("each rule needs a match_regex", resource)
            if len(pattern) > 500:
                self.deny("match_regex too long (max 500)", resource)
            self.regex(pattern, resource)
            if strategy not in strategies:
                self.deny(f"strategy must be one of {sorted(strategies)}", resource)
            output: dict = {
                "name": str(row.get("name", "")).strip()[:80],
                "match_regex": pattern,
                "strategy": strategy,
            }
            for name in ("head", "tail"):
                try:
                    count = int(row.get(name, 0) or 0)
                except (TypeError, ValueError):
                    self.deny(f"{name} must be an integer", resource)
                if not 0 <= count <= 10000:
                    self.deny(f"{name} must be 0..10000", resource)
                if count:
                    output[name] = count
            for name in ("keep", "skip", "count"):
                operation = str(row.get(name, "") or "").strip()
                if not operation:
                    continue
                if len(operation) > 500:
                    self.deny(f"{name} regex too long (max 500)", resource)
                self.regex(operation, resource, name)
                output[name] = operation
            result.append(output)
        return result

    def https(self):
        if not isinstance(self.value, str):
            self.deny("must be a string")
        self.value = self.value.strip()
        maximum = self.spec.get("max_len", 512)
        if len(self.value) > maximum:
            self.deny(f"must be at most {maximum} characters", self.path)
        if self.value:
            address = urlparse(self.value)
            if address.scheme != "https" or not address.netloc:
                self.deny(
                    "must be a full https:// URL — a push ping must not travel in the clear",
                    self.path,
                )
        return self.value

    def catalogs(self):
        result = []
        for row, resource in self.records("catalog"):
            url = str(row.get("url", "")).strip()
            kind = str(row.get("kind", "index")).strip().lower() or "index"
            if not url:
                self.deny("each catalog needs a url", resource)
            if len(url) > 512:
                self.deny("url too long (max 512)", resource)
            if not url.startswith(("https://", "http://")):
                self.deny("url must be http(s)", resource)
            if kind not in ("index", "tap"):
                self.deny("kind must be 'index' or 'tap'", resource)
            result.append(
                {
                    "name": str(row.get("name", "")).strip()[:80],
                    "url": url,
                    "kind": kind,
                }
            )
        return result


_EDIT_POLICIES = {
    "enum": EditCandidate.choices,
    "int": lambda candidate: candidate.numeric(True),
    "float": lambda candidate: candidate.numeric(False),
    "bool": EditCandidate.boolean,
    "duration": EditCandidate.duration,
    "str_list": EditCandidate.strings,
    "str": EditCandidate.text,
    "egress": EditCandidate.egress,
    "projection_rules": EditCandidate.projection,
    "https_url": EditCandidate.https,
    "skill_catalogs": EditCandidate.catalogs,
}


def coerce_edit_value(path_key: str, value: Any, spec: dict) -> Any:
    candidate = EditCandidate(path_key, value, spec)
    policy = _EDIT_POLICIES.get(spec["type"])
    if policy is None:
        candidate.deny("unsupported config type", status=500)
    return policy(candidate)
