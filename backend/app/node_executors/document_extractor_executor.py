"""
Document Extractor Node Executor
Extracts content from various document types (PDF, DOCX, TXT) with table and structure support

Enhanced with PyMuPDF for production-grade document extraction:
- PDF text extraction with layout preservation
- Table detection and extraction
- Image extraction (optional)
- Multi-format output (text, markdown, JSON)
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import json
import re
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class DocumentExtractorExecutor(BaseNodeExecutor):
    """Executor for Document Extractor node - extracts content from documents"""

    node_type = "document_extractor"

    # Supported document formats and their extraction methods
    SUPPORTED_FORMATS = {
        ".pdf": "_extract_pdf",
        ".docx": "_extract_docx",
        ".doc": "_extract_docx",
        ".xlsx": "_extract_excel",
        ".xls": "_extract_excel",
        ".csv": "_extract_csv",
        ".txt": "_extract_text",
        ".md": "_extract_text",
        ".json": "_extract_json",
    }

    # Maximum file size (10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data
            input_ref = node_data.get("inputDocument", "")
            extraction_type = node_data.get("extractionType", "full_text")
            output_format = node_data.get("outputFormat", "text")
            extract_tables = node_data.get("extractTables", True)
            extract_images = node_data.get("extractImages", False)
            model = node_data.get("model", "gpt-4o-mini")

            # Check if input is a file path or document content
            file_path = node_data.get("filePath")
            document_content = None

            if file_path:
                # Extract from file
                result = await self._extract_from_file(
                    file_path,
                    extraction_type,
                    output_format,
                    extract_tables,
                    extract_images,
                    context
                )
            else:
                # Resolve document content from inputs
                document_content = self._resolve_document(input_ref, context.variables, context.inputs)

                if not document_content:
                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="failed",
                        error=f"Document not found or empty: {input_ref}",
                        elapsed_ms=int((time.time() - start_time) * 1000)
                    )

                # Extract from content
                result = await self._extract_from_content(
                    document_content,
                    extraction_type,
                    output_format,
                    model,
                    db
                )

            elapsed_ms = int((time.time() - start_time) * 1000)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": result.get("text", ""),
                    "text": result.get("text", ""),
                    "tables": result.get("tables", []),
                    "metadata": result.get("metadata", {}),
                    "extraction_type": extraction_type,
                    "output_format": output_format,
                    "page_count": result.get("page_count", 1),
                    "word_count": len(result.get("text", "").split()),
                },
                elapsed_ms=elapsed_ms
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Document extraction failed: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )

    def _resolve_document(self, input_ref: str, variables: dict, inputs: dict) -> Optional[str]:
        """Resolve document content from reference"""
        ref = input_ref.replace("{{", "").replace("}}", "").strip()

        if not ref:
            return None

        # Try inputs
        if ref in inputs:
            val = inputs[ref]
            if isinstance(val, dict):
                return val.get("output") or val.get("text") or val.get("content")
            return str(val)

        # Try nested path
        if "." in ref:
            parts = ref.split(".", 1)
            if parts[0] in inputs:
                node_output = inputs[parts[0]]
                if isinstance(node_output, dict):
                    return node_output.get(parts[1])

        # Try variables
        if ref in variables:
            return str(variables[ref])

        return None

    async def _extract_from_file(
        self,
        file_path: str,
        extraction_type: str,
        output_format: str,
        extract_tables: bool,
        extract_images: bool,
        context: NodeExecutionContext
    ) -> Dict[str, Any]:
        """Extract content from a file based on its extension"""
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Check file size
        file_size = path.stat().st_size
        if file_size > self.MAX_FILE_SIZE:
            raise ValueError(f"File size ({file_size / 1024 / 1024:.2f}MB) exceeds maximum ({self.MAX_FILE_SIZE / 1024 / 1024}MB)")

        ext = path.suffix.lower()
        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported file format: {ext}. Supported formats: {list(self.SUPPORTED_FORMATS.keys())}")

        # Get extraction method
        method_name = self.SUPPORTED_FORMATS[ext]
        method = getattr(self, method_name)

        # Run extraction in thread pool to avoid blocking
        return await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: method(file_path, extraction_type, output_format, extract_tables)
        )

    def _extract_pdf(
        self,
        file_path: str,
        extraction_type: str,
        output_format: str,
        extract_tables: bool
    ) -> Dict[str, Any]:
        """Extract content from PDF using PyMuPDF"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.warning("PyMuPDF not installed, falling back to basic extraction")
            return self._extract_pdf_basic(file_path, extraction_type, output_format)

        result = {
            "text": "",
            "tables": [],
            "metadata": {},
            "page_count": 0
        }

        doc = fitz.open(file_path)
        result["page_count"] = len(doc)
        result["metadata"] = {
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "subject": doc.metadata.get("subject", ""),
            "creator": doc.metadata.get("creator", ""),
            "producer": doc.metadata.get("producer", ""),
            "creation_date": doc.metadata.get("creationDate", ""),
            "modification_date": doc.metadata.get("modDate", ""),
        }

        if output_format == "markdown":
            # Use pymupdf4llm for markdown conversion
            try:
                import pymupdf4llm
                result["text"] = pymupdf4llm.to_markdown(file_path)
            except ImportError:
                # Fallback to basic text extraction
                text_parts = []
                for page in doc:
                    text_parts.append(page.get_text())
                result["text"] = "\n\n".join(text_parts)
        else:
            # Extract text from each page
            text_parts = []
            for page_num, page in enumerate(doc):
                page_text = page.get_text()
                if output_format == "json":
                    text_parts.append({
                        "page": page_num + 1,
                        "text": page_text
                    })
                else:
                    text_parts.append(f"--- Page {page_num + 1} ---\n{page_text}")

            if output_format == "json":
                result["text"] = json.dumps(text_parts, indent=2)
            else:
                result["text"] = "\n\n".join(text_parts)

        # Extract tables if requested
        if extract_tables:
            for page_num, page in enumerate(doc):
                try:
                    tabs = page.find_tables()
                    for tab in tabs:
                        table_data = tab.extract()
                        if table_data and len(table_data) > 1:
                            result["tables"].append({
                                "page": page_num + 1,
                                "headers": table_data[0] if table_data else [],
                                "rows": table_data[1:] if len(table_data) > 1 else [],
                                "row_count": len(table_data) - 1
                            })
                except Exception as e:
                    logger.warning(f"Table extraction failed for page {page_num + 1}: {e}")

        doc.close()
        return result

    def _extract_pdf_basic(
        self,
        file_path: str,
        extraction_type: str,
        output_format: str
    ) -> Dict[str, Any]:
        """Basic PDF extraction fallback using pypdf"""
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            text_parts = []
            for page in reader.pages:
                text_parts.append(page.extract_text())
            return {
                "text": "\n\n".join(text_parts),
                "tables": [],
                "metadata": dict(reader.metadata) if reader.metadata else {},
                "page_count": len(reader.pages)
            }
        except Exception as e:
            logger.error(f"Basic PDF extraction failed: {e}")
            return {"text": "", "tables": [], "metadata": {}, "page_count": 0}

    def _extract_docx(
        self,
        file_path: str,
        extraction_type: str,
        output_format: str,
        extract_tables: bool
    ) -> Dict[str, Any]:
        """Extract content from DOCX using python-docx"""
        try:
            from docx import Document
        except ImportError:
            raise ImportError("python-docx is required for DOCX extraction. Install with: pip install python-docx")

        doc = Document(file_path)
        result = {
            "text": "",
            "tables": [],
            "metadata": {
                "paragraphs": len(doc.paragraphs),
                "tables": len(doc.tables),
            },
            "page_count": 1  # DOCX doesn't have easy page count
        }

        # Extract paragraphs
        paragraphs = []
        for para in doc.paragraphs:
            if para.text.strip():
                if output_format == "markdown":
                    # Check for headings
                    if para.style.name.startswith("Heading"):
                        level = int(para.style.name[-1]) if para.style.name[-1].isdigit() else 1
                        paragraphs.append(f"{'#' * level} {para.text}")
                    else:
                        paragraphs.append(para.text)
                else:
                    paragraphs.append(para.text)

        result["text"] = "\n\n".join(paragraphs)

        # Extract tables
        if extract_tables:
            for table in doc.tables:
                table_data = []
                for row in table.rows:
                    row_data = [cell.text.strip() for cell in row.cells]
                    table_data.append(row_data)

                if table_data:
                    result["tables"].append({
                        "headers": table_data[0] if table_data else [],
                        "rows": table_data[1:] if len(table_data) > 1 else [],
                        "row_count": len(table_data) - 1
                    })

        return result

    def _extract_excel(
        self,
        file_path: str,
        extraction_type: str,
        output_format: str,
        extract_tables: bool
    ) -> Dict[str, Any]:
        """Extract content from Excel using pandas/openpyxl"""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is required for Excel extraction. Install with: pip install pandas openpyxl")

        result = {
            "text": "",
            "tables": [],
            "metadata": {},
            "page_count": 1
        }

        # Read all sheets
        xlsx = pd.ExcelFile(file_path, engine='openpyxl')
        sheet_names = xlsx.sheet_names
        result["metadata"]["sheets"] = sheet_names
        result["metadata"]["sheet_count"] = len(sheet_names)

        all_text = []
        for sheet_name in sheet_names:
            df = pd.read_excel(xlsx, sheet_name=sheet_name)

            # Add to tables
            result["tables"].append({
                "sheet": sheet_name,
                "headers": list(df.columns),
                "rows": df.values.tolist(),
                "row_count": len(df)
            })

            # Convert to text
            if output_format == "markdown":
                all_text.append(f"## {sheet_name}\n\n{df.to_markdown(index=False)}")
            elif output_format == "json":
                all_text.append(df.to_json(orient='records'))
            else:
                all_text.append(f"Sheet: {sheet_name}\n{df.to_string(index=False)}")

        result["text"] = "\n\n".join(all_text)
        return result

    def _extract_csv(
        self,
        file_path: str,
        extraction_type: str,
        output_format: str,
        extract_tables: bool
    ) -> Dict[str, Any]:
        """Extract content from CSV using pandas"""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is required for CSV extraction. Install with: pip install pandas")

        df = pd.read_csv(file_path)

        result = {
            "tables": [{
                "headers": list(df.columns),
                "rows": df.values.tolist(),
                "row_count": len(df)
            }],
            "metadata": {
                "columns": list(df.columns),
                "row_count": len(df)
            },
            "page_count": 1
        }

        if output_format == "markdown":
            result["text"] = df.to_markdown(index=False)
        elif output_format == "json":
            result["text"] = df.to_json(orient='records')
        else:
            result["text"] = df.to_string(index=False)

        return result

    def _extract_text(
        self,
        file_path: str,
        extraction_type: str,
        output_format: str,
        extract_tables: bool
    ) -> Dict[str, Any]:
        """Extract content from plain text files"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        return {
            "text": content,
            "tables": [],
            "metadata": {
                "encoding": "utf-8",
                "line_count": content.count('\n') + 1
            },
            "page_count": 1
        }

    def _extract_json(
        self,
        file_path: str,
        extraction_type: str,
        output_format: str,
        extract_tables: bool
    ) -> Dict[str, Any]:
        """Extract content from JSON files"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if output_format == "text":
            text = json.dumps(data, indent=2)
        elif output_format == "markdown":
            text = f"```json\n{json.dumps(data, indent=2)}\n```"
        else:
            text = json.dumps(data)

        return {
            "text": text,
            "tables": [],
            "metadata": {
                "type": type(data).__name__,
                "keys": list(data.keys()) if isinstance(data, dict) else None
            },
            "page_count": 1
        }

    async def _extract_from_content(
        self,
        content: str,
        extraction_type: str,
        output_format: str,
        model: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """Extract structured data from text content using heuristics"""
        result = {
            "text": "",
            "tables": [],
            "metadata": {},
            "page_count": 1
        }

        if extraction_type == "full_text":
            # Clean and return text
            text = self._clean_text(content)
            if output_format == "json":
                result["text"] = json.dumps({"text": text, "word_count": len(text.split())})
            elif output_format == "markdown":
                result["text"] = f"# Extracted Text\n\n{text}"
            else:
                result["text"] = text

        elif extraction_type == "sections":
            # Extract document sections
            sections = self._extract_sections(content)
            result["metadata"]["section_count"] = len(sections)
            if output_format == "json":
                result["text"] = json.dumps(sections)
            elif output_format == "markdown":
                md = ""
                for section in sections:
                    md += f"## {section['title']}\n\n{section['content']}\n\n"
                result["text"] = md
            else:
                result["text"] = "\n\n".join([f"{s['title']}: {s['content']}" for s in sections])

        elif extraction_type == "tables":
            # Extract tables from content
            tables = self._extract_markdown_tables(content)
            result["tables"] = tables
            if output_format == "json":
                result["text"] = json.dumps(tables)
            elif output_format == "markdown":
                result["text"] = self._tables_to_markdown(tables)
            else:
                result["text"] = json.dumps(tables, indent=2)

        elif extraction_type == "key_value":
            # Extract key-value pairs
            pairs = self._extract_key_value_pairs(content)
            result["metadata"]["pair_count"] = len(pairs)
            if output_format == "json":
                result["text"] = json.dumps(pairs)
            elif output_format == "markdown":
                md = "# Extracted Key-Value Pairs\n\n"
                for key, value in pairs.items():
                    md += f"- **{key}**: {value}\n"
                result["text"] = md
            else:
                result["text"] = json.dumps(pairs)

        else:
            result["text"] = content

        return result

    def _clean_text(self, content: str) -> str:
        """Clean and normalize text content"""
        # Remove excess whitespace
        text = re.sub(r'\s+', ' ', content)
        # Remove null bytes and control characters
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
        return text.strip()

    def _extract_sections(self, content: str) -> List[Dict[str, str]]:
        """Extract sections from document"""
        sections = []

        # Try to find markdown-style headers
        header_pattern = r'^(#{1,6})\s+(.+)$'
        lines = content.split('\n')

        current_section = {"title": "Introduction", "content": ""}
        for line in lines:
            match = re.match(header_pattern, line)
            if match:
                if current_section["content"].strip():
                    sections.append(current_section)
                current_section = {"title": match.group(2), "content": ""}
            else:
                current_section["content"] += line + "\n"

        if current_section["content"].strip():
            sections.append(current_section)

        # If no sections found, treat as single section
        if not sections:
            sections = [{"title": "Content", "content": content}]

        return sections

    def _extract_markdown_tables(self, content: str) -> List[Dict[str, Any]]:
        """Extract table-like data from content"""
        tables = []

        # Look for markdown tables
        table_pattern = r'\|(.+)\|'
        lines = content.split('\n')

        current_table = []
        for line in lines:
            if re.match(table_pattern, line):
                # Skip separator lines
                if re.match(r'\|[\s\-:]+\|', line):
                    continue
                cells = [cell.strip() for cell in line.split('|')[1:-1]]
                current_table.append(cells)
            elif current_table:
                if len(current_table) > 1:
                    tables.append({
                        "headers": current_table[0],
                        "rows": current_table[1:],
                        "row_count": len(current_table) - 1
                    })
                current_table = []

        if current_table and len(current_table) > 1:
            tables.append({
                "headers": current_table[0],
                "rows": current_table[1:],
                "row_count": len(current_table) - 1
            })

        return tables

    def _tables_to_markdown(self, tables: List[Dict[str, Any]]) -> str:
        """Convert tables to markdown format"""
        md = ""
        for i, table in enumerate(tables):
            md += f"### Table {i + 1}\n\n"
            headers = table.get("headers", [])
            rows = table.get("rows", [])

            if headers:
                md += "| " + " | ".join(str(h) for h in headers) + " |\n"
                md += "| " + " | ".join(["---"] * len(headers)) + " |\n"
            for row in rows:
                md += "| " + " | ".join(str(c) for c in row) + " |\n"
            md += "\n"
        return md

    def _extract_key_value_pairs(self, content: str) -> Dict[str, str]:
        """Extract key-value pairs from content"""
        pairs = {}

        # Pattern for "Key: Value" or "Key = Value"
        patterns = [
            r'^([A-Za-z][A-Za-z0-9\s_]+):\s*(.+)$',
            r'^([A-Za-z][A-Za-z0-9\s_]+)\s*=\s*(.+)$',
        ]

        lines = content.split('\n')
        for line in lines:
            for pattern in patterns:
                match = re.match(pattern, line.strip())
                if match:
                    key = match.group(1).strip()
                    value = match.group(2).strip()
                    pairs[key] = value
                    break

        return pairs
