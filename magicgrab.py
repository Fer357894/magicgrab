"""
MagicGrab - Fixed & Accurate Screen OCR
Fixes:
  - Correct screen coordinate mapping (mapToGlobal(QPoint(0,0)))
  - Real rubber-band drag-to-select overlay (full-screen transparent capture window)
  - Proper thread safety: all Qt calls on main thread via QTimer.singleShot
  - Better OCR pipeline with adaptive thresholding fallback
  - Live preview of captured region in scan area
"""

import sys
import webbrowser
import threading
import tempfile
import os

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QTextEdit, QPushButton, QFrame, QMessageBox, QSlider, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, QPoint, QRect, Signal, QObject
from PySide6.QtGui import (
    QCursor, QPainter, QColor, QPen, QPixmap, QFont, QBrush
)

from PIL import Image, ImageEnhance, ImageFilter, ImageGrab, ImageOps
import pytesseract


# ── Signal bridge (worker → main thread) ─────────────────────────
class CaptureSignals(QObject):
    done = Signal(str, object)   # text, PIL image or None
    error = Signal(str)


# ── Full-screen rubber-band selector ─────────────────────────────
class ScreenSelector(QWidget):
    """Transparent full-screen overlay; user drags to select a rectangle."""

    selected = Signal(QRect)  # emitted with PHYSICAL pixel rect on mouse release

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(QCursor(Qt.CrossCursor))
        self._origin = QPoint()
        self._current = QPoint()
        self._dragging = False
        # Grab DPR once at construction — constant for the session
        self._dpr = QApplication.primaryScreen().devicePixelRatio()

    def showFullScreen(self):
        super().showFullScreen()

    def paintEvent(self, event):
        painter = QPainter(self)
        # Dim the screen
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self._dragging:
            sel = QRect(self._origin, self._current).normalized()
            # Cut-out (show original screen color through selection)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(sel, QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            # Selection border
            pen = QPen(QColor("#7c6fff"), 2)
            painter.setPen(pen)
            painter.drawRect(sel)
            # Size label
            painter.setPen(QColor("white"))
            painter.setFont(QFont("Consolas", 10))
            label = f"{sel.width()} × {sel.height()}"
            painter.drawText(sel.left() + 4, sel.top() - 6, label)
        else:
            painter.setPen(QColor("white"))
            painter.setFont(QFont("Consolas", 14))
            painter.drawText(
                self.rect(), Qt.AlignCenter,
                "Drag to select the region to capture\nPress Esc to cancel"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._origin = event.globalPosition().toPoint()
            self._current = self._origin
            self._dragging = True
            self.update()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._current = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            rect = QRect(self._origin, self._current).normalized()
            self.close()
            if rect.width() > 10 and rect.height() > 10:
                # Convert logical pixels -> physical pixels for ImageGrab
                dpr = self._dpr
                physical = QRect(
                    int(rect.left()   * dpr),
                    int(rect.top()    * dpr),
                    int(rect.width()  * dpr),
                    int(rect.height() * dpr),
                )
                self.selected.emit(physical)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()


# ── Main window ───────────────────────────────────────────────────
class MagicGrabWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setGeometry(400, 100, 540, 760)
        self.setMinimumSize(460, 600)

        self._current_text = ""
        self._threshold = 140
        self._signals = CaptureSignals()
        self._signals.done.connect(self._show_result)
        self._signals.error.connect(self._capture_failed)
        self._drag_pos = QPoint()

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        layout.addWidget(self._create_title_bar())
        layout.addWidget(self._create_capture_button())
        layout.addWidget(self._create_scan_area())
        layout.addWidget(self._create_threshold_control())
        layout.addWidget(self._create_result_area())
        layout.addLayout(self._create_toolbar())

        self.apply_styles()

    # ── UI builders ───────────────────────────────────────────────
    def _create_title_bar(self):
        bar = QFrame()
        bar.setFixedHeight(48)
        bar.setStyleSheet("background-color: #0f0e1a; border-radius: 10px;")
        h = QHBoxLayout(bar)
        h.setContentsMargins(18, 0, 12, 0)
        title = QLabel("✦ MagicGrab")
        title.setStyleSheet("color: #a594ff; font-size: 16px; font-weight: bold;")
        h.addWidget(title)
        h.addStretch()
        for txt, fn in [("−", self.showMinimized), ("✕", self.close)]:
            btn = QPushButton(txt)
            btn.setFixedSize(34, 34)
            btn.clicked.connect(fn)
            btn.setStyleSheet("background: transparent; color: #aaa; font-size: 18px;")
            h.addWidget(btn)
        bar.mousePressEvent = self._bar_press
        bar.mouseMoveEvent = self._bar_move
        return bar

    def _bar_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def _bar_move(self, event):
        if not self._drag_pos.isNull():
            delta = event.globalPosition().toPoint() - self._drag_pos
            self.move(self.pos() + delta)
            self._drag_pos = event.globalPosition().toPoint()

    def _create_capture_button(self):
        self.capture_btn = QPushButton("⚡  CAPTURE  (drag to select region)")
        self.capture_btn.setMinimumHeight(54)
        self.capture_btn.setStyleSheet("""
            QPushButton {
                background-color: #7c6fff; color: white; font-size: 15px;
                font-weight: bold; border-radius: 10px;
            }
            QPushButton:pressed  { background-color: #6354e6; }
            QPushButton:disabled { background-color: #555; color: #888; }
        """)
        self.capture_btn.clicked.connect(self.start_capture)
        return self.capture_btn

    def _create_scan_area(self):
        self.scan_area = QLabel("📷 Preview of last captured region")
        self.scan_area.setMinimumHeight(200)
        self.scan_area.setAlignment(Qt.AlignCenter)
        self.scan_area.setStyleSheet("""
            background-color: #1c1c22; border: 2px dashed #7c6fff;
            border-radius: 10px; color: #9b96bc; font-size: 13px;
        """)
        self.scan_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return self.scan_area

    def _create_threshold_control(self):
        frame = QFrame()
        frame.setStyleSheet("background-color: #1c1c22; border-radius: 8px; padding: 8px;")
        layout = QVBoxLayout(frame)
        label = QLabel("Binarisation threshold  (lower = keep darker pixels)")
        label.setStyleSheet("color: #9b96bc; font-size: 12px;")
        layout.addWidget(label)

        row = QHBoxLayout()
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(60, 220)
        self.threshold_slider.setValue(self._threshold)
        self.threshold_slider.valueChanged.connect(self._update_threshold)
        row.addWidget(self.threshold_slider)

        self.threshold_value = QLabel(str(self._threshold))
        self.threshold_value.setFixedWidth(34)
        self.threshold_value.setStyleSheet("color: #7c6fff; font-weight: bold;")
        self.threshold_value.setAlignment(Qt.AlignCenter)
        row.addWidget(self.threshold_value)
        layout.addLayout(row)
        return frame

    def _update_threshold(self, value):
        self._threshold = value
        self.threshold_value.setText(str(value))

    def _create_result_area(self):
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(False)
        self.result_text.setPlaceholderText("Captured text will appear here…")
        self.result_text.setMinimumHeight(140)
        self.result_text.setStyleSheet("""
            QTextEdit {
                background-color: #1c1c22; color: #f0eeff; border-radius: 10px;
                padding: 14px; font-family: Consolas, monospace; font-size: 12.5px;
            }
            QTextEdit:focus {
                border: 1px solid #7c6fff;
            }
        """)
        self.result_text.textChanged.connect(self._sync_current_text)
        return self.result_text

    def _create_toolbar(self):
        h = QHBoxLayout()
        for text, slot in [
            ("⎘ Copy",      self.copy_text),
            ("🔎 Search",   self.search_web),
            ("🌐 Translate", self.translate_text),
            ("✕ Clear",     self.clear_text),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            h.addWidget(btn)
        return h

    def apply_styles(self):
        self.setStyleSheet("""
            QWidget#central {
                background-color: rgba(13, 13, 15, 245);
                border: 2px solid #7c6fff; border-radius: 14px;
            }
            QPushButton {
                background-color: #2a2a38; color: #f0eeff;
                border: none; border-radius: 6px; padding: 8px;
            }
            QPushButton:hover { background-color: #3a3a4a; }
            QSlider::groove:horizontal {
                height: 6px; background: #2a2a38; border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 16px; height: 16px; background: #7c6fff;
                border-radius: 8px; margin: -5px 0;
            }
            QSlider::sub-page:horizontal { background: #7c6fff; border-radius: 3px; }
        """)

    # ── Capture flow ──────────────────────────────────────────────
    def start_capture(self):
        """Hide the main window, show the full-screen selector."""
        self.capture_btn.setEnabled(False)
        self.capture_btn.setText("🖱  Select region on screen…")
        self.hide()
        # Small delay so window has time to disappear before the overlay
        QTimer.singleShot(150, self._show_selector)

    def _show_selector(self):
        self._selector = ScreenSelector()
        self._selector.selected.connect(self._on_region_selected)
        # If user cancels (Esc / closes without selecting), restore window
        self._selector.destroyed.connect(self._on_selector_closed)
        self._selector.showFullScreen()

    def _on_selector_closed(self):
        """Called when the selector widget is destroyed (Esc or after selection)."""
        # Only restore if capture didn't start (no rect selected)
        QTimer.singleShot(50, lambda: self._maybe_restore())

    def _maybe_restore(self):
        # If OCR thread is running, don't restore yet (it will do so)
        if self.capture_btn.isEnabled():
            return  # already restored by _show_result/_capture_failed
        # Button still disabled means OCR is running — let it restore
        # But if it was cancelled, restore now
        if not hasattr(self, '_ocr_running') or not self._ocr_running:
            self._restore_window()

    def _restore_window(self):
        self.show()
        self.raise_()
        self.capture_btn.setText("⚡  CAPTURE  (drag to select region)")
        self.capture_btn.setEnabled(True)

    def _on_region_selected(self, rect: QRect):
        """rect is already in physical pixel coordinates — grab exactly that."""
        self._ocr_running = True
        self.scan_area.setText("⏳ Running OCR…")
        bbox = (rect.left(), rect.top(), rect.right(), rect.bottom())
        threading.Thread(target=self._run_ocr_thread, args=(bbox,), daemon=True).start()

    def _run_ocr_thread(self, bbox):
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)

            # Save debug snapshot
            debug_path = os.path.join(tempfile.gettempdir(), "magicgrab_last_capture.png")
            img.save(debug_path)
            print(f"💾 Debug saved: {debug_path}")

            text = self._ocr(img)
            self._signals.done.emit(text, img)
        except Exception as e:
            self._signals.error.emit(str(e))
        finally:
            self._ocr_running = False

    # ── OCR pipeline ──────────────────────────────────────────────
    def _ocr(self, img: Image.Image) -> str:
        """
        Multi-pass OCR with background-subtraction + Sauvola adaptive threshold.

        Pass 1 – background subtraction (handles busy/checkered/gradient BGs):
            Estimate the local background with a heavy blur, subtract it so
            text strokes become high-contrast on a neutral grey.
        Pass 2 – Sauvola local adaptive threshold (handles uneven lighting /
            low global contrast where a fixed threshold fails).
        Pass 3 – standard luminance threshold (user-tunable slider)
        Pass 4 – inverted threshold (light text on dark BG)
        Pass 5 – raw greyscale fallback (let Tesseract decide)
        """
        import numpy as np

        # ── Upscale first — Tesseract accuracy scales with resolution ──
        scale = max(2, min(4, int(2000 / max(img.width, 1))))
        img_up = img.resize((img.width * scale, img.height * scale), Image.LANCZOS)
        gray = img_up.convert("L")

        # ── Pass 1: background subtraction ────────────────────────────
        blur_r = max(15, int(min(gray.width, gray.height) * 0.10))
        if blur_r % 2 == 0:
            blur_r += 1

        bg = gray.filter(ImageFilter.GaussianBlur(radius=blur_r))
        arr_gray = np.array(gray, dtype=np.int16)
        arr_bg   = np.array(bg,   dtype=np.int16)
        diff = np.clip(arr_gray - arr_bg + 128, 0, 255).astype(np.uint8)
        diff_img = Image.fromarray(diff, mode="L")
        diff_img = ImageEnhance.Contrast(diff_img).enhance(3.0)
        diff_img = diff_img.filter(ImageFilter.MedianFilter(size=3))

        bw_sub = diff_img.point(lambda p: 0 if p >= 128 else 255, '1')
        text = self._tess(bw_sub)
        if text:
            return text

        bw_sub_inv = diff_img.point(lambda p: 255 if p >= 128 else 0, '1')
        text = self._tess(bw_sub_inv)
        if text:
            return text

        # ── Pass 2: Sauvola local adaptive threshold (pure Pillow/numpy) ─
        arr = np.array(gray, dtype=np.float32)
        win = max(15, int(min(gray.width, gray.height) * 0.12))
        blur_pil = gray.filter(ImageFilter.GaussianBlur(radius=win // 2))
        blur_sq  = Image.fromarray(
                       np.clip(arr ** 2 / 255, 0, 255).astype(np.uint8), "L"
                   ).filter(ImageFilter.GaussianBlur(radius=win // 2))
        local_mean = np.array(blur_pil, dtype=np.float32)
        local_sq   = np.array(blur_sq,  dtype=np.float32) * 255  # undo /255 scaling
        local_std  = np.sqrt(np.maximum(local_sq - local_mean ** 2, 0))
        k, R = 0.2, 128.0
        thresh = local_mean * (1 + k * (local_std / R - 1))
        bw_s = ((arr >= thresh) * 255).astype(np.uint8)
        text = self._tess(Image.fromarray(bw_s, "L"))
        if text:
            return text
        text = self._tess(Image.fromarray(255 - bw_s, "L"))
        if text:
            return text

        # ── Pass 3: classic luminance threshold (slider value) ────────
        clean = ImageEnhance.Contrast(gray).enhance(2.5)
        clean = ImageEnhance.Sharpness(clean).enhance(2.0)
        clean = clean.filter(ImageFilter.MedianFilter(size=3))

        bw = clean.point(lambda p: 255 if p >= self._threshold else 0, '1')
        text = self._tess(bw)
        if text:
            return text

        # ── Pass 4: inverted luminance ────────────────────────────────
        bw_inv = ImageOps.invert(clean.convert("L")).point(
            lambda p: 255 if p >= self._threshold else 0, '1'
        )
        text = self._tess(bw_inv)
        if text:
            return text

        # ── Pass 5: raw greyscale (Tesseract auto-threshold) ──────────
        text = self._tess(gray)
        return text or "No text detected — try adjusting the threshold or selecting a cleaner region."

    @staticmethod
    def _tess(img: Image.Image) -> str:
        for psm in ("6", "3", "4", "11"):
            try:
                result = pytesseract.image_to_string(
                    img, config=f"--psm {psm} --oem 3"
                ).strip()
                if not result:
                    continue
                # Reject noise: require at least 40% of non-whitespace chars
                # to be alphanumeric. Pure punctuation/symbol output is garbage.
                non_ws = result.replace(" ", "").replace("\n", "")
                if not non_ws:
                    continue
                alnum_ratio = sum(c.isalnum() for c in non_ws) / len(non_ws)
                if alnum_ratio >= 0.4:
                    return result
            except Exception:
                pass
        return ""

    def _sync_current_text(self):
        """Keep _current_text in sync when the user edits the result box."""
        self._current_text = self.result_text.toPlainText()

    # ── Result handlers (main thread) ─────────────────────────────
    def _show_result(self, text: str, img):
        self._current_text = text
        self.result_text.setPlainText(text)

        # Show thumbnail of what was captured
        if img is not None:
            try:
                thumb_w = self.scan_area.width() - 8
                thumb_h = self.scan_area.height() - 8
                thumb = img.copy()
                thumb.thumbnail((thumb_w, thumb_h), Image.LANCZOS)
                data = thumb.tobytes("raw", "RGB")
                from PySide6.QtGui import QImage
                qimg = QImage(data, thumb.width, thumb.height, QImage.Format_RGB888)
                pix = QPixmap.fromImage(qimg)
                self.scan_area.setPixmap(pix)
            except Exception:
                self.scan_area.setText("📷 Preview unavailable")

        self._restore_window()

    def _capture_failed(self, msg: str):
        self.result_text.setPlainText(f"Error: {msg}")
        self.scan_area.setText("📷 Capture failed")
        self._restore_window()
        QMessageBox.warning(self, "Capture Failed", msg)

    # ── Toolbar actions ───────────────────────────────────────────
    def copy_text(self):
        if self._current_text:
            QApplication.clipboard().setText(self._current_text)

    def search_web(self):
        if self._current_text:
            import urllib.parse
            webbrowser.open(f"https://google.com/search?q={urllib.parse.quote(self._current_text[:300])}")

    def translate_text(self):
        if self._current_text:
            import urllib.parse
            webbrowser.open(
                f"https://translate.google.com/?sl=auto&tl=en&text={urllib.parse.quote(self._current_text[:500])}"
            )

    def clear_text(self):
        self.result_text.clear()
        self.scan_area.setText("📷 Preview of last captured region")
        self._current_text = ""


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MagicGrabWindow()
    window.show()
    sys.exit(app.exec())