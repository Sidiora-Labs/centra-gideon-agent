from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from gideon.extensions.providers.image_input import clear_cache, image_reader
from gideon.integrations.llm.anthropic import _translate_messages
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.interfaces.dashboard.attachment_extract import AttachmentExtractor
from gideon.interfaces.dashboard.attachment_images import MAX_EDGE_PX, image_part_url


def _png(size: tuple[int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_uploaded_image_is_resized_and_keeps_its_real_pixels(tmp_path):
    path = tmp_path / "large.png"
    original = _png((MAX_EDGE_PX + 200, 700))
    path.write_bytes(original)

    data_url = image_part_url(str(path))

    assert data_url.startswith("data:image/png;base64,")
    payload = base64.b64decode(data_url.partition(",")[2], validate=True)
    with Image.open(io.BytesIO(payload)) as delivered:
        assert max(delivered.size) <= MAX_EDGE_PX
        assert delivered.getpixel((0, 0)) == (200, 30, 30)
    assert payload != original


def test_image_bytes_reject_symlinks_and_extension_mismatch(tmp_path):
    png = _png((2, 2))
    image = tmp_path / "actual.png"
    image.write_bytes(png)
    mismatch = tmp_path / "claimed.jpg"
    mismatch.write_bytes(png)
    link = tmp_path / "linked.png"
    link.symlink_to(image)

    assert image_part_url(str(mismatch)) == ""
    assert image_part_url(str(link)) == ""


async def test_image_preview_is_lazy_and_no_reader_does_not_start_model_work(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    clear_cache()
    image = Path(tmp_path / "home" / "uploads" / ("a" * 32 + "_screen.png"))
    image.parent.mkdir(parents=True)
    image.write_bytes(_png((4, 3)))
    extractor = AttachmentExtractor()

    extractor.start(str(image), "image/png")
    assert extractor._tasks == {}
    reader = await image_reader()
    assert not reader.ref
    text = await extractor.get(str(image), "image/png")

    assert extractor._tasks == {}
    assert "screen.png" in text
    assert "not read" in text.lower() or "no image model" in text.lower()


def test_real_provider_message_adapters_translate_neutral_image_part():
    data_url = "data:image/png;base64," + base64.b64encode(_png((2, 2))).decode("ascii")
    neutral = {"type": "image_url", "image_url": {"url": data_url}}

    assert OpenAIProvider._image_content(None, data_url) == neutral
    _, anthropic_messages = _translate_messages(
        [{"role": "user", "content": [neutral]}]
    )
    block = anthropic_messages[0]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
    assert block["source"]["data"] == data_url.partition(",")[2]
