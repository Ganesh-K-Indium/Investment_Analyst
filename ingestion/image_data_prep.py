import io
import os
import fitz
import json
import openai
import base64
import hashlib
from datetime import datetime
from tqdm import tqdm
from PIL import Image, ImageEnhance
from langchain_core.documents import Document
from pathlib import Path
from dotenv import load_dotenv
import pytesseract

load_dotenv()

# Use the explicit binary path if set (required for Docker/ECS environments)
_tesseract_cmd = os.environ.get("TESSERACT_CMD")
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

class ImageDescription:
    "This method is used to get the description of the image."
    def __init__(self,pdf_path):
        """
        This constructor is used to initialize the path of the pdf.
        Args:
            pdf_path : The path of the pdf.
        """
        self.pdf_path = pdf_path
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
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
                print(f"   ⚠️  Tesseract OCR not installed - using vision-only mode")
                return "[OCR unavailable - using GPT-4o vision only]"
            print(f"   ⚠️  OCR extraction failed for {os.path.basename(image_path)}: {e}")
            return ""
        if not self.openai_client.api_key:
            raise ValueError("OpenAI API key not found in environment variables")
    
    def calculate_image_content_hash(self, image_data: bytes) -> str:
        """Calculate a deterministic hash of individual image content."""
        try:
            return hashlib.sha256(image_data).hexdigest()
        except Exception as e:
            print(f"Error calculating image content hash: {e}")
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
    
    def save_images(self,img_info,page_num,pdf_document,output_dir):
        """
        Enhanced image preprocessing optimized for financial data extraction with better detail preservation.
        """
        try:
            xref = img_info[0]
            base_image = pdf_document.extract_image(xref)
            if not base_image:
                return None, None
                
            image_bytes = base_image["image"]
            original_img = Image.open(io.BytesIO(image_bytes))
            
            # Convert to RGB for consistent processing
            if original_img.mode != 'RGB':
                img = original_img.convert('RGB')
            else:
                img = original_img.copy()
            
            original_size = img.size
            min_dimension = min(img.size)
            max_dimension = max(img.size)
            
            # Skip tiny images (likely decorative)
            if min_dimension < 50 or max_dimension < 50:
                print(f"Skipping very small image: {original_size}")
                return None, None
            
            # Multi-stage enhancement for financial documents
            
            # 1. Size optimization first (do this before enhancement for better quality)
            target_size = None
            if min_dimension < 512:
                # Scale up small images significantly for better OCR/analysis
                scale_factor = 512 / min_dimension
                target_size = (int(img.size[0] * scale_factor), int(img.size[1] * scale_factor))
                print(f"Upscaling image from {original_size} to {target_size}")
            elif max_dimension > 2048:
                # Scale down very large images but keep good detail
                scale_factor = 2048 / max_dimension
                target_size = (int(img.size[0] * scale_factor), int(img.size[1] * scale_factor))
                print(f"Downscaling image from {original_size} to {target_size}")
            
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
            
            print(f"✓ Enhanced image: {os.path.basename(img_path)} (final size: {img.size})")
            return img_path, xref
            
        except Exception as e:
            print(f"Error processing image {xref}: {e}")
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
            print(f"Error extracting context for image {xref}: {e}")
            return ""
    

    
    def get_preceeding_text(self, xref, page, text_blocks):
        """Get clean context text around image for efficient RAG retrieval."""
        return self.get_comprehensive_image_context(xref, page, text_blocks)
    
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
        
        try:
            print(f"\n🖼️  Processing PDF: {os.path.basename(self.pdf_path)}")
            
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
                    img_path, xref = self.save_images(img_info, page_num, pdf_document, output_path)
                    
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
                            print(f"Warning: Could not hash image {xref}: {e}")
                        
                        # Get clean context text around the image
                        context_text = self.get_comprehensive_image_context(xref, page, text_blocks)
                        image_details[img_path] = context_text
                        processed_images += 1
                    
            print(f"Successfully processed {processed_images}/{total_images} images")
            print(f"Generated {len(image_hashes)} image hashes")
            return image_details, image_hashes  # Return both details and hashes
            
        except Exception as e:
            print(f"Error during image extraction: {e}")
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
            print(f"Error encoding image {image_path}: {e}")
            return None

    def analyze_image_with_context(self, image_path, context_text):
        """
        COMPREHENSIVE image analysis using OCR + GPT-4o Vision.
        Ensures ALL financial data is extracted - critical for 10-K documents.
        """
        if not os.path.exists(image_path):
            return "Error: Image file not found"
            
        try:
            # Step 1: Extract ALL text using OCR
            print(f"   📝 Running OCR on {os.path.basename(image_path)}...")
            ocr_text = self.extract_text_from_image_ocr(image_path)
            
            if ocr_text:
                print(f"   ✅ OCR extracted {len(ocr_text)} characters")
            else:
                print(f"   ⚠️  OCR found no text")
            
            # Step 2: Encode image for GPT-4o
            image_base64 = self.encode_image(image_path)
            if not image_base64:
                return "Error: Failed to encode image"
            
            # Step 3: Enhanced prompt with OCR data - OPTIMIZED FOR RAG RETRIEVAL
            prompt = f"""
            You are analyzing a financial document image (10-K filing). Your job is to extract EVERY piece of financial data in a format optimized for semantic search and retrieval.
            
            CONTEXT FROM SURROUNDING TEXT IN PDF:
            {context_text}
            
            OCR-EXTRACTED TEXT FROM IMAGE:
            {ocr_text}
            
            CRITICAL INSTRUCTIONS FOR 10-K DOCUMENTS:
            ============================================
            1. Extract EVERY SINGLE NUMBER visible in the image
            2. Extract ALL row and column headers from tables
            3. Extract ALL data points from charts/graphs
            4. Include units (millions, billions, %, $, etc.)
            5. Preserve exact values - DO NOT round or summarize
            6. If OCR text contains numbers, USE THEM - don't guess from visual
            7. Financial images are NEVER decorative - always extract data
            8. START with searchable summary for optimal retrieval
            
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
            ❌ BAD: "Data Series 1 [Stock A]: Data points not numerically specified"
            ✅ GOOD: "Data Series 1 [Stock A]: 12/19=$100, 3/20=$95, 6/20=$110, 9/20=$130..."
            
            Only respond "INVALID_IMAGE" if this is purely decorative (logo, border, background) with ZERO data.
            """

            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": """You are a financial data extraction specialist for 10-K SEC filings optimized for RAG retrieval systems. 
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
            
            result = response.choices[0].message.content.strip()
            
            # Debug logging
            if not result or len(result) < 10:
                print(f"\n⚠️  WARNING: Got very short/empty response for {os.path.basename(image_path)}")
                print(f"Response length: {len(result)}, Content: '{result[:100]}'")
            
            # Only skip if explicitly marked invalid (and only if it's truly a logo/decoration)
            if "INVALID_IMAGE" in result and len(result) < 50:
                return None
            
            # If result is empty or too short, this is likely an error - return a placeholder
            if not result or len(result) < 10:
                return f"[Image analysis failed or returned empty - {os.path.basename(image_path)}]"
            
            return result
                
        except Exception as e:
            print(f"Error analyzing image {image_path}: {e}")
            return f"Error analyzing image: {str(e)}"
    

        
    
    def get_image_description(self, contexts):
        """Simple image description processing with clean output for efficient RAG."""
        image_analyses = {}
        output_file = os.path.splitext(self.pdf_path)[0] + "_analysis.json"
        
        print(f"\n🤖 Analyzing {len(contexts)} images with GPT-4o...")
        processed_count = 0
        skipped_count = 0
        
        for image_path, context_text in tqdm(contexts.items(), desc="Analyzing images", unit="img"):
            try:
                result = self.analyze_image_with_context(image_path, context_text)
                
                # Only skip if explicitly None (truly invalid decoration)
                if result is None:
                    skipped_count += 1
                    print(f"\n⏭️  Skipped decorative image: {os.path.basename(image_path)}")
                    continue
                
                # Store result even if it contains INVALID_IMAGE string (might have other data)
                # or if it's a placeholder for failed analysis
                image_analyses[image_path] = result
                processed_count += 1
                
                # Warn if result seems insufficient
                if len(result) < 50:
                    print(f"\n⚠️  Short description for {os.path.basename(image_path)}: {len(result)} chars")
                
            except Exception as e:
                print(f"\n❌ Error analyzing {image_path}: {e}")
                # Store error message instead of skipping
                image_analyses[image_path] = f"[Analysis error: {str(e)}]"
                continue
        
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
        
        print(f"\n✅ Analysis complete:")
        print(f"   • Successfully analyzed: {processed_count}/{len(contexts)} images")
        print(f"   • Skipped (decorative/invalid): {skipped_count} images")
        print(f"   • Results saved to: {output_file}")
        
        # Return the dictionary of image analyses for ingestion
        return image_analyses

    def get_image_data(self,image_path,caption,company):
        """Fixed path handling for cross-platform compatibility."""
        try:
            # Use os.path.sep for cross-platform compatibility
            path_parts = image_path.replace("\\", "/").split("/")
            filename = path_parts[-1]
            
            # Extract page number more reliably
            if "_p" in filename:
                pagenumber = filename.split("_p")[1].split("_")[0]
            else:
                pagenumber = "1"  # fallback
            
            # Extract xref more reliably
            if "_" in filename:
                image_xref = filename.split("_")[1] if len(filename.split("_")) > 1 else "0"
            else:
                image_xref = "0"
            
            file_name = os.path.basename(self.pdf_path).replace(".pdf", "")
            image_source_in_file = f"{file_name}-page{pagenumber}-{image_xref}"
            
            image_metadata = {
                "source_file": self.pdf_path,
                "image_source_in_file": image_source_in_file,
                "image": image_path,
                "company": company,
                "type": "image",
                "page_num": pagenumber,
                "caption": caption 
            }
            return image_metadata
        except Exception as e:
            print(f"Error creating image metadata: {e}")
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