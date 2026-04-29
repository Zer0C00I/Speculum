from __future__ import annotations

import shutil
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import fitz

from pdftranslator.logging_config import get_logger

if TYPE_CHECKING:
    from pdftranslator.ai.base import AbstractTranslator

_log = get_logger(__name__)


@dataclass
class PageTextBlock:
    id: int
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float
    font_name: str


@dataclass
class ImageRegion:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class PageInfo:
    number: int
    text_blocks: list[PageTextBlock]
    image_regions: list[ImageRegion]
    width: float
    height: float


def _rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _wrap_text(text: str, rect_width: float, fontsize: float, font: fitz.Font) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        test_line = " ".join(current + [word])
        w = font.text_length(test_line, fontsize=fontsize)
        if w <= rect_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _find_best_font_size(rect: fitz.Rect, text: str, font: fitz.Font, start_size: float, min_size: float = 4.0) -> float:
    rect_w = rect.width
    rect_h = rect.height
    if rect_w <= 0 or rect_h <= 0:
        return start_size

    def _fits(size: float) -> bool:
        lines = _wrap_text(text, rect_w, size, font)
        return len(lines) * size * 1.2 <= rect_h

    best = start_size
    low = min_size
    high = start_size * 2.0
    for _ in range(12):
        mid = (low + high) / 2.0
        if _fits(mid):
            best = mid
            low = mid
        else:
            high = mid
    return best


_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
_FONT_REGULAR = str(_FONTS_DIR / "DejaVuSans.ttf")
_FONT_BOLD = str(_FONTS_DIR / "DejaVuSans-Bold.ttf")
_FONT_ITALIC = str(_FONTS_DIR / "DejaVuSans-Oblique.ttf")
_FONT_SERIF = str(_FONTS_DIR / "DejaVuSerif.ttf")
_FONT_MONO = str(_FONTS_DIR / "DejaVuSansMono.ttf")

_FALLBACK_FONTS = [
    _FONT_REGULAR, _FONT_BOLD, _FONT_ITALIC, _FONT_SERIF, _FONT_MONO,
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
]

_FONT_CACHE: dict[str, fitz.Font | None] = {}

FONT_NAMES = ["DVReg", "DVBold", "DVItalic", "DVSerif", "DVMono"]
FONT_PATHS = [_FONT_REGULAR, _FONT_BOLD, _FONT_ITALIC, _FONT_SERIF, _FONT_MONO]


def _find_font_file(name: str) -> str | None:
    candidates = _FALLBACK_FONTS.copy()
    candidates.insert(0, name)
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def _load_font(path: str) -> fitz.Font | None:
    if path in _FONT_CACHE:
        return _FONT_CACHE[path]
    found = _find_font_file(path)
    if found:
        font = fitz.Font(fontfile=found)
        _FONT_CACHE[path] = font
        return font
    _FONT_CACHE[path] = None
    return None


class PDFEngine:
    def __init__(self, path: str | None = None, doc: fitz.Document | None = None) -> None:
        if doc is not None:
            self._path = ""
            self._doc = doc
        elif path is not None:
            self._path = path
            _log.info("Opening PDF: %s", path)
            self._doc = fitz.open(path)
        else:
            self._path = ""
            self._doc = fitz.open()

        _log.info("PDF: %d pages", self._doc.page_count if hasattr(self, '_doc') else 0)

        self._font_regular = _load_font(_FONT_REGULAR)
        self._font_bold = _load_font(_FONT_BOLD)
        self._font_italic = _load_font(_FONT_ITALIC)
        self._font_serif = _load_font(_FONT_SERIF)
        self._font_mono = _load_font(_FONT_MONO)

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def page_info(self, page_num: int) -> PageInfo:
        page = self._doc[page_num]
        blocks = page.get_text("dict")["blocks"]
        text_blocks: list[PageTextBlock] = []
        image_regions: list[ImageRegion] = []

        for block in blocks:
            if block["type"] == 0:
                tb = self._extract_text_block(block, len(text_blocks))
                if tb:
                    text_blocks.append(tb)
            elif block["type"] == 1:
                bbox = block["bbox"]
                image_regions.append(ImageRegion(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3]))

        rect = page.rect
        return PageInfo(
            number=page_num,
            text_blocks=self._merge_nearby_blocks(text_blocks),
            image_regions=image_regions,
            width=rect.width,
            height=rect.height,
        )

    def render_page_pixmap(
        self,
        page_num: int,
        dpi: float = 150,
        clip: fitz.Rect | None = None,
    ) -> fitz.Pixmap:
        if page_num >= self._doc.page_count:
            return fitz.Pixmap(fitz.csRGB, irange=(0, 0, 1, 1), alpha=False)
        page = self._doc[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        return page.get_pixmap(matrix=mat, clip=clip)

    def page_pixel_size(self, page_num: int, dpi: float = 150) -> tuple[int, int]:
        if page_num >= self._doc.page_count:
            return 1, 1
        page = self._doc[page_num]
        rect = page.rect
        return int(rect.width * dpi / 72), int(rect.height * dpi / 72)

    def build_translated_page(
        self,
        src_engine: PDFEngine,
        src_page_num: int,
        translator: AbstractTranslator,
        source_lang: str,
        target_lang: str,
        batch_size: int = 8,
        verbose: bool = False,
    ) -> int:
        if src_page_num < self._doc.page_count:
            _log.info("Page %d: already in output, skipping", src_page_num)
            return 0

        while self._doc.page_count < src_page_num:
            gap_idx = self._doc.page_count
            src_gap = src_engine._doc[gap_idx]
            pg = self._doc.new_page(width=src_gap.rect.width, height=src_gap.rect.height)
            PDFEngine._copy_page_content(src_gap, pg, src_engine._doc)

        src_fitz_page = src_engine._doc[src_page_num]
        out_page = self._doc.new_page(
            width=src_fitz_page.rect.width,
            height=src_fitz_page.rect.height,
        )

        info = src_engine.page_info(src_page_num)

        _log.info("Page %d: translating %d blocks...", src_page_num, len(info.text_blocks))
        translated_count = 0
        all_pairs: list[tuple[PageTextBlock, object]] = []

        for batch_start in range(0, len(info.text_blocks), batch_size):
            batch = info.text_blocks[batch_start : batch_start + batch_size]
            context_before = info.text_blocks[batch_start - 1].text if batch_start > 0 else ""
            context_after = ""
            after_idx = batch_start + batch_size
            if after_idx < len(info.text_blocks):
                context_after = info.text_blocks[after_idx].text

            translated = translator.translate_batch(
                [src_engine._to_ai_block(b) for b in batch],
                source_lang, target_lang, context_before, context_after,
            )

            for tb in translated:
                if verbose:
                    _log.info("  [%d] EN: %s", tb.id, tb.original_text[:120])
                    _log.info("  [%d] %s: %s", tb.id, target_lang.upper(), tb.translated_text[:120])
                original = next(b for b in batch if b.id == tb.id)
                all_pairs.append((original, tb))
                translated_count += 1

        self._render_page(src_fitz_page, out_page, all_pairs)

        _log.info("Page %d: built %d/%d blocks", src_page_num, translated_count, len(info.text_blocks))
        return translated_count

    def save(self, path: str) -> None:
        self._doc.save(path, garbage=4, deflate=True)

    def close(self) -> None:
        if self._is_doc_closed():
            return
        self._doc.close()

    def reload_from_path(self) -> None:
        if not self._path:
            raise RuntimeError("Cannot reload PDFEngine without a file path")
        _log.info("Reloading PDF: %s", self._path)
        if not self._is_doc_closed():
            self._doc.close()
        self._doc = fitz.open(self._path)
        _log.info("PDF reloaded: %d pages", self._doc.page_count)

    def _is_doc_closed(self) -> bool:
        try:
            return bool(getattr(self._doc, "is_closed", False))
        except Exception:
            return True

    def save_atomic(self, path: str) -> None:
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp.close()
        try:
            self._doc.save(tmp.name, garbage=4, deflate=True)
            shutil.move(tmp.name, path)
        finally:
            try:
                Path(tmp.name).unlink(missing_ok=True)
            except Exception:
                pass

    def replace_pages_from_document(self, src_doc: fitz.Document, page_numbers: list[int]) -> None:
        for page_num in sorted(set(page_numbers), reverse=True):
            if page_num < 0 or page_num >= self._doc.page_count or page_num >= src_doc.page_count:
                continue
            self._doc.delete_page(page_num)
            self._doc.insert_pdf(src_doc, from_page=page_num, to_page=page_num, start_at=page_num)

    @staticmethod
    def _copy_page_content(src: fitz.Page, dst: fitz.Page, src_doc: fitz.Document) -> None:
        try:
            dst.show_pdf_page(dst.rect, src_doc, src.number)
        except Exception:
            try:
                pix = src.get_pixmap(dpi=100)
                dst.insert_image(dst.rect, pixmap=pix)
            except Exception as exc:
                _log.warning("_copy_page_content: both methods failed: %s", exc)

    @staticmethod
    def _copy_images_static(src: fitz.Page, dst: fitz.Page) -> None:
        for img_info in src.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = src.parent.extract_image(xref)
                if base_image:
                    img_bytes = base_image["image"]
                    img_rects = src.get_image_rects(xref)
                    if img_rects:
                        for r in img_rects:
                            dst.insert_image(r, stream=img_bytes)
            except Exception as exc:
                _log.warning("_copy_images_static xref=%d failed: %s", xref, exc)

    def _render_page(
        self,
        src_page: fitz.Page,
        out_page: fitz.Page,
        pairs: list[tuple[PageTextBlock, object]],
        render_dpi: float = 150,
    ) -> None:
        # Background: source page as DeviceRGB raster (avoids ICC colorspace issues)
        pix = src_page.get_pixmap(dpi=render_dpi)
        bg = fitz.Pixmap(fitz.csRGB, pix.width, pix.height, bytes(pix.samples), False)
        out_page.insert_image(out_page.rect, pixmap=bg, keep_proportion=False)

        # White out + vector text for each translated block
        # Pass fontfile on every call — fitz deduplicates embedded fonts automatically
        for original, tb in pairs:
            rect = fitz.Rect(original.x0, original.y0, original.x1, original.y1)
            if rect.is_empty or rect.is_infinite:
                continue

            fname = self._get_target_font_name(original.font_name)
            fpath = self._get_target_font_path(original.font_name)
            font = self._get_target_font(original.font_name)
            font_size = original.font_size
            if font and rect.width > 0 and rect.height > 0:
                font_size = _find_best_font_size(rect, tb.translated_text, font, font_size)

            out_page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
            out_page.insert_textbox(
                rect,
                tb.translated_text,
                fontname=fname,
                fontfile=fpath,
                fontsize=font_size,
                color=(0, 0, 0),
                overlay=True,
                align=fitz.TEXT_ALIGN_LEFT,
            )

    @staticmethod
    def _wrap_for_width(text: str, max_width: float, font: "ImageFont.FreeTypeFont") -> tuple[list[str], float]:
        words = text.split()
        if not words:
            return [text], 10

        lines: list[str] = []
        current: list[str] = []
        for word in words:
            test_line = " ".join(current + [word])
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
            if w <= max_width or not current:
                current.append(word)
            else:
                lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))

        bbox = font.getbbox("A")
        char_h = bbox[3] - bbox[1]
        return lines, char_h

    def _extract_text_block(self, block: dict, idx: int) -> PageTextBlock | None:
        text_parts: list[str] = []
        font_sizes: list[float] = []
        font_names: list[str] = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span["text"].strip()
                if text:
                    text_parts.append(text)
                    font_sizes.append(span["size"])
                    font_names.append(span["font"])
        if not text_parts:
            return None
        bbox = block["bbox"]
        return PageTextBlock(
            id=idx,
            text=" ".join(text_parts),
            x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3],
            font_size=sum(font_sizes) / len(font_sizes),
            font_name=max(set(font_names), key=font_names.count) if font_names else "helv",
        )

    def _merge_nearby_blocks(self, blocks: list[PageTextBlock]) -> list[PageTextBlock]:
        if len(blocks) <= 1:
            return blocks
        blocks.sort(key=lambda b: (b.y0, b.x0))
        merged: list[PageTextBlock] = [blocks[0]]
        for b in blocks[1:]:
            prev = merged[-1]
            vertical_gap = b.y0 - prev.y1
            horizontal_overlap = min(prev.x1, b.x1) - max(prev.x0, b.x0)
            box_width = max(prev.x1 - prev.x0, b.x1 - b.x0, 1.0)
            overlap_ratio = horizontal_overlap / box_width
            if vertical_gap < 5 and overlap_ratio > 0.3 and abs(prev.font_size - b.font_size) < 2:
                merged[-1] = PageTextBlock(
                    id=prev.id, text=prev.text + " " + b.text,
                    x0=min(prev.x0, b.x0), y0=prev.y0,
                    x1=max(prev.x1, b.x1), y1=max(prev.y1, b.y1),
                    font_size=prev.font_size, font_name=prev.font_name,
                )
            else:
                merged.append(b)
        for i, blk in enumerate(merged):
            blk.id = i
        return merged

    @staticmethod
    def _to_ai_block(b: PageTextBlock) -> "TextBlock":
        from pdftranslator.ai.base import TextBlock
        return TextBlock(id=b.id, text=b.text, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1, font_size=b.font_size, font_name=b.font_name)

    def _get_target_font(self, original_font: str) -> fitz.Font | None:
        name_lower = original_font.lower()
        if "bold" in name_lower:
            return self._font_bold or self._font_regular
        if "italic" in name_lower or "oblique" in name_lower:
            return self._font_italic or self._font_regular
        if "courier" in name_lower or "mono" in name_lower:
            return self._font_mono or self._font_regular
        if "times" in name_lower or "serif" in name_lower:
            return self._font_serif or self._font_regular
        return self._font_regular

    def _get_target_font_path(self, original_font: str) -> str:
        name_lower = original_font.lower()
        if "bold" in name_lower:
            return _FONT_BOLD
        if "italic" in name_lower or "oblique" in name_lower:
            return _FONT_ITALIC
        if "courier" in name_lower or "mono" in name_lower:
            return _FONT_MONO
        if "times" in name_lower or "serif" in name_lower:
            return _FONT_SERIF
        return _FONT_REGULAR

    def _get_target_font_name(self, original_font: str) -> str:
        name_lower = original_font.lower()
        if "bold" in name_lower:
            return "DVBold"
        if "italic" in name_lower or "oblique" in name_lower:
            return "DVItalic"
        if "courier" in name_lower or "mono" in name_lower:
            return "DVMono"
        if "times" in name_lower or "serif" in name_lower:
            return "DVSerif"
        return "DVReg"
