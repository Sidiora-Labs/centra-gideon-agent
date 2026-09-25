"""Physical card layouts and complete conventional deck rosters."""

import io

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

MAJORS = (
    "The Fool",
    "The Magician",
    "The High Priestess",
    "The Empress",
    "The Emperor",
    "The Hierophant",
    "The Lovers",
    "The Chariot",
    "Strength",
    "The Hermit",
    "Wheel of Fortune",
    "Justice",
    "The Hanged Man",
    "Death",
    "Temperance",
    "The Devil",
    "The Tower",
    "The Star",
    "The Moon",
    "The Sun",
    "Judgement",
    "The World",
)


def roster(kind):
    cards = []
    if kind == "tarot":
        cards.extend(
            {"key": f"major-{i}", "name": name, "group": "major", "rank": str(i)}
            for i, name in enumerate(MAJORS)
        )
        suits = ("wands", "cups", "swords", "pentacles")
        ranks = (
            "ace",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "page",
            "knight",
            "queen",
            "king",
        )
    else:
        suits = ("spades", "hearts", "diamonds", "clubs")
        ranks = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
    cards.extend(
        {
            "key": suit + "-" + rank,
            "name": rank.title() + " of " + suit.title(),
            "group": suit,
            "rank": rank,
        }
        for suit in suits
        for rank in ranks
    )
    if kind == "playing":
        cards.extend(
            {
                "key": f"joker-{i}",
                "name": "Joker " + str(i),
                "group": "jokers",
                "rank": "Joker",
            }
            for i in (1, 2)
        )
    cards.append({"key": "back", "name": "Card back", "group": "back", "rank": ""})
    return [
        {**card, "prompt": "", "negative_prompt": "", "artifact_ref": None}
        for card in cards
    ]


def compose(deck, card):
    orientation = (
        "Matching top-left and bottom-right indices rotated 180 degrees."
        if deck["orientation"] == "two_way"
        else "All card lettering upright in one direction."
    )
    parts = {
        "style": deck["style_notes"],
        "layout": deck["layout_prompt"],
        "orientation": orientation,
        "subject": card["name"] + ": " + card["prompt"],
    }
    negative = ", ".join(
        value for value in (deck["negative_prompt"], card["negative_prompt"]) if value
    )
    return {
        "prompt": ". ".join(value for value in parts.values() if value)
        + ((". Avoid: " + negative) if negative else ""),
        "parts": parts,
        "negative_prompt": negative,
    }


def pdf_bytes(deck, images):
    width, height, bleed, safe = (
        deck[key] * mm for key in ("width_mm", "height_mm", "bleed_mm", "safe_mm")
    )
    output = io.BytesIO()
    pdf = canvas.Canvas(
        output,
        pagesize=(width + 2 * bleed, height + 2 * bleed),
        pageCompression=1,
        invariant=1,
    )
    pdf.setTitle(deck["name"])
    pdf.setAuthor("Gideon")
    for card in deck["cards"]:
        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(0, 0, width + 2 * bleed, height + 2 * bleed, fill=1, stroke=0)
        image = images.get(card["key"])
        if image is not None:
            available_w, available_h = width - 2 * safe, height - 2 * safe - 22
            scale = min(available_w / image.width, available_h / image.height)
            iw, ih = image.width * scale, image.height * scale
            pdf.drawImage(
                ImageReader(image),
                bleed + (width - iw) / 2,
                bleed + safe + 11 + (available_h - ih) / 2,
                iw,
                ih,
                mask="auto",
            )
        pdf.setStrokeColorRGB(0.3, 0.3, 0.3)
        pdf.setLineWidth(0.3)
        for x in (bleed, bleed + width):
            for y in (bleed, bleed + height):
                pdf.line(x, max(0, y - bleed), x, min(height + 2 * bleed, y + bleed))
                pdf.line(max(0, x - bleed), y, min(width + 2 * bleed, x + bleed), y)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont("Helvetica", 8)
        name = card["name"]
        pdf.setFont(
            "Helvetica",
            min(8, (width - 2 * safe) / max(1, stringWidth(name, "Helvetica", 1))),
        )
        pdf.drawCentredString(bleed + width / 2, bleed + safe + 2, name)
        pdf.setFont("Helvetica", 8)
        index = (card["rank"] + " " + card["group"]).strip()
        if card["key"] != "back":
            pdf.drawString(bleed + safe, bleed + height - safe - 8, index)
            pdf.saveState()
            pdf.translate(bleed + width - safe, bleed + safe + 14)
            if deck["orientation"] == "two_way":
                pdf.rotate(180)
            pdf.drawString(0, 0, index)
            pdf.restoreState()
        pdf.showPage()
    pdf.save()
    return output.getvalue()
