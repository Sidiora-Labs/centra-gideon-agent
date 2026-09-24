from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gideon.integrations.browse.loop import _click_vision
from gideon.integrations.browse.page import CdpPageDriver
from gideon.integrations.browse.sentinels import (
    ClickVisionAction,
    SubmitAction,
    parse_sentinel,
)
from gideon.integrations.browse.vision import (
    VISION_RECOMMENDATIONS,
    VisionClick,
    VisionRefusal,
    audit_vision,
    ground_click,
    has_captcha,
    parse_vision_click,
    require_unreferenced_page,
)


@pytest.mark.parametrize("enabled", [False, None, "true", "false", 1])
def test_loop_refuses_without_explicit_opt_in(enabled):
    page = CdpPageDriver(None, vision_enabled=True)
    with pytest.raises(VisionRefusal, match="explicit opt-in"):
        asyncio.run(
            _click_vision(
                ClickVisionAction("canvas control"),
                page=page,
                html="<canvas></canvas>",
                enabled=enabled,
            )
        )


@pytest.mark.parametrize("enabled", [False, None, "true", "false", 1])
def test_grounding_independently_refuses_without_opt_in(enabled):
    with pytest.raises(VisionRefusal, match="explicit opt-in"):
        asyncio.run(
            ground_click(
                what="canvas control",
                html="<canvas></canvas>",
                screenshot="",
                enabled=enabled,
            )
        )


@pytest.mark.parametrize("enabled", [False, None, "true", "false", 1])
def test_driver_independently_refuses_without_opt_in(enabled):
    page = CdpPageDriver(None, vision_enabled=enabled)
    with pytest.raises(VisionRefusal, match="disabled"):
        asyncio.run(page.click_vision(VisionClick(0.5, 0.5), expected_html=""))


@pytest.mark.parametrize(
    "html",
    [
        '<a href="https://example.com">Next</a>',
        "<button>Next</button>",
        '<input name="query">',
        '<form><input name="password" type="password"></form>',
    ],
)
def test_refs_prevent_grounding_and_loop_dispatch(html):
    with pytest.raises(VisionRefusal, match="existing element refs"):
        asyncio.run(ground_click(what="Next", html=html, screenshot="", enabled=True))
    with pytest.raises(VisionRefusal, match="existing element refs"):
        asyncio.run(
            _click_vision(
                ClickVisionAction("Next"),
                page=CdpPageDriver(None, vision_enabled=True),
                html=html,
                enabled=True,
            )
        )


@pytest.mark.parametrize(
    "html",
    [
        '<iframe src="https://example.com/recaptcha"></iframe>',
        '<div class="cf-turnstile"></div>',
        "<div>Verify you are human</div>",
        "<div>I&#39;m not a robot</div>",
        "<div>CAPTCHA</div>",
    ],
)
def test_captcha_refused_before_binding_or_screenshot(html):
    assert has_captcha(html)
    with pytest.raises(VisionRefusal) as caught:
        asyncio.run(ground_click(what="Next", html=html, screenshot="", enabled=True))
    assert caught.value.reason == "captcha_required"
    with pytest.raises(VisionRefusal) as caught:
        asyncio.run(
            _click_vision(
                ClickVisionAction("Next"),
                page=CdpPageDriver(None, vision_enabled=True),
                html=html,
                enabled=True,
            )
        )
    assert caught.value.reason == "captcha_required"


def test_missing_vision_model_produces_typed_park_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    with pytest.raises(VisionRefusal) as caught:
        asyncio.run(
            ground_click(
                what="Next", html="<canvas></canvas>", screenshot="", enabled=True
            )
        )
    assert caught.value.reason == "vision_unavailable"
    assert "image_modality" in str(caught.value)


def test_only_click_vision_sentinel_is_supported():
    action = ClickVisionAction("the canvas Next control")
    assert parse_sentinel(action.render()) == action
    for line in (
        "TYPE_VISION password",
        "SUBMIT_VISION",
        "NAVIGATE_VISION url",
        "CLICK_VISION",
    ):
        assert parse_sentinel(line) is None
    with pytest.raises(VisionRefusal, match="only CLICK_VISION"):
        asyncio.run(
            _click_vision(
                SubmitAction(), page=CdpPageDriver(None), html="", enabled=True
            )
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"action":"TYPE","x":0.5,"y":0.5}',
        '{"action":"SUBMIT","x":0.5,"y":0.5}',
        '{"action":"NAVIGATE","x":0.5,"y":0.5}',
        '{"action":"CLICK","x":-1,"y":0.5}',
        '{"action":"CLICK","x":1,"y":0.5}',
        '{"action":"CLICK","x":true,"y":0.5}',
        '{"action":"CLICK","x":NaN,"y":0.5}',
        '{"action":"CLICK","x":0.5,"y":Infinity}',
        '{"action":"CLICK","x":0.5,"y":0.5,"value":"secret"}',
        "null",
        "[]",
        "CLICK 10 20",
    ],
)
def test_grounding_output_cannot_expand_verb_or_coordinates(raw):
    with pytest.raises(VisionRefusal):
        parse_vision_click(raw)


def test_valid_click_and_unreferenced_canvas():
    require_unreferenced_page('<canvas id="controls"></canvas>')
    assert parse_vision_click('{"action":"CLICK","x":0.25,"y":0.5}') == VisionClick(
        0.25, 0.5
    )


def test_vision_audit_is_written_to_real_sel():
    from gideon.security.sel import sel

    audit_vision("attempt")
    audit_vision("clicked")
    events = [json.loads(line) for line in sel()._path.read_text().splitlines()]
    events = [e for e in events if e.get("operation") == "browse.click:vision"]
    assert [e["outcome"] for e in events] == ["attempt", "clicked"]


def test_recommendations_are_apache_and_setting_defaults_off():
    assert VISION_RECOMMENDATIONS
    assert all(license == "Apache-2.0" for _, license in VISION_RECOMMENDATIONS)
    path = (
        Path(__file__).resolve().parents[2]
        / "runtime/gideon/extensions/apps/native/browse-action/app.json"
    )
    setting = json.loads(path.read_text())["provider"]["settingsSchema"]["properties"][
        "vision_enabled"
    ]
    assert setting["default"] is False
    for name, license in VISION_RECOMMENDATIONS:
        assert name in setting["x-meta"]["help"]
        assert license in setting["x-meta"]["help"]
