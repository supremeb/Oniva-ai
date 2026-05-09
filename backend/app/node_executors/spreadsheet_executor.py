"""
Spreadsheet Node Executor
Processes Excel/CSV files with filtering, transformation, and formula support

Capabilities:
- Read Excel/CSV files from file path or uploaded content
- Filter rows based on conditions
- Transform data (map, aggregate, pivot)
- Apply safe column calculations
- Export to various formats (JSON, CSV, markdown table)
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import logging
import io
import base64
import operator
import re

logger = logging.getLogger(__name__)


class SpreadsheetExecutor(BaseNodeExecutor):
    """Executor for Spreadsheet node - Excel/CSV data processing"""

    node_type = "spreadsheet"

    # Maximum rows to process (prevent memory issues)
    MAX_ROWS = 100000
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

    # Safe operators for calculated columns
    SAFE_OPERATORS = {
        '+': operator.add,
        '-': operator.sub,
        '*': operator.mul,
        '/': operator.truediv,
        '//': operator.floordiv,
        '%': operator.mod,
    }

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data

            # Get input configuration
            file_path = node_data.get("filePath")
            file_content = node_data.get("fileContent")  # Base64 encoded
            input_ref = node_data.get("inputData", "")
            file_type = node_data.get("fileType", "auto")  # auto, csv, xlsx

            # Operations to perform
            operations = node_data.get("operations", [])
            output_format = node_data.get("outputFormat", "json")
            sheet_name = node_data.get("sheetName")  # For Excel files

            # Load the data
            df = await self._load_data(
                file_path,
                file_content,
                input_ref,
                file_type,
                sheet_name,
                context
            )

            if df is None:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error="Failed to load spreadsheet data",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Validate row count
            if len(df) > self.MAX_ROWS:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"Row count ({len(df)}) exceeds maximum ({self.MAX_ROWS})",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Store original stats
            original_rows = len(df)
            original_cols = len(df.columns)

            # Apply operations
            for op in operations:
                df = await self._apply_operation(df, op, context)

            # Format output
            output_data = await self._format_output(df, output_format)

            elapsed_ms = int((time.time() - start_time) * 1000)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "output": output_data,
                    "data": output_data,
                    "row_count": len(df),
                    "column_count": len(df.columns),
                    "columns": list(df.columns),
                    "original_rows": original_rows,
                    "original_cols": original_cols,
                    "operations_applied": len(operations),
                    "output_format": output_format,
                },
                elapsed_ms=elapsed_ms
            )

        except ImportError as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=f"Required library not installed: {e}. Install with: pip install pandas openpyxl",
                elapsed_ms=elapsed_ms
            )
        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Spreadsheet processing failed: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )

    async def _load_data(
        self,
        file_path: Optional[str],
        file_content: Optional[str],
        input_ref: str,
        file_type: str,
        sheet_name: Optional[str],
        context: NodeExecutionContext
    ) -> Any:
        """Load data from various sources into a pandas DataFrame"""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is required for spreadsheet processing")

        # Try file path first
        if file_path:
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")

            # Check file size
            if path.stat().st_size > self.MAX_FILE_SIZE:
                raise ValueError(f"File size exceeds maximum ({self.MAX_FILE_SIZE / 1024 / 1024}MB)")

            ext = path.suffix.lower()
            if ext in [".xlsx", ".xls"] or file_type == "xlsx":
                return pd.read_excel(file_path, sheet_name=sheet_name or 0, engine='openpyxl')
            elif ext == ".csv" or file_type == "csv":
                return pd.read_csv(file_path)
            elif ext == ".json":
                return pd.read_json(file_path)
            else:
                # Try to auto-detect
                try:
                    return pd.read_csv(file_path)
                except Exception:
                    return pd.read_excel(file_path, sheet_name=sheet_name or 0)

        # Try base64 content
        if file_content:
            content_bytes = base64.b64decode(file_content)
            if file_type == "xlsx" or file_type == "auto":
                try:
                    return pd.read_excel(io.BytesIO(content_bytes), sheet_name=sheet_name or 0, engine='openpyxl')
                except Exception:
                    if file_type == "auto":
                        return pd.read_csv(io.BytesIO(content_bytes))
                    raise
            else:
                return pd.read_csv(io.BytesIO(content_bytes))

        # Try input reference
        if input_ref:
            ref = input_ref.replace("{{", "").replace("}}", "").strip()

            # Check inputs
            if ref in context.inputs:
                val = context.inputs[ref]
                if isinstance(val, dict):
                    # Could be output from another node
                    if "data" in val:
                        data = val["data"]
                        if isinstance(data, list):
                            return pd.DataFrame(data)
                        elif isinstance(data, str):
                            return pd.read_json(io.StringIO(data))
                    elif "tables" in val and val["tables"]:
                        # From document extractor
                        table = val["tables"][0]
                        return pd.DataFrame(table.get("rows", []), columns=table.get("headers", []))
                elif isinstance(val, list):
                    return pd.DataFrame(val)
                elif isinstance(val, str):
                    # Try to parse as JSON
                    try:
                        data = json.loads(val)
                        return pd.DataFrame(data)
                    except json.JSONDecodeError:
                        # Try as CSV
                        return pd.read_csv(io.StringIO(val))

            # Check nested path
            if "." in ref:
                parts = ref.split(".", 1)
                if parts[0] in context.inputs:
                    node_output = context.inputs[parts[0]]
                    if isinstance(node_output, dict) and parts[1] in node_output:
                        data = node_output[parts[1]]
                        if isinstance(data, list):
                            return pd.DataFrame(data)

            # Check variables
            if ref in context.variables:
                val = context.variables[ref]
                if isinstance(val, list):
                    return pd.DataFrame(val)
                elif isinstance(val, str):
                    try:
                        return pd.DataFrame(json.loads(val))
                    except json.JSONDecodeError:
                        return pd.read_csv(io.StringIO(val))

        return None

    def _safe_calculate_column(
        self,
        df: Any,
        expression: str
    ) -> Any:
        """
        Safely calculate a new column using only allowed operations.

        Supports expressions like:
        - "column_a + column_b"
        - "column_a * 2"
        - "column_a / column_b"

        Does NOT use eval() - parses and applies operations safely.
        """
        import pandas as pd
        import numpy as np

        # Parse simple binary expressions: "col1 op col2" or "col1 op number"
        expression = expression.strip()

        # Try to match pattern: operand1 operator operand2
        pattern = r'^([a-zA-Z_][a-zA-Z0-9_]*|\d+\.?\d*)\s*([+\-*/%]+)\s*([a-zA-Z_][a-zA-Z0-9_]*|\d+\.?\d*)$'
        match = re.match(pattern, expression)

        if not match:
            logger.warning(f"Expression '{expression}' does not match safe pattern")
            return None

        left_operand = match.group(1)
        op_symbol = match.group(2)
        right_operand = match.group(3)

        if op_symbol not in self.SAFE_OPERATORS:
            logger.warning(f"Operator '{op_symbol}' is not allowed")
            return None

        op_func = self.SAFE_OPERATORS[op_symbol]

        # Get left value (column or number)
        if left_operand in df.columns:
            left_val = pd.to_numeric(df[left_operand], errors='coerce')
        else:
            try:
                left_val = float(left_operand)
            except ValueError:
                logger.warning(f"Left operand '{left_operand}' is not a valid column or number")
                return None

        # Get right value (column or number)
        if right_operand in df.columns:
            right_val = pd.to_numeric(df[right_operand], errors='coerce')
        else:
            try:
                right_val = float(right_operand)
            except ValueError:
                logger.warning(f"Right operand '{right_operand}' is not a valid column or number")
                return None

        # Handle division by zero
        if op_symbol in ['/', '//', '%']:
            if isinstance(right_val, (int, float)) and right_val == 0:
                return np.nan
            elif hasattr(right_val, 'replace'):
                right_val = right_val.replace(0, np.nan)

        try:
            return op_func(left_val, right_val)
        except Exception as e:
            logger.warning(f"Calculation failed: {e}")
            return None

    async def _apply_operation(
        self,
        df: Any,
        operation: Dict[str, Any],
        context: NodeExecutionContext
    ) -> Any:
        """Apply a single operation to the DataFrame"""
        import pandas as pd

        op_type = operation.get("type")

        if op_type == "filter":
            # Filter rows based on condition
            column = operation.get("column")
            op = operation.get("operator", "==")
            value = operation.get("value")

            if column and column in df.columns:
                if op == "==":
                    df = df[df[column] == value]
                elif op == "!=":
                    df = df[df[column] != value]
                elif op == ">":
                    df = df[df[column] > value]
                elif op == ">=":
                    df = df[df[column] >= value]
                elif op == "<":
                    df = df[df[column] < value]
                elif op == "<=":
                    df = df[df[column] <= value]
                elif op == "contains":
                    df = df[df[column].astype(str).str.contains(str(value), case=False, na=False)]
                elif op == "startswith":
                    df = df[df[column].astype(str).str.startswith(str(value), na=False)]
                elif op == "endswith":
                    df = df[df[column].astype(str).str.endswith(str(value), na=False)]
                elif op == "isnull":
                    df = df[df[column].isnull()]
                elif op == "notnull":
                    df = df[df[column].notnull()]
                elif op == "in":
                    if isinstance(value, list):
                        df = df[df[column].isin(value)]

        elif op_type == "select":
            # Select specific columns
            columns = operation.get("columns", [])
            if columns:
                existing = [c for c in columns if c in df.columns]
                df = df[existing]

        elif op_type == "rename":
            # Rename columns
            mapping = operation.get("mapping", {})
            if mapping:
                df = df.rename(columns=mapping)

        elif op_type == "sort":
            # Sort by columns
            columns = operation.get("columns", [])
            ascending = operation.get("ascending", True)
            if columns:
                existing = [c for c in columns if c in df.columns]
                if existing:
                    df = df.sort_values(by=existing, ascending=ascending)

        elif op_type == "limit":
            # Limit rows
            n = operation.get("n", 100)
            offset = operation.get("offset", 0)
            df = df.iloc[offset:offset + n]

        elif op_type == "groupby":
            # Group and aggregate
            columns = operation.get("columns", [])
            agg_column = operation.get("aggColumn")
            agg_func = operation.get("aggFunc", "sum")

            if columns and agg_column and agg_column in df.columns:
                existing = [c for c in columns if c in df.columns]
                if existing:
                    if agg_func == "sum":
                        df = df.groupby(existing)[agg_column].sum().reset_index()
                    elif agg_func == "mean":
                        df = df.groupby(existing)[agg_column].mean().reset_index()
                    elif agg_func == "count":
                        df = df.groupby(existing)[agg_column].count().reset_index()
                    elif agg_func == "min":
                        df = df.groupby(existing)[agg_column].min().reset_index()
                    elif agg_func == "max":
                        df = df.groupby(existing)[agg_column].max().reset_index()

        elif op_type == "pivot":
            # Pivot table
            index = operation.get("index")
            columns = operation.get("columns")
            values = operation.get("values")
            agg_func = operation.get("aggFunc", "sum")

            if index and columns and values:
                df = pd.pivot_table(
                    df,
                    index=index,
                    columns=columns,
                    values=values,
                    aggfunc=agg_func
                ).reset_index()

        elif op_type == "add_column":
            # Add calculated column using safe calculation
            name = operation.get("name")
            expression = operation.get("expression")
            value = operation.get("value")

            if name:
                if expression:
                    # Use safe calculation (no eval)
                    result = self._safe_calculate_column(df, expression)
                    if result is not None:
                        df[name] = result
                    else:
                        df[name] = None
                elif value is not None:
                    df[name] = value

        elif op_type == "drop_columns":
            # Remove columns
            columns = operation.get("columns", [])
            if columns:
                existing = [c for c in columns if c in df.columns]
                df = df.drop(columns=existing)

        elif op_type == "drop_duplicates":
            # Remove duplicate rows
            subset = operation.get("subset")
            keep = operation.get("keep", "first")
            if subset:
                existing = [c for c in subset if c in df.columns]
                df = df.drop_duplicates(subset=existing if existing else None, keep=keep)
            else:
                df = df.drop_duplicates(keep=keep)

        elif op_type == "fillna":
            # Fill missing values
            column = operation.get("column")
            value = operation.get("value")
            method = operation.get("method")  # 'ffill', 'bfill'

            if column and column in df.columns:
                if method:
                    df[column] = df[column].fillna(method=method)
                elif value is not None:
                    df[column] = df[column].fillna(value)
            elif value is not None:
                df = df.fillna(value)
            elif method:
                df = df.fillna(method=method)

        elif op_type == "cast":
            # Change column type
            column = operation.get("column")
            dtype = operation.get("dtype")  # 'string', 'int', 'float', 'datetime'

            if column and column in df.columns and dtype:
                try:
                    if dtype == "string":
                        df[column] = df[column].astype(str)
                    elif dtype == "int":
                        df[column] = pd.to_numeric(df[column], errors='coerce').astype('Int64')
                    elif dtype == "float":
                        df[column] = pd.to_numeric(df[column], errors='coerce')
                    elif dtype == "datetime":
                        df[column] = pd.to_datetime(df[column], errors='coerce')
                except Exception as e:
                    logger.warning(f"Type conversion failed for {column}: {e}")

        elif op_type == "merge":
            # Merge with another dataset (from variable)
            right_ref = operation.get("rightData")
            on = operation.get("on")
            how = operation.get("how", "left")

            if right_ref and on:
                # Try to get right DataFrame from context
                ref = right_ref.replace("{{", "").replace("}}", "").strip()
                right_data = None

                if ref in context.inputs:
                    val = context.inputs[ref]
                    if isinstance(val, dict) and "data" in val:
                        right_data = pd.DataFrame(val["data"])
                    elif isinstance(val, list):
                        right_data = pd.DataFrame(val)

                if ref in context.variables:
                    val = context.variables[ref]
                    if isinstance(val, list):
                        right_data = pd.DataFrame(val)

                if right_data is not None:
                    df = pd.merge(df, right_data, on=on, how=how)

        return df

    async def _format_output(
        self,
        df: Any,
        output_format: str
    ) -> Union[str, List, Dict]:
        """Format DataFrame to requested output format"""

        if output_format == "json":
            return df.to_dict(orient='records')
        elif output_format == "csv":
            return df.to_csv(index=False)
        elif output_format == "markdown":
            return df.to_markdown(index=False)
        elif output_format == "html":
            return df.to_html(index=False)
        elif output_format == "dict":
            return df.to_dict()
        elif output_format == "records":
            return df.to_dict(orient='records')
        elif output_format == "columns":
            return {
                "columns": list(df.columns),
                "data": df.values.tolist()
            }
        else:
            # Default to records
            return df.to_dict(orient='records')
