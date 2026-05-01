"""
FileLogger — ghi log vào file theo ngày (.NET-style).

Re-export from ``core.logging`` for backward-compatible imports::

    from core.log_file import FileLogger
"""

from core.logging.file_logger import FileLogger

__all__ = ["FileLogger"]
