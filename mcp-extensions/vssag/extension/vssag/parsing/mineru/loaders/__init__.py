"""Loader namespace reserved for parser-specific adapters."""
from .docx import load as load_docx
from .excel import load as load_excel
from .markdown import load as load_markdown
from .pdf import load as load_pdf
from .pptx import load as load_pptx

__all__ = ["load_markdown", "load_pdf", "load_docx", "load_excel", "load_pptx"]
