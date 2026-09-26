"""Chromium raster review of the editable deck model, not an Office rendering."""

from __future__ import annotations

import base64
import html
import io
import os
from typing import Any

from PIL import Image, ImageChops, ImageStat

from gideon.workspace.documents.model import DeckModel, ShapeBox, Slide


def _box_style(box: ShapeBox, width: float, height: float, fallback: str) -> str:
    if box.width_in <= 0 or box.height_in <= 0:
        return fallback
    return (
        f"left:{max(0, box.left_in / width * 100):.3f}%;"
        f"top:{max(0, box.top_in / height * 100):.3f}%;"
        f"width:{min(100, box.width_in / width * 100):.3f}%;"
        f"height:{min(100, box.height_in / height * 100):.3f}%"
    )


def _slide_html(slide: Slide, model: DeckModel, image: tuple[bytes, str] | None) -> str:
    width = max(1.0, model.width_in or 13.333)
    height = max(1.0, model.height_in or 7.5)
    ratio = width / height
    body_style = _box_style(slide.body_box, width, height, "left:7%;top:27%;width:86%;height:64%")
    title_style = _box_style(slide.title_box, width, height, "left:7%;top:8%;width:86%;height:16%")
    has_image = image is not None
    bullets = "".join(
        f'<li style="margin-left:{min(8, max(0, bullet.level)) * 2.6}%">{html.escape(bullet.text)}</li>'
        for bullet in slide.bullets
    )
    picture = ""
    if image is not None:
        data, mime = image
        picture = (
            '<img class="figure" alt="Slide image" src="data:'
            + html.escape(mime, quote=True)
            + ";base64,"
            + base64.b64encode(data).decode("ascii")
            + '">'
        )
    body_width = "width:48%;" if has_image and slide.body_box.width_in <= 0 else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        f'html,body{{margin:0;width:1280px;height:{round(1280 / ratio)}px;overflow:hidden}}'
        'body{font-family:Arial,Helvetica,sans-serif;color:#18243a;background:#fff}'
        '.slide{position:relative;width:100%;height:100%;box-sizing:border-box;overflow:hidden;'
        'background:#fff;border-top:12px solid #304d83}'
        '.title,.body{position:absolute;overflow:hidden;box-sizing:border-box}'
        '.title{font-size:48px;line-height:1.14;font-weight:700}'
        '.body{font-size:29px;line-height:1.36}'
        '.body ul{padding:0;margin:0;list-style:none}.body li{margin-bottom:12px}'
        '.body li:before{content:"• ";color:#304d83}'
        '.figure{position:absolute;right:7%;top:29%;width:38%;height:59%;object-fit:contain}'
        '</style></head><body><main class="slide">'
        f'<div class="title" style="{title_style}">{html.escape(slide.title)}</div>'
        f'<div class="body" style="{body_style};{body_width}"><ul>{bullets}</ul></div>'
        f'{picture}</main></body></html>'
    )


def critique_png(data: bytes, slide: Slide) -> list[str]:
    """Use actual rendered pixels to flag sparse or crowded visual balance."""
    image = Image.open(io.BytesIO(data)).convert("RGB")
    background = Image.new("RGB", image.size, "white")
    difference = ImageChops.difference(image, background).convert("L")
    stats = ImageStat.Stat(difference.resize((160, 90)))
    occupancy = stats.mean[0] / 255
    notes: list[str] = []
    if occupancy < 0.015:
        notes.append("The rendered slide is visually sparse; consider adding a useful visual or claim.")
    if occupancy > 0.28:
        notes.append("The rendered slide is visually dense; inspect legibility and split content if needed.")
    if not slide.title.strip():
        notes.append("The rendered slide has no headline.")
    if len(slide.bullets) > 6:
        notes.append("More than six bullets may be difficult to scan at presentation size.")
    return notes


async def _layout_critique(page: Any) -> list[str]:
    metrics = await page.evaluate("""() => {
      const slide = document.querySelector('.slide');
      const title = document.querySelector('.title');
      const body = document.querySelector('.body');
      const figure = document.querySelector('.figure');
      const box = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom} };
      const clipped = el => el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2;
      const intersects = (a,b) => a && b && a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y;
      const outer = box(slide), heading = box(title), content = box(body), visual = figure ? box(figure) : null;
      const outside = r => r.x < outer.x || r.y < outer.y || r.right > outer.right || r.bottom > outer.bottom;
      return { titleClipped:clipped(title), bodyClipped:clipped(body), titleOutside:outside(heading), bodyOutside:outside(content),
        titleBodyOverlap:intersects(heading,content), figureBodyOverlap:intersects(visual,content), figureTitleOverlap:intersects(visual,heading) };
    }""")
    notes: list[str] = []
    if metrics["titleClipped"]:
        notes.append("The rendered headline is clipped; shorten it or enlarge its title box.")
    if metrics["bodyClipped"]:
        notes.append("The rendered bullet area overflows; split the content or enlarge its body box.")
    if metrics["titleOutside"] or metrics["bodyOutside"]:
        notes.append("A positioned text box extends beyond the slide edge; move or resize it.")
    if metrics["titleBodyOverlap"]:
        notes.append("Headline and body boxes overlap in the rendered layout; separate their positions.")
    if metrics["figureBodyOverlap"] or metrics["figureTitleOverlap"]:
        notes.append("The figure overlaps rendered text; resize or reposition the figure or text boxes.")
    return notes


async def render_deck_preview(model: DeckModel, provider: Any) -> list[tuple[bytes, list[str]]]:
    """Render every editable slide in Chromium and inspect the resulting PNG bytes."""
    from playwright.async_api import async_playwright

    slides = model.slides
    if not slides or len(slides) > 60:
        raise ValueError("deck preview requires 1 to 60 slides")
    image_refs: list[tuple[bytes, str] | None] = []
    for slide in slides:
        if not slide.artifact_slug:
            image_refs.append(None)
            continue
        artifact = provider.get(slide.artifact_slug)
        image = provider.raw_bytes(slide.artifact_slug) if artifact and artifact.kind == "image" else None
        if image is None:
            raise ValueError(f"slide image artifact {slide.artifact_slug!r} is unavailable")
        image_refs.append(image)
    result: list[tuple[bytes, list[str]]] = []
    async with async_playwright() as playwright:
        browser_args: dict[str, Any] = {"headless": True, "args": ["--no-sandbox"]}
        executable = os.environ.get("GIDEON_CHROMIUM_EXECUTABLE", "")
        if (
            not executable
            and not os.path.exists(playwright.chromium.executable_path)
            and os.path.exists("/snap/bin/chromium")
        ):
            executable = "/snap/bin/chromium"
        if executable:
            browser_args["executable_path"] = executable
        browser = await playwright.chromium.launch(**browser_args)
        try:
            for slide, image in zip(slides, image_refs, strict=True):
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                try:
                    await page.set_content(_slide_html(slide, model, image), wait_until="load")
                    await page.evaluate("document.fonts.ready")
                    layout_notes = await _layout_critique(page)
                    png = await page.locator(".slide").screenshot(type="png")
                    result.append((png, layout_notes + critique_png(png, slide)))
                finally:
                    await page.close()
        finally:
            await browser.close()
    return result
