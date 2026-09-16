"""Import record projections, selection plans and source descriptors."""


class RecordProjection:
    plans = {
        "item": (
            "fingerprint",
            "source",
            ("category", lambda row: row.category.value),
            "key",
            ("title", lambda row: row.title or row.key),
            "redactions",
        ),
        "scan": (
            "source",
            "display_name",
            "root",
            "present",
            ("counts", lambda row: row.counts()),
            ("items", lambda row: [item.to_dict() for item in row.items]),
            "secrets_skipped",
            "redactions",
            ("notes", lambda row: list(row.notes)),
        ),
        "write": (
            "fingerprint",
            "source",
            ("category", lambda row: row.category.value),
            "key",
            ("outcome", lambda row: row.outcome.value),
            "destination",
            "detail",
        ),
        "report": (
            ("counts", lambda row: row.counts()),
            ("results", lambda row: [item.to_dict() for item in row.results]),
            "secrets_skipped",
            "redactions",
            ("notes", lambda row: list(row.notes)),
        ),
        "source": ("name", "display_name", "env_var", "default_root"),
    }

    @classmethod
    def render(cls, kind, record):
        result = {}
        for field in cls.plans[kind]:
            name, value = (
                (field, getattr(record, field))
                if isinstance(field, str)
                else (field[0], field[1](record))
            )
            result[name] = value
        return result

    @staticmethod
    def tally(records, attribute, vocabulary):
        counts = {}
        for record in records:
            name = getattr(record, attribute).value
            counts[name] = counts.get(name, 0) + 1
        return {entry.value: counts.get(entry.value, 0) for entry in vocabulary}

    @staticmethod
    def matching(records, attribute, selected):
        return list(
            filter(lambda record: getattr(record, attribute) is selected, records)
        )

    @staticmethod
    def withheld(secrets_skipped, redactions):
        patterns = (
            (
                secrets_skipped,
                "credential value{plural} or file{plural}",
                "skipped and not imported.",
            ),
            (
                redactions,
                "credential-like string{plural}",
                "redacted from imported text.",
            ),
        )
        result = []
        for count, subject, action in patterns:
            if count:
                plural = "" if count == 1 else "s"
                verb = "was" if count == 1 else "were"
                result.append(
                    f"{count} {subject.format(plural=plural)} {verb} {action}"
                )
        return result


class SelectionPlan:
    def __init__(self, category_type, categories, sources):
        self.categories = (
            None
            if categories is None
            else set(
                value if isinstance(value, category_type) else category_type(value)
                for value in categories
            )
        )
        self.sources = None if sources is None else set(sources)

    def items(self, results):
        for result in results:
            if self.sources is None or result.source in self.sources:
                yield from (
                    item
                    for item in result.items
                    if self.categories is None or item.category in self.categories
                )

    @staticmethod
    def run(api, results, categories, sources):
        scans = tuple(results)
        selected = api.select_items(scans, categories=categories, sources=sources)
        admitted_sources = set(map(lambda item: item.source, selected))
        withheld = sum(
            result.secrets_skipped
            for result in scans
            if result.source in admitted_sources
        )
        return api.import_report(selected, secrets_skipped=withheld)

    @staticmethod
    def scan(api, roots):
        locations = roots or {}
        scans = []
        for source in api.list_sources():
            scans.append(source.scan(locations.get(source.name)))
        return scans

    @staticmethod
    def existing(api, results):
        known = api.imported_fingerprints()
        found = set()
        for scan in results:
            found.update(item.fingerprint for item in scan.items)
        return found.intersection(known)


class SourceDirectory:
    fields = (
        ("name", "NAME"),
        ("display_name", "DISPLAY_NAME"),
        ("env_var", "ENV_VAR"),
        ("default_root", "DEFAULT_ROOT"),
        ("scan", "scan"),
        ("resolve_root", "resolve_root"),
    )

    @classmethod
    def describe(cls, factory, module):
        return factory(
            **{target: getattr(module, source) for target, source in cls.fields}
        )

    @staticmethod
    def lookup(entries, name):
        if name in entries:
            return entries[name]
        known = ", ".join(sorted(entries))
        raise KeyError(f"unknown import source {name!r} (known: {known})") from None
