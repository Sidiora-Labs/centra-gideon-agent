"""Bounded static content scan entry point, without gateway startup."""

import json
import sys

SCAN_WINDOW = 256 * 1024
WHOLE_FILE_BYTES = 2 * SCAN_WINDOW


def main() -> int:
    from gideon.security.supply_chain import SkillScanner, Verdict

    window = sys.stdin.buffer.read(WHOLE_FILE_BYTES + 2)
    if len(window) > WHOLE_FILE_BYTES + 1:
        return 2
    text = window.decode("utf-8", errors="replace")
    scanner = SkillScanner()
    dangerous = any(
        scanner.scan_text(text, surface=surface).verdict is Verdict.DANGEROUS
        for surface in ("script", "manifest")
    )
    sys.stdout.write(json.dumps({"dangerous": dangerous}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
