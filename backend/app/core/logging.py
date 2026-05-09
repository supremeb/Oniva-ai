"""
Structured Logging Configuration - JSON logging for production

Provides structured logs for better observability and log aggregation.
"""

import logging
import sys
from datetime import datetime
from typing import Any, Dict
from pythonjsonlogger import jsonlogger

from app.config import get_settings


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter with additional fields for observability.
    """
    
    def add_fields(
        self,
        log_record: Dict[str, Any],
        record: logging.LogRecord,
        message_dict: Dict[str, Any]
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        
        # Add timestamp in ISO format
        log_record["timestamp"] = datetime.utcnow().isoformat() + "Z"
        
        # Add level name
        log_record["level"] = record.levelname
        
        # Add source location
        log_record["logger"] = record.name
        log_record["module"] = record.module
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno
        
        # Add process/thread info for debugging
        log_record["process_id"] = record.process
        log_record["thread_id"] = record.thread


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    include_debug: bool = False
) -> None:
    """
    Configure application logging.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON format (for production)
        include_debug: Include debug logs from libraries
    """
    settings = get_settings()
    
    # Determine log level
    log_level = getattr(logging, level.upper(), logging.INFO)
    if settings.DEBUG:
        log_level = logging.DEBUG
    
    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    
    # Set formatter based on environment
    if json_format and not settings.DEBUG:
        formatter = CustomJsonFormatter(
            "%(timestamp)s %(level)s %(name)s %(message)s"
        )
    else:
        # Human-readable format for development
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    
    handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)
    
    # Reduce noise from libraries unless debug mode
    if not include_debug:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    logging.info("Logging configured", extra={"json_format": json_format, "level": level})


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name"""
    return logging.getLogger(name)
