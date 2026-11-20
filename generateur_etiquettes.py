#!/usr/bin/env python3
"""Générateur d'étiquettes pour tableau électrique (PDF noir et blanc).

Usage : python3 generateur_etiquettes.py [config.json] [-o sortie.pdf] [--liste-icones]
Aucune dépendance externe (Python 3.8+).
"""
import argparse
import json
import os
import struct
import sys
import unicodedata
import zlib

from icones import ICONS, Pen

MM = 72.0 / 25.4
PAGES = {"A4": (210, 297), "A5": (148, 210), "A3": (297, 420), "LETTER": (215.9, 279.4)}

# ---------------------------------------------------------------- polices

_W_REG = (
    [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278]
    + [556] * 10
    + [278, 278, 584, 584, 584, 556, 1015]
    + [667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
       667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]
    + [278, 278, 278, 469, 556, 333]
    + [556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
       556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500]
    + [334, 260, 334, 584]
)
_W_BOLD = (
    [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278]
    + [556] * 10
    + [333, 333, 584, 584, 584, 611, 975]
    + [722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
       667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611]
    + [333, 278, 333, 584, 556, 333]
    + [556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
       611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500]
    + [389, 280, 389, 584]
)


def text_width(s, size, bold=False):
    table = _W_BOLD if bold else _W_REG
    total = 0
    for ch in s:
        base = unicodedata.normalize("NFD", ch)[0]
        o = ord(base)
        total += table[o - 32] if 32 <= o <= 126 else 556
    return total * size / 1000.0


def pdf_str(s):
    b = s.encode("cp1252", errors="replace")
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


# ---------------------------------------------------------------- PNG

def load_png_gray(path):
    """Décode un PNG (non entrelacé) en niveaux de gris 8 bits, alpha sur fond blanc."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("%s n'est pas un PNG (seul le PNG est supporté)" % path)
    pos, idat, plte = 8, b"", None
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            plte = body
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    if interlace:
        raise ValueError("%s : PNG entrelacé non supporté" % path)
    chans = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp_bits = chans * depth
    bpp = max(1, bpp_bits // 8)
    stride = (w * bpp_bits + 7) // 8
    raw = zlib.decompress(idat)
    prev = bytearray(stride)
    rows = []
    p = 0
    for _ in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        prev = line
        rows.append(line)

    def samples(line):
        if depth == 8:
            return line
        if depth == 16:
            return line[0::2]
        out = bytearray()
        mask = (1 << depth) - 1
        for byte in line:
            for s in range(8 - depth, -1, -depth):
                out.append((byte >> s) & mask)
        return out

    out = bytearray()
    for line in rows:
        s = samples(line)
        for x in range(w):
            if ctype == 0:
                v = s[x] * 255 // ((1 << depth) - 1) if depth < 8 else s[x]
            elif ctype == 3:
                i = s[x] * 3
                r, g, b = plte[i], plte[i + 1], plte[i + 2]
                v = (r * 299 + g * 587 + b * 114) // 1000
            elif ctype == 2:
                r, g, b = s[3 * x], s[3 * x + 1], s[3 * x + 2]
                v = (r * 299 + g * 587 + b * 114) // 1000
            elif ctype == 4:
                v = (s[2 * x] * s[2 * x + 1] + 255 * (255 - s[2 * x + 1])) // 255
            else:
                r, g, b, a = s[4 * x:4 * x + 4]
                lum = (r * 299 + g * 587 + b * 114) // 1000
                v = (lum * a + 255 * (255 - a)) // 255
            out.append(v)
    return w, h, bytes(out)


# ---------------------------------------------------------------- PDF

class Pdf:
    def __init__(self):
        self.objs = []          # bytes de chaque objet
        self.pages = []         # (contenu, ressources images)
        self.images = {}        # clé -> (nom, numéro d'objet)

    def add(self, body):
        self.objs.append(body)
        return len(self.objs)

    def image(self, key, loader):
        if key not in self.images:
            w, h, px = loader()
            comp = zlib.compress(px)
            head = ("<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                    "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                    "/Length %d >>\nstream\n" % (w, h, len(comp))).encode()
            num = self.add(head + comp + b"\nstream-end")
            self.images[key] = ("Im%d" % (len(self.images) + 1), num, w, h)
        return self.images[key]

    def save(self, path, page_w, page_h, pages):
        objs = [None] * 4   # 1 catalogue, 2 pages, 3 Helvetica, 4 Helvetica-Bold
        base = []
        for n, f in ((3, "Helvetica"), (4, "Helvetica-Bold")):
            base.append(("<< /Type /Font /Subtype /Type1 /BaseFont /%s "
                         "/Encoding /WinAnsiEncoding >>" % f).encode())
        # on reconstruit la numérotation : images déjà ajoutées commencent à 1
        shift = 4
        img_objs = [o.replace(b"\nstream-end", b"\nendstream") for o in self.objs]
        img_num = {k: v[1] + shift for k, v in self.images.items()}
        body = []
        kids = []
        next_num = shift + len(img_objs) + 1
        page_objs = []
        for content, used in pages:
            c = zlib.compress(content.encode("latin-1"))
            cnum = next_num
            pnum = next_num + 1
            next_num += 2
            xo = " ".join("/%s %d 0 R" % (self.images[k][0], img_num[k]) for k in used)
            page_objs.append(("<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(c)).encode()
                             + c + b"\nendstream")
            page_objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] "
                              "/Contents %d 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> "
                              "/XObject << %s >> >> >>" % (page_w, page_h, cnum, xo)).encode())
            kids.append("%d 0 R" % pnum)
        all_objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(kids), len(kids))).encode(),
        ] + base + img_objs + page_objs
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offs = []
        for i, o in enumerate(all_objs, 1):
            offs.append(len(out))
            out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(all_objs) + 1)
        for o in offs:
            out += b"%010d 00000 n \n" % o
        out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                % (len(all_objs) + 1, xref))
        with open(path, "wb") as f:
            f.write(out)


# ---------------------------------------------------------------- mise en page

def wrap(text, size, maxw, bold):
    lines, cur = [], ""
    for word in text.split():
        cand = (cur + " " + word).strip()
        if text_width(cand, size, bold) <= maxw or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def fit_text(text, maxw, maxh, size_max, size_min, bold, leading=1.15):
    size = size_max
    while True:
        lines = wrap(text, size, maxw, bold)
        ok = (len(lines) * size * leading <= maxh and
              all(text_width(l, size, bold) <= maxw for l in lines))
        if ok or size <= size_min:
            return size, lines
        size = max(size_min, round(size - 0.25, 2))


class Renderer:
    def __init__(self, cfg, base_dir, pdf):
        self.cfg, self.base, self.pdf = cfg, base_dir, pdf
        self.lab = cfg.get("etiquette", {})
        self.used = set()
        self.ops = []

    def image_for(self, name):
        if name in ICONS:
            return ("icon", name)
        path = name if os.path.isabs(name) else os.path.join(self.base, name)
        if not os.path.exists(path):
            raise SystemExit("Image introuvable : %s (icônes intégrées : %s)"
                             % (name, ", ".join(sorted(ICONS))))
        return ("png", path)

    def draw_image(self, spec, x, y, w, h):
        kind, val = spec
        if kind == "icon":
            s = min(w, h)
            lw = max(0.5, s / 22.0)
            self.ops.append("q %.3f 0 0 %.3f %.3f %.3f cm %.3f w 1 J 1 j 0 G 0 g" % (
                s, s, x + (w - s) / 2, y + (h - s) / 2, lw / s))
            pen = Pen()
            ICONS[val](pen)
            self.ops += pen.ops
            self.ops.append("Q")
        else:
            thr = self.lab.get("seuil_noir_blanc")

            def loader():
                iw, ih, px = load_png_gray(val)
                if thr is not None:
                    px = bytes(255 if v >= thr else 0 for v in px)
                return iw, ih, px
            name, _, iw, ih = self.pdf.image((val, thr), loader)
            self.used.add((val, thr))
            r = min(w / iw, h / ih)
            dw, dh = iw * r, ih * r
            self.ops.append("q %.3f 0 0 %.3f %.3f %.3f cm /%s Do Q" % (
                dw, dh, x + (w - dw) / 2, y + (h - dh) / 2, name))

    def text_line(self, s, size, bold, cx, y):
        w = text_width(s, size, bold)
        self.ops.append("BT /%s %.2f Tf %.2f %.2f Td (%s) Tj ET" % (
            "F2" if bold else "F1", size, cx - w / 2, y, pdf_str(s).decode("latin-1")))

    def label(self, item, x, y, w, h):
        lab = self.lab
        pad = lab.get("marge_interne_mm", 1.0) * MM
        if lab.get("bordure", True):
            self.ops.append("q %.2f w 0 G %.2f %.2f %.2f %.2f re S Q" % (
                lab.get("epaisseur_bordure_pt", 0.5), x, y, w, h))
        text = item.get("texte", "")
        sub = item.get("sous_texte", "")
        img = item.get("image")
        bold = item.get("gras", lab.get("gras", True))
        iw, ih = w - 2 * pad, h - 2 * pad
        top = y + h - pad
        # zone image
        if img:
            ratio = item.get("ratio_image", lab.get("ratio_image", 0.5))
            box = ih * ratio if (text or sub) else ih
            self.draw_image(self.image_for(img), x + pad, top - box, iw, box)
            top -= box
        remaining = top - (y + pad)
        cx = x + w / 2
        smax = lab.get("taille_texte_max_pt", 9)
        smin = lab.get("taille_texte_min_pt", 4)
        sub_size = 0
        if sub:
            sub_size = min(lab.get("taille_sous_texte_pt", 6.5), smax)
            while sub_size > smin and text_width(sub, sub_size, False) > iw:
                sub_size -= 0.25
            remaining -= sub_size * 1.2
        if text and remaining > 0:
            size, lines = fit_text(text, iw, remaining, smax, smin, bold)
            block = len(lines) * size * 1.15
            ty = top - (remaining - block) / 2 - size * 0.9 if not img else top - size * 0.95
            if not img:
                ty = top - (remaining - block) / 2 - size * 0.9
            for ln in lines:
                self.text_line(ln, size, bold, cx, ty)
                ty -= size * 1.15
        if sub:
            self.text_line(sub, sub_size, False, cx, y + pad + 0.5)


def build(cfg, base_dir, out):
    pg = cfg.get("page", {})
    pw, ph = PAGES.get(str(pg.get("format", "A4")).upper(), PAGES["A4"])
    if pg.get("orientation", "portrait") == "paysage":
        pw, ph = ph, pw
    margin = pg.get("marge_mm", 10)
    lab = cfg.get("etiquette", {})
    mod_w = lab.get("largeur_module_mm", 17.5)
    height = lab.get("hauteur_mm", 25)
    row_gap = pg.get("espace_entre_rangees_mm", 8)
    show_titles = pg.get("afficher_titres", True)
    usable_w = pw - 2 * margin

    pdf = Pdf()
    rend = Renderer(cfg, base_dir, pdf)
    pages = []
    y = ph - margin
    page_ops = []
    used_all = set()

    def flush():
        nonlocal rend
        if rend.ops:
            pages.append(("\n".join(rend.ops), set(rend.used)))
        rend.ops, rend.used = [], set()

    strips = []
    for row in cfg.get("rangees", []):
        title = row.get("titre", "")
        cur, cur_w = [], 0.0
        for item in row.get("etiquettes", []):
            w = item.get("modules", 1) * mod_w
            if cur and cur_w + w > usable_w + 0.01:
                strips.append((title, cur))
                title = (title + " (suite)") if title else ""
                cur, cur_w = [], 0.0
                print("Avertissement : rangée '%s' plus large que la page, coupée."
                      % row.get("titre", ""), file=sys.stderr)
            cur.append(item)
            cur_w += w
        if cur:
            strips.append((title, cur))

    for title, items in strips:
        need = height + (4 if (show_titles and title) else 0) + row_gap
        if y - need * MM < margin * MM and rend.ops:
            flush()
            y = ph - margin
        if not rend.ops and not pages and y == ph - margin:
            pass
        y_mm = y
        if show_titles and title:
            rend.ops.append("BT /F2 7 Tf %.2f %.2f Td (%s) Tj ET" % (
                margin * MM, (y_mm - 3) * MM, pdf_str(title).decode("latin-1")))
            y_mm -= 4
        x = margin
        for item in items:
            w = item.get("modules", 1) * mod_w
            rend.label(item, x * MM, (y_mm - height) * MM, w * MM, height * MM)
            x += w
        y = y_mm - height - row_gap
    flush()
    if not pages:
        raise SystemExit("Aucune étiquette à générer (clé 'rangees' vide ?)")
    pdf.save(out, pw * MM, ph * MM, pages)
    return len(pages), sum(len(i) for _, i in strips)


def main():
    ap = argparse.ArgumentParser(description="Étiquettes pour tableau électrique")
    ap.add_argument("config", nargs="?", default="config.json")
    ap.add_argument("-o", "--sortie", default=None)
    ap.add_argument("--liste-icones", action="store_true")
    ap.add_argument("--apercu-icones", action="store_true",
                    help="génère une planche PDF de toutes les icônes intégrées")
    a = ap.parse_args()
    if a.liste_icones:
        print("\n".join(sorted(ICONS)))
        return
    if a.apercu_icones:
        noms = sorted(ICONS)
        cfg = {"etiquette": {"hauteur_mm": 28, "taille_texte_max_pt": 6, "ratio_image": 0.65},
               "page": {"afficher_titres": False, "espace_entre_rangees_mm": 4},
               "rangees": [{"etiquettes": [{"texte": n, "image": n} for n in noms[i:i + 10]]}
                           for i in range(0, len(noms), 10)]}
        out = a.sortie or "apercu_icones.pdf"
        build(cfg, os.getcwd(), out)
        print("%s généré : %d icônes" % (out, len(noms)))
        return
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    out = a.sortie or "mon_tableau.pdf"
    pages, n = build(cfg, os.path.dirname(os.path.abspath(a.config)), out)
    print("%s généré : %d étiquettes, %d page(s)" % (out, n, pages))


if __name__ == "__main__":
    main()
