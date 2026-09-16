"""Destination admission and provenance publication for selected imports."""


class ImportLedger:
    @staticmethod
    def empty():
        return {"version": 1, "items": {}}

    @classmethod
    def load(cls, api):
        path = api.state_path()
        if path.is_file():
            try:
                contents = api.json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                api.logger.warning(
                    "unreadable onboarding import state at %s — treating as empty", path
                )
            else:
                if isinstance(contents, dict) and isinstance(
                    contents.get("items"), dict
                ):
                    return contents
        return cls.empty()

    @staticmethod
    def record(api, item, destination):
        state = api._load_state()
        entry = dict(
            source=item.source,
            category=item.category.value,
            key=item.key,
            destination=destination,
            at=api.datetime.now(tz=api.timezone.utc).isoformat(),
        )
        state["items"][item.fingerprint] = entry
        path = api.state_path()
        FileAdmission.publish(
            api, path, api.json.dumps(state, indent=2, sort_keys=True) + "\n"
        )


class FileAdmission:
    @staticmethod
    def compare(path, expected):
        if not path.is_file():
            return None
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError:
            actual = ""
        return actual == expected

    @staticmethod
    def publish(api, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        api.atomic_write(path, text)

    @staticmethod
    def result(api, item, current, destination, unchanged, conflict):
        if current is None:
            return None
        verdict = api.WriteOutcome.EXISTING if current else api.WriteOutcome.CONFLICT
        return api._result(
            item, verdict, destination, unchanged if current else conflict
        )


class WriteReceipt:
    @staticmethod
    def audit(api, operation, outcome, resources, error):
        try:
            from gideon.security.sel import sel

            fields = dict(
                caller="onboarding.import",
                operation=operation,
                outcome=outcome,
                source="dashboard",
                resources=resources,
                error=error,
            )
            sel().log_api_access(**fields)
        except Exception:
            api.logger.debug(
                "onboarding import SEL audit failed for %s", operation, exc_info=True
            )

    @staticmethod
    def create(api, item, outcome, destination, detail):
        api._audit(
            f"import.{item.category.value}",
            outcome.value,
            resources=f"{item.source}:{item.category.value}:{item.key}",
        )
        identity = dict(
            fingerprint=item.fingerprint,
            source=item.source,
            category=item.category,
            key=item.key,
        )
        return api.WriteResult(
            **identity, outcome=outcome, destination=destination, detail=detail
        )


class DocumentCommit:
    @staticmethod
    def memory(api, item):
        from gideon.cognition.memory import MemoryJournal
        from gideon.cognition.memory_record import MemoryKind, MemoryRecord
        from gideon.integrations.memory_providers.filesystem import (
            FilesystemMemoryProvider,
        )

        document = api._memory_doc_path(item)
        destination = api._rel_to_home(document)
        content = item.text + ("" if item.text.endswith("\n") else "\n")
        prior = FileAdmission.compare(document, content)
        refusal = FileAdmission.result(
            api,
            item,
            prior,
            destination,
            "already imported, unchanged",
            "an imported document of this name already exists with different content; the existing document was kept",
        )
        if refusal is not None:
            return refusal
        memory = MemoryJournal()
        memory.init()
        FileAdmission.publish(api, document, content)
        summary = api.re.sub(r"\s+", " ", item.text).strip()[: api._SUMMARY_CHARS]
        record = MemoryRecord(
            id=f"import:{item.source}:{item.fingerprint}",
            kind=MemoryKind.NOTE,
            text=f"Imported from {item.source} ({item.key}): {summary}",
            source=f"onboarding_import:{item.source}",
            category=item.category.value,
        )
        FilesystemMemoryProvider(memory).put([record])
        api._record(item, destination)
        return api._result(item, api.WriteOutcome.IMPORTED, destination)

    @staticmethod
    def settings(api, item):
        path = api.staged_settings_path(item.source, item.key)
        destination = api._rel_to_home(path)
        content = (
            api.json.dumps(
                {"source": item.source, "key": item.key, "settings": item.payload},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        prior = FileAdmission.compare(path, content)
        refusal = FileAdmission.result(
            api,
            item,
            prior,
            destination,
            "already staged for review",
            "different settings from this source are already staged for review; the staged copy was kept",
        )
        if refusal is not None:
            return refusal
        FileAdmission.publish(api, path, content)
        api._record(item, destination)
        return api._result(
            item,
            api.WriteOutcome.IMPORTED,
            destination,
            "staged for review — not applied to config",
        )

    @staticmethod
    def server(api, item):
        path = api.mcp_config_path()
        destination = f"{api._rel_to_home(path)}#mcpServers.{item.key}"
        configuration = {}
        if path.is_file():
            try:
                decoded = api.json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                api.logger.warning("unreadable %s — refusing to overwrite it", path)
                return api._result(
                    item,
                    api.WriteOutcome.CONFLICT,
                    destination,
                    "the existing mcp.json could not be parsed; it was left untouched",
                )
            if isinstance(decoded, dict):
                configuration = decoded
        servers = configuration.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        existing = servers.get(item.key)
        if isinstance(existing, dict):
            return FileAdmission.result(
                api,
                item,
                existing == item.payload,
                destination,
                "already configured identically",
                "an MCP server of this name is already configured differently; the existing entry was kept",
            )
        servers[item.key] = dict(item.payload)
        configuration["mcpServers"] = servers
        FileAdmission.publish(
            api, path, api.json.dumps(configuration, indent=2, sort_keys=True) + "\n"
        )
        api._record(item, destination)
        return api._result(item, api.WriteOutcome.IMPORTED, destination)


class SkillCommit:
    @staticmethod
    def files(api, directory):
        readable = (
            path
            for path in sorted(directory.rglob("*"))
            if path.is_file() and not api.refuses(path)
        )
        captured = []
        for path in readable:
            try:
                entry = api.read_skill_file_entry(
                    path, path.relative_to(directory).as_posix()
                )
            except OSError:
                api.logger.warning(
                    "skipping unreadable file in imported skill %s", directory.name
                )
            else:
                captured.append(entry)
        return captured

    @staticmethod
    def install(api, item):
        from gideon.extensions.skills.marketplace import (
            SkillInstallRefused,
            install_scanned,
        )

        target = api.imported_skills_dir(item.source)
        destination = api._rel_to_home(target / item.key)
        source = api.Path(item.path) if item.path else None
        refusal = ""
        if source is None or not source.is_dir():
            refusal = "source skill directory is missing"
        elif api.refuses(source):
            refusal = "source path is a sensitive location"
        if refusal:
            return api._result(item, api.WriteOutcome.REJECTED, destination, refusal)
        if (target / item.key).exists():
            return FileAdmission.result(
                api,
                item,
                api._ours(item.fingerprint),
                destination,
                "already imported",
                "a skill of this name already exists here and was not written by an import; it was kept",
            )
        target.mkdir(parents=True, exist_ok=True)
        marketplace = api._ImportedSkillsMarketplace(source)
        try:
            install_scanned(
                marketplace, f"import:{item.source}", item.key, target, force=False
            )
        except SkillInstallRefused as exc:
            refusal = f"the skill supply-chain scan refused this skill: {exc}"
        except (ValueError, OSError) as exc:
            refusal = f"could not install: {exc}"
        if refusal:
            return api._result(item, api.WriteOutcome.REJECTED, destination, refusal)
        api._record(item, destination)
        return api._result(item, api.WriteOutcome.IMPORTED, destination)

    @staticmethod
    def detail(api, market, skill_id):
        identity = {"id": skill_id, "name": market._skill_dir.name}
        identity["files"] = api._imported_skills_marketplace_files(market._skill_dir)
        return api.SkillDetail(**identity, audit_status="pass")


class ImportBatch:
    @staticmethod
    def dispatch(api, item):
        if item.category not in api._WRITERS:
            raise KeyError(f"no writer for import category {item.category!r}") from None
        return api._WRITERS[item.category](item)

    @staticmethod
    def write(api, items):
        return list(map(api.write_item, items))

    @staticmethod
    def report(api, items, secrets_skipped):
        results = api.write_items(items)
        totals = sum(item.redactions for item in items)
        return api.ImportReport(
            results=results, secrets_skipped=secrets_skipped, redactions=totals
        )
