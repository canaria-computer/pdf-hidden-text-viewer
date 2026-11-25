# pdf-hidden-text-viewer

PDF の「検索には引っかかるのに目に見えない」テキストを可視化するデスクトップアプリ。

画像や図形が上から重なって隠された文字列、白地に白文字、OCR レイヤーなど、
Ctrl+F では見つかるのに表示上は見えないテキストを検出して一覧表示します。

## 使い方

### デスクトップアプリ (既定)

```bash
uv run main.py
```

pywebview (WebView2) によるネイティブウィンドウが開きます。
Tauri と同じ構成 (システム WebView + ローカルバックエンド) で、
ブラウザは起動しません。

### ブラウザモード

```bash
uv run main.py --browser
```

`http://127.0.0.1:8317` がブラウザで開きます。

1. PDF をドラッグ＆ドロップ（またはパスを入力して「開く」）
2. ページ上の **赤枠** が「隠れたテキスト」の位置（一部だけ隠れている場合は橙枠）
   - 枠にホバーすると内容と原因（画像 / 塗りつぶし図形 / 同色など）を表示
3. **レントゲン** スイッチで ON にすると、テキストを除去したレンダリングの上に
   隠れた文字を赤で描き出します（被覆物の下から文字が浮かび上がる表示）
4. 右パネルでテキスト検索・隠れ率での絞り込み、項目クリックで該当ページへジャンプ
5. **全ページ解析** で文書全体をまとめて解析

## 検出原理 (レンダリング差分方式)

1. 対象ページを通常どおりレンダリング（画像 A）
2. 同じページから redaction で **テキストだけを除去** して
   （画像・ベクター図形はそのまま）レンダリング（画像 B）
3. 文字単位で A/B をピクセル比較し、差分がなければその文字は
   「検索にはヒットするが目に見えていない」と判定する

描画順序を解析せず「読者に実際に見えているか」で判定するため、
画像の上にキャプションが載るような正常なレイアウトを誤検出しません。

## コマンドラインでも使えます

```bash
uv run python -m pdfhidden samples/test_data.pdf
```

隠れたテキストをテキスト出力します（CI やバッチ確認用）。

## 単一 exe のビルド

Python / uv 環境のない PC でも動く単一実行ファイルを作成できます。
既定でネイティブウィンドウ (デスクトップアプリ) として起動し、
コンソールウィンドウは表示されません。
exe のアイコンにはプロジェクト直下の `icon.ico` が使われます
（ウィンドウとタスクバーのアイコンにも適用。生成方法は下記「制限事項」参照）。

```bash
uv run pyinstaller --onefile --noconsole --name pdf-hidden-text-viewer `
  --icon icon.ico --add-data "icon.ico;." `
  --collect-all nicegui --collect-all pymupdf `
  --collect-all webview --collect-all clr_loader --collect-all pythonnet `
  main.py
```

→ `dist/pdf-hidden-text-viewer.exe`（約 125 MB）が生成されます。
ダブルクリックでデスクトップアプリとして起動します
（WebView2 ランタイムが必要です。Windows 11 / 最新の Windows 10 には標準搭載）。
ブラウザで使いたい場合は `pdf-hidden-text-viewer.exe --browser` を実行します。

## CI/CD (GitHub Actions)

`.github/workflows/ci.yml` により、main ブランチへの push・PR のマージで
4 プラットフォームの自動ビルドが走ります:

| 成果物 | ランナー |
|---|---|
| `pdf-hidden-text-viewer-windows-x64.exe` | windows-latest |
| `pdf-hidden-text-viewer-macos-arm64.zip` | macos-14 (Apple Silicon) |
| `pdf-hidden-text-viewer-macos-x64.zip` | macos-13 (Intel) |
| `pdf-hidden-text-viewer-linux-x64` | ubuntu-latest |

ビルド前に `samples/test_data.pdf` でのスモークテスト（flag 検出の自動検証）が
各プラットフォームで実行されます。PR では Actions Artifacts に成果物が
アップロードされ、main への push (マージ) では **GitHub Release
(v`pyproject.toml` の version)** に全成果物が添付されます。
`pyproject.toml` の `version` を bump すると次の push で新しいタグの
Release が作成されます。

Linux 版の実行には webkit2gtk-4.1 / GTK3 が必要です
（Debian/Ubuntu: `sudo apt install libwebkit2gtk-4.1-0 libgtk-3-0`）。
macOS 版は未署名のため、初回は右クリック →「開く」で起動してください。

## 制限事項

- パスワードで保護された PDF は未対応
- 注釈（ハイライト等）で隠された文字は「見えている」判定になる
- アクセシビリティ: WCAG 2.1 AA の自動検査 (axe-core) に対応済み
  （ダークモード・コントラスト 4.5:1 以上・`lang="ja"`・ページ送りは文字付きボタンでアクセシブル名を内容から取得）。
  残る既知の指摘はフレームワーク内部起因の 2 件のみ:
  Quasar アップローダー内部の `nested-interactive` と、
  ブラウザー拡張 (DeepL) が注入する要素への `avoid-inline-spacing`
- 同梱の `samples/01.pdf`〜`09.pdf` は実際に配布された目隠し資料のサンプル、
  `samples/test_data.pdf` は動作確認用（`flag{...}` が隠されている）
- `icon.png` / `icon.svg` はアプリのアイコン素材。`icon.ico` は
  `uv run python -c "from PIL import Image; Image.open('icon.png').save('icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"`
  で再生成できます
