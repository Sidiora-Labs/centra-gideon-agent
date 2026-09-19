"""Read-only source survey stages and secret-eliding tree projection."""


class SecretTree:
    @classmethod
    def project(cls, api, value):
        if not isinstance(value, (dict, list)):
            return value, 0
        if isinstance(value, dict):
            clean: dict = {}
            removed = 0
            for key, child in value.items():
                if isinstance(key, str) and api._SECRET_KEY_RE.search(key):
                    removed += 1
                    continue
                filtered, count = cls.project(api, child)
                removed += count
                clean[key] = filtered
            return clean, removed
        clean_list = []
        removed = 0
        for child in value:
            filtered, count = cls.project(api, child)
            removed += count
            clean_list.append(filtered)
        return clean_list, removed

    @staticmethod
    def text(api, text):
        counts = []
        for redact in (api.redact_credentials, api.redact_exfiltration_urls):
            text, matches = redact(text)
            counts.append(len(matches))
        return text, sum(counts)

    @staticmethod
    def read(api, path, *, structured):
        if api.refuses(path):
            return (None, 1) if structured else ("", 0, 1)
        if structured:
            try:
                content = path.read_text(encoding="utf-8")
                parsed = api.json.loads(content)
            except (OSError, ValueError):
                return None, 0
            return api.strip_secrets(parsed)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "", 0, 0
        text, count = api.safe_text(content)
        return text, count, 0


class SourceCapture:
    def __init__(self, api, base, result):
        self.api, self.base, self.result = api, base, result

    @staticmethod
    def root(api):
        configured = api.os.environ.get(api.ENV_VAR, "").strip()
        return api.Path(configured or api.DEFAULT_ROOT).expanduser()

    @classmethod
    def survey(cls, api, root, phases):
        base = api.resolve_root() if root is None else api.Path(root).expanduser()
        result = api.ScanResult(
            source=api.NAME,
            display_name=api.DISPLAY_NAME,
            root=str(base),
            present=base.is_dir(),
        )
        if result.present:
            for phase in phases:
                getattr(api, phase)(base, result)
            result.note_withheld()
        return result

    def text_files(self, entries, category):
        for path, key, title in entries:
            if path.is_file():
                text, redactions, skipped = self.api.read_text_safely(path)
                self.result.secrets_skipped += skipped
                if text.strip():
                    self.result.redactions += redactions
                    self.result.items.append(
                        self.api.ImportItem(
                            source=self.api.NAME,
                            category=category,
                            key=key,
                            title=title,
                            text=text,
                            redactions=redactions,
                        )
                    )

    def instructions(self):
        self.text_files(
            ((self.base / name, name, name) for name in self.api._INSTRUCTION_FILES),
            self.api.ImportCategory.INSTRUCTIONS,
        )

    def memories(self):
        directory = self.base / self.api._MEMORIES_DIR
        if directory.is_dir():
            files = (
                (path, f"{self.api._MEMORIES_DIR}/{path.name}", path.stem)
                for path in sorted(directory.glob("*.md"))
            )
            self.text_files(files, self.api.ImportCategory.MEMORIES)

    def structured(self, filename):
        path = self.base / filename
        if not path.is_file():
            return None
        content, skipped = self.api.read_json_safely(path)
        self.result.secrets_skipped += skipped
        return content

    def servers(self, servers):
        if not isinstance(servers, dict):
            return
        for name, specification in sorted(servers.items()):
            if not isinstance(specification, dict) or not str(name).strip():
                continue
            self.result.items.append(
                self.api.ImportItem(
                    source=self.api.NAME,
                    category=self.api.ImportCategory.MCP_SERVERS,
                    key=str(name),
                    title=str(name),
                    payload=specification,
                )
            )

    def mcp(self):
        content = self.structured(self.api._MCP_FILE)
        if isinstance(content, dict):
            self.servers(content.get("mcpServers"))

    def settings(self, filename, content):
        if isinstance(content, dict) and content:
            self.result.items.append(
                self.api.ImportItem(
                    source=self.api.NAME,
                    category=self.api.ImportCategory.SETTINGS,
                    key=filename,
                    title=f"{self.api.DISPLAY_NAME} settings",
                    payload=content,
                )
            )

    def skills(self):
        directory = self.base / self.api._SKILLS_DIR
        if not directory.is_dir():
            return
        eligible = sorted(path for path in directory.iterdir() if path.is_dir())
        for skill in eligible:
            if (skill / "SKILL.md").is_file():
                self.result.secrets_skipped += sum(
                    1
                    for path in skill.rglob("*")
                    if path.is_file() and self.api.refuses(path)
                )
                self.result.items.append(
                    self.api.ImportItem(
                        source=self.api.NAME,
                        category=self.api.ImportCategory.SKILLS,
                        key=skill.name,
                        title=skill.name,
                        path=str(skill),
                    )
                )

    def root_secrets(self):
        known = {
            *self.api._INSTRUCTION_FILES,
            self.api._MCP_FILE,
            self.api._SETTINGS_FILE,
        }
        withheld = (
            path
            for path in sorted(self.base.iterdir())
            if path.is_file() and path.name not in known and self.api.refuses(path)
        )
        for _ in withheld:
            self.result.secrets_skipped += 1

    def codex_config(self):
        candidates = ((self.api._TOML_CONFIG, True), (self.api._JSON_CONFIG, False))
        for filename, toml in candidates:
            path = self.base / filename
            if not path.is_file():
                continue
            if not toml:
                content, skipped = self.api.read_json_safely(path)
            elif self.api.refuses(path):
                content, skipped = None, 1
            else:
                try:
                    with path.open("rb") as stream:
                        parsed = self.api.tomllib.load(stream)
                except (OSError, self.api.tomllib.TOMLDecodeError):
                    return None, filename
                content, skipped = self.api.strip_secrets(parsed)
            self.result.secrets_skipped += skipped
            return content, filename
        return None, ""

    def partition_config(self, config, name):
        settings = dict(config)
        for key in self.api._MCP_KEYS:
            self.servers(settings.pop(key, None))
        self.settings(name, settings)

    @classmethod
    def codex(cls, api, root):
        base = api.resolve_root() if root is None else api.Path(root).expanduser()
        result = api.ScanResult(
            source=api.NAME,
            display_name=api.DISPLAY_NAME,
            root=str(base),
            present=base.is_dir(),
        )
        if not result.present:
            return result
        cls(api, base, result).instructions()
        content, name = api._read_config(base, result)
        if isinstance(content, dict) and content:
            api._scan_config(content, name, result)
        result.note_withheld()
        return result
