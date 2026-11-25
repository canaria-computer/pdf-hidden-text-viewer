"""pdf-hidden-text-viewer: PDF の隠れたテキスト可視化ツール。"""

from .analyzer import HiddenSpan, HiddenTextAnalyzer, PageReport

__all__ = ["HiddenSpan", "HiddenTextAnalyzer", "PageReport"]
