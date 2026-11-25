"""NiceGUI による GUI。

起動: uv run main.py  →  http://localhost:8317
"""

from __future__ import annotations

import asyncio
import base64
import html as html_mod
import sys
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import ui

from .analyzer import HiddenSpan, HiddenTextAnalyzer, PageReport

ZOOM_OPTIONS = {0.5: "50%", 0.75: "75%", 1.0: "100%", 1.5: "150%", 2.0: "200%", 3.0: "300%"}
RATIO_OPTIONS = {0.0: "すべて表示", 0.25: "25%以上隠れ", 0.5: "50%以上隠れ", 1.0: "完全に隠れている"}

HEAD_CSS = """
<style>
.nicegui-content {
  height: 100vh; padding: 0 !important; overflow: hidden;
  display: flex; flex-direction: column;
}
.hv-marker {
  position: absolute; box-sizing: border-box; cursor: pointer;
  border: 2px solid #ef4444; background: rgba(239, 68, 68, 0.15);
}
.hv-marker.partial { border-color: #f59e0b; background: rgba(245, 158, 11, 0.15); }
.hv-marker.reveal { border-style: dashed; background: transparent; }
.hv-marker:hover { border-color: #ffffff; }
.hv-reveal-text {
  position: absolute; box-sizing: border-box;
  color: #dc2626; background: rgba(255, 255, 255, 0.85);
  font-family: sans-serif; font-weight: 600; line-height: 1.15;
  white-space: pre; padding: 0 1px; border-radius: 2px;
}
@keyframes hvflash {
  0% { box-shadow: 0 0 0 6px rgba(239, 68, 68, 0.9); }
  100% { box-shadow: 0 0 0 18px rgba(239, 68, 68, 0); }
}
.hv-flash { animation: hvflash 1.2s ease-out; }
.hv-card.selected { border: 1px solid #ef4444; }
</style>
"""


@dataclass
class State:
    analyzer: HiddenTextAnalyzer | None = None
    file_name: str = ""
    page_no: int = 0
    zoom: float = 1.0
    show_boxes: bool = True
    reveal: bool = False
    min_ratio: float = 0.25
    query: str = ""
    selected: tuple[int, int] | None = None  # (page_no, span index)
    running: bool = False
    progress_done: int = 0
    progress_total: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _data_uri(png: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(png).decode()}"


def _snippet(text: str, limit: int = 90) -> str:
    text = text.replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _icon_path() -> str:
    """ウィンドウ・タブのアイコンを返す。

    exe 実行時は同梱された icon.ico (PyInstaller の --add-data) を使う。
    開発時はプロジェクト直下の icon.ico / icon.png。無ければ絵文字フォールバック。
    Windows ネイティブモードでは .ico がウィンドウ・タスクバーのアイコンに適用される。
    """
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "icon.ico"
        if bundled.is_file():
            return str(bundled)
    for name in ("icon.ico", "icon.png"):
        p = Path(name)
        if p.is_file():
            return str(p)
    return "📄"


def build_app() -> None:
    """ページを登録する。main() から呼び出し、その後 ui.run() すること。"""

    @ui.page("/", language="ja")
    def index() -> None:
        state = State()

        ui.dark_mode(True)
        ui.colors(primary="#467aab")
        ui.add_head_html(HEAD_CSS)

        # ------------------------------------------------------------------
        # ハンドラ (UI 要素は後で定義するので、実行時に名前解決する)

        async def load_pdf(data: bytes, name: str) -> None:
            try:
                analyzer = await asyncio.to_thread(HiddenTextAnalyzer, data)
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
                return
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"PDF を開けませんでした: {exc}", type="negative")
                return
            async with state.lock:
                if state.analyzer is not None:
                    state.analyzer.close()
                state.analyzer = analyzer
                state.file_name = name
                state.page_no = 0
                state.selected = None
            ui.notify(f"{name} を読み込みました ({analyzer.page_count} ページ)", type="positive")
            await refresh_all()

        async def on_upload(e) -> None:
            data = await e.file.read()
            await load_pdf(data, e.file.name)

        async def load_from_path() -> None:
            raw = path_input.value.strip().strip('"')
            if not raw:
                ui.notify("PDF のパスを入力してください", type="warning")
                return
            p = Path(raw)
            if not p.is_absolute():
                p = Path.cwd() / p
            if not p.is_file():
                ui.notify(f"ファイルが見つかりません: {p}", type="negative")
                return
            try:
                data = await asyncio.to_thread(p.read_bytes)
            except OSError as exc:
                ui.notify(f"読み込みエラー: {exc}", type="negative")
                return
            await load_pdf(data, p.name)

        async def goto_page(delta: int) -> None:
            if state.analyzer is None:
                return
            new = max(0, min(state.analyzer.page_count - 1, state.page_no + delta))
            if new == state.page_no:
                return
            state.page_no = new
            state.selected = None
            await refresh_all()

        async def on_zoom_change(e) -> None:
            state.zoom = float(e.value)
            await refresh_all()

        async def on_toggle_boxes(e) -> None:
            state.show_boxes = bool(e.value)
            await refresh_all()

        async def on_toggle_reveal(e) -> None:
            state.reveal = bool(e.value)
            await refresh_all()

        async def on_ratio_change(e) -> None:
            state.min_ratio = float(e.value)
            await refresh_all()

        async def on_search_change(e) -> None:
            state.query = str(e.value)
            await refresh_all()

        async def analyze_all() -> None:
            if state.analyzer is None or state.running:
                return
            state.running = True
            state.progress_total = state.analyzer.page_count
            state.progress_done = 0
            analyze_btn.props("loading")
            try:
                for pno in range(state.analyzer.page_count):
                    async with state.lock:
                        await asyncio.to_thread(state.analyzer.analyze_page, pno)
                    state.progress_done = pno + 1
                ui.notify("全ページの解析が完了しました", type="positive")
            except Exception as exc:  # noqa: BLE001
                ui.notify(f"解析エラー: {exc}", type="negative")
            finally:
                state.running = False
                analyze_btn.props(remove="loading")
            rebuild_list()
            update_labels()

        def tick() -> None:
            if state.running:
                progress_bar.set_value(state.progress_done / max(1, state.progress_total))
                progress_bar.set_visibility(True)
            else:
                progress_bar.set_visibility(False)

        # ------------------------------------------------------------------
        # レイアウト

        with (
            ui.row()
            .classes("w-full shrink-0 items-center gap-2 q-px-md q-py-sm flex-nowrap")
            .style("background: #1f2937; border-bottom: 1px solid #374151; flex-wrap: nowrap;")
        ):
            ui.label("PDF 隠れたテキストビューア").classes("text-h6 q-mr-md whitespace-nowrap")

            ui.upload(
                on_upload=on_upload,
                auto_upload=True,
                label="PDFをドラッグ＆ドロップ / クリックして選択",
            ).props("accept=application/pdf,.pdf").classes("q-ml-sm")

            with ui.row().classes("items-center gap-1 q-ml-sm"):
                path_input = (
                    ui.input(placeholder="または PDF のパス (例: samples/test_data.pdf)")
                    .classes("w-72")
                    .props("dense outlined dark")
                )
                ui.button("開く", icon="folder_open").props("dense").on_click(load_from_path)

            ui.element("div").classes("flex-grow")

            prev_btn = (
                ui.button("前へ", icon="chevron_left").props("flat dense color=white")
            )
            page_label = ui.label("- / -").classes("q-mx-xs")
            next_btn = (
                ui.button("次へ", icon="chevron_right").props("flat dense color=white")
            )

            ui.select(ZOOM_OPTIONS, value=state.zoom, on_change=on_zoom_change) \
                .classes("w-24 q-ml-md").props("dense outlined dark")

            ui.switch("強調", value=state.show_boxes, on_change=on_toggle_boxes).props("dense")
            ui.switch("レントゲン", value=state.reveal, on_change=on_toggle_reveal).props("dense")

            analyze_btn = (
                ui.button("全ページ解析", icon="search").props("dense").on_click(analyze_all)
            )

        progress_bar = ui.linear_progress(0.0).style("height: 6px;").set_visibility(False)

        with ui.row().classes("w-full flex-nowrap items-stretch flex-grow min-h-0 gap-0 q-pa-none"):
            viewer = (
                ui.column()
                .classes("flex-grow min-w-0")
                .style("overflow: auto; background: #111827;")
            )
            with (
                ui.column()
                .classes("w-[420px] shrink-0")
                .style("overflow: auto; border-left: 1px solid #374151; padding: 12px;")
            ):
                ui.label("隠れたテキスト").classes("text-h6")
                summary_label = ui.label("PDF を開いてください").classes("text-caption")
                ui.input(
                    placeholder="テキストで絞り込み (Ctrl+F 相当)",
                    on_change=on_search_change,
                ).classes("w-full q-mt-sm").props("dense outlined dark")
                ui.select(RATIO_OPTIONS, value=state.min_ratio, on_change=on_ratio_change) \
                    .classes("w-full q-mt-xs").props("dense outlined dark")
                list_holder = ui.column().classes("w-full gap-1 q-mt-sm")

        prev_btn.on_click(lambda: asyncio.create_task(goto_page(-1)))
        next_btn.on_click(lambda: asyncio.create_task(goto_page(1)))
        ui.timer(0.25, tick)

        # ------------------------------------------------------------------
        # 描画更新

        def filtered_spans(report: PageReport) -> list[HiddenSpan]:
            spans = report.hidden_spans
            if state.min_ratio > 0:
                spans = [s for s in spans if s.hidden_ratio >= state.min_ratio]
            q = state.query.strip().lower()
            if q:
                spans = [s for s in spans if q in s.text.lower()]
            return spans

        def update_labels() -> None:
            if state.analyzer is None:
                page_label.set_text("- / -")
                summary_label.set_text("PDF を開いてください")
                return
            page_label.set_text(f"{state.page_no + 1} / {state.analyzer.page_count}")
            reports = state.analyzer.all_reports()
            analyzed = len(reports)
            n_spans = sum(len(r.hidden_spans) for r in reports)
            report = state.analyzer.report(state.page_no)
            state_here = f"このページ {len(report.hidden_spans)} 件" if report is not None else "未解析"
            summary_label.set_text(
                f"{state.file_name} | {state_here} | 解析済み {analyzed}"
                f"/{state.analyzer.page_count} ページ・合計 {n_spans} 件"
            )

        def rebuild_viewer(
            report: PageReport | None, png: bytes, png_reveal: bytes | None
        ) -> None:
            viewer.clear()
            with viewer:
                if report is None:
                    if state.analyzer is None:
                        ui.label(
                            "PDF をアップロードするか、パスを入力して「開く」を押してください。\n"
                            "同梱の samples/test_data.pdf で動作を確認できます。"
                        ).classes("q-ma-md text-gray-400").style("white-space: pre-wrap;")
                    return

                rect = state.analyzer.page_rect(report.page_no)  # type: ignore[union-attr]
                zoom = state.zoom
                w = rect.width * zoom
                h = rect.height * zoom
                holder = (
                    ui.element("div")
                    .style(
                        f"position: relative; width: {w:.0f}px; height: {h:.0f}px; "
                        "margin: 16px auto; box-shadow: 0 2px 12px rgba(0,0,0,0.5);"
                    )
                )
                with holder:
                    src = _data_uri(png_reveal if state.reveal else png)
                    ui.image(src).style(f"width: {w:.0f}px; height: {h:.0f}px; display: block;")

                    spans = filtered_spans(report)
                    if state.reveal and png_reveal:
                        # レントゲン表示: テキスト除去画像の上に隠れた文字を赤で描く
                        parts = []
                        for span in spans:
                            x = span.bbox[0] * zoom
                            y = span.bbox[1] * zoom
                            fs = max(6.0, span.size * zoom)
                            escaped = html_mod.escape(_snippet(span.text, 160))
                            parts.append(
                                f'<div class="hv-reveal-text" style="left: {x:.1f}px; '
                                f'top: {y:.1f}px; font-size: {fs:.1f}px;">{escaped}</div>'
                            )
                        if parts:
                            ui.html("".join(parts))

                    if state.show_boxes:
                        for idx, span in enumerate(spans):
                            x = span.bbox[0] * zoom
                            y = span.bbox[1] * zoom
                            bw = max(3.0, (span.bbox[2] - span.bbox[0]) * zoom)
                            bh = max(3.0, (span.bbox[3] - span.bbox[1]) * zoom)
                            cls = "hv-marker"
                            if not span.fully_hidden:
                                cls += " partial"
                            if state.reveal:
                                cls += " reveal"
                            cls += f" hv-m{report.page_no}-{idx}"
                            m = (
                                ui.element("div")
                                .classes(cls)
                                .style(
                                    f"left: {x:.1f}px; top: {y:.1f}px; "
                                    f"width: {bw:.1f}px; height: {bh:.1f}px;"
                                )
                            )
                            with m:
                                ui.tooltip(
                                    f"{span.hidden_chars}/{span.total_chars} 文字が隠れています\n"
                                    f"{span.reason}\n{_snippet(span.text, 200)}"
                                )
                            m.on("click", _marker_click_factory(report.page_no, idx))

        def _marker_click_factory(pno: int, idx: int):
            def handler(_) -> None:
                state.selected = (pno, idx)
                rebuild_list()
                ui.run_javascript(
                    f"""
                    const el = document.querySelector('.hv-item-{pno}-{idx}');
                    if (el) {{
                        el.scrollIntoView({{block: 'center', behavior: 'smooth'}});
                        el.classList.remove('hv-flash');
                        void el.offsetWidth;
                        el.classList.add('hv-flash');
                    }}
                    """
                )

            return handler

        def _item_click_factory(pno: int, idx: int):
            async def handler() -> None:
                state.selected = (pno, idx)
                rebuild_list()
                if pno != state.page_no:
                    state.page_no = pno
                    await refresh_all()
                ui.run_javascript(
                    f"""
                    const el = document.querySelector('.hv-m{pno}-{idx}');
                    if (el) {{
                        el.scrollIntoView({{block: 'center', behavior: 'smooth'}});
                        el.classList.remove('hv-flash');
                        void el.offsetWidth;
                        el.classList.add('hv-flash');
                    }}
                    """
                )

            return handler

        def rebuild_list() -> None:
            list_holder.clear()
            with list_holder:
                if state.analyzer is None:
                    ui.label(
                        "PDF を開くと、隠れたテキストの一覧がここに表示されます。"
                    ).classes("text-caption text-gray-400")
                    return
                filtering = bool(state.query.strip()) or state.min_ratio > 0
                shown = 0
                for report in state.analyzer.all_reports():
                    spans = filtered_spans(report)
                    if not spans:
                        continue
                    if not filtering and report.page_no != state.page_no:
                        # 未絞り込みのときは他ページの件数だけ表示 (軽量化)
                        ui.label(f"ページ {report.page_no + 1}: {len(spans)} 件").classes(
                            "text-caption text-gray-400 q-mt-sm"
                        )
                        continue
                    for idx, span in enumerate(spans):
                        shown += 1
                        key = (report.page_no, idx)
                        classes_ = "hv-card cursor-pointer q-pa-sm"
                        if state.selected == key:
                            classes_ += " selected"
                        card = ui.card().classes(classes_)
                        card.classes(f"hv-item-{report.page_no}-{idx}")
                        with card:
                            with ui.row().classes("items-center gap-1 q-pa-none flex-nowrap"):
                                color = "red" if span.fully_hidden else "amber"
                                ui.badge(
                                    f"{span.hidden_chars}/{span.total_chars}", color=color
                                ).props("outline")
                                ui.label(f"P{span.page_no + 1}").classes(
                                    "text-caption text-gray-400"
                                )
                                ui.label(span.reason).classes(
                                    "text-caption text-gray-400 ellipsis"
                                )
                            ui.label(_snippet(span.text)).classes("text-sm break-all")
                        card.on("click", _item_click_factory(report.page_no, idx))
                if shown == 0:
                    ui.label(
                        "条件に一致する隠れたテキストはありません。\n"
                        "他のページは「全ページ解析」で調べられます。"
                    ).classes("text-caption text-gray-400").style("white-space: pre-wrap;")
            update_labels()

        async def refresh_all() -> None:
            if state.analyzer is None:
                rebuild_viewer(None, b"", None)
                rebuild_list()
                update_labels()
                return
            pno = state.page_no
            async with state.lock:
                try:
                    report = await asyncio.to_thread(state.analyzer.analyze_page, pno)
                    png = await asyncio.to_thread(state.analyzer.render_page, pno, state.zoom)
                    png_reveal = None
                    if state.reveal:
                        png_reveal = await asyncio.to_thread(
                            state.analyzer.render_page, pno, state.zoom, text_removed=True
                        )
                except Exception as exc:  # noqa: BLE001
                    ui.notify(f"ページ解析エラー: {exc}", type="negative")
                    return
            rebuild_viewer(report, png, png_reveal)
            rebuild_list()
            update_labels()

        # 初期表示
        rebuild_viewer(None, b"", None)
        rebuild_list()
        update_labels()


def main() -> None:
    """アプリを起動する。

    既定では pywebview によるネイティブウィンドウ (デスクトップアプリ) で開く。
    main.py --browser を指定するとブラウザモードで起動する。
    """
    build_app()

    browser = "--browser" in sys.argv

    if browser:
        ui.run(
            title="PDF 隠れたテキストビューア",
            port=8317,
            host="127.0.0.1",
            reload=False,
            show=True,
            favicon=_icon_path(),
        )
    else:
        # Tauri と同様の構成: システム WebView (WebView2) + ローカルサーバー
        ui.run(
            title="PDF 隠れたテキストビューア",
            port=8317,
            host="127.0.0.1",
            reload=False,
            native=True,
            window_size=(1440, 900),
            show=True,
            favicon=_icon_path(),  # Windows ではウィンドウ・タスクバーのアイコンになる
        )
