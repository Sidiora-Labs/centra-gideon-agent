"""Generation catalogs, edit-file ownership and the real offline preview codec."""

import asyncio
import base64
import hashlib
import json
import os
import struct
import zlib
from dataclasses import asdict
from functools import partial
from pathlib import Path

import aiohttp
import pytest

from gideon.extensions.providers import media_scanners, use_cases
from gideon.integrations.generation_catalog import model_metadata, reconcile_scanners
from gideon.integrations.image_gen import openai_provider
from gideon.integrations.image_gen import registry as images
from gideon.integrations.image_gen.openai_provider import (
    OpenAIImageProvider,
    _ImageCall,
    decode_b64_image,
)
from gideon.integrations.image_gen.provider import (
    ImageGenError,
    ImageGenModel,
    ImageResult,
)
from gideon.integrations.image_gen.stub_provider import StubImageProvider, _solid_png
from gideon.integrations.media_catalogs import (
    MediaCatalog,
    MediaModel,
    register_media_catalog,
    unregister_media_catalogs,
)
from gideon.integrations.video_gen import registry as videos
from gideon.integrations.video_gen.provider import (
    VideoGenModel,
    VideoGenProvider,
    VideoResult,
)


@pytest.fixture
def generation_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GIDEON_IMAGE_GEN_STUB", raising=False)
    monkeypatch.setattr(images, "_providers", {})
    monkeypatch.setattr(images, "_auto_registered", False)
    monkeypatch.setattr(images, "_scanner_names", set())
    monkeypatch.setattr(videos, "_providers", {})
    monkeypatch.setattr(videos, "_scanner_names", set())
    monkeypatch.setattr(media_scanners, "_scanners", {})
    (tmp_path / "config.json").write_text('{"providers": []}')
    return tmp_path


def _config(home, *names):
    entries = [
        {
            "name": name,
            "type": "openai",
            "model": "",
            "options": {"api_key": "", "endpoint": "http://localhost:1/v1"},
        }
        for name in names
    ]
    (home / "config.json").write_text(json.dumps({"providers": entries}))


def _png_pixels(raw):
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    position = 8
    chunks = []
    while position < len(raw):
        size = int.from_bytes(raw[position : position + 4], "big")
        tag = raw[position + 4 : position + 8]
        data = raw[position + 8 : position + 8 + size]
        checksum = int.from_bytes(
            raw[position + 8 + size : position + 12 + size], "big"
        )
        assert checksum == zlib.crc32(tag + data) & 0xFFFFFFFF
        chunks.append((tag, data))
        position += size + 12
    assert position == len(raw)
    assert [tag for tag, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    width, height, bits, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    assert (bits, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)
    pixels = zlib.decompress(chunks[1][1])
    assert len(pixels) == height * (width * 3 + 1)
    rows = [
        pixels[offset : offset + width * 3 + 1]
        for offset in range(0, len(pixels), width * 3 + 1)
    ]
    assert all(row[0] == 0 for row in rows)
    return width, height, [row[1:] for row in rows]


@pytest.mark.parametrize("size", [1, 7, 64])
def test_png_encoder_outputs_decodable_chunks_and_exact_pixels(size, tmp_path):
    data = _solid_png((0, 127, 255), size=size)
    path = tmp_path / "preview.png"
    path.write_bytes(data)
    width, height, rows = _png_pixels(path.read_bytes())
    assert (width, height) == (size, size)
    assert rows == [bytes((0, 127, 255)) * size] * size
    assert decode_b64_image(base64.b64encode(data).decode()) == data


@pytest.mark.asyncio
async def test_existing_preview_keeps_single_result_and_deterministic_edit_colors():
    provider = StubImageProvider()
    prompt = "a retained preview"
    generated = await provider.generate(prompt, n=5, size="2048x2048", model="ignored")
    edited = await provider.edit(
        prompt, source_image="absent.png", mask="absent-mask.png", n=4
    )
    repeated = await provider.generate(prompt)
    assert len(generated) == len(edited) == 1
    assert generated[0].b64 == repeated[0].b64
    assert generated[0].revised_prompt == f"stub render of: {prompt}"
    assert edited[0].revised_prompt == ""
    assert generated[0].b64 != edited[0].b64
    for result, seed in ((generated[0], prompt), (edited[0], prompt + "::edit")):
        width, height, rows = _png_pixels(decode_b64_image(result.b64))
        assert (width, height) == (64, 64)
        assert rows[0] == hashlib.sha256(seed.encode()).digest()[:3] * 64
        assert result.mime == "image/png"


@pytest.mark.asyncio
async def test_preview_env_gate_requires_refresh_and_exposes_real_model(
    generation_home, monkeypatch
):
    images._ensure_registered()
    assert images.get_provider("stub") is None
    monkeypatch.setenv("GIDEON_IMAGE_GEN_STUB", "1")
    images._ensure_registered()
    assert images.get_provider("stub") is None
    images.refresh_providers()
    use_cases.save_active_models({"image_gen": ["stub:stub-1"]})
    provider, model = images.active_image_gen()
    assert isinstance(provider, StubImageProvider) and model == "stub-1"
    assert images.get_active_provider() is provider
    assert await images.list_models_for_provider("stub") == [
        {
            "name": "stub-1",
            "description": "Deterministic offline test image",
            "sizes": ["64x64"],
            "supports_edit": True,
            "downloaded": True,
            "active": True,
        }
    ]
    assert await images.list_all_providers_info() == [
        {
            "name": "stub",
            "display_name": "Stub (offline test image)",
            "available": True,
            "active": True,
        }
    ]
    assert await provider.download_model("stub-1") is False
    assert await provider.delete_model("stub-1") is False
    monkeypatch.delenv("GIDEON_IMAGE_GEN_STUB")
    images.refresh_providers()
    assert images.active_image_gen() is None


def test_remote_refresh_rebuilds_configured_adapter_and_keeps_unmanaged_provider(
    generation_home,
):
    manual = StubImageProvider()
    images.register_provider(manual)
    remote_bundle = OpenAIImageProvider(provider_name="bundle")
    images.register_provider(remote_bundle)
    _config(generation_home, "account")
    use_cases.save_active_models({"image_gen": ["account:model:edition"]})
    first, model = images.active_image_gen()
    assert model == "model:edition"
    images.refresh_providers()
    assert images.get_provider("stub") is None
    assert images.get_provider("account") is None
    assert images.get_provider("bundle") is remote_bundle
    replacement, _ = images.active_image_gen()
    assert replacement is not first
    images.unregister_provider("absent")


def test_shared_scanner_reconciliation_preserves_manual_entries_and_removes_stale():
    manual = StubImageProvider()
    old = OpenAIImageProvider(provider_name="old")
    first = OpenAIImageProvider(provider_name="account")
    last = OpenAIImageProvider(
        provider_name="account", endpoint="http://localhost:2/v1"
    )
    providers = {manual.name: manual, old.name: old, first.name: first}
    tracked = {"old", "account"}
    reconcile_scanners(providers, tracked, [first, last])
    assert providers == {"stub": manual, "account": last}
    assert tracked == {"account"}
    reconcile_scanners(providers, tracked, [])
    assert providers == {"stub": manual} and tracked == set()


@pytest.mark.asyncio
async def test_real_config_selection_preserves_first_reference_and_missing_video_state(
    generation_home,
):
    _config(generation_home, "account")
    use_cases.save_active_models(
        {"image_gen": ["unqualified", "account:later"], "video_gen": ["account:video"]}
    )
    assert images.active_image_gen() is None
    assert videos.active_video_gen() is None
    assert videos.get_active_provider() is None
    assert videos.get_provider("account") is None
    assert videos.list_providers() == []
    assert await videos.list_all_providers_info() == []
    assert await videos.list_models_for_provider("account") == []
    videos.unregister_provider("absent")
    videos.refresh_providers()


def test_video_wire_schema_and_model_projection_keep_defaults():
    model = VideoGenModel("video", aspect_ratios=["16:9"], max_duration_s=12)
    assert model_metadata([model], videos._MODEL_FIELDS) == [
        {
            "name": "video",
            "description": "",
            "aspect_ratios": ["16:9"],
            "max_duration_s": 12,
            "downloaded": True,
            "active": False,
        }
    ]
    assert asdict(VideoResult()) == {
        "url": "",
        "mime": "video/mp4",
        "local_path": "",
        "duration_s": 0.0,
    }
    with pytest.raises(TypeError):
        VideoGenProvider()
    image = ImageGenModel("image")
    assert (
        image.sizes == [] and image.supports_edit is False and image.downloaded is True
    )
    assert ImageResult().mime == "image/png"


@pytest.mark.asyncio
async def test_catalog_models_are_detached_and_mark_active_from_real_binding(
    generation_home,
):
    _config(generation_home, "account")
    provider_type = "local-image-catalog-contract"
    catalog = MediaCatalog(
        (
            MediaModel("m1", "First", {"sizes": ["512x512"], "supports_edit": True}),
            MediaModel("m2"),
        ),
        "m2",
    )
    register_media_catalog("image_gen", provider_type, catalog)
    provider = OpenAIImageProvider(provider_name="account", provider_type=provider_type)
    images.register_provider(provider)
    use_cases.save_active_models({"image_gen": ["account:m1"]})
    try:
        listed = await provider.list_models()
        assert [(entry.name, entry.active) for entry in listed] == [
            ("m1", True),
            ("m2", False),
        ]
        listed[0].sizes.append("caller mutation")
        assert catalog.models[0].extra["sizes"] == ["512x512"]
        assert provider._default_model("") == "m2"
        assert provider._default_model("pinned") == "pinned"
    finally:
        unregister_media_catalogs(provider_type)


def _open_file_descriptors(path):
    matches = []
    for descriptor in Path("/proc/self/fd").iterdir():
        try:
            if os.readlink(descriptor) == str(path):
                matches.append(descriptor.name)
        except OSError:
            continue
    return matches


def test_edit_arguments_own_and_close_actual_source_and_mask_files(tmp_path):
    source, mask = tmp_path / "source.png", tmp_path / "mask.png"
    source.write_bytes(_solid_png((1, 2, 3)))
    mask.write_bytes(_solid_png((255, 255, 255)))
    request = _ImageCall(
        "edit", {"model": "m", "prompt": "p", "n": 1}, str(source), str(mask)
    )
    with request.arguments() as arguments:
        image_handle, mask_handle = arguments["image"], arguments["mask"]
        assert image_handle.read() == source.read_bytes()
        assert mask_handle.read() == mask.read_bytes()
    assert image_handle.closed and mask_handle.closed
    assert "image" not in request.parameters
    before = _open_file_descriptors(source)
    invalid = _ImageCall("edit", {}, str(source), str(tmp_path / "missing-mask.png"))
    with pytest.raises(FileNotFoundError):
        with invalid.arguments():
            pass
    assert _open_file_descriptors(source) == before


@pytest.mark.asyncio
async def test_image_error_boundary_wraps_real_file_errors_and_keeps_typed_errors(
    tmp_path,
):
    provider = OpenAIImageProvider(provider_name="local")
    read = partial(asyncio.to_thread, (tmp_path / "missing").read_bytes)
    with pytest.raises(ImageGenError, match="Image edit failed:") as failure:
        await provider._await(read, "edit")
    assert isinstance(failure.value.__cause__, FileNotFoundError)
    decode = partial(asyncio.to_thread, provider._parse_response, None)
    with pytest.raises(
        ImageGenError, match="Image provider returned no images"
    ) as failure:
        await provider._await(decode, "generate")
    assert failure.value.__cause__ is None


@pytest.mark.asyncio
async def test_image_timeout_and_cancellation_use_the_actual_event_loop(monkeypatch):
    provider = OpenAIImageProvider(provider_name="local")
    monkeypatch.setattr(openai_provider, "_GEN_TIMEOUT_S", 0.01)
    with pytest.raises(
        ImageGenError, match="Image generate timed out for provider 'local'"
    ):
        await provider._await(partial(asyncio.sleep, 1), "generate")
    task = asyncio.create_task(provider._await(partial(asyncio.sleep, 1), "edit"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_close_releases_a_real_async_http_session_without_sending_requests():
    session = aiohttp.ClientSession()
    await OpenAIImageProvider._close(session)
    assert session.closed


@pytest.mark.asyncio
async def test_unpinned_request_fails_before_optional_sdk_and_no_credentials_are_available(
    generation_home,
):
    provider = OpenAIImageProvider(
        provider_name="unconfigured", provider_type="uncontributed"
    )
    assert await provider.is_available() is False
    with pytest.raises(ImageGenError, match="no contributed default"):
        await provider.generate("prompt")
    with pytest.raises(ImageGenError, match="no contributed default"):
        await provider.edit("prompt", source_image="unused.png")
    assert provider.info() == {
        "name": "unconfigured",
        "display_name": "unconfigured (remote image)",
    }
