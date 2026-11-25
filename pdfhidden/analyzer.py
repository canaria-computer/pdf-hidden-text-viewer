"""PDF 内の「見えないテキスト」を検出するエンジン。

検出原理 (レンダリング差分方式):

1. 対象ページを通常どおりレンダリングする (画像 A)。
2. 同じページのコピーにページ全体を覆う redact 注釈を適用し、
   「テキストだけ」を除去して (画像・ベクター図形はそのまま) レンダリングする (画像 B)。
3. 文字単位で A/B をピクセル比較し、差分がなければその文字は
   「Ctrl+F などの検索にはヒットするが、目には見えていない」状態と判定できる。

描画順序 (z-order) を解析せず、読者に実際に見えているかで判定するため、
画像の上にキャプションが載っているような正常なレイアウトを誤検出しない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import pymupdf


@dataclass
class CharInfo:
    """文字 1 個ぶんの判定結果。bbox は PDF ページ座標 (pt)。"""

    bbox: tuple[float, float, float, float]
    char: str
    hidden: bool


@dataclass
class HiddenSpan:
    """隠れている文字を 1 個以上含むテキスト span。"""

    page_no: int  # 0 始まり
    bbox: tuple[float, float, float, float]
    text: str
    font: str
    size: float
    color: int  # sRGB 整数
    alpha: float
    hidden_chars: int
    total_chars: int
    covers: list[str] = field(default_factory=list)
    chars: list[CharInfo] = field(default_factory=list)

    @property
    def hidden_ratio(self) -> float:
        return self.hidden_chars / self.total_chars if self.total_chars else 0.0

    @property
    def fully_hidden(self) -> bool:
        return self.total_chars > 0 and self.hidden_chars == self.total_chars

    @property
    def hex_color(self) -> str:
        return f"#{self.color:06x}"

    @property
    def reason(self) -> str:
        """隠れている原因の推定ラベル。"""
        if self.covers:
            return "・".join(dict.fromkeys(self.covers)) + " が上に重なっている"
        if self.alpha <= 0.01:
            return "非描画テキスト (OCR レイヤーなどの可能性)"
        return "背景と同色のテキストの可能性"


@dataclass
class PageReport:
    """1 ページぶんの解析結果。"""

    page_no: int  # 0 始まり
    hidden_spans: list[HiddenSpan] = field(default_factory=list)
    total_spans: int = 0
    page_fully_hidden: bool = False  # ページ上の全テキストが隠れている

    @property
    def hidden_char_count(self) -> int:
        return sum(s.hidden_chars for s in self.hidden_spans)

    @property
    def total_char_count(self) -> int:
        return sum(s.total_chars for s in self.hidden_spans)


def _crop_samples(
    pix: pymupdf.Pixmap, rect: pymupdf.Rect
) -> tuple[bytes, int, int, int] | None:
    """pixmap の一部を生バイト列として切り出す。

    返り値は (samples, 1px あたりバイト数, 幅, 高さ)。範囲外はクリップし、
    空になったら None を返す。Pixmap コンストラクタを使わないのは
    pymupdf 2.1 の Pixmap(pix, irect) に内部バグがあるため。
    """
    px, py = pix.x, pix.y
    n = pix.n
    stride = pix.stride
    x0 = max(px, math.floor(rect.x0))
    y0 = max(py, math.floor(rect.y0))
    x1 = min(px + pix.width, math.ceil(rect.x1))
    y1 = min(py + pix.height, math.ceil(rect.y1))
    if x1 <= x0 or y1 <= y0:
        return None
    samples = pix.samples
    byte_x0 = (x0 - px) * n
    byte_w = (x1 - x0) * n
    out = bytearray((y1 - y0) * byte_w)
    pos = 0
    for y in range(y0, y1):
        row = (y - py) * stride + byte_x0
        out[pos : pos + byte_w] = samples[row : row + byte_w]
        pos += byte_w
    return bytes(out), n, x1 - x0, y1 - y0


def _is_hidden(crop_a: tuple[bytes, int, int, int], crop_b: tuple[bytes, int, int, int], tol: float) -> bool:
    """2 つの切り抜きを比較し、差分ピクセルが許容範囲内なら「見えていない」扱い。"""
    sa, n, w, h = crop_a
    sb = crop_b[0]
    if sa == sb:
        return True
    limit = max(2, int(w * h * tol))
    diff = 0
    for i in range(len(sa)):
        if sa[i] != sb[i]:
            diff += 1
            if diff > limit:
                return False
    return True


class HiddenTextAnalyzer:
    """PDF を開き、ページ単位で隠れたテキストを検出する。

    内部に 2 つの Document を持つ:
    - view_doc   : 元のまま (表示用レンダリング)
    - scratch_doc: redaction でテキストを除去したもの (差分比較用)
    """

    DETECT_ZOOM = 1.5  # 差分検出に使うレンダリング倍率
    TOLERANCE = 0.02  # 差分ピクセルがこの割合以下なら「隠れている」扱い

    def __init__(self, data: bytes):
        self._data = data
        self.view_doc = pymupdf.open(stream=data, filetype="pdf")
        if self.view_doc.needs_pass:
            self.view_doc.close()
            raise ValueError("パスワードで保護された PDF はサポートしていません")
        self.scratch_doc = pymupdf.open(stream=data, filetype="pdf")
        self._reports: dict[int, PageReport] = {}

    # ------------------------------------------------------------------
    # 基本情報

    @property
    def page_count(self) -> int:
        return self.view_doc.page_count

    def page_rect(self, page_no: int) -> pymupdf.Rect:
        return self.view_doc[page_no].rect

    def has_report(self, page_no: int) -> bool:
        return page_no in self._reports

    def report(self, page_no: int) -> PageReport | None:
        return self._reports.get(page_no)

    def all_reports(self) -> list[PageReport]:
        return [self._reports[p] for p in sorted(self._reports)]

    def close(self) -> None:
        self.view_doc.close()
        self.scratch_doc.close()

    # ------------------------------------------------------------------
    # 解析

    def analyze_page(self, page_no: int) -> PageReport:
        """1 ページを解析して PageReport を返す (結果はキャッシュされる)。"""
        if page_no in self._reports:
            return self._reports[page_no]

        view_page = self.view_doc[page_no]
        scratch_page = self.scratch_doc[page_no]

        # --- 画像 B 用意: テキストだけを除去する ---
        scratch_page.add_redact_annot(scratch_page.rect, fill=False)
        scratch_page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        )

        z = self.DETECT_ZOOM
        mat = pymupdf.Matrix(z, z)
        pix_a = view_page.get_pixmap(matrix=mat, alpha=False, annots=False)
        pix_b = scratch_page.get_pixmap(matrix=mat, alpha=False, annots=False)
        page_equal = pix_a.samples == pix_b.samples

        # --- 被覆候補 (span と交差する画像・図形) ---
        image_rects = [
            pymupdf.Rect(info["bbox"]) for info in view_page.get_image_info()
        ]
        drawing_items: list[tuple[pymupdf.Rect, str]] = []
        for d in view_page.get_drawings():
            fill = d.get("fill")
            stroke = d.get("stroke")
            width = d.get("width") or 0
            if fill is not None:
                label = "塗りつぶし図形"
            elif stroke is not None and width >= 2.0:
                label = "太い線"
            else:
                continue
            drawing_items.append((pymupdf.Rect(d["rect"]), label))

        def covers_for(bbox: pymupdf.Rect) -> list[str]:
            found: list[str] = []
            for r in image_rects:
                if (r & bbox).get_area() > 0.5:
                    found.append("画像")
                    break
            for r, label in drawing_items:
                if (r & bbox).get_area() > 0.5:
                    found.append(label)
            return found

        # --- テキスト span を取り出し、文字単位で比較 ---
        report = PageReport(page_no=page_no)
        raw = view_page.get_text("rawdict")
        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    report.total_spans += 1
                    chars = [
                        c for c in span.get("chars", []) if not c["c"].isspace()
                    ]
                    if not chars:
                        continue

                    span_bbox = pymupdf.Rect(span["bbox"])
                    char_infos: list[CharInfo] = []
                    hidden = 0
                    total = 0

                    if page_equal:
                        # ページ全体のレンダリングが一致 = 全テキストが見えていない
                        for c in chars:
                            char_infos.append(CharInfo(tuple(c["bbox"]), c["c"], True))
                        hidden = total = len(chars)
                    else:
                        # span 単位の高速チェック (完全一致なら全文字隠れ)
                        sa = _crop_samples(pix_a, span_bbox * z)
                        sb = _crop_samples(pix_b, span_bbox * z)
                        span_hidden = (
                            sa is not None
                            and sb is not None
                            and _is_hidden(sa, sb, self.TOLERANCE)
                        )
                        for c in chars:
                            cb = pymupdf.Rect(c["bbox"])
                            ca = _crop_samples(pix_a, cb * z)
                            cbx = _crop_samples(pix_b, cb * z)
                            if ca is None or cbx is None:
                                continue  # ページ外の文字は数えない
                            h = span_hidden or _is_hidden(ca, cbx, self.TOLERANCE)
                            total += 1
                            hidden += int(h)
                            char_infos.append(CharInfo(tuple(c["bbox"]), c["c"], h))

                    if hidden == 0 or total == 0:
                        continue
                    hs = HiddenSpan(
                        page_no=page_no,
                        bbox=tuple(span_bbox),
                        text="".join(c["c"] for c in span.get("chars", [])),
                        font=span.get("font", ""),
                        size=float(span.get("size", 0.0)),
                        color=int(span.get("color", 0)),
                        alpha=float(span.get("alpha", 1.0)),
                        hidden_chars=hidden,
                        total_chars=total,
                        covers=covers_for(span_bbox),
                        chars=char_infos,
                    )
                    report.hidden_spans.append(hs)

        report.page_fully_hidden = page_equal and bool(report.hidden_spans)
        self._reports[page_no] = report
        return report

    def analyze_document(
        self, progress: Callable[[int, int], None] | None = None
    ) -> list[PageReport]:
        """全ページを解析する。progress(完了数, 全体数) で進捗を通知する。"""
        total = self.page_count
        for pno in range(total):
            self.analyze_page(pno)
            if progress is not None:
                progress(pno + 1, total)
        return self.all_reports()

    # ------------------------------------------------------------------
    # レンダリング

    def render_page(
        self, page_no: int, zoom: float, *, text_removed: bool = False
    ) -> bytes:
        """表示用 PNG を返す。text_removed=True ならテキスト除去後の画像。"""
        if text_removed:
            self.analyze_page(page_no)  # redaction 適用を保証
            page = self.scratch_doc[page_no]
        else:
            page = self.view_doc[page_no]
        pix = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=True
        )
        return pix.tobytes("png")
