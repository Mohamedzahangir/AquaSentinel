"""
macOS Vision OCR helper.

Renders a clip of a PDF page at high DPI and runs Apple's Vision text recognizer,
returning recognised strings with their bounding boxes mapped back into PDF-point
coordinates of the ORIGINAL page.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import fitz
import Quartz
import Vision
from Foundation import NSData


@dataclass
class OcrItem:
    text: str
    conf: float
    # bbox in PDF points on the source page (x0, y0, x1, y1), y measured from top
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


def _cgimage_from_png(png_bytes: bytes):
    data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    if src is None:
        raise RuntimeError("failed to create CGImageSource")
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def ocr_image_bytes(
    png_bytes: bytes,
    *,
    level: str = "accurate",
    languages=("en-US",),
    min_conf: float = 0.0,
    custom_words=(),
):
    """Run Vision OCR. Returns list of (text, conf, (nx0, ny0, nx1, ny1)) with
    normalised coords: origin BOTTOM-left, 0..1."""
    cg = _cgimage_from_png(png_bytes)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)

    results = []

    def _handler(request, error):
        obs = request.results()
        if not obs:
            return
        for o in obs:
            cands = o.topCandidates_(1)
            if not cands:
                continue
            c = cands[0]
            conf = float(c.confidence())
            if conf < min_conf:
                continue
            bb = o.boundingBox()
            nx0 = bb.origin.x
            ny0 = bb.origin.y
            nx1 = bb.origin.x + bb.size.width
            ny1 = bb.origin.y + bb.size.height
            results.append((str(c.string()), conf, (nx0, ny0, nx1, ny1)))

    req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_handler)
    req.setRecognitionLevel_(
        Vision.VNRequestTextRecognitionLevelAccurate
        if level == "accurate"
        else Vision.VNRequestTextRecognitionLevelFast
    )
    req.setUsesLanguageCorrection_(False)  # numbers/IDs — correction hurts
    req.setRecognitionLanguages_(list(languages))
    if custom_words:
        req.setCustomWords_(list(custom_words))
    # revision 3 supports more languages / better accuracy where available
    try:
        req.setRevision_(3)
    except Exception:
        pass

    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision failed: {err}")
    return results


def _binarize_keep_dark(png_bytes: bytes, thresh: int = 130) -> bytes:
    """Keep only near-black ink; everything else becomes white.

    The AutoCAD tables use coloured hatch fills to highlight rows. The hatch
    dots sit at mid luminance and badly confuse the recogniser, while the text
    itself is pure black — so a hard luminance threshold removes the shading
    without touching the glyphs.
    """
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    a = np.asarray(im).astype(np.int16)
    # luminance
    lum = (0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2])
    out = np.where(lum < thresh, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(out, mode="L").convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def ocr_pdf_clip(
    page: fitz.Page,
    clip: fitz.Rect,
    *,
    dpi: int = 400,
    rotate: int = 0,
    min_conf: float = 0.3,
    level: str = "accurate",
    custom_words=(),
    binarize: bool = False,
    thresh: int = 130,
) -> list[OcrItem]:
    """OCR a rectangular region of a PDF page.

    `rotate` (0/90/180/270) rotates the rendered bitmap before OCR — needed for
    the sideways UTM labels along the map frame. Returned bboxes are always in
    the source page's PDF-point space (y from top).
    """
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    if rotate:
        mat = mat.prerotate(rotate)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    png = pix.tobytes("png")
    W, H = pix.width, pix.height
    if binarize:
        png = _binarize_keep_dark(png, thresh=thresh)

    raw = ocr_image_bytes(png, min_conf=min_conf, level=level, custom_words=custom_words)

    items: list[OcrItem] = []
    for text, conf, (nx0, ny0, nx1, ny1) in raw:
        # normalised (origin bottom-left) -> pixel (origin top-left) of rendered bitmap
        px0, px1 = nx0 * W, nx1 * W
        py0, py1 = (1 - ny1) * H, (1 - ny0) * H
        # undo the rotation to get pixel coords in the un-rotated clip render
        if rotate == 0:
            qx0, qy0, qx1, qy1 = px0, py0, px1, py1
            CW, CH = W, H
        elif rotate == 90:
            # rendered = source rotated 90° CW: src_x = y, src_y = W_r - x
            CW, CH = H, W
            qx0, qx1 = py0, py1
            qy0, qy1 = W - px1, W - px0
        elif rotate == 180:
            CW, CH = W, H
            qx0, qx1 = W - px1, W - px0
            qy0, qy1 = H - py1, H - py0
        elif rotate == 270:
            CW, CH = H, W
            qx0, qx1 = H - py1, H - py0
            qy0, qy1 = px0, px1
        else:
            raise ValueError(rotate)

        items.append(
            OcrItem(
                text=text,
                conf=conf,
                x0=clip.x0 + qx0 / zoom,
                y0=clip.y0 + qy0 / zoom,
                x1=clip.x0 + qx1 / zoom,
                y1=clip.y0 + qy1 / zoom,
            )
        )
    return items
