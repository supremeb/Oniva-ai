"""
OCR Node Executor
Extracts text from images using Tesseract or cloud OCR providers
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import base64
import re
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class OCRExecutor(BaseNodeExecutor):
    """Executor for OCR node - extracts text from images"""

    node_type = "ocr"

    # Cloud OCR providers (Google Vision, AWS Textract) are external
    makes_external_calls = True

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            input_ref = node_data.get("inputImage", "")
            provider = node_data.get("provider", "tesseract")
            language = node_data.get("language", "eng")
            output_format = node_data.get("outputFormat", "text")
            preprocess = node_data.get("preprocessImage", False)
            detect_tables = node_data.get("detectTables", False)
            detect_handwriting = node_data.get("detectHandwriting", False)

            # Resolve input image
            image_data = self._resolve_image(input_ref, context.variables, context.inputs)

            if not image_data:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Image not found: {input_ref}",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Perform OCR based on provider
            if provider == "tesseract":
                result = await self._ocr_tesseract(image_data, language, output_format, preprocess)
            elif provider == "google_vision":
                result = await self._ocr_google_vision(image_data, language, detect_tables, detect_handwriting, db)
            elif provider == "azure_vision":
                result = await self._ocr_azure_vision(image_data, language, detect_tables, db)
            elif provider == "aws_textract":
                result = await self._ocr_aws_textract(image_data, detect_tables, db)
            else:
                result = {"text": "", "error": f"Unknown provider: {provider}"}

            elapsed_ms = int((time.time() - start_time) * 1000)

            if "error" in result and result["error"]:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=result["error"],
                    elapsed_ms=elapsed_ms
                )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": result.get("text", ""),
                    "text": result.get("text", ""),
                    "blocks": result.get("blocks", []),
                    "tables": result.get("tables", []),
                    "confidence": result.get("confidence", 0.0),
                    "provider": provider,
                    "language": language,
                },
                elapsed_ms=elapsed_ms
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )

    def _resolve_image(self, input_ref: str, variables: dict, inputs: dict) -> Optional[bytes]:
        """Resolve image data from reference"""
        ref = input_ref.replace("{{", "").replace("}}", "").strip()

        if not ref:
            return None

        # Try inputs
        if ref in inputs:
            val = inputs[ref]
            return self._extract_image_data(val)

        # Try nested path
        if "." in ref:
            parts = ref.split(".", 1)
            if parts[0] in inputs:
                node_output = inputs[parts[0]]
                if isinstance(node_output, dict):
                    return self._extract_image_data(node_output.get(parts[1]))

        # Try variables
        if ref in variables:
            return self._extract_image_data(variables[ref])

        return None

    def _extract_image_data(self, value: Any) -> Optional[bytes]:
        """Extract image bytes from various input formats"""
        if value is None:
            return None

        if isinstance(value, bytes):
            return value

        if isinstance(value, str):
            # Base64 encoded
            if value.startswith("data:image"):
                # Data URL format
                match = re.match(r'data:image/[^;]+;base64,(.+)', value)
                if match:
                    return base64.b64decode(match.group(1))
            else:
                try:
                    return base64.b64decode(value)
                except Exception:
                    pass

        if isinstance(value, dict):
            # Try common fields
            for key in ["data", "content", "bytes", "base64"]:
                if key in value:
                    return self._extract_image_data(value[key])

        return None

    async def _ocr_tesseract(
        self,
        image_data: bytes,
        language: str,
        output_format: str,
        preprocess: bool
    ) -> Dict[str, Any]:
        """Perform OCR using Tesseract"""
        try:
            # Try to import pytesseract
            import pytesseract
            from PIL import Image
            import io

            # Convert bytes to PIL Image
            image = Image.open(io.BytesIO(image_data))

            # Preprocess if requested
            if preprocess:
                image = self._preprocess_image(image)

            # Run OCR
            if output_format == "hocr":
                text = pytesseract.image_to_pdf_or_hocr(image, lang=language, extension='hocr')
                return {"text": text.decode('utf-8') if isinstance(text, bytes) else text}
            elif output_format == "blocks":
                data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)
                blocks = self._parse_tesseract_blocks(data)
                text = pytesseract.image_to_string(image, lang=language)
                return {"text": text, "blocks": blocks}
            else:
                text = pytesseract.image_to_string(image, lang=language)
                return {"text": text, "confidence": 0.0}  # Tesseract doesn't always give confidence

        except ImportError:
            return {
                "text": "",
                "error": "Tesseract/pytesseract not installed. Install with: pip install pytesseract pillow"
            }
        except Exception as e:
            return {"text": "", "error": str(e)}

    def _preprocess_image(self, image):
        """Preprocess image for better OCR results"""
        try:
            from PIL import ImageFilter, ImageOps

            # Convert to grayscale
            if image.mode != 'L':
                image = image.convert('L')

            # Increase contrast
            image = ImageOps.autocontrast(image)

            # Apply slight sharpening
            image = image.filter(ImageFilter.SHARPEN)

            return image
        except Exception:
            return image

    def _parse_tesseract_blocks(self, data: dict) -> List[Dict]:
        """Parse Tesseract output into blocks"""
        blocks = []
        n_boxes = len(data['text'])

        for i in range(n_boxes):
            text = data['text'][i].strip()
            if text:
                blocks.append({
                    "text": text,
                    "confidence": float(data['conf'][i]) / 100 if data['conf'][i] != '-1' else 0.0,
                    "left": data['left'][i],
                    "top": data['top'][i],
                    "width": data['width'][i],
                    "height": data['height'][i],
                    "block_num": data['block_num'][i],
                    "line_num": data['line_num'][i],
                })

        return blocks

    async def _ocr_google_vision(
        self,
        image_data: bytes,
        language: str,
        detect_tables: bool,
        detect_handwriting: bool,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Perform OCR using Google Cloud Vision API"""
        # In production, this would call the Google Vision API
        # For now, return a stub with placeholder data
        return {
            "text": "[Google Vision OCR - requires API configuration]",
            "blocks": [],
            "tables": [] if detect_tables else None,
            "confidence": 0.0,
            "error": None
        }

    async def _ocr_azure_vision(
        self,
        image_data: bytes,
        language: str,
        detect_tables: bool,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Perform OCR using Azure Computer Vision"""
        # In production, this would call the Azure Vision API
        return {
            "text": "[Azure Vision OCR - requires API configuration]",
            "blocks": [],
            "tables": [] if detect_tables else None,
            "confidence": 0.0,
            "error": None
        }

    async def _ocr_aws_textract(
        self,
        image_data: bytes,
        detect_tables: bool,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Perform OCR using AWS Textract"""
        # In production, this would call the AWS Textract API
        return {
            "text": "[AWS Textract OCR - requires API configuration]",
            "blocks": [],
            "tables": [] if detect_tables else None,
            "confidence": 0.0,
            "error": None
        }
