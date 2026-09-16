"""Closed boolean expressions over workflow bindings."""

from __future__ import annotations

from typing import Any

from gideon.automation.workflows.bindings import (
    BindingContext,
    BindingError,
    resolve_expr,
)

_LITERALS: dict[str, Any] = {"true": True, "false": False, "null": None, "none": None}
_COMPARISONS = ("==", "!=")


class ConditionScanner:
    def __init__(self, text: str):
        self.text = text

    def delimiters(self, tokens):
        position, depth, quoted = 0, 0, ""
        while position < len(self.text):
            character = self.text[position]
            if quoted:
                if character == quoted:
                    quoted = ""
            elif character in "\"'":
                quoted = character
            else:
                depth += int(character == "(") - int(character == ")")
                if depth == 0:
                    token = next(
                        (
                            candidate
                            for candidate in tokens
                            if self.text.startswith(candidate, position)
                        ),
                        None,
                    )
                    if token is not None:
                        yield position, token
                        position += len(token)
                        continue
            position += 1

    def split(self, token: str) -> list[str]:
        pieces, start = [], 0
        for position, found in self.delimiters((token,)):
            pieces.append(self.text[start:position])
            start = position + len(found)
        return [*pieces, self.text[start:]]

    def closing(self) -> int:
        depth, quoted = 0, ""
        for position, character in enumerate(self.text):
            if quoted:
                if character == quoted:
                    quoted = ""
                continue
            if character in "\"'":
                quoted = character
                continue
            depth += int(character == "(") - int(character == ")")
            if character == ")" and not depth:
                return position
        return -1


class ConditionEvaluation:
    def __init__(self, context: BindingContext, expression: str):
        self.context, self.expression = context, expression

    def combine(self, text: str, level: int = 0) -> bool:
        if level == 2:
            return self.unary(text)
        token, aggregate = (("||", any), ("&&", all))[level]
        parts = _split_top(text, token)
        values = [self.combine(part, level + 1) for part in parts]
        return aggregate(values)

    def unary(self, text: str) -> bool:
        body, inverted = text.strip(), False
        while body.startswith("!") and not body.startswith("!="):
            inverted = not inverted
            body = body[1:].strip()
        value = self.leaf(body)
        return not value if inverted else value

    def leaf(self, text: str) -> bool:
        body = text.strip()
        if not body:
            raise BindingError("empty condition operand", self.expression)
        if body.startswith("(") and _matching_paren(body) == len(body) - 1:
            return self.combine(body[1:-1])
        operation, left, right = _split_comparison(body)
        if operation is None:
            return truthy(self.operand(body))
        operands = [self.operand(part) for part in (left, right)]
        if operation not in _COMPARISONS:
            raise BindingError(f"unsupported comparison {operation!r}", self.expression)
        equal = _equal(*operands)
        return equal if operation == "==" else not equal

    def operand(self, text: str) -> Any:
        value = text.strip()
        if not value:
            raise BindingError("empty condition operand", self.expression)
        if value.startswith("{{") and value.endswith("}}"):
            return resolve_expr(value[2:-2].strip(), self.context)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            return value[1:-1]
        if value.lower() in _LITERALS:
            return _LITERALS[value.lower()]
        for parse in (int, float):
            try:
                return parse(value)
            except ValueError:
                continue
        return resolve_expr(value, self.context)


def truthy(value: Any) -> bool:
    return (
        value.strip().lower() not in ("", "false", "0", "no", "null", "none")
        if isinstance(value, str)
        else bool(value)
    )


def evaluate(expr: str, ctx: BindingContext) -> bool:
    expression = expr or ""
    if not expression.strip():
        raise BindingError("empty condition", expression)
    return ConditionEvaluation(ctx, expression).combine(expression.strip())


def _or(text: str, ctx: BindingContext, whole: str) -> bool:
    return ConditionEvaluation(ctx, whole).combine(text)


def _and(text: str, ctx: BindingContext, whole: str) -> bool:
    return ConditionEvaluation(ctx, whole).combine(text, 1)


def _unary(text: str, ctx: BindingContext, whole: str) -> bool:
    return ConditionEvaluation(ctx, whole).unary(text)


def _leaf(text: str, ctx: BindingContext, whole: str) -> bool:
    return ConditionEvaluation(ctx, whole).leaf(text)


def _equal(left: Any, right: Any) -> bool:
    for boolean, text in ((left, right), (right, left)):
        if isinstance(boolean, bool) and isinstance(text, str):
            return boolean is _LITERALS.get(text.strip().lower(), object())
    return bool(left == right)


def _operand(text: str, ctx: BindingContext, whole: str) -> Any:
    return ConditionEvaluation(ctx, whole).operand(text)


def _split_top(text: str, token: str) -> list[str]:
    return ConditionScanner(text).split(token)


def _split_comparison(text: str) -> tuple[str | None, str, str]:
    first = next(ConditionScanner(text).delimiters(_COMPARISONS), None)
    if first is None:
        return None, "", ""
    position, token = first
    return token, text[:position], text[position + len(token) :]


def _matching_paren(text: str) -> int:
    return ConditionScanner(text).closing()
