"""Free Mode decks: escaped local previews and native editable PowerPoint shapes."""
import base64
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

THEMES = {
    'dark': {
        'bg': '0A0D14', 'surface': '121926', 'border': '24324A',
        'text': 'E6EDF7', 'sub': '9FB3C8', 'accent': '38BDF8', 'accent2': '004BDF',
        'cube1': '004BDF', 'cube2': '152B50',
    },
    'light': {
        'bg': 'F4F7FB', 'surface': 'FFFFFF', 'border': 'D6E0EC',
        'text': '0F172A', 'sub': '475569', 'accent': '0284C7', 'accent2': '0EA5E9',
        'cube1': '0284C7', 'cube2': 'BAE6FD',
    },
    'colorful': {
        'bg': '140F2D', 'surface': '1E1742', 'border': '3E3175',
        'text': 'FFFBEB', 'sub': 'FDE68A', 'accent': 'F472B6', 'accent2': '6366F1',
        'cube1': 'EC4899', 'cube2': '6366F1',
    },
    'sober': {
        'bg': '1A1D21', 'surface': '23272D', 'border': '383E47',
        'text': 'EAECEF', 'sub': '9CA3AF', 'accent': 'D4A373', 'accent2': '6B7280',
        'cube1': '6B7280', 'cube2': '374151',
    },
    'hp': {
        'bg': 'FFFFFF', 'surface': 'F0F6FC', 'border': 'C5DDF2',
        'text': '0F172A', 'sub': '475569', 'accent': '0096D6', 'accent2': '004B87',
        'cube1': '0096D6', 'cube2': '004B87',
    },
}


class Slide(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=65)
    subtitle: str = Field(default='', max_length=110)
    layout: Literal['cover', 'cards', 'metrics', 'split', 'roadmap'] = 'cards'
    points: list[str] = Field(min_length=2, max_length=3)
    notes: str = Field(default='', max_length=700)


class Deck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=90)
    theme: Literal['dark', 'light', 'colorful', 'sober', 'hp'] = 'dark'
    cover_prompt: str = Field(default='', max_length=300)
    slides: list[Slide] = Field(min_length=3, max_length=8)


def export_deck(data, folder: Path, theme_override: str = 'auto', cover_image_path: Path | None = None):
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt
    from showcase_app.gb10_deck_design import block, lines

    deck = Deck.model_validate(data)
    if theme_override in THEMES:
        deck.theme = theme_override
    pal = THEMES.get(deck.theme, THEMES['dark'])
    if any(len(point) > 180 for slide in deck.slides for point in slide.points):
        raise ValueError('Keep each slide point under 180 characters')

    cover_b64 = ''
    if cover_image_path and cover_image_path.exists():
        cover_b64 = base64.b64encode(cover_image_path.read_bytes()).decode('ascii')

    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(16), Inches(9)
    previews = []

    for index, content in enumerate(deck.slides):
        layout = 'cover' if index == 0 else (content.layout if content.layout != 'cover' else 'cards')
        svg = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">',
            f'<rect width="1920" height="1080" fill="#{pal["bg"]}"/>',
            f'<rect x="0" y="0" width="1920" height="10" fill="#{pal["accent"]}"/>',
            '<g font-family="Arial,Helvetica,sans-serif">',
        ]
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string(pal['bg'])

        def add_rect(x, y, w, h, fill_hex, border_hex='', rx=18):
            stroke = f' stroke="#{border_hex}" stroke-width="2"' if border_hex else ''
            svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="#{fill_hex}"{stroke}/>')
            shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x / 120), Inches(y / 120), Inches(w / 120), Inches(h / 120))
            shp.fill.solid()
            shp.fill.fore_color.rgb = RGBColor.from_string(fill_hex)
            if border_hex:
                shp.line.color.rgb = RGBColor.from_string(border_hex)
                shp.line.width = Pt(1.2)
            else:
                shp.line.fill.background()

        def text(x, y, width, height, value, size, color=pal['text'], bold=False):
            while len(lines(value, size, width, bold)) * size * 1.18 > height and size > 22:
                size -= 2
            rendered, _ = block(x, y, value, size, width, color='#' + color, bold=bold, limit=y + height)
            svg.append(rendered)
            box = slide.shapes.add_textbox(Inches(x / 120), Inches(y / 120), Inches(width / 120), Inches(height / 120))
            box.text_frame.word_wrap = True
            box.text_frame.margin_left = box.text_frame.margin_top = 0
            box.text_frame.margin_right = box.text_frame.margin_bottom = 0
            paragraph = box.text_frame.paragraphs[0]
            paragraph.text = value
            paragraph.font.name = 'Arial'
            paragraph.font.size = Pt(size * 0.6)
            paragraph.font.bold = bold
            paragraph.font.color.rgb = RGBColor.from_string(color)

        add_rect(0, 0, 1920, 10, pal['accent'], '', rx=0)
        text(100, 48, 1200, 50, f'HP ZGX NANO / {deck.theme.upper()}', 22, pal['accent'], True)
        text(1420, 48, 400, 50, layout.upper(), 20, pal['sub'], True)

        if layout == 'cover':
            text(100, 145, 980, 220, content.title, 70, pal['text'], bold=True)
            text(100, 380, 980, 120, content.subtitle, 30, pal['sub'])
            for idx, pt in enumerate(content.points):
                py = 540 + idx * 130
                add_rect(100, py, 960, 106, pal['surface'], pal['border'], rx=16)
                add_rect(100, py, 10, 106, pal['accent'], '', rx=4)
                text(135, py + 24, 900, 72, pt, 28, pal['text'])
            if cover_b64 and cover_image_path:
                add_rect(1120, 150, 700, 760, pal['surface'], pal['border'], rx=22)
                svg.append(f'<image href="data:image/png;base64,{cover_b64}" x="1136" y="166" width="668" height="728" preserveAspectRatio="xMidYMid slice"/>')
                slide.shapes.add_picture(str(cover_image_path), Inches(1136 / 120), Inches(166 / 120), Inches(668 / 120), Inches(728 / 120))
            else:
                add_rect(1140, 160, 680, 740, pal['surface'], pal['border'], rx=22)
                svg.append(f'<path d="m1480 280 240 130-240 140-240-140Z" fill="#{pal["cube1"]}"/><path d="m1480 550 240-140v210l-240 140-240-140V410Z" fill="#{pal["cube2"]}"/>')
                shape = slide.shapes.add_shape(MSO_SHAPE.CUBE, Inches(11.3), Inches(2.6), Inches(3.2), Inches(3.8))
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor.from_string(pal['cube1'])
                shape.line.fill.background()

        elif layout in ('cards', 'metrics'):
            text(100, 120, 1720, 140, content.title, 58, pal['text'], bold=True)
            text(100, 260, 1720, 90, content.subtitle, 28, pal['sub'])
            cnt = len(content.points)
            gap = 40
            card_w = int((1720 - gap * (cnt - 1)) / cnt)
            for idx, pt in enumerate(content.points):
                cx = 100 + idx * (card_w + gap)
                cy, ch = 390, 520
                add_rect(cx, cy, card_w, ch, pal['surface'], pal['border'], rx=20)
                add_rect(cx, cy, card_w, 12, pal['accent'] if idx % 2 == 0 else pal['accent2'], '', rx=6)
                badge = f'KPI 0{idx+1}' if layout == 'metrics' else f'0{idx+1}'
                text(cx + 32, cy + 44, card_w - 64, 65, badge, 40 if layout == 'metrics' else 32, pal['accent'], True)
                text(cx + 32, cy + 135, card_w - 64, 340, pt, 32, pal['text'])

        elif layout == 'split':
            text(100, 120, 1720, 140, content.title, 58, pal['text'], bold=True)
            add_rect(100, 310, 740, 600, pal['surface'], pal['border'], rx=20)
            add_rect(100, 310, 14, 600, pal['accent'], '', rx=6)
            text(145, 355, 650, 70, 'EXECUTIVE SUMMARY', 22, pal['accent'], True)
            text(145, 435, 650, 420, content.subtitle or content.points[0], 34, pal['text'])
            for idx, pt in enumerate(content.points):
                py = 310 + idx * 205
                add_rect(880, py, 940, 185, pal['surface'], pal['border'], rx=18)
                text(915, py + 24, 120, 50, f'0{idx+1}', 28, pal['accent'], True)
                text(915, py + 76, 870, 95, pt, 29, pal['text'])

        else:  # roadmap
            text(100, 120, 1720, 140, content.title, 58, pal['text'], bold=True)
            text(100, 260, 1720, 80, content.subtitle, 28, pal['sub'])
            add_rect(160, 445, 1600, 8, pal['border'], '', rx=4)
            cnt = len(content.points)
            gap = 40
            card_w = int((1720 - gap * (cnt - 1)) / cnt)
            for idx, pt in enumerate(content.points):
                cx = 100 + idx * (card_w + gap)
                add_rect(cx + int(card_w / 2) - 42, 405, 84, 84, pal['accent'], '', rx=42)
                text(cx + int(card_w / 2) - 24, 426, 60, 50, f'0{idx+1}', 30, pal['bg'], True)
                add_rect(cx, 520, card_w, 390, pal['surface'], pal['border'], rx=20)
                text(cx + 30, 555, card_w - 60, 50, f'PHASE {idx+1}', 22, pal['accent'], True)
                text(cx + 30, 615, card_w - 60, 260, pt, 30, pal['text'])

        text(100, 1005, 1480, 50, 'LOCAL AI DRAFT / REVIEW BEFORE USE', 20, pal['sub'])
        text(1680, 1005, 200, 50, f'{index+1:02d} / {len(deck.slides):02d}', 20, pal['accent'], True)
        slide.notes_slide.notes_text_frame.text = content.notes
        name = f'slide-{index+1}.svg'
        (folder / name).write_text(''.join(svg) + '</g></svg>', encoding='utf-8')
        previews.append(name)

    presentation.save(folder / 'presentation.pptx')
    (folder / 'presentation.json').write_text(deck.model_dump_json(indent=2), encoding='utf-8')
    return {'deck': deck.model_dump(), 'previews': previews, 'files': ['presentation.pptx', 'presentation.json', *previews]}
