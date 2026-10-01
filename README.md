# ✦ MagicGrab
Recreated the Microsoft Store app Text Grab - OCR and much more as a free version using AI-assisted development.
![UI magicgrab](/UI_magicgrab.png)

**Fixed & Accurate Screen OCR**

MagicGrab is a lightweight, always on top desktop tool that lets you quickly capture any region of your screen and extract text using advanced multi-pass OCR. Perfect for grabbing text from images, videos, PDFs, games, or any application that doesn’t allow easy copying.

---

## ✨ Features

- **Drag to select capture** — Fullscreen transparent overlay with live rubberband selection
- **High-accuracy OCR** — Multi-pass pipeline with background subtraction, Sauvola adaptive thresholding, and fallback methods
- **Live preview** — See exactly what was captured
- **Adjustable threshold** — Fine tune binarisation for difficult backgrounds
- **Quick actions** — Copy text, search Google, translate, or clear with one click
- **Modern dark UI** — Frameless, always on top window with purple accent theme

---

## 📖 User Stories

### As a student
- I want to quickly extract text from lecture slides or PDF pages that don’t allow copying, so that I can take notes faster.
- I want to capture text from educational videos or diagrams, so that I can search or translate key terms instantly.

### As a researcher / content creator
- I want to extract text from research papers, charts, or web pages with complex layouts, so that I can quote or cite material accurately.
- I want to translate foreign-language text from images or videos with one click, so that I can understand content faster.

### As a general user
- I want a simple drag to select interface, so that I don’t need to learn complex tools.
- I want the ability to adjust OCR sensitivity, so that text from low-contrast or busy backgrounds is still readable.
- I want quick actions (Copy / Search / Translate), so that I can act on the extracted text immediately.

---

## 🚀 Installation

### Requirements
- Python 3.9+
- Tesseract OCR engine

### 1. Install Tesseract

Download and install

**Windows**
```bash
      https://github.com/UB-Mannheim/tesseract/wiki
```
**macOS**
```bash
  brew install tesseract
```
**Linux**
```bash
  sudo apt update
  sudo apt install tesseract-ocr
```
### 2. Install Python dependencies
```bash
  pip install PySide6 Pillow pytesseract numpy
```
### 3. Run the app
```bash
  python magicgrab.py
```

---

## 🖱️ How to Use

1. Drag a rectangle over the text you want to extract.
2. Release the mouse — OCR runs automatically.
3. View the result in the text box and the captured region preview.
4. Adjust the Binarisation threshold slider if the text is hard to read.
5. Use the toolbar:
   * ⎘ Copy → copies text to clipboard
   * 🔎 Search → opens Google search
   * 🌐 Translate → opens Google Translate
   * ✕ Clear → resets the result

Press Esc during selection to cancel.
