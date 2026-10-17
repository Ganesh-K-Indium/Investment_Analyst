import io
import logging
import os
import time
import fitz
import json
import openai
import base64
import hashlib
from collections import Counter
from datetime import datetime
from tqdm import tqdm
from PIL import Image, ImageEnhance
from langchain_core.documents import Document
from pathlib import Path
from dotenv import load_dotenv
import pytesseract

# Ensure app/utils is importable when this module is loaded standalone from
# ingestion/ (mirrors the same sys.path setup in ingestion/pdf_processor1.py)
import sys
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from app.utils.usage_tracker import record_usage

load_dotenv()

logger = logging.getLogger("ingestion.image_data_prep")

# --- Pre-LLM image filtering -------------------------------------------------
# Heavily designed PDFs (e.g. Sanofi's 20-F) embed hundreds of decorative
# elements: nav sidebars repeated on every page, icons, transparent overlays,
# portraits. Every image that reaches GPT-4o costs ~2.5K tokens, so these are
# rejected deterministically (free) before any vision call is made.
REPEAT_PAGE_THRESHOLD = 3    # same image on >= N pages -> header/sidebar/logo
MIN_DISPLAY_PT = 40          # rendered smaller than this (points) on the page -> icon
MAX_ASPECT_RATIO = 12        # thinner than this -> rule/strip, not a chart or table
MIN_INK_FRACTION = 0.005     # < 0.5% of pixels differ from the background -> blank/overlay
# OCR gate: a chart/table has many words Tesseract reads with high confidence
# (measured 30-57 per image on real 10-K charts); photos and ornaments have 0-1
# (garbage characters, low confidence). Raw character counts do NOT separate
# them — Tesseract emits noise on photos. 0 disables the gate.
MIN_OCR_WORDS = int(os.getenv("IMAGE_MIN_OCR_WORDS", "3"))
OCR_WORD_CONFIDENCE = 70

# Refusals the vision model returns for photos/blank images. They are not
# captions and must never be stored or embedded.
_REFUSAL_MARKERS = (
    "i'm sorry", "i am sorry", "i can't", "i cannot", "i'm unable", "i am unable",
    "unable to process", "unable to extract", "unable to assist", "can't assist",
    "cannot assist", "can't extract", "cannot extract", "portrait photograph",
)

# Use the explicit binary path if set (required for Docker/ECS environments)
_tesseract_cmd = os.environ.get("TESSERACT_CMD")
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

class ImageDescription:
    "This method is used to get the description of the image."
    def __init__(self, pdf_path, filing_type: str = None):
        """
        This constructor is used to initialize the path of the pdf.
        Args:
            pdf_path : The path of the pdf.
            filing_type: SEC filing type ("10-K", "10-Q", "8-K", "20-F", "6-K"), if known — used to
                frame the vision-analysis prompt accurately instead of assuming 10-K.
        """
        self.pdf_path = pdf_path
        self.filing_type = filing_type
        self.openai_client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # Why images were dropped before/after analysis, logged as a summary.
        self.filter_stats = Counter()
    
    def extract_text_from_image_ocr(self, image_path):
        """
        Extract ALL text and numbers from image using OCR.
        Critical for financial documents where every number matters.
        Uses Tesseract OCR to capture all visible text.
        """
        try:
            if not os.path.exists(image_path):
                return ""
            
            # Read and preprocess image for better OCR
            img = Image.open(image_path)
            
            # Convert to RGB if needed
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Enhance for better OCR
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.5)  # Increase contrast
            
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.5)  # Increase sharpness
            
            # Extract text using Tesseract with optimized config for tables/numbers
            # --psm 6: Assume a single uniform block of text
            # --oem 3: Use both legacy and LSTM engines
            custom_config = r'--oem 3 --psm 6'
            extracted_text = pytesseract.image_to_string(img, config=custom_config)
            
            return extracted_text.strip()
            
        except Exception as e:
            # If Tesseract not installed, gracefully degrade
            if "TesseractNotFoundError" in str(type(e).__name__):
                logger.warning("     Tesseract OCR not installed - using vision-only mode")
                return "[OCR unavailable - using GPT-4o vision only]"
            logger.error("     OCR extraction failed for %s: %s", os.path.basename(image_path), e)
            return ""
    
    def count_confident_ocr_words(self, image_path):
        """
        Number of words Tesseract reads with confidence >= OCR_WORD_CONFIDENCE.
        Returns None when OCR is unavailable or fails, so callers fail open
        (never discard an image just because the gate could not run).
        """
        try:
            img = Image.open(image_path).convert('RGB')
            data = pytesseract.image_to_data(
                img, output_type=pytesseract.Output.DICT, config=r'--oem 3 --psm 6')
            return sum(
                1 for text, conf in zip(data["text"], data["conf"])
                if len(text.strip()) >= 2 and float(conf) >= OCR_WORD_CONFIDENCE
            )
        except Exception as e:
            logger.warning("     OCR gate unavailable for %s (%s) — sending to vision",
                           os.path.basename(image_path), type(e).__name__)
            return None

    def calculate_image_content_hash(self, image_data: bytes) -> str:
        """Calculate a deterministic hash of individual image content."""
        try:
            return hashlib.sha256(image_data).hexdigest()
        except Exception as e:
            logger.error("Error calculating image content hash: %s", e)
            return ""
    
    def get_pdf_data(self):
        """
        this method is used to get the fitz object (pdf_document) of the pdf.
        Args:
            None
        Return:
            pdf_document : fitz object of the pdf document.
        """
        pdf_document = fitz.open(self.pdf_path)
        return pdf_document
    
    def _flatten_on_white(self, original_img, smask_xref, pdf_document):
        """
        Composite an extracted image onto a white background using its
        transparency, so it looks the way it does on the rendered page.

        PDFs store transparency separately from the colour data: an image with
        an /SMask has its alpha in a second image object, and extract_image()
        returns only the colour layer. Converting that straight to RGB turns
        every transparent pixel black, so icons, charts and overlays on
        designed pages come out as black rectangles.
        """
        rgb = original_img.convert('RGB')
        mask = None
        try:
            if smask_xref:
                mask_data = pdf_document.extract_image(smask_xref)
                if mask_data:
                    mask = Image.open(io.BytesIO(mask_data["image"])).convert('L')
                    if mask.size != rgb.size:
                        mask = mask.resize(rgb.size, Image.Resampling.LANCZOS)
            elif original_img.mode in ('RGBA', 'LA') or (
                    original_img.mode == 'P' and 'transparency' in original_img.info):
                mask = original_img.convert('RGBA').getchannel('A')
        except Exception as e:
            logger.warning("Could not apply transparency mask (smask=%s): %s", smask_xref, e)
            mask = None

        if mask is None:
            return rgb
        background = Image.new('RGB', rgb.size, (255, 255, 255))
        background.paste(rgb, mask=mask)
        return background

    @staticmethod
    def _ink_fraction(img):
        """Fraction of pixels that differ noticeably from the dominant background."""
        gray = img.convert('L')
        gray.thumbnail((256, 256), Image.Resampling.BOX)  # BOX keeps thin lines from vanishing entirely
        hist = gray.histogram()
        total = sum(hist)
        if not total:
            return 0.0
        # Background = most common grey level; ink = anything meaningfully different.
        bg = max(range(256), key=lambda i: hist[i])
        ink = sum(hist[i] for i in range(256) if abs(i - bg) > 12)
        return ink / total

    def save_images(self,img_info,page_num,pdf_document,output_dir):
        """
        Enhanced image preprocessing optimized for financial data extraction with better detail preservation.
        """
        try:
            xref = img_info[0]
            smask_xref = img_info[1] if len(img_info) > 1 else 0
            base_image = pdf_document.extract_image(xref)
            if not base_image:
                return None, None

            image_bytes = base_image["image"]
            original_img = Image.open(io.BytesIO(image_bytes))

            # Flatten onto white using the image's transparency (see _flatten_on_white)
            img = self._flatten_on_white(original_img, smask_xref, pdf_document)

            original_size = img.size
            min_dimension = min(img.size)
            max_dimension = max(img.size)

            # Skip tiny images (likely decorative)
            if min_dimension < 50 or max_dimension < 50:
                logger.warning("Skipping very small image: %s", original_size)
                self.filter_stats["tiny"] += 1
                return None, None

            # Skip blank/near-blank images: overlays, spacers, a single rule line.
            # Checked on the flattened image, before the enhancement steps below
            # amplify noise.
            if self._ink_fraction(img) < MIN_INK_FRACTION:
                logger.info("Skipping blank/overlay image xref=%s on page %d", xref, page_num + 1)
                self.filter_stats["blank"] += 1
                return None, None
            
            # Multi-stage enhancement for financial documents
            
            # 1. Size optimization first (do this before enhancement for better quality)
            target_size = None
            if min_dimension < 512:
                # Scale up small images significantly for better OCR/analysis
                scale_factor = 512 / min_dimension
                target_size = (int(img.size[0] * scale_factor), int(img.size[1] * scale_factor))
                logger.info("Upscaling image from %s to %s", original_size, target_size)
            elif max_dimension > 2048:
                # Scale down very large images but keep good detail
                scale_factor = 2048 / max_dimension
                target_size = (int(img.size[0] * scale_factor), int(img.size[1] * scale_factor))
                logger.info("Downscaling image from %s to %s", original_size, target_size)
            
            if target_size:
                img = img.resize(target_size, Image.Resampling.LANCZOS)
            
            # 2. Enhanced contrast for better text/number visibility
            contrast_enhancer = ImageEnhance.Contrast(img)
            img = contrast_enhancer.enhance(1.4)  # Higher contrast for charts/tables
            
            # 3. Sharpness enhancement for clearer details
            sharpness_enhancer = ImageEnhance.Sharpness(img)
            img = sharpness_enhancer.enhance(1.3)  # More sharpness for text
            
            # 4. Slight brightness adjustment for optimal viewing
            brightness_enhancer = ImageEnhance.Brightness(img)
            img = brightness_enhancer.enhance(1.08)
            
            # Create descriptive filename with metadata
            img_hash = hashlib.md5(image_bytes).hexdigest()[:8]
            img_path = os.path.join(output_dir, f"financial_img_{xref}_page{page_num+1}_{img_hash}.png")
            
            # Save with high quality settings (lower compression for better detail)
            img.save(img_path, "PNG", optimize=True, compress_level=3)
            
            logger.info("✓ Enhanced image: %s (final size: %s)", os.path.basename(img_path), img.size)
            return img_path, xref

        except Exception as e:
            logger.error("Error processing image %s: %s", xref, e)
            return None, None
    
    def get_comprehensive_image_context(self, xref, page, text_blocks):
        """
        Enhanced context extraction that finds relevant text even for standalone tables/charts.
        Uses multi-stage approach: nearby text > section headers > page content.
        """
        try:
            img_rects = page.get_image_rects(xref)
            if not img_rects:
                return ""
            
            img_rect = img_rects[0]
            
            # Stage 1: Collect text in proximity to image (wider search)
            nearby_text = []
            section_headers = []
            all_text = []
            
            for block in text_blocks:
                block_rect = fitz.Rect(block[:4])
                block_text = block[4].strip()
                
                if not block_text or len(block_text) < 3:
                    continue
                
                # Clean and normalize text
                block_text = ' '.join(block_text.split())
                
                # Calculate distance from image
                img_center_y = (img_rect.y0 + img_rect.y1) / 2
                block_center_y = (block_rect.y0 + block_rect.y1) / 2
                vertical_distance = abs(img_center_y - block_center_y)
                
                # Identify section headers (typically larger/bold text above image)
                is_above = block_center_y < img_rect.y0
                is_likely_header = (
                    is_above and 
                    vertical_distance < 150 and 
                    len(block_text) < 200 and
                    not block_text[0].islower()  # Starts with capital
                )
                
                if is_likely_header:
                    section_headers.append({
                        'text': block_text,
                        'distance': vertical_distance
                    })
                
                # Nearby text (expanded range)
                if vertical_distance <= 400:  # Increased from 200
                    nearby_text.append({
                        'text': block_text,
                        'distance': vertical_distance,
                        'position': 'above' if is_above else 'below'
                    })
                
                # Keep all page text as fallback
                all_text.append(block_text)
            
            # Build context from best available source
            context_parts = []
            
            # Prefer section headers
            if section_headers:
                section_headers.sort(key=lambda x: x['distance'])
                for header in section_headers[:2]:
                    if header['text'] not in context_parts:
                        context_parts.append(header['text'])
            
            # Add nearby text
            if nearby_text:
                nearby_text.sort(key=lambda x: x['distance'])
                for item in nearby_text[:6]:  # Take more text blocks
                    text = item['text']
                    if len(text) > 10 and text not in context_parts:
                        context_parts.append(text)
            
            # Fallback: use any meaningful page content
            if not context_parts and all_text:
                # Take first few meaningful paragraphs from page
                for text in all_text[:10]:
                    if len(text) > 20:
                        context_parts.append(text)
                        if len(context_parts) >= 3:
                            break
            
            # Join context
            final_context = " ".join(context_parts)
            
            # Limit context length for efficiency
            if len(final_context) > 1000:
                words = final_context.split()
                final_context = " ".join(words[:150])
            
            return final_context
            
        except Exception as e:
            logger.error("Error extracting context for image %s: %s", xref, e)
            return ""
    

    
    def get_preceeding_text(self, xref, page, text_blocks):
        """Get clean context text around image for efficient RAG retrieval."""
        return self.get_comprehensive_image_context(xref, page, text_blocks)
    
    def _prefilter_reason(self, img_info, page, pages_per_xref):
        """
        Return why an image is decorative, or None if it deserves a closer look.
        Uses only metadata already in the PDF (no pixel decoding).
        """
        xref, width, height = img_info[0], img_info[2], img_info[3]

        if pages_per_xref[xref] >= REPEAT_PAGE_THRESHOLD:
            return "repeated"  # logo / sidebar / header strip

        if width and height and max(width, height) / max(1, min(width, height)) > MAX_ASPECT_RATIO:
            return "thin"      # rule or strip, not a chart/table

        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        if rects and min(rects[0].width, rects[0].height) < MIN_DISPLAY_PT:
            return "small"     # icon: rendered tiny on the page regardless of pixel size
        return None

    def get_image_information(self):
        """
        Simplified image extraction with clean context text and image hashing for efficient RAG retrieval.
        Returns both image details and image hashes.
        """
        image_details = {}
        image_hashes = {}  # Store image hashes during extraction
        output_path = os.path.splitext(self.pdf_path)[0]
        os.makedirs(output_path, exist_ok=True)
        
        pdf_document = self.get_pdf_data()
        total_images = 0
        processed_images = 0
        self.filter_stats = Counter()
        seen_hashes = set()

        try:
            logger.info("\n  Processing PDF: %s", os.path.basename(self.pdf_path))

            # Pre-scan: on how many pages does each image object appear? A logo,
            # nav sidebar or header strip repeats on many pages; real charts and
            # tables appear once.
            pages_per_xref = Counter()
            for _pn in range(len(pdf_document)):
                for _xref in {info[0] for info in pdf_document[_pn].get_images(full=True)}:
                    pages_per_xref[_xref] += 1

            # Process each page with progress bar
            for page_num in tqdm(range(len(pdf_document)), desc="Scanning pages for images", unit="page"):
                page = pdf_document[page_num]
                text_blocks = page.get_text("blocks")
                images = page.get_images(full=True)

                if not images:
                    continue

                total_images += len(images)

                # Process each image on the page
                for img_index, img_info in enumerate(images):
                    xref = img_info[0]

                    # Cheap, free rejections first — nothing is extracted, saved or sent to an LLM.
                    skip_reason = self._prefilter_reason(img_info, page, pages_per_xref)
                    if skip_reason:
                        self.filter_stats[skip_reason] += 1
                        continue

                    # Same bytes already handled (e.g. one photo placed on two pages,
                    # or re-embedded under a different object number).
                    try:
                        _base = pdf_document.extract_image(xref)
                        _digest = hashlib.sha256(_base["image"]).hexdigest() if _base else None
                    except Exception:
                        _digest = None
                    if _digest and _digest in seen_hashes:
                        self.filter_stats["duplicate"] += 1
                        continue

                    img_path, xref = self.save_images(img_info, page_num, pdf_document, output_path)
                    if img_path and _digest:
                        seen_hashes.add(_digest)

                    if img_path and xref:
                        # Calculate hash for this image during extraction
                        try:
                            # Get the image data for hashing
                            base_image = pdf_document.extract_image(xref)
                            if base_image:
                                image_bytes = base_image["image"]
                                img_hash = self.calculate_image_content_hash(image_bytes)

                                # Store hash with unique identifier
                                img_id = f"page{page_num + 1}_img{img_index}"
                                image_hashes[img_id] = {
                                    "hash": img_hash,
                                    "page": page_num + 1,
                                    "index": img_index,
                                    "size": len(image_bytes),
                                    "xref": xref,
                                    "path": img_path
                                }
                        except Exception as e:
                            logger.warning("Could not hash image %s: %s", xref, e)
                        
                        # Get clean context text around the image
                        context_text = self.get_comprehensive_image_context(xref, page, text_blocks)
                        image_details[img_path] = context_text
                        processed_images += 1
                    
            logger.info("Successfully processed %d/%d images", processed_images, total_images)
            logger.info("Generated %d image hashes", len(image_hashes))
            if self.filter_stats:
                logger.info(
                    "Image pre-filter: %d found -> %d kept for analysis | dropped before any LLM call: %s",
                    total_images, processed_images,
                    ", ".join(f"{k}={v}" for k, v in sorted(self.filter_stats.items())),
                )
            return image_details, image_hashes  # Return both details and hashes

        except Exception as e:
            logger.error("Error during image extraction: %s", e)
            return image_details, image_hashes
        finally:
            pdf_document.close()
    
    def encode_image(self,image_path):
        """
        Optimized image encoding with size management for API limits.
        """
        try:
            if not os.path.exists(image_path):
                return None
            
            # Check file size (OpenAI has 20MB limit)
            file_size = os.path.getsize(image_path)
            max_size = 20 * 1024 * 1024  # 20MB
            
            if file_size > max_size:
                # Compress large images
                img = Image.open(image_path)
                img_io = io.BytesIO()
                quality = max(60, int(100 * max_size / file_size))
                img.save(img_io, format='JPEG', quality=quality, optimize=True)
                img_bytes = img_io.getvalue()
            else:
                with open(image_path, "rb") as img_file:
                    img_bytes = img_file.read()
            
            return base64.b64encode(img_bytes).decode("utf-8")
            
        except Exception as e:
            logger.error("Error encoding image %s: %s", image_path, e)
            return None

    async def analyze_image_with_context(self, image_path, context_text):
        """
        COMPREHENSIVE image analysis using OCR + GPT-4o Vision.
        Ensures ALL financial data is extracted - critical for 10-K documents.
        """
        if not os.path.exists(image_path):
            return "Error: Image file not found"

        try:
            # Step 0: free local gate before the paid vision call. A chart or table
            # always contains words Tesseract reads confidently; photos, icons and
            # ornaments do not. This is the last deterministic filter before GPT-4o.
            confident_words = self.count_confident_ocr_words(image_path)
            if MIN_OCR_WORDS and confident_words is not None and confident_words < MIN_OCR_WORDS:
                logger.info("    Skipping %s: %d confident OCR word(s) (< %d) — no data to extract",
                            os.path.basename(image_path), confident_words, MIN_OCR_WORDS)
                self.filter_stats["no_text"] += 1
                return None

            # Step 1: Extract ALL text using OCR
            logger.info("    Running OCR on %s...", os.path.basename(image_path))
            _ocr_start = time.perf_counter()
            ocr_text = self.extract_text_from_image_ocr(image_path)
            record_usage(
                stage="ingestion.image_ocr", model="tesseract",
                duration_seconds=time.perf_counter() - _ocr_start,
                detail=os.path.basename(image_path),
            )

            if ocr_text:
                logger.info("    OCR extracted %d characters", len(ocr_text))
            else:
                logger.warning("     OCR found no text")

            # Step 2: Encode image for GPT-4o
            image_base64 = self.encode_image(image_path)
            if not image_base64:
                return "Error: Failed to encode image"
            
            # Step 3: Enhanced prompt with OCR data - OPTIMIZED FOR RAG RETRIEVAL
            filing_label = f"{self.filing_type} filing" if self.filing_type else "SEC filing"
            prompt = f"""
            You are analyzing a financial document image ({filing_label}). Your job is to extract EVERY piece of financial data in a format optimized for semantic search and retrieval.

            CONTEXT FROM SURROUNDING TEXT IN PDF:
            {context_text}

            OCR-EXTRACTED TEXT FROM IMAGE:
            {ocr_text}

            CRITICAL INSTRUCTIONS FOR {filing_label.upper()} DOCUMENTS:
            ============================================
            1. Extract EVERY SINGLE NUMBER visible in the image
            2. Extract ALL row and column headers from tables
            3. Extract ALL data points from charts/graphs
            4. Include units (millions, billions, %, $, etc.)
            5. Preserve exact values - DO NOT round or summarize
            6. If OCR text contains numbers, USE THEM - don't guess from visual
            7. Describe ONLY what is visibly in the image or in the OCR text. The surrounding PDF text is
               context for naming things, NEVER a source of values — do not invent a chart or table
               that is not in the image
            8. If the image has no readable data (icon, logo, photo, ornament, blank), respond with exactly INVALID_IMAGE
            9. START with searchable summary for optimal retrieval
            
            REQUIRED OUTPUT FORMAT (RETRIEVAL-OPTIMIZED):
            =============================================
            
            **SEARCHABLE SUMMARY:** [Write 2-3 sentences describing what data this contains, mentioning specific years/periods, metrics, and key values. This helps retrieval systems find this data. Example: "Stock price performance comparison of Alphabet Inc. Class A from 2019 to 2024, showing prices ranging from $100 to $240. Includes quarterly data points from 12/19 through 12/24 with comparisons to S&P 500, NASDAQ Composite, and RDG Internet Composite indices."]
            
            **KEYWORDS:** [List 10-15 searchable terms: company names, metric types (stock price, revenue, etc.), years, chart type, financial terms. Example: Alphabet, Google, stock price, 2022, 2023, 2024, cumulative return, performance, S&P 500, NASDAQ, line chart, investment, comparison]
            
            **Image Type:** [Table | Bar Chart | Line Chart | Pie Chart | Financial Statement | Other]
            
            **Title/Topic:** [Exact title or main topic]
            
            **Time Periods Covered:** [List ALL years, quarters, or specific dates: 2019, 2020, 2021, 2022, 2023, 2024, etc.]
            
            **Companies/Entities Mentioned:** [List all company names or indices mentioned]
            
            **Complete Data Extraction:**
            
            [FOR TABLES - Extract in this format:]
            TABLE STRUCTURE:
            - Column Headers: [list ALL column names]
            - Row Labels: [list ALL row names]
            
            TABLE DATA (extract EVERY cell):
            Row1: Col1=value1, Col2=value2, Col3=value3, ...
            Row2: Col1=value1, Col2=value2, Col3=value3, ...
            [Continue for ALL rows]
            
            [FOR CHARTS/GRAPHS - CRITICAL: Extract ACTUAL VALUES, not descriptions!]
            CHART DATA:
            - Type: [bar/line/pie/scatter]
            - X-axis Label: [name]
            - Y-axis Label: [name and unit]
            - Time Points: [list ALL dates/periods on X-axis]
            
            DATA SERIES (MANDATORY - Read actual values from the chart):
            For EACH line/series, trace it across the chart and READ the Y-axis value at each X-axis point:
            
            Series: [Name from legend]
            - [Date1]: $[value] (read from Y-axis where line intersects)
            - [Date2]: $[value] (read from Y-axis where line intersects)
            - [Date3]: $[value] (read from Y-axis where line intersects)
            [Continue for EVERY time point]
            
            Example for stock chart:
            Series: Alphabet Inc. Class A
            - 12/19: $100
            - 3/20: $95
            - 6/20: $110
            [etc. for all points]
            
            CRITICAL: Do NOT write "Data points not numerically specified"
            You MUST estimate values by visually reading where each line crosses each time point.
            
            **All Numbers Found:** [List EVERY number with its label: "Total Revenue 2023: $134,902M", "Growth Rate: 15.7%", etc.]
            
            **Key Metrics:** [Main KPIs and their values]
            
            **Notes/Footnotes:** [Any footnotes, asterisks, or notes visible]
            
            **Search Keywords:** [Terms for searchability: revenue, EBITDA, assets, etc.]
            
            VALIDATION: Before submitting, verify you've extracted:
            - ✓ Every number from OCR text
            - ✓ Every row and column if it's a table
            - ✓ Every data point if it's a chart (NOT "not specified")
            - ✓ For line charts: Actual values at EVERY time point for EVERY line
            - ✓ For stock charts: Price at every date shown
            - ✓ All time periods
            - ✓ All labels and headers
            
            CHART VALIDATION EXAMPLE:
             BAD: "Data Series 1 [Stock A]: Data points not numerically specified"
             GOOD: "Data Series 1 [Stock A]: 12/19=$100, 3/20=$95, 6/20=$110, 9/20=$130..."
            
            Respond with exactly "INVALID_IMAGE" (nothing else) if the image is an icon, logo, photo, border,
            background or any graphic with no data. The rules above about estimating chart values apply
            ONLY to images that really contain a chart.
            """

            _vision_start = time.perf_counter()
            response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": f"""You are a financial data extraction specialist for SEC filings (10-K, 10-Q, 8-K, 20-F, 6-K) optimized for RAG retrieval systems. This image is from a {filing_label}.
                        Your SOLE PURPOSE is to extract EVERY SINGLE piece of data from financial images in a format that enables precise semantic search.
                        
                        MANDATORY EXTRACTION RULES:
                        ===========================
                        1. START with SEARCHABLE SUMMARY - describe the data in natural language mentioning years, metrics, and values
                        2. Include KEYWORDS upfront - list 10-15 terms users would search for
                        3. Extract 100% of numbers - EVERY value, percentage, dollar amount
                        4. For tables: Extract EVERY cell in EVERY row and column
                        5. For LINE/BAR CHARTS: READ actual values where lines/bars intersect time points
                        6. Use OCR text provided - it contains actual numbers
                        7. VISUALLY READ values from charts - trace each line and read Y-axis value
                        8. Never write "not specified" or "not numerically specified"
                        9. Never summarize - extract complete raw data
                        10. Never skip rows because they seem similar
                        11. Include ALL units ($, M, B, %, basis points)
                        12. Preserve ALL time periods (years, quarters, dates)
                        13. Extract ALL headers, labels, footnotes, asterisks
                        14. Cross-reference image with OCR to ensure accuracy
                        
                        CHART-SPECIFIC RULES:
                        =====================
                        - For LINE CHARTS: Trace each line from left to right
                        - At EACH X-axis point, read the Y-axis value where the line crosses
                        - For STOCK CHARTS: Extract the price at every time period shown
                        - If OCR doesn't have values, ESTIMATE from visual by reading grid lines
                        - Approximate to nearest visible grid value (e.g., if between $145-$150, estimate $147)
                        
                        QUALITY STANDARDS:
                        ==================
                        - If table has 50 rows, extract ALL 50 rows
                        - If line chart has 20 time points, extract ALL 20 values per line
                        - Missing even 1 number is FAILURE
                        - Writing "not specified" is FAILURE
                        - Rounding numbers is acceptable for line chart estimates
                        - Summarizing data is FAILURE
                        
                        NO-DATA RULE (overrides everything above): the extraction rules apply only to images that
                        actually contain a table or chart. If the image is an icon, logo, photo, ornament or blank,
                        reply with exactly INVALID_IMAGE. Never fabricate data that is not visible in the image.

                        This is SEC filing data. Accuracy is legally critical. Be exhaustive."""
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}}
                        ]
                    }
                ],
                max_tokens=3000,  # Increased for complete table extraction
                temperature=0  # Zero temperature for maximum accuracy
            )
            
            _vision_duration = time.perf_counter() - _vision_start
            usage = getattr(response, "usage", None)
            record_usage(
                stage="ingestion.image_vision", model="gpt-4o",
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
                duration_seconds=_vision_duration,
                detail=os.path.basename(image_path),
            )

            result = response.choices[0].message.content.strip()

            # Debug logging
            if not result or len(result) < 10:
                logger.warning("\n  WARNING: Got very short/empty response for %s", os.path.basename(image_path))
                logger.warning("Response length: %d, Content: '%s'", len(result), result[:100])
            
            # Only skip if explicitly marked invalid (and only if it's truly a logo/decoration)
            if "INVALID_IMAGE" in result and len(result) < 50:
                self.filter_stats["model_invalid"] += 1
                return None

            # A refusal ("I'm sorry, I can't...", "portrait photograph") is not a
            # caption — storing it would embed junk into the vector DB.
            if len(result) < 600 and any(m in result.lower()[:250] for m in _REFUSAL_MARKERS):
                logger.warning("  Dropping refusal-style response for %s", os.path.basename(image_path))
                self.filter_stats["refusal"] += 1
                return None

            # If result is empty or too short, this is likely an error - return a placeholder
            if not result or len(result) < 10:
                return f"[Image analysis failed or returned empty - {os.path.basename(image_path)}]"
            
            return result
                
        except Exception as e:
            logger.error("Error analyzing image %s: %s", image_path, e)
            return f"Error analyzing image: {str(e)}"
    

        
    
    async def get_image_description(self, contexts):
        """Simple image description processing with clean output for efficient RAG."""
        import asyncio

        image_analyses = {}
        output_file = os.path.splitext(self.pdf_path)[0] + "_analysis.json"

        logger.info("\n Analyzing %d images with GPT-4o...", len(contexts))
        processed_count = 0
        skipped_count = 0

        # Bounded concurrency — GPT-4o vision calls are slow; running them
        # concurrently (rather than one at a time) is the main ingestion speedup here.
        semaphore = asyncio.Semaphore(5)

        async def _analyze_one(image_path, context_text):
            async with semaphore:
                try:
                    result = await self.analyze_image_with_context(image_path, context_text)
                    return image_path, result, None
                except Exception as e:
                    return image_path, None, e

        analysis_results = await asyncio.gather(*(
            _analyze_one(image_path, context_text) for image_path, context_text in contexts.items()
        ))

        for image_path, result, error in analysis_results:
            if error is not None:
                logger.error("\n Error analyzing %s: %s", image_path, error)
                image_analyses[image_path] = f"[Analysis error: {str(error)}]"
                continue

            # Only skip if explicitly None (truly invalid decoration)
            if result is None:
                skipped_count += 1
                logger.warning("\n  Skipped decorative image: %s", os.path.basename(image_path))
                continue

            # Store result even if it contains INVALID_IMAGE string (might have other data)
            # or if it's a placeholder for failed analysis
            image_analyses[image_path] = result
            processed_count += 1

            # Warn if result seems insufficient
            if len(result) < 50:
                logger.warning("\n  Short description for %s: %d chars", os.path.basename(image_path), len(result))

        # Save simple analysis results
        analysis_data = {
            "pdf_source": self.pdf_path,
            "total_images_found": len(contexts),
            "successfully_analyzed": processed_count,
            "skipped_images": skipped_count,
            "analysis_timestamp": str(datetime.now()),
            "image_analyses": image_analyses
        }
        
        with open(output_file, "w", encoding="utf-8") as json_file:
            json.dump(analysis_data, json_file, ensure_ascii=False, indent=2)
        
        logger.info("\n Analysis complete:")
        logger.info("   • Successfully analyzed: %d/%d images", processed_count, len(contexts))
        logger.info("   • Skipped (decorative/invalid): %d images", skipped_count)
        logger.info("   • Results saved to: %s", output_file)
        
        # Return the dictionary of image analyses for ingestion
        return image_analyses

    def get_image_data(self,image_path,caption,company):
        """Fixed path handling for cross-platform compatibility."""
        try:
            # Use os.path.sep for cross-platform compatibility
            path_parts = image_path.replace("\\", "/").split("/")
            filename = path_parts[-1]

            # Filenames are produced by save_images() as exactly:
            #   financial_img_{xref}_page{page_num}_{hash}.png
            # Parse with a regex matching that literal format rather than
            # fragile split()/substring logic — a prior version matched on
            # "_p" which also matches inside "_page", producing garbage
            # like pagenumber="age5" and image_xref="img".
            import re as _re
            match = _re.match(r'financial_img_(\d+)_page(\d+)_', filename)
            if match:
                image_xref = match.group(1)
                pagenumber = match.group(2)
            else:
                # Fallback for any non-standard filename (e.g. externally supplied images)
                image_xref = "0"
                pagenumber = "1"
            
            file_name = os.path.basename(self.pdf_path).replace(".pdf", "")
            image_source_in_file = f"{file_name}-page{pagenumber}-{image_xref}"
            
            image_metadata = {
                "source_file": self.pdf_path,
                "image_source_in_file": image_source_in_file,
                "image": image_path,
                "company": company,
                "type": "image",
                "page_num": int(pagenumber),  # Qdrant indexes metadata.page_num as INTEGER
                "caption": caption
            }
            return image_metadata
        except Exception as e:
            logger.error("Error creating image metadata: %s", e)
            return {
                "source_file": self.pdf_path,
                "image": image_path,
                "company": company,
                "type": "image",
                "caption": caption
            }

    def getRetriever(self, json_file_path, company, image_hashes=None):
        """
        Enhanced retriever optimized for RAG retrieval of financial data.
        CRITICAL: Store caption directly as page_content - LLM already optimized it for retrieval.
        """
        with open(json_file_path, "r", encoding="utf-8") as file:
            image_descriptions = json.load(file)
        
        image_docs = []
        
        # Handle nested 'metadata' key for backward compatibility
        if isinstance(image_descriptions, dict) and "metadata" in image_descriptions:
            actual_images = image_descriptions["metadata"]
        else:
            actual_images = image_descriptions
        
        for i, (image_path, caption) in enumerate(actual_images.items()):
            # Ensure caption is a string (not a dict)
            if isinstance(caption, dict):
                caption = str(caption)
            
            image_metadata = self.get_image_data(image_path, caption, company)
            
            # Add image content hash if available
            if image_hashes:
                # Try to find matching hash by path or index
                img_hash = ""
                for img_id, hash_info in image_hashes.items():
                    if hash_info.get("path") == image_path:
                        img_hash = hash_info["hash"]
                        break
                
                # Fallback: use index-based matching
                if not img_hash and i < len(image_hashes):
                    hash_values = list(image_hashes.values())
                    if i < len(hash_values):
                        img_hash = hash_values[i]["hash"]
                
                image_metadata["image_content_hash"] = img_hash
            
            # CRITICAL FIX: Store caption directly without generic wrapper
            # LLM output is already optimized with searchable summary and keywords upfront
            # This enables precise semantic search and retrieval
            doc = Document(
                page_content=caption,  # Store LLM-optimized caption with searchable summary first
                metadata=image_metadata
            )
            image_docs.append(doc)
        return image_docs