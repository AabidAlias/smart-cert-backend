"""
services/pdf_generator.py
Generates a certificate PDF by compositing the name onto the background image.
- QR code removed (template already has its own)
- Certificate number printed in plain sans-serif font at bottom right
"""
import io
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from app.core.config import settings
from app.services.font_service import get_auto_sized_font
from app.utils.helpers import get_logger, cm_to_px

logger = get_logger(__name__)

# ── Certificate number config ──────────────────────────────────────────────────
# Change ORG_PREFIX to match your organization name
ORG_PREFIX = "TEDxSNPSU"
CERT_NUMBER_RANDOM_CHARS = 5


def _generate_cert_number(certificate_id: str) -> str:
    """
    Generate a short unique certificate number.
    Format: TEDxSNPSU-2026-A3F7K
    Derived from the certificate UUID so it's unique per person.
    """
    year = datetime.utcnow().year
    short_id = certificate_id.replace("-", "").upper()[:CERT_NUMBER_RANDOM_CHARS]
    return f"{ORG_PREFIX}-{year}-{short_id}"


def _load_plain_font(size: int = 28) -> ImageFont.FreeTypeFont:
    """
    Load a plain readable sans-serif font for the certificate number.
    Intentionally does NOT use AlexBrush — we want normal text, not cursive.
    """
    candidate_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidate_paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    # Last resort fallback
    logger.warning("No system sans-serif font found, using default font.")
    return ImageFont.load_default()


def generate_certificate_pdf(
    name: str,
    certificate_id: str,
    output_path: str | Path,
    template_path: str | Path | None = None,
) -> Path:
    """
    Generate a PDF certificate for the given name.

    Steps:
      1. Open the certificate template PNG
      2. Draw the recipient name centered at the configured (X, Y) position
      3. Draw a unique certificate number at the bottom right in plain text
      4. Save as a PDF using ReportLab
    """
    template_path = Path(template_path or settings.TEMPLATE_PATH)
    output_path   = Path(output_path)

    if not template_path.exists():
        raise FileNotFoundError(f"Certificate template not found: {template_path}")

    # ── 1. Open background image ──────────────────────────────────────────────
    bg = Image.open(template_path).convert("RGBA")
    img_width_px, img_height_px = bg.size
    logger.info(f"Template size: {img_width_px}x{img_height_px} px")

    # ── 2. Create transparent text layer ─────────────────────────────────────
    # All text is drawn on this transparent layer, then composited on top.
    # This ensures text is always fully visible regardless of background.
    txt_layer = Image.new("RGBA", bg.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)

    # ── 3. Compute name position from .env settings ───────────────────────────
    origin_x_px  = cm_to_px(settings.NAME_X_CM, settings.CM_TO_PX)
    origin_y_px  = cm_to_px(settings.NAME_Y_CM, settings.CM_TO_PX)
    box_width_px = cm_to_px(settings.TEXT_BOX_WIDTH_CM, settings.CM_TO_PX)

    font, font_size = get_auto_sized_font(name)
    logger.info(f"Rendering '{name}' | font size: {font_size} | origin: ({origin_x_px}px, {origin_y_px}px)")

    # ── 4. Center name horizontally within the text box ───────────────────────
    bbox        = font.getbbox(name)
    text_width  = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    text_x = origin_x_px + (box_width_px - text_width) // 2
    text_y = origin_y_px - (text_height // 2)  # vertically center on the Y line

    logger.info(f"Final name position: ({text_x}px, {text_y}px)")

    # ── 5. Draw name in solid black (AlexBrush font) ──────────────────────────
    draw.text((text_x, text_y), name, font=font, fill=(0, 0, 0, 255))

    # ── 6. Draw certificate number at bottom right in plain sans-serif ────────
    cert_number = _generate_cert_number(certificate_id)
    cert_font   = _load_plain_font(size=28)   # plain font, NOT cursive

    cert_bbox = cert_font.getbbox(cert_number)
    cert_w    = cert_bbox[2] - cert_bbox[0]
    cert_h    = cert_bbox[3] - cert_bbox[1]

    margin_right  = cm_to_px(0.6, settings.CM_TO_PX)  # 0.6 cm from right edge
    margin_bottom = cm_to_px(0.4, settings.CM_TO_PX)  # 0.4 cm from bottom edge

    cert_x = img_width_px - cert_w - margin_right
    cert_y = img_height_px - cert_h - margin_bottom

    # Subtle white pill background for readability on any template color
    padding = 10
    draw.rounded_rectangle(
        [cert_x - padding, cert_y - padding,
         cert_x + cert_w + padding, cert_y + cert_h + padding],
        radius=6,
        fill=(255, 255, 255, 180),  # semi-transparent white
    )

    # Draw certificate number in dark gray
    draw.text((cert_x, cert_y), cert_number, font=cert_font, fill=(50, 50, 50, 255))
    logger.info(f"Certificate number '{cert_number}' placed at ({cert_x}px, {cert_y}px)")

    # ── 7. Composite text layer onto background ───────────────────────────────
    combined = Image.alpha_composite(bg, txt_layer)

    # ── 8. Convert RGBA → RGB for PDF embedding ───────────────────────────────
    rgb_img = combined.convert("RGB")

    # ── 9. Write PDF via ReportLab ────────────────────────────────────────────
    img_width_pt  = img_width_px  * 72 / settings.CERT_DPI
    img_height_pt = img_height_px * 72 / settings.CERT_DPI

    buffer = io.BytesIO()
    rgb_img.save(buffer, format="PNG", dpi=(settings.CERT_DPI, settings.CERT_DPI))
    buffer.seek(0)

    pdf_canvas = canvas.Canvas(str(output_path), pagesize=(img_width_pt, img_height_pt))
    pdf_canvas.drawImage(
        ImageReader(buffer),
        0, 0,
        width=img_width_pt,
        height=img_height_pt,
    )
    pdf_canvas.save()

    logger.info(f"Certificate PDF saved: {output_path}")
    return output_path