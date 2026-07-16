"""Compress oversized PDFs so they fit within OpenAI's file size limit.

Bulletins are occasionally exported with huge embedded images (50MB+).
When a PDF exceeds the limit, we rasterize each page to a JPEG and
rebuild the PDF, stepping down DPI/quality until it fits.
"""

import asyncio
import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# OpenAI rejects files over 50MB; leave headroom.
MAX_PDF_BYTES = 45 * 1024 * 1024

# (dpi, jpeg_quality) steps, tried in order until the result fits
_COMPRESSION_STEPS = [(150, 75), (120, 65), (100, 50)]


def _rasterize(pdf_bytes: bytes, dpi: int, quality: int) -> bytes:
    """Rebuild the PDF with each page rendered as a JPEG."""
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()
    try:
        for page in src:
            pix = page.get_pixmap(dpi=dpi)
            img = pix.tobytes("jpg", jpg_quality=quality)
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=img)
        return out.tobytes(garbage=4, deflate=True)
    finally:
        out.close()
        src.close()


def _compress_sync(pdf_bytes: bytes, max_bytes: int) -> bytes:
    for dpi, quality in _COMPRESSION_STEPS:
        result = _rasterize(pdf_bytes, dpi, quality)
        logger.info(
            f"Compressed PDF at {dpi} DPI / q{quality}: "
            f"{len(pdf_bytes) / 1e6:.1f}MB -> {len(result) / 1e6:.1f}MB"
        )
        if len(result) <= max_bytes:
            return result
    # Best effort: return the smallest attempt even if still over the limit
    return result


async def compress_if_needed(pdf_bytes: bytes, max_bytes: int = MAX_PDF_BYTES) -> bytes:
    """Return pdf_bytes unchanged if within the limit, else a compressed version.

    Rasterization is CPU-bound, so it runs in a thread to avoid blocking
    concurrent parish processing.
    """
    if len(pdf_bytes) <= max_bytes:
        return pdf_bytes
    logger.info(f"PDF is {len(pdf_bytes) / 1e6:.1f}MB (limit {max_bytes / 1e6:.0f}MB), compressing")
    return await asyncio.to_thread(_compress_sync, pdf_bytes, max_bytes)
