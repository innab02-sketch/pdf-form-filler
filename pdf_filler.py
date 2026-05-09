"""
PDF Form Filler Module
Uses OpenAI Vision API to detect blank fields in flat PDFs,
then overlays text using reportlab at the correct positions.
Handles Hebrew (RTL) text properly.
"""

import os
import io
import json
import base64
import tempfile
import logging
from pathlib import Path
from typing import Optional

from openai import OpenAI
from pdf2image import convert_from_path
from PIL import Image
from PyPDF2 import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm
import arabic_reshaper
from bidi.algorithm import get_display

logger = logging.getLogger(__name__)

# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent
FONTS_DIR = SCRIPT_DIR / "fonts"

# Register Hebrew-compatible fonts
FONT_PATH = FONTS_DIR / "DejaVuSans.ttf"
if FONT_PATH.exists():
    pdfmetrics.registerFont(TTFont("DejaVuSans", str(FONT_PATH)))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(FONTS_DIR / "DejaVuSans-Bold.ttf")))

# Load profiles
PROFILES_PATH = SCRIPT_DIR / "profiles.json"
with open(PROFILES_PATH, "r", encoding="utf-8") as f:
    PROFILES = json.load(f)


def reshape_hebrew(text: str) -> str:
    """Reshape and reorder Hebrew text for proper RTL display in PDF."""
    if not text:
        return text
    reshaped = arabic_reshaper.reshape(text)
    bidi_text = get_display(reshaped)
    return bidi_text


def image_to_base64(image: Image.Image, max_size: int = 1800) -> str:
    """Convert PIL Image to base64 string, resizing if needed."""
    w, h = image.size
    if max(w, h) > max_size:
        ratio = max_size / max(w, h)
        image = image.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def analyze_page_with_ai(image: Image.Image, page_num: int, profile: dict, client: OpenAI) -> list:
    """
    Use OpenAI Vision API to analyze a PDF page image and identify
    blank fields that should be filled with personal details.
    
    Returns a list of dictionaries with field placement info.
    """
    img_base64 = image_to_base64(image)
    
    profile_info = json.dumps(profile, ensure_ascii=False, indent=2)
    
    prompt = f"""You are analyzing page {page_num} of a Hebrew PDF form to find BLANK fields that need to be filled with personal details.

Person's details to fill in:
{profile_info}

YOUR TASK: Find blank/empty fields and specify EXACTLY where to place the text.

CRITICAL RULES:
1. ONLY fill fields that are genuinely BLANK - look for empty underlines (___), blank spaces after colons, or empty table cells
2. DO NOT fill fields that already have text/data in them
3. NEVER fill credit card number, CVV, expiration date, or any payment-related fields
4. NEVER return empty "text" values - if a field matches profile data, fill it with the correct value
5. For "שם מלא" fields → fill with "{profile.get('שם_מלא', '')}"
6. For "ת.ז" fields → fill with "{profile.get('תז', '')}"
7. For "טלפון נייד" fields → fill with "{profile.get('טלפון_נייד', '')}"
8. For "כתובת מייל" fields → fill with "{profile.get('כתובת_מייל', '')}"
9. For "כתובת" fields → fill with "{profile.get('כתובת', '')}"
10. For "תפקיד" fields → fill with "{profile.get('תפקיד', '')}"
11. Skip "טלפון במשרד" if the profile has no office phone

COORDINATE SYSTEM:
- x_percent: 0 = left edge, 100 = right edge of page
- y_percent: 0 = top edge, 100 = bottom edge of page
- Place text ON the blank line/space, centered horizontally within the blank area

Return a JSON array. Each element must have ALL these fields:
{{
  "field_name": "what this field is",
  "text": "the actual text value to write (NEVER empty if field is identified)",
  "x_percent": <number>,
  "y_percent": <number>,
  "font_size": <number, 10-11 for normal fields>,
  "is_hebrew": <boolean>,
  "alignment": "center"
}}

If there are NO blank fields to fill on this page, return exactly: []
Return ONLY the JSON array with no extra text or markdown."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img_base64}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=3000,
            temperature=0.1
        )
        
        result_text = response.choices[0].message.content.strip()
        
        # Clean up the response - remove markdown code blocks if present
        if result_text.startswith("```"):
            lines = result_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            result_text = "\n".join(lines).strip()
        
        # Try to find JSON array in the response
        if not result_text.startswith("["):
            start = result_text.find("[")
            end = result_text.rfind("]") + 1
            if start != -1 and end > start:
                result_text = result_text[start:end]
        
        fields = json.loads(result_text)
        
        # Filter out fields with empty text or payment-related fields
        filtered_fields = []
        payment_keywords = ["כרטיס", "cvv", "תאריך תוקף", "בתוקף", "מספר כרטיס", "credit", "card"]
        
        for f in fields:
            text = f.get("text", "").strip()
            field_name = f.get("field_name", "").lower()
            
            # Skip empty text fields
            if not text:
                continue
            
            # Skip payment-related fields
            if any(kw in field_name for kw in payment_keywords):
                continue
            
            # Skip if field_name contains "הסכם" (contract number - we don't have this)
            if "הסכם" in field_name and text == "":
                continue
                
            filtered_fields.append(f)
        
        logger.info(f"Page {page_num}: Found {len(filtered_fields)} fields to fill")
        for f in filtered_fields:
            logger.info(f"  - {f.get('field_name')}: '{f.get('text')}' at ({f.get('x_percent')}, {f.get('y_percent')})")
        return filtered_fields
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response for page {page_num}: {e}")
        logger.error(f"Response was: {result_text[:500]}")
        return []
    except Exception as e:
        logger.error(f"AI analysis failed for page {page_num}: {e}")
        return []


def create_overlay_pdf(fields: list, page_width: float, page_height: float) -> io.BytesIO:
    """
    Create a PDF overlay with text at the specified positions.
    Uses reportlab to generate a transparent PDF page with text.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_width, page_height))
    
    for field in fields:
        text = field.get("text", "")
        if not text:
            continue
            
        x_pct = field.get("x_percent", 50)
        y_pct = field.get("y_percent", 50)
        font_size = field.get("font_size", 11)
        is_hebrew = field.get("is_hebrew", False)
        alignment = field.get("alignment", "center")
        
        # Convert percentage to actual PDF coordinates
        # PDF origin is bottom-left, image origin is top-left
        x = (x_pct / 100.0) * page_width
        y = page_height - (y_pct / 100.0) * page_height
        
        # Set font
        c.setFont("DejaVuSans", font_size)
        c.setFillColorRGB(0, 0, 0)  # Black text
        
        # Process text for display
        if is_hebrew:
            display_text = reshape_hebrew(text)
        else:
            display_text = text
        
        # Calculate text width for alignment
        text_width = c.stringWidth(display_text, "DejaVuSans", font_size)
        
        # Center the text at the specified position
        draw_x = x - text_width / 2
        
        # Ensure text doesn't go off-page
        if draw_x < 5:
            draw_x = 5
        if draw_x + text_width > page_width - 5:
            draw_x = page_width - text_width - 5
        
        c.drawString(draw_x, y, display_text)
    
    c.save()
    buffer.seek(0)
    return buffer


def fill_pdf(input_pdf_path: str, profile_name: str) -> Optional[str]:
    """
    Main function to fill a PDF form with personal details.
    
    Args:
        input_pdf_path: Path to the input PDF file
        profile_name: Either "אינה" or "אריק"
    
    Returns:
        Path to the filled PDF file, or None if failed
    """
    if profile_name not in PROFILES:
        logger.error(f"Unknown profile: {profile_name}")
        return None
    
    profile = PROFILES[profile_name]
    
    # Initialize OpenAI client with explicit API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY environment variable is not set!")
        raise ValueError("Missing credentials. Please set the OPENAI_API_KEY environment variable.")
    logger.info(f"OpenAI API key found: {api_key[:8]}...")
    client = OpenAI(api_key=api_key)
    
    # Step 1: Convert PDF pages to images for AI analysis
    logger.info("Converting PDF to images...")
    try:
        images = convert_from_path(input_pdf_path, dpi=200)
    except Exception as e:
        logger.error(f"Failed to convert PDF to images: {e}")
        return None
    
    # Step 2: Read the original PDF
    reader = PdfReader(input_pdf_path)
    writer = PdfWriter()
    
    # Step 3: Process each page
    for page_num, (image, page) in enumerate(zip(images, reader.pages), 1):
        logger.info(f"Processing page {page_num}/{len(reader.pages)}...")
        
        # Get page dimensions in points (PDF units)
        page_box = page.mediabox
        page_width = float(page_box.width)
        page_height = float(page_box.height)
        
        # Analyze the page with AI
        fields = analyze_page_with_ai(image, page_num, profile, client)
        
        if fields:
            # Create overlay PDF with the text
            overlay_buffer = create_overlay_pdf(fields, page_width, page_height)
            
            # Merge overlay with original page
            overlay_reader = PdfReader(overlay_buffer)
            if len(overlay_reader.pages) > 0:
                overlay_page = overlay_reader.pages[0]
                page.merge_page(overlay_page)
        
        writer.add_page(page)
    
    # Step 4: Save the filled PDF
    output_path = input_pdf_path.replace(".pdf", "_filled.pdf")
    if output_path == input_pdf_path:
        output_path = input_pdf_path + "_filled.pdf"
    
    with open(output_path, "wb") as f:
        writer.write(f)
    
    logger.info(f"Filled PDF saved to: {output_path}")
    return output_path


if __name__ == "__main__":
    # Test with a sample PDF
    import sys
    logging.basicConfig(level=logging.INFO)
    
    if len(sys.argv) < 3:
        print("Usage: python pdf_filler.py <pdf_path> <profile_name>")
        print("Profile names: אינה, אריק")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    profile = sys.argv[2]
    
    result = fill_pdf(pdf_path, profile)
    if result:
        print(f"Success! Filled PDF: {result}")
    else:
        print("Failed to fill PDF")
        sys.exit(1)
