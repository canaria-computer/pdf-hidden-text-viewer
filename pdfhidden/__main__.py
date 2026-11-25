"""コマンドラインからの検出テスト。

使い方:
    uv run python -m pdfhidden samples/test_data.pdf
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pymupdf

from .analyzer import HiddenTextAnalyzer


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("使い方: python -m pdfhidden <PDFファイル>")
        return 2

    path = Path(args[0])
    data = path.read_bytes()

    try:
        analyzer = HiddenTextAnalyzer(data)
    except ValueError as exc:
        print(f"エラー: {exc}")
        return 1

    print(f"{path.name}: {analyzer.page_count} ページ")
    t0 = time.perf_counter()
    total_spans = 0
    total_chars = 0
    for report in analyzer.analyze_document():
        if not report.hidden_spans:
            continue
        if report.page_fully_hidden:
            print(f"-- ページ {report.page_no + 1}: 全テキストが隠れています --")
        else:
            print(f"-- ページ {report.page_no + 1} --")
        for span in report.hidden_spans:
            total_spans += 1
            total_chars += span.hidden_chars
            pct = span.hidden_ratio * 100
            print(
                f"  [{pct:5.1f}%] {span.reason}  "
                f"size={span.size:.1f} color={span.hex_color}"
            )
            print(f"    text   = {span.text!r}")
            if not span.fully_hidden:
                chars = "".join("■" if c.hidden else c.char for c in span.chars)
                print(f"    hidden = {chars!r}  ({span.hidden_chars}/{span.total_chars})")

    dt = time.perf_counter() - t0
    print(f"\n合計: {total_spans} span / {total_chars} 文字  ({dt:.2f} 秒)")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
