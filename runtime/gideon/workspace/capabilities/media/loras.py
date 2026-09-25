import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path

from .sketches import SketchError

DTYPES = {
    "F64": 8,
    "F32": 4,
    "F16": 2,
    "BF16": 2,
    "I64": 8,
    "I32": 4,
    "I16": 2,
    "I8": 1,
    "U8": 1,
    "BOOL": 1,
}
MAX_BYTES = 512 * 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SketchError("Duplicate tensor metadata key")
        result[key] = value
    return result


class LoraCatalog:
    def __init__(self, root):
        self.root = Path(root).absolute()

    def _path(self, adapter_id):
        if not isinstance(adapter_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}\.safetensors", adapter_id
        ):
            raise SketchError("Invalid adapter ID")
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise SketchError("Adapter root must not contain symbolic links")
        return self.root / adapter_id

    def get(self, adapter_id, base_model=""):
        path = self._path(adapter_id)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as exc:
            raise SketchError("Adapter file is unavailable", 404) from exc
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise SketchError("Adapter is not a supported bounded regular file")
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 8 < before.st_size <= MAX_BYTES:
                raise SketchError("Adapter is not a supported bounded regular file")
            prefix = stream.read(8)
            length = int.from_bytes(prefix, "little")
            if not 2 <= length <= min(1024 * 1024, before.st_size - 8):
                raise SketchError("Invalid tensor header length")
            header_bytes = stream.read(length)
            try:
                header = json.loads(header_bytes, object_pairs_hook=unique_object)
            except (ValueError, UnicodeError) as exc:
                raise SketchError("Invalid tensor header JSON") from exc
            if not isinstance(header, dict):
                raise SketchError("Invalid tensor header object")
            metadata = header.pop("__metadata__", {})
            if not isinstance(metadata, dict) or any(
                not isinstance(value, str) for value in metadata.values()
            ):
                raise SketchError("Invalid tensor metadata")
            if not header or len(header) > 10000:
                raise SketchError("Invalid tensor count")
            offsets = []
            for descriptor in header.values():
                if not isinstance(descriptor, dict):
                    raise SketchError("Invalid tensor descriptor")
                shape, pair = descriptor.get("shape"), descriptor.get("data_offsets")
                dtype = descriptor.get("dtype")
                width = DTYPES.get(dtype) if isinstance(dtype, str) else None
                if (
                    not width
                    or not isinstance(shape, list)
                    or len(shape) > 8
                    or any(type(size) is not int or size < 0 for size in shape)
                ):
                    raise SketchError("Unsupported tensor shape or dtype")
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or any(type(value) is not int or value < 0 for value in pair)
                    or pair[1] - pair[0] != math.prod(shape) * width
                ):
                    raise SketchError("Invalid tensor offsets")
                offsets.append(pair)
            end = 0
            for start, stop in sorted(offsets):
                if start != end:
                    raise SketchError(
                        "Tensor payload contains overlapping or missing regions"
                    )
                end = stop
            if end != before.st_size - 8 - length:
                raise SketchError("Tensor payload is truncated or has trailing data")
            names = list(header)
            if not any(
                "lora_A" in name or "lora_down" in name for name in names
            ) or not any("lora_B" in name or "lora_up" in name for name in names):
                raise SketchError(
                    "Tensor names do not describe a supported LoRA layout"
                )
            digest = hashlib.sha256(prefix + header_bytes)
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (
                after.st_size,
                after.st_mtime_ns,
            ):
                raise SketchError("Adapter changed during discovery", 409)
        declared = metadata.get("base_model") or metadata.get("ss_sd_model_name") or ""
        state = (
            "unknown"
            if not declared or not base_model
            else "metadata_match" if declared == base_model else "metadata_mismatch"
        )
        return dict(
            id=adapter_id,
            sha256=digest.hexdigest(),
            bytes=before.st_size,
            tensor_count=len(header),
            base_model=declared[:200],
            compatibility=state,
            effect_verified=False,
            trigger_words=metadata.get("trigger_words", "")[:500],
        )

    def list(self, base_model=""):
        if not self.root.exists():
            return {
                "items": [],
                "invalid": [],
                "truncated": False,
                "effect_verified": False,
            }
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise SketchError("Adapter root must not contain symbolic links")
        paths = sorted(self.root.glob("*.safetensors"))
        items, invalid, consumed = [], [], 0
        for path in paths[:32]:
            try:
                item = self.get(path.name, base_model)
                items.append(item)
                consumed += item["bytes"]
            except SketchError as exc:
                invalid.append({"id": path.name, "error": str(exc)})
            if consumed >= MAX_BYTES:
                break
        return {
            "items": items,
            "invalid": invalid,
            "truncated": len(items) + len(invalid) < len(paths),
            "effect_verified": False,
        }

    def resolve(self, selection, base_model):
        if not isinstance(selection, dict) or set(selection) != {
            "id",
            "sha256",
            "scale",
        }:
            raise SketchError("Adapter selection requires id, sha256 and scale")
        scale = selection["scale"]
        if (
            isinstance(scale, bool)
            or not isinstance(scale, (int, float))
            or not math.isfinite(scale)
            or not -2 <= scale <= 2
        ):
            raise SketchError("Adapter scale must be between -2 and 2")
        item = self.get(selection["id"], base_model)
        if selection["sha256"] != item["sha256"]:
            raise SketchError("Adapter bytes changed; select the current adapter", 409)
        if item["compatibility"] != "metadata_match":
            raise SketchError("Adapter base-model metadata is missing or mismatched")
        return {
            "path": str(self._path(selection["id"])),
            "scale": scale,
            "sha256": item["sha256"],
        }

    def stage(self, selection, base_model, destination):
        resolved = self.resolve(selection, base_model)
        fd = os.open(resolved["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise SketchError("Adapter is not a regular file")
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb") as source, open(destination, "xb") as target:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise SketchError("Adapter is not a regular file")
            copied = 0
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_BYTES:
                    raise SketchError("Adapter exceeds the supported size")
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != selection["sha256"]:
            Path(destination).unlink(missing_ok=True)
            raise SketchError("Adapter changed during staging", 409)
        return {
            "path": str(destination),
            "scale": resolved["scale"],
            "sha256": resolved["sha256"],
        }
