"""A small, dependency-free PDF writer for printable documents.

Uses the PDF standard Type 1 fonts (Helvetica family and Courier) with
WinAnsiEncoding, so no font files are embedded. Text outside that encoding is
transliterated where possible and otherwise replaced with "?"; the number of
replaced characters is reported so documents can disclose it.
"""

import datetime
import re
import unicodedata
import zlib

PAGE_SIZES = {"letter": (612.0, 792.0), "a4": (595.28, 841.89)}

# Glyph widths (1/1000 em) for WinAnsi codes 32..126, from the Adobe core font metrics.
_HELVETICA = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]
_HELVETICA_BOLD = [
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
]
# A few WinAnsi codes above 126 that documents commonly use.
_EXTRA = {0x80: 556, 0x85: 1000, 0x91: 222, 0x92: 222, 0x93: 333, 0x94: 333, 0x95: 350,
          0x96: 556, 0x97: 1000, 0x99: 1000, 0xA0: 278, 0xA7: 556, 0xA9: 737, 0xAE: 737, 0xB0: 400, 0xB7: 278}

FONTS = {
    "F1": ("Helvetica", _HELVETICA),
    "F2": ("Helvetica-Bold", _HELVETICA_BOLD),
    "F3": ("Helvetica-Oblique", _HELVETICA),
    "F4": ("Courier", None),
}

_REPLACEMENTS = {
    "‐": "-", "‑": "-", "‒": "-", "−": "-", "→": "->", "←": "<-",
    "≤": "<=", "≥": ">=", "✓": "v", "✗": "x", "☐": "[ ]", "☑": "[x]",
    " ": " ", " ": " ", " ": " ",
}


def encode(text, stats=None):
    """Encode text to cp1252 bytes, transliterating or replacing unsupported characters."""
    out = bytearray()
    for ch in text:
        if ch in "\r\t":
            ch = " "
        if ch == "\n":
            continue
        ch = _REPLACEMENTS.get(ch, ch)
        try:
            out += ch.encode("cp1252")
            continue
        except UnicodeEncodeError:
            pass
        decomposed = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        try:
            if not decomposed:
                raise UnicodeEncodeError("cp1252", ch, 0, 1, "no transliteration")
            out += decomposed.encode("cp1252")
        except UnicodeEncodeError:
            out += b"?"
            if stats is not None:
                stats["replaced"].add(ch)
    return bytes(out)


def _char_width(code, table):
    if table is None:
        return 600
    if 32 <= code <= 126:
        return table[code - 32]
    if code in _EXTRA:
        return _EXTRA[code]
    if code < 32:
        return 0
    return 667 if 0xC0 <= code <= 0xDE else 556


def text_width(data, font, size):
    table = FONTS[font][1]
    return sum(_char_width(b, table) for b in data) * size / 1000.0


def _escape(data):
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def wrap(text, font, size, max_width, stats=None):
    """Wrap text into encoded lines that fit max_width points. Honors explicit newlines."""
    lines = []
    for para in (text or "").split("\n"):
        words = encode(para, stats).split(b" ")
        current = b""
        for word in words:
            candidate = word if not current else current + b" " + word
            if text_width(candidate, font, size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            while text_width(word, font, size) > max_width and len(word) > 1:
                cut = len(word)
                while cut > 1 and text_width(word[:cut], font, size) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        lines.append(current)
    return lines


LANG_TAG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8}){0,4}$")


class Document(object):
    def __init__(self, title, page_size="letter", margin=54.0, header=None, footer=None, creation=None, lang="en"):
        if not LANG_TAG.match(lang or ""):
            raise ValueError("language must be a BCP 47 tag such as en or en-US")
        self.title = title
        self.lang = lang
        self.width, self.height = PAGE_SIZES[page_size]
        self.margin = margin
        self.header = header
        self.footer = footer
        self.creation = creation or datetime.datetime.now(datetime.timezone.utc)
        self.stats = {"replaced": set()}
        self.pages = []
        self.y = 0.0
        self._new_page()

    # -- page management --------------------------------------------------
    @property
    def content_width(self):
        return self.width - 2 * self.margin

    @property
    def _bottom(self):
        return self.margin + 30

    def _new_page(self):
        self.pages.append([])
        self.y = self.height - self.margin
        if self.header and len(self.pages) > 1:
            self._text_line(encode(self.header, self.stats), "F3", 8, self.margin, self.y, gray=0.45)
            self.y -= 18

    def _ops(self):
        return self.pages[-1]

    def _ensure(self, needed):
        if self.y - needed < self._bottom:
            self._new_page()

    def page_break(self):
        self._new_page()

    # -- primitives ---------------------------------------------------------
    def _text_line(self, data, font, size, x, y, gray=0.0):
        self._ops().append(("%.3f g BT /%s %.2f Tf %.2f %.2f Td (" % (gray, font, size, x, y)).encode("ascii")
                           + _escape(data) + b") Tj ET 0 g")

    def _rect(self, x, y, w, h, fill_gray=None, stroke=True, line_width=0.6):
        ops = "%.2f w " % line_width
        if fill_gray is not None:
            ops += "%.3f g %.2f %.2f %.2f %.2f re f 0 g " % (fill_gray, x, y, w, h)
        if stroke:
            ops += "%.2f %.2f %.2f %.2f re S" % (x, y, w, h)
        self._ops().append(ops.encode("ascii"))

    def _hline(self, x1, x2, y, gray=0.6, line_width=0.5):
        self._ops().append(("%.3f G %.2f w %.2f %.2f m %.2f %.2f l S 0 G" % (gray, line_width, x1, y, x2, y)).encode("ascii"))

    # -- flowing content ----------------------------------------------------
    def paragraph(self, text, font="F1", size=10.0, indent=0.0, leading=None, gray=0.0, space_after=6.0):
        leading = leading or size * 1.35
        for line in wrap(text, font, size, self.content_width - indent, self.stats):
            self._ensure(leading)
            self.y -= leading
            self._text_line(line, font, size, self.margin + indent, self.y + leading * 0.25, gray)
        self.y -= space_after

    def heading(self, text, level=1):
        size = {0: 20.0, 1: 15.0, 2: 12.5, 3: 11.0}[level]
        before = {0: 0.0, 1: 14.0, 2: 10.0, 3: 6.0}[level]
        lines = wrap(text, "F2", size, self.content_width, self.stats)
        self._ensure(before + size * 1.4 * min(len(lines), 2) + 40)
        self.y -= before
        self.paragraph(text, font="F2", size=size, space_after=4.0)
        if level <= 1:
            self._hline(self.margin, self.width - self.margin, self.y + 2, gray=0.3, line_width=0.8)
            self.y -= 6

    def bullets(self, items, size=10.0, numbered=False):
        for i, item in enumerate(items, 1):
            marker = ("%d." % i) if numbered else "•"
            leading = size * 1.35
            lines = wrap(item, "F1", size, self.content_width - 18, self.stats)
            self._ensure(leading)
            self.y -= leading
            self._text_line(encode(marker, self.stats), "F1", size, self.margin + 2, self.y + leading * 0.25)
            for j, line in enumerate(lines):
                if j:
                    self._ensure(leading)
                    self.y -= leading
                self._text_line(line, "F1", size, self.margin + 18, self.y + leading * 0.25)
            self.y -= 2
        self.y -= 4

    def code(self, text, size=8.5):
        leading = size * 1.3
        lines = wrap(text, "F4", size, self.content_width - 12, self.stats)
        i = 0
        while i < len(lines):
            self._ensure(leading + 8)
            room = int((self.y - self._bottom - 8) // leading)
            chunk = lines[i:i + max(room, 1)]
            height = leading * len(chunk) + 8
            self._rect(self.margin, self.y - height, self.content_width, height, fill_gray=0.95, stroke=False)
            y = self.y - 4
            for line in chunk:
                y -= leading
                self._text_line(line, "F4", size, self.margin + 6, y + leading * 0.25)
            self.y -= height
            i += len(chunk)
        self.y -= 6

    def key_values(self, rows, size=9.5, label_width=150.0):
        leading = size * 1.35
        for label, value in rows:
            vlines = wrap(str(value), "F1", size, self.content_width - label_width - 6, self.stats)
            llines = wrap(str(label), "F2", size, label_width - 6, self.stats)
            n = max(len(vlines), len(llines))
            # Keep short rows together; let very long values flow across pages line by line.
            self._ensure(leading * min(n, 6) + 4)
            for j in range(n):
                self._ensure(leading)
                self.y -= leading
                if j < len(llines):
                    self._text_line(llines[j], "F2", size, self.margin, self.y + leading * 0.25)
                if j < len(vlines):
                    self._text_line(vlines[j], "F1", size, self.margin + label_width, self.y + leading * 0.25)
            self.y -= 3
            self._hline(self.margin, self.width - self.margin, self.y + 1, gray=0.85, line_width=0.4)
        self.y -= 6

    def table(self, headers, rows, widths, size=8.5):
        """A simple grid table. widths are fractions of the content width."""
        leading = size * 1.3
        cols = [w * self.content_width for w in widths]

        def draw_row(cells, font, fill):
            wrapped = [wrap(str(c), font, size, cols[i] - 6, self.stats) for i, c in enumerate(cells)]
            n = max(len(w) for w in wrapped)
            height = n * leading + 6
            if self.y - height < self._bottom:
                self._new_page()
                if font != "F2":
                    draw_row(headers, "F2", 0.88)
            x = self.margin
            for i, w in enumerate(cols):
                self._rect(x, self.y - height, w, height, fill_gray=fill, stroke=True, line_width=0.4)
                x += w
            x = self.margin
            for i, lines in enumerate(wrapped):
                y = self.y - 3
                for line in lines:
                    y -= leading
                    self._text_line(line, font, size, x + 3, y + leading * 0.25)
                x += cols[i]
            self.y -= height

        draw_row(headers, "F2", 0.88)
        for r in rows:
            draw_row(r, "F1", None)
        self.y -= 8

    def boxed(self, text, size=9.5, title=None, fill_gray=0.93):
        leading = size * 1.35
        lines = wrap(text, "F1", size, self.content_width - 16, self.stats)
        title_lines = wrap(title, "F2", size, self.content_width - 16, self.stats) if title else []
        height = (len(lines) + len(title_lines)) * leading + 14
        self._ensure(height + 4)
        self._rect(self.margin, self.y - height, self.content_width, height, fill_gray=fill_gray, stroke=True, line_width=0.8)
        y = self.y - 7
        for line in title_lines:
            y -= leading
            self._text_line(line, "F2", size, self.margin + 8, y + leading * 0.25)
        for line in lines:
            y -= leading
            self._text_line(line, "F1", size, self.margin + 8, y + leading * 0.25)
        self.y -= height + 10

    def notes_box(self, label, lines=6, line_gap=20.0):
        height = lines * line_gap + 22
        self._ensure(height + 16)
        self.y -= 10
        self._text_line(encode(label, self.stats), "F2", 10, self.margin, self.y - 12)
        top = self.y - 18
        self._rect(self.margin, top - lines * line_gap - 4, self.content_width, lines * line_gap + 4, stroke=True, line_width=0.6)
        for i in range(1, lines):
            self._hline(self.margin + 6, self.width - self.margin - 6, top - i * line_gap, gray=0.75, line_width=0.4)
        self.y = top - lines * line_gap - 14

    def spacer(self, h=8.0):
        self.y -= h

    # -- output -------------------------------------------------------------
    def _footer_ops(self, index, total):
        ops = []
        y = self.margin - 18
        label = "Page %d of %d" % (index, total)
        data = encode(label)
        x = self.width - self.margin - text_width(data, "F1", 8)
        ops.append(("0.4 g BT /F1 8 Tf %.2f %.2f Td (" % (x, y)).encode("ascii") + _escape(data) + b") Tj ET 0 g")
        if self.footer:
            avail = self.content_width - text_width(data, "F1", 8) - 12
            line = wrap(self.footer, "F3", 7.5, avail)[0]
            ops.append(("0.4 g BT /F3 7.5 Tf %.2f %.2f Td (" % (self.margin, y)).encode("ascii") + _escape(line) + b") Tj ET 0 g")
        return ops

    def to_bytes(self):
        total = len(self.pages)
        objects = []  # list of bytes, object number = index + 1

        def add(body):
            objects.append(body)
            return len(objects)

        catalog_no = add(None)
        pages_no = add(None)
        font_nos = {}
        for key, (base, _) in FONTS.items():
            font_nos[key] = add(("<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % base).encode("ascii"))
        font_res = " ".join("/%s %d 0 R" % (k, n) for k, n in font_nos.items())
        page_nos = []
        for i, ops in enumerate(self.pages, 1):
            stream = b"\n".join(ops + self._footer_ops(i, total))
            data = zlib.compress(stream, 9)
            content_no = add(("<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data)).encode("ascii") + data + b"\nendstream")
            page_no = add(("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << %s >> >> /Contents %d 0 R >>"
                           % (pages_no, self.width, self.height, font_res, content_no)).encode("ascii"))
            page_nos.append(page_no)
        # Declare the document language and show the title in the viewer's window bar (assistive
        # technology announces both). The PDF is not tagged; the Markdown copy is the accessible version.
        objects[catalog_no - 1] = ("<< /Type /Catalog /Pages %d 0 R /Lang (%s) /ViewerPreferences << /DisplayDocTitle true >> >>"
                                   % (pages_no, self.lang)).encode("ascii")
        objects[pages_no - 1] = ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % n for n in page_nos), total)).encode("ascii")
        stamp = self.creation.strftime("D:%Y%m%d%H%M%SZ")
        # Document metadata uses PDFDocEncoding, not WinAnsi; UTF-16BE with a BOM represents any title exactly.
        title_hex = ("FEFF" + self.title.encode("utf-16-be").hex().upper()).encode("ascii")
        info_no = add(b"<< /Title <" + title_hex + b"> /Producer (Fairtide) /Creator (Fairtide) /CreationDate (" + stamp.encode("ascii") + b") >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, 1):
            offsets.append(len(out))
            out += ("%d 0 obj\n" % i).encode("ascii") + body + b"\nendobj\n"
        xref = len(out)
        out += ("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii")
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += ("%010d 00000 n \n" % off).encode("ascii")
        out += ("trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog_no, info_no, xref)).encode("ascii")
        return bytes(out)
