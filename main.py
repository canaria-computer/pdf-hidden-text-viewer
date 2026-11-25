"""PDF 隠れたテキストビューア のエントリポイント。

起動:
    uv run main.py
→ ブラウザで http://localhost:8317 が開きます。
"""

from pdfhidden.ui import main

if __name__ == "__main__":
    main()
