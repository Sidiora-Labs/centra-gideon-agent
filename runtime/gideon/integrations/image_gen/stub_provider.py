"""The existing opt-in deterministic image preview engine."""

from __future__ import annotations

import base64
import hashlib
import struct
import zlib
from typing import Any

from gideon.integrations.image_gen.provider import (
    ImageGenModel,
    ImageGenProvider,
    ImageResult,
)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
    return b"".join(
        (len(payload).to_bytes(4, "big"), kind, payload, checksum.to_bytes(4, "big"))
    )


def _solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    row = b"\x00" + bytes(rgb) * size
    compressor = zlib.compressobj(level=9)
    encoded = bytearray()
    for _ in range(size):
        encoded.extend(compressor.compress(row))
    encoded.extend(compressor.flush())
    chunks = (
        (b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)),
        (b"IDAT", bytes(encoded)),
        (b"IEND", b""),
    )
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        _png_chunk(kind, payload) for kind, payload in chunks
    )


def _color_for(text: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(text.encode()).digest()
    return digest[0], digest[1], digest[2]


def _preview(prompt: str, revised: str = "") -> list[ImageResult]:
    encoded = base64.b64encode(_solid_png(_color_for(prompt))).decode()
    return [ImageResult(b64=encoded, mime="image/png", revised_prompt=revised)]


class StubImageProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return "stub"

    @property
    def display_name(self) -> str:
        return "Stub (offline test image)"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[ImageGenModel]:
        from gideon.integrations.image_gen.registry import active_image_gen

        selected = active_image_gen()
        active = (
            selected is not None
            and selected[0].name == self.name
            and selected[1] == "stub-1"
        )
        return [
            ImageGenModel(
                name="stub-1",
                description="Deterministic offline test image",
                sizes=["64x64"],
                supports_edit=True,
                downloaded=True,
                active=active,
            )
        ]

    async def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        return _preview(prompt, f"stub render of: {prompt}")

    async def edit(
        self,
        prompt: str,
        *,
        source_image: str,
        mask: str = "",
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        return _preview(f"{prompt}::edit")
