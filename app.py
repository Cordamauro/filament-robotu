from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import webbrowser
import concurrent.futures
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request
from waitress import serve


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def data_dir() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(os.getenv("LOCALAPPDATA", Path.home())) / "FilamentKarsilastirici"
    else:
        root = Path(__file__).resolve().parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


APP_DIR = Path(__file__).resolve().parent
DB_PATH = data_dir() / "filaments_v9.db"
SOURCES_PATH = APP_DIR / "sources.json" if (APP_DIR / "sources.json").exists() else resource_path("sources.json")
app = Flask(__name__, template_folder=str(resource_path("templates")), static_folder=str(resource_path("static")))
update_lock = threading.Lock()
update_state = {"running": False, "message": "Hazır", "updated_at": None}


@app.after_request
def disable_browser_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

COLOR_WORDS = ["siyah", "beyaz", "kırmızı", "mavi", "yeşil", "sarı", "turuncu", "mor", "pembe", "gri", "gümüş", "altın", "kahve", "bej", "ten", "mint", "turkuaz", "lila", "şeffaf", "doğal", "naturel", "natural", "bordo", "magenta", "cyan", "camgöbeği", "lacivert", "bronz", "bakır", "fuşya", "haki", "fildişi", "şampanya", "mercan", "somon", "kiremit", "çikolata", "antrasit", "violet", "indigo", "teal", "maroon", "scarlet", "ruby", "coral", "orange", "yellow", "green", "blue", "red", "white", "black", "grey", "gray", "brown", "purple", "pink", "gold", "silver", "aqua", "transparent", "clear"]
MATERIAL_PATTERNS = [
    ("PLA+", r"\bPLA\s*(?:\+|PLUS|PRO)\b"), ("PETG-CF", r"\bPETG[- ]?CF\b"),
    ("PET-CF", r"\bPET[- ]?CF\d*\b"), ("PA-CF", r"\bPA\d*[- ]?CF\d*\b|NYLON[- ]?CF\d*"),
    ("PAHT-CF", r"\bPAHT[- ]?CF\b"), ("PA-ESD", r"\bPA\d+[- ]?ESD\b"),
    ("PPS-CF", r"\bPPS[- ]?CF\d*\b"), ("PPS-GF", r"\bPPS[- ]?GF\d*\b"),
    ("PET-GF", r"\bPET[- ]?GF\d*\b"), ("PC-CF", r"\bPC[- ]?CF\d*\b"), ("PETG", r"\bPETG\b"), ("ABS", r"\bABS\b"),
    ("PLA-CF", r"\bPLA[- ]?CF\b|KARBON F[İI]BER.*PLA"), ("PLA-GF", r"\bPLA[- ]?GF\b"),
    ("ABS-CF", r"\bABS[- ]?CF\b"), ("ABS-GF", r"\bABS[- ]?GF\b"), ("ASA-CF", r"\bASA[- ]?CF\d*\b"),
    ("ASA", r"\bASA\b"), ("TPU", r"\bTPU\d*(?:[- ]?HF)?\b|ESNEK F[İI]LAMENT|EASY FLEX|FLEX FOAM"), ("PVA", r"\bPVA\b"),
    ("HIPS", r"\bHIPS\b"), ("Naylon/PA", r"\bNYLON\b|\bPA(?:6|12)?\b"),
    ("PC", r"\bPOL[İI]KARBONAT\b|\bPC\b"), ("Destek", r"SUPPORT|DESTEK F[İI]LAMENT"),
    ("PEBA", r"\bPEBA\b"), ("CoPE", r"\bCOPE\b"), ("PLA+", r"\bXPLA\b"),
    ("PLA", r"\bPLA\b|PANCHROMA|FILENTA.*PREMIUM|CR[- ]?WOOD|SILK\s+RAINBOW\s+FILAMENT"), ("PET", r"\bPET\b")
]
FILAMENT_TERMS = re.compile(r"filament|\bpla\b|\bpetg\b|\babs\b|\basa\b|\btpu\b|\bpva\b|\bhips\b|nylon|poliamid|\bpa[- ]?cf\b|\bpet[- ]?cf\b|support", re.I)
ACCESSORY_TERMS = re.compile(r"vakum|poşet|saklama|kurutucu|dryer|dry box|nem alıcı|çanta|sens[oö]r|algıla|tutucu|holder|buffer|cutter|kesici|feeding|besleme|connector|bağlantı|connection board|filament hub|huni|filament guide|filament tube|filament system|filament spools|filament makaraları|heater|kurutma|feeding assembly|kesici modül|vase|vazo|satin plate|build plate|baskı tablası|tabla|nozzle|hotend|extruder|rulman|bearing|motor|fan|anakart|board|kablo|kayış|kasnak|kaplin|somun|vida|spatula|yapıştırıcı|adhesive|reçine|resin|fep film|silikon çorap|termistör|güç kaynağı|ekran|kamera|tarayıcı|scanner|3d kalem|3d pen|yedek parça", re.I)
NON_FILAMENT_TERMS = re.compile(r"reçine|resin|nozzle|hotend|yazıcı|printer|rulman|poşet|saklama|kurutucu|dryer|granül|pellet|kalem|3d pen|satin plate|build plate|baskı tablası|tabla|extruder|motor|fan|anakart|kablo|kayış|kasnak|kaplin|somun|vida|spatula|yapıştırıcı|adhesive|fep film", re.I)
URL_MATERIAL_TERMS = re.compile(r"filament|(?:^|[-_/])(pla|petg|abs|asa|tpu|hips|pva|nylon|pa6|pa12|pet-cf|pa-cf|pc-cf|pps-cf|cope)(?:[-_/]|$)", re.I)
KNOWN_BRANDS = ["Marka Filament", "Beta Filament", "R3D Filament", "Revo Filament", "Bambu Lab", "Teknik Filament 3D", "Creality", "Flashforge", "Anycubic", "Smartfil", "Snapmaker", "Innovatefil", "Inslogic", "Makerment", "Polyture", "CCF3D", "Filetto", "Fimenta", "Solvix", "UZYSTORE", "BASF", "SAVA", "UZY", "CCF", "Sunlu", "eSUN", "Filamix", "Filenta", "Porima", "Fillamentum", "Polymaker", "Elegoo", "Microzey", "Fibromast", "Fiberon", "Panchroma", "PolyFlex", "PolyLite", "Kingroon", "Valment", "Soleyin", "Elas", "Apex", "ABG"]


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    seed_db = resource_path("data/filaments.db")
    if seed_db.exists() and seed_db.resolve() != DB_PATH.resolve():
        use_seed = not DB_PATH.exists()
        with sqlite3.connect(seed_db) as seed:
            seed_count = seed.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            seed_updated = seed.execute("SELECT COALESCE(MAX(updated_at), '') FROM products").fetchone()[0]
        if DB_PATH.exists():
            try:
                with sqlite3.connect(DB_PATH) as existing:
                    existing_count = existing.execute("SELECT COUNT(*) FROM products").fetchone()[0]
                    existing_updated = existing.execute("SELECT COALESCE(MAX(updated_at), '') FROM products").fetchone()[0]
                    use_seed = existing_count == 0 or seed_updated > existing_updated
            except sqlite3.Error:
                use_seed = True
        if use_seed:
            shutil.copy2(seed_db, DB_PATH)
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS products (
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT, name TEXT NOT NULL,
          brand TEXT, material TEXT, color TEXT, weight_g INTEGER, price REAL, old_price REAL,
          in_stock INTEGER DEFAULT 1, url TEXT NOT NULL, image TEXT, updated_at TEXT,
          UNIQUE(source, external_id, name, color)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_filters ON products(brand, material, color, source, price)")


def clean_text(value) -> str:
    text = re.sub(r"\s+", " ", BeautifulSoup(str(value or ""), "html.parser").get_text(" ")).strip()
    repairs = {"Krmz":"Kırmızı", "krmz":"kırmızı", "Sar":"Sarı", "sar":"sarı",
               "Yeil":"Yeşil", "yeil":"yeşil", "Gm":"Gümüş", "gm":"gümüş",
               "effaf":"Şeffaf", "ok":"Çok", "Ak":"Açık", "ak":"açık",
               "Fildii":"Fildişi", "Gk":"Gök", "Gbei":"Göbeği", "nci":"İnci",
               "FLAMENT":"FİLAMENT", "SYAH":"SİYAH", "GR":"GRİ", "Krmz":"Kırmızı"}
    for broken, correct in repairs.items():
        text = text.replace(broken, correct)
    return text


def price_number(value) -> float | None:
    if value is None:
        return None
    text = re.sub(r"[^\d,.]", "", str(value))
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def infer(name: str, source: str, vendor: str = "") -> dict:
    upper = name.upper()
    material = next((label for label, pattern in MATERIAL_PATTERNS if re.search(pattern, upper, re.I)), "Diğer")
    color = next((c.title() for c in COLOR_WORDS if c in name.casefold()), "Belirtilmemiş")
    vendor = clean_text(vendor)
    brand = next((b for b in KNOWN_BRANDS if b.casefold() in name.casefold()), "")
    # "Bambu" can be the material (for example "Ahşap Bambu"), so it is a
    # Bambu Lab brand signal only when the product name starts with Bambu.
    if not brand and re.match(r"^\s*bambu(?:\s|$)", name, re.I):
        brand = "Bambu Lab"
    if not brand and vendor and vendor.casefold() != source.casefold():
        brand = vendor
    if not brand:
        brand = name.split()[0] if name.split() else "Marka belirtilmemiş"
    brand = {"Beta Filament": "Beta", "R3D Filament": "R3D", "Revo Filament": "Revo",
             "PolyLite": "Polymaker", "PolyFlex": "Polymaker", "Panchroma": "Polymaker",
             "FLASHFORGE": "Flashforge", "SUNLU": "Sunlu"}.get(brand, brand)
    if source == "Microzey" and "bambu lab" not in name.casefold() and brand.casefold() == "bambu":
        brand = "Microzey"
    if source == "Microzey" and brand.casefold() in {"pla", "abs", "asa", "petg", "tpu", "10"}:
        brand = "Microzey"
    weight = re.search(r"(?:(\d+(?:[.,]\d+)?)\s*kg)|(?:(\d+)\s*g(?:r|ram)?)", name, re.I)
    weight_g = 1000
    if weight:
        weight_g = int(float((weight.group(1) or "0").replace(",", ".")) * 1000) if weight.group(1) else int(weight.group(2))
    return {"brand": brand, "material": material, "color": color, "weight_g": weight_g}


def variant_color(variant_title: str, product_title: str, inferred: str) -> str:
    if inferred != "Belirtilmemiş":
        return inferred
    option = re.sub(r"\s*/\s*\d+(?:[.,]\d+)?\s*(?:kg|g|gr)\b.*$", "", variant_title, flags=re.I).strip(" -/")
    option = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:kg|g|gr)\b", "", option, flags=re.I).strip(" -/")
    if option and option.casefold() not in {"default title", "standart", "standard"} and not re.search(r"filament|\b(?:pla|petg|abs|asa|tpu|pa|pc)\b", option, re.I):
        return option
    effects = [("ColorShift", "Renk Değiştiren"), ("Chameleon", "Renk Değiştiren"),
               ("Rainbow", "Renk Geçişli"), ("Gradient", "Renk Geçişli"), ("Gradyant", "Renk Geçişli"),
               ("Renk Geçiş", "Renk Geçişli"), ("Dört Renk", "Dört Renk"), ("Üç Renk", "Üç Renk"),
               ("İki Renk", "Çift Renk"), ("2 Renk", "Çift Renk"), ("Çift Renk", "Çift Renk"), ("Dual", "Çift Renk"),
               ("Transparan", "Şeffaf"), ("Clear", "Şeffaf"), ("Wood", "Ahşap"), ("Ahşap", "Ahşap"),
               ("Glow", "Fosforlu"), ("Luminous", "Fosforlu"), ("Fosfor", "Fosforlu"),
               ("Marble", "Mermer"), ("Mermer", "Mermer")]
    return next((label for word, label in effects if word.casefold() in product_title.casefold()), "")


COLOR_LABELS = {
    "black":"Siyah", "white":"Beyaz", "red":"Kırmızı", "blue":"Mavi", "green":"Yeşil",
    "yellow":"Sarı", "orange":"Turuncu", "purple":"Mor", "pink":"Pembe", "grey":"Gri",
    "gray":"Gri", "brown":"Kahverengi", "gold":"Altın", "silver":"Gümüş", "clear":"Şeffaf",
    "transparent":"Şeffaf", "natural":"Naturel", "aqua":"Aqua", "cyan":"Camgöbeği",
    "violet":"Violet", "teal":"Teal", "maroon":"Bordo", "scarlet":"Kırmızı", "ruby":"Yakut Kırmızısı",
    "coral":"Mercan", "indigo":"İndigo", "cocoa":"Kakao", "chocolate":"Çikolata",
    "siyah":"Siyah", "beyaz":"Beyaz", "kırmızı":"Kırmızı", "mavi":"Mavi", "yeşil":"Yeşil",
    "sarı":"Sarı", "turuncu":"Turuncu", "mor":"Mor", "pembe":"Pembe", "gri":"Gri",
    "kahverengi":"Kahverengi", "gümüş":"Gümüş", "altın":"Altın", "şeffaf":"Şeffaf",
    "naturel":"Naturel", "bordo":"Bordo", "lacivert":"Lacivert", "bakır":"Bakır",
    "bronz":"Bronz", "fuşya":"Fuşya", "magenta":"Magenta", "haki":"Haki", "bej":"Bej",
    "fildişi":"Fildişi", "turkuaz":"Turkuaz", "lila":"Lila", "lavanta":"Lavanta", "krem":"Krem", "mint":"Mint", "ten":"Ten",
    "somon":"Somon", "mercan":"Mercan", "kiremit":"Kiremit", "şampanya":"Şampanya",
    "kirmizi":"Kırmızı", "sari":"Sarı", "yesil":"Yeşil", "gumus":"Gümüş",
    "fildisi":"Fildişi", "gok mavisi":"Gök Mavisi", "acik mavi":"Açık Mavi",
    "acik pembe":"Açık Pembe", "acik yesil":"Açık Yeşil", "inci beyazi":"İnci Beyazı",
    "sutlu kahve":"Sütlü Kahve", "ates kirmizi":"Ateş Kırmızısı", "neon sari":"Neon Sarı",
    "neon turuncu":"Neon Turuncu", "neon yesil":"Neon Yeşil",
    "cosmic nebula":"Kozmik Bulutsu", "forest shadow":"Orman Gölgesi",
    "metallic glory":"Metalik Görkem", "sweet crisis":"Tatlı Kriz",
    "sakura sky":"Sakura Gökyüzü", "sakura gokyuzu":"Sakura Gökyüzü",
    "sunset oasis":"Gün Batımı Vahası", "turkish delight":"Lokum",
    "grape jelly":"Üzüm Jölesi", "very peri":"Very Peri", "very pery":"Very Peri",
    "seftali tozu":"Şeftali Tozu", "seftali tuyu":"Şeftali Tüyü", "fluorite":"Florit",
    "champagne":"Şampanya", "chompogne":"Şampanya", "cam gobegi":"Camgöbeği", "camgobegi":"Camgöbeği", "colrengi":"Çöl Rengi",
    "gece yarisi":"Gece Yarısı", "kavrulmus kestane":"Kavrulmuş Kestane",
    "mese":"Meşe", "seramik":"Seramik", "kil rengi":"Kil", "pirinc":"Pirinç",
    "acik kayisi":"Açık Kayısı", "zeytin":"Zeytin Yeşili", "rosehip":"Kuşburnu",
    "cilek kremasi":"Çilek Kreması", "kayisi":"Kayısı", "lavanta esintisi":"Lavanta Esintisi",
    "col rengi":"Çöl Rengi", "yesim":"Yeşim", "mandarin":"Mandalina",
    "menekse":"Menekşe", "fil disi":"Fildişi", "aurora":"Kutup Işığı",
    "comet":"Kuyruklu Yıldız", "jupiter":"Jüpiter", "meteor":"Meteor",
    "mercury":"Merkür", "neptune":"Neptün", "neptun":"Neptün",
    "starlit flow":"Yıldız Akışı", "tugce sekeri":"Tuğçe Şekeri",
    "granit":"Granit", "nebula":"Bulutsu", "carbon":"Siyah", "karbon":"Siyah", "antarist":"Antrasit", "antrasit":"Antrasit",
    "serenity":"Huzur Mavisi", "pearl":"İnci", "mandalina":"Mandalina",
    "coffee":"Kahve", "latte":"Latte", "skin":"Ten", "butter":"Tereyağı Sarısı",
    "clay":"Kil", "cream":"Krem", "fuchsia":"Fuşya", "ivory":"Fildişi",
    "lavender":"Lavanta", "ocean":"Okyanus Mavisi", "taupe":"Vizon",
    "mocha":"Mocha", "caramel":"Karamel", "ebony":"Siyah", "emerald":"Zümrüt Yeşili",
    "kraft":"Kraft Kahverengi", "olive":"Zeytin Yeşili", "peach":"Şeftali",
    "terracotta":"Terakota", "wine":"Şarap Kırmızısı", "copper":"Bakır",
    "rose":"Gül Kurusu", "beige":"Bej", "translucent":"Yarı Şeffaf",
    "mahogany":"Maun", "ochre":"Toprak Sarısı", "pumpkin":"Balkabağı Turuncusu",
    "tiffany":"Tiffany Mavisi", "navy":"Lacivert", "lime":"Limon Yeşili",
    "sand":"Kum", "khaki":"Haki", "charcoal":"Antrasit", "army":"Asker Yeşili",
    "stone":"Taş", "winter":"Kış Tonları", "lotus powder":"Lotus Pembe",
    "dusty rose":"Gül Kurusu", "eflatun":"Eflatun", "gül kurusu":"Gül Kurusu",
    "mürdüm":"Mürdüm", "nar çiçeği":"Nar Çiçeği", "açık nane":"Açık Nane"
}

# Ürün sayfasında renk alanı bulunmayan kayıtların ana görselleri incelenerek
# doğrulanmış ürün-görseli renk eşleştirmeleri. Görsel dosya adı ürün ailesini
# kararlı biçimde tanımladığı için sonraki fiyat güncellemelerinde de korunur.
VISUAL_COLOR_PATTERNS = [
    (r"anycubic-pla-filament-4lu-paket|cmyk-lithophane-bundle|color-trial-set", "Çok Renkli"),
    (r"pla-matte-magic|pla-matte-mystic|silk-magic|silk-mystic", "Renk Değiştiren"),
    (r"anycubic-refill|beta-peba|elas-petg-filament-3kg|elegoo-pla-cf|esun-abs|esun-petg|esun-twinkling|polylite-petg|polymax-sert-pc|porima-abs|porima-easy-flex|porima-premium-pla|porima-tough-pla|revo-hyper-speed-abs|sunlu-pla-filament-3kg|sunlu-high-speed-abs|sunlu-petg-filament-3kg|porima-ht-petg", "Siyah"),
    (r"creality-cr-silk|sunlu-lw-pla|sunlu-pc-filament", "Beyaz"),
    (r"fibromast-pla-cf|sunlu-pa6-gf|sunlu-easy-naylon", "Gri"),
    (r"innovatefil-abs-medikal", "Krem"),
    (r"porima-pastel-pla-filament-1kg", "Mavi"),
    (r"sunlu-galaxy-pla", "Yeşil"),
    (r"sunlu-pla-metal", "Bakır"),
    (r"valment-pla-premium-silk", "Altın"),
    (r"anycubic-pla-filament-73874657", "Beyaz"),
    (r"smartfil-flex", "Siyah"),
]


def normalized_color(name: str, current: str, image: str = "") -> str:
    evidence = f"{name} {urlsplit(image).path if image else ''}".casefold()
    evidence = re.sub(r"[-_]+", " ", evidence)
    effects = variant_color("", evidence, "Belirtilmemiş")
    if effects:
        return effects
    for word, label in COLOR_LABELS.items():
        if re.search(rf"(?<!\w){re.escape(word.casefold())}(?!\w)", evidence):
            return label
    candidate = re.sub(r"\s*/\s*(?:eco|\+?spool|makaralı|makarasız|\d+(?:[.,]\d+)?\s*(?:kg|g|gr|mm)).*$", "", current or "", flags=re.I).strip(" -/")
    if candidate and candidate not in {"Diğer", "Belirtilmemiş", "Ürün Görselindeki Renk"} and len(candidate) <= 35 and not re.search(r"filament|\b(?:pla|petg|abs|asa|tpu|pa|pc)\b|\d+\s*mm", candidate, re.I):
        return candidate
    suffix = name.rsplit(" - ", 1)[-1] if " - " in name else ""
    suffix = re.sub(r"\s*/.*$|\b\d+(?:[.,]\d+)?\s*(?:kg|g|gr|mm)\b.*$", "", suffix, flags=re.I).strip(" -/")
    if suffix and len(suffix) <= 35 and not re.search(r"filament|\b(?:pla|petg|abs|asa|tpu|pa|pc)\b", suffix, re.I):
        return suffix
    for pattern, label in VISUAL_COLOR_PATTERNS:
        if re.search(pattern, evidence, re.I):
            return label
    return ""


def validated_product(product: dict) -> dict | None:
    name = clean_text(product.get("name"))
    if not name or ACCESSORY_TERMS.search(name):
        return None
    meta = infer(name, product.get("source", ""), product.get("brand", ""))
    if meta["material"] == "Diğer":
        return None
    invalid_brands = {"pla", "petg", "abs", "asa", "tpu", "pa", "pc", "10", "marka belirtilmemiş"}
    if meta["brand"].casefold() in invalid_brands:
        return None
    color_evidence = f"{product.get('image', '') or ''} {product.get('url', '') or ''}"
    color = normalized_color(name, product.get("color", ""), color_evidence)
    if not color or color.casefold() in {"esun", "sunlu", "creality", "porima", "filamix", "polymaker"} or re.search(r"makarasız|yüksek kaliteli|baskı malzemesi|eşsiz|\b(?:esun|sunlu|creality|porima|filamix)\b.*\b(?:pla|petg|abs|asa|pa\d*)\b", color, re.I):
        color = "Diğer"
    product.update(meta, color=color)
    return product


def paged_url(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(limit="250", page=str(page))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def shopify_products(source: dict) -> list[dict]:
    products = []
    for page in range(1, 30):
        response = requests.get(paged_url(source["url"], page), timeout=30, headers={"User-Agent": "FilamentKarsilastirici/1.1 (personal price comparison)"})
        response.raise_for_status()
        batch = response.json().get("products", [])
        products.extend(batch)
        if len(batch) < 250:
            break
        time.sleep(.25)
    base = f"{urlsplit(source['url']).scheme}://{urlsplit(source['url']).netloc}"
    result = []
    for product in products:
        title = clean_text(product.get("title", ""))
        if "filament" not in title.casefold() or ACCESSORY_TERMS.search(title):
            continue
        variant_images = {}
        for product_image in product.get("images", []):
            for variant_id in product_image.get("variant_ids", []):
                variant_images[str(variant_id)] = product_image.get("src")
        for variant in product.get("variants", []):
            name = clean_text(product.get("title"))
            title = clean_text(variant.get("title"))
            full_name = name if title.lower() == "default title" else f"{name} - {title}"
            meta = infer(full_name, source["name"], product.get("vendor", ""))
            meta["color"] = variant_color(title, name, meta["color"])
            image = (variant.get("featured_image") or {}).get("src") or variant_images.get(str(variant.get("id")))
            if not image and product.get("images"): image = product["images"][0].get("src")
            result.append({**meta, "source": source["name"], "external_id": str(variant.get("id")), "name": full_name,
                "price": price_number(variant.get("price")), "old_price": price_number(variant.get("compare_at_price")),
                "in_stock": int(bool(variant.get("available", True))), "url": f"{base}/products/{product.get('handle')}", "image": image})
    return result


def walk_json(value):
    if isinstance(value, list):
        for item in value: yield from walk_json(item)
    elif isinstance(value, dict):
        if value.get("@type") == "Product": yield value
        for child in value.values(): yield from walk_json(child)


def xml_locs(content: bytes) -> list[str]:
    try:
        root = ET.fromstring(content)
        return [node.text.strip() for node in root.iter() if node.tag.endswith("loc") and node.text]
    except ET.ParseError:
        return []


def stocked_variant_options(soup: BeautifulSoup) -> list[tuple[str, str | None]]:
    """Return only stocked colors with the image explicitly assigned to that color."""
    images = {}
    for node in soup.select('[data-variant-value]'):
        raw = clean_text(node.get('data-variant-value'))
        image = node.find('img')
        src = image.get('src') or image.get('data-src') if image else None
        if raw and src:
            images.setdefault(raw.casefold(), src)
    result = []
    for option in soup.select('option[data-variant-value]'):
        raw = clean_text(option.get('data-variant-value') or option.get('value'))
        visible = clean_text(option.get_text(' '))
        if not raw or raw.casefold() in {'standart', 'standard', 'default title'}:
            continue
        if re.search(r'tükendi|stokta\s*yok|out\s*of\s*stock|\(', visible, re.I):
            continue
        color = normalized_color('', raw)
        if color and not any(existing == color for existing, _ in result):
            result.append((color, images.get(raw.casefold())))
    return result


def expand_html_colors(items: list[dict], soup: BeautifulSoup) -> list[dict]:
    options = stocked_variant_options(soup)
    if not options:
        return items
    expanded = []
    for item in items:
        for color, image in options:
            copy = dict(item)
            copy["color"] = color
            copy["name"] = f"{item['name']} - {color}"
            copy["external_id"] = f"{item['external_id']}:{color}"
            if image:
                copy["image"] = urljoin(item["url"], image)
            expanded.append(copy)
    return expanded


def product_from_html(source: dict, url: str) -> list[dict]:
    response = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 FilamentKarsilastirici/2.0"})
    response.raise_for_status()
    # Bazı Türk mağazaları UTF-8 içeriği yanlış/eksik HTTP karakter bilgisiyle gönderiyor.
    # HTML UTF-8 olarak çözülebiliyorsa başlık ve renklerin bozulmasını önlemek için onu kullan.
    try:
        response.content.decode("utf-8")
        response.encoding = "utf-8"
    except UnicodeDecodeError:
        response.encoding = response.apparent_encoding
    soup = BeautifulSoup(response.text, "html.parser")
    found = []
    payloads = []
    for script in soup.select('script[type="application/ld+json"]'):
        try: payloads.append(json.loads(script.string or script.get_text()))
        except (json.JSONDecodeError, TypeError): pass
    for payload in payloads:
        for product in walk_json(payload):
            name = clean_text(product.get("name"))
            description = clean_text(product.get("description"))
            evidence = f"{name} {description}"
            if not name or ACCESSORY_TERMS.search(name) or (NON_FILAMENT_TERMS.search(name) and "filament" not in name.casefold()):
                continue
            if "filament" not in evidence.casefold() and not (URL_MATERIAL_TERMS.search(urlsplit(url).path) and FILAMENT_TERMS.search(evidence)):
                continue
            offers = product.get("offers") or {}
            offers = offers if isinstance(offers, list) else [offers]
            valid_offers = [o for o in offers if isinstance(o, dict) and "OutOfStock" not in str(o.get("availability", ""))]
            if not valid_offers:
                continue
            offer = min(valid_offers, key=lambda o: price_number(o.get("price") or o.get("lowPrice")) or float("inf"))
            price = price_number(offer.get("price") or offer.get("lowPrice") or product.get("price"))
            if price is None:
                continue
            availability = str(offer.get("availability", ""))
            if not availability and re.search(r"stokta\s*yok|tükendi|out[ -]?of[ -]?stock", response.text, re.I):
                continue
            brand = product.get("brand") or ""
            if isinstance(brand, dict): brand = brand.get("name", "")
            meta = infer(name, source["name"], brand)
            if meta["color"] == "Belirtilmemiş":
                slug_tail = urlsplit(url).path.rstrip("/").split("/")[-1].replace("-", " ")
                meta["color"] = variant_color(slug_tail, name, meta["color"])
            image = product.get("image")
            if isinstance(image, list): image = image[0] if image else None
            if isinstance(image, dict): image = image.get("url") or image.get("contentUrl")
            found.append({**meta, "source": source["name"], "external_id": str(product.get("sku") or product.get("productID") or url),
                "name": name, "price": price, "old_price": price_number(offer.get("highPrice")), "in_stock": 1,
                "url": url, "image": urljoin(url, image) if image else None})
    if found:
        return expand_html_colors(found[:1], soup)

    # Bazı Türk e-ticaret altyapıları Product JSON-LD yerine HTML/meta alanları kullanır.
    og_title = soup.select_one('meta[property="og:title"]')
    name = clean_text(og_title.get("content") if og_title else (soup.title.get_text() if soup.title else ""))
    name = re.split(r"\s+[|–]\s+", name)[0].strip()
    page_text = clean_text(soup.get_text(" "))
    if not name or ACCESSORY_TERMS.search(name) or (NON_FILAMENT_TERMS.search(name) and "filament" not in name.casefold()):
        return []
    if "filament" not in name.casefold() and not (URL_MATERIAL_TERMS.search(urlsplit(url).path) and FILAMENT_TERMS.search(f"{name} {page_text[:4000]}")):
        return []

    price = None
    price_selectors = ['meta[itemprop="price"]', 'meta[property="product:price:amount"]',
                       '.sale-variant-price', '.product-price-group .sale-price', '.pd-price-block .sale-price',
                       '.PriceList', '[itemprop="price"]']
    for selector in price_selectors:
        node = soup.select_one(selector)
        if not node: continue
        value = node.get("content") or node.get("data-price") or node.get_text(" ", strip=True)
        match = re.search(r"(?:Fiyat\s*:)?\s*([\d.]+(?:,\d{1,2})?)\s*(?:₺|TL)?", value)
        if match:
            price = price_number(match.group(1))
            if price is not None: break
    if price is None:
        return []

    in_stock = False
    stock_match = re.search(r'"totalStockAmount"\s*:\s*([\d.]+)', response.text, re.I)
    if stock_match:
        in_stock = float(stock_match.group(1)) > 0
    elif re.search(r'(?:schema\.org/|"availability"\s*:\s*"[^"]*)InStock', response.text, re.I):
        in_stock = True
    else:
        cart = soup.select_one('.product-detail .btn-cart, .product-detail-container .btn-cart, .pd-detail .btn-cart, .btn-cart')
        if cart:
            cart_text = clean_text(cart.get_text(" ")).casefold()
            classes = " ".join(cart.get("class", [])).casefold()
            in_stock = "disabled" not in classes and "stokta yok" not in cart_text and "tükendi" not in cart_text
    if not in_stock:
        return []

    old_price_node = soup.select_one('.sale-list-price, .list-price')
    old_price = price_number(old_price_node.get_text(" ", strip=True)) if old_price_node else None
    brand_node = soup.select_one('[itemprop="brand"], meta[property="product:brand"]')
    brand = (brand_node.get("content") or brand_node.get_text(" ", strip=True)) if brand_node else ""
    meta = infer(name, source["name"], brand)
    if meta["color"] == "Belirtilmemiş":
        meta["color"] = variant_color(urlsplit(url).path.rstrip("/").split("/")[-1].replace("-", " "), name, meta["color"])
    image_node = soup.select_one('meta[property="og:image"], meta[itemprop="image"]')
    image = image_node.get("content") if image_node else None
    sku_node = soup.select_one('[itemprop="sku"], meta[property="product:retailer_item_id"]')
    external_id = (sku_node.get("content") or sku_node.get_text(" ", strip=True)) if sku_node else url
    return expand_html_colors([{**meta, "source": source["name"], "external_id": str(external_id), "name": name,
             "price": price, "old_price": old_price, "in_stock": 1, "url": url,
             "image": urljoin(url, image) if image else None}], soup)


FM_BASE = "https://www.filamentmarketim.com"


def fm_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
    })
    return session


def fm_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=35)
    response.raise_for_status()
    # Qukasoft zaman zaman yanlış HTTP charset'i gönderiyor; içerik UTF-8.
    return response.content.decode("utf-8", errors="replace")


def fm_json_after(text: str, marker: str):
    position = text.find(marker)
    if position < 0:
        return None
    position += len(marker)
    while position < len(text) and text[position].isspace():
        position += 1
    try:
        value, _ = json.JSONDecoder().raw_decode(text[position:])
        return value
    except (json.JSONDecodeError, TypeError):
        return None


def fm_brand(soup: BeautifulSoup, name: str) -> str:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text() or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for product in walk_json(payload):
            brand = product.get("brand") or ""
            if isinstance(brand, dict):
                brand = brand.get("name", "")
            if clean_text(brand):
                return clean_text(brand)
    return infer(name, "Filament Marketim")["brand"]


def fm_product(url: str) -> list[dict]:
    """FilamentMarketim ürün sayfasındaki yalnız stoklu gerçek varyantları okur."""
    html = fm_html(fm_session(), url)
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("[data-product-id]")
    product_id = str(root.get("data-product-id") or "") if root else url
    title_node = soup.select_one("h1")
    name = clean_text(title_node.get_text(" ") if title_node else "")
    if not name:
        og_title = soup.select_one('meta[property="og:title"]')
        name = clean_text(og_title.get("content") if og_title else "")
    if not name or ACCESSORY_TERMS.search(name) or "filament" not in name.casefold():
        return []

    data_match = re.search(r"const\s+PRODUCT_PAGE_DATA\s*=\s*\{", html)
    data_area = html[data_match.start():] if data_match else html
    price_match = re.search(r"\bprice\s*:\s*['\"]([^'\"]+)", data_area)
    price = price_number(price_match.group(1)) if price_match else None
    if price is None:
        price_node = soup.select_one(".sale-variant-price, .sale-price")
        price = price_number(price_node.get_text(" ", strip=True)) if price_node else None
    if price is None or price <= 0:
        return []

    old_node = soup.select_one(".list-price")
    old_price = price_number(old_node.get_text(" ", strip=True)) if old_node else None
    if old_price is not None and old_price <= price:
        old_price = None
    brand = fm_brand(soup, name)
    base_meta = infer(name, "Filament Marketim", brand)

    default_image_node = soup.select_one('meta[property="og:image"]')
    default_image = urljoin(url, default_image_node.get("content")) if default_image_node else None
    variant_images = {}
    for link in soup.select(".variant-box a[data-variant-value]"):
        raw = clean_text(link.get("data-variant-value"))
        image_node = link.select_one("img")
        image = (image_node.get("data-src") or image_node.get("src")) if image_node else None
        if raw and image:
            variant_images[raw.casefold()] = urljoin(url, image)

    variants = fm_json_after(data_area, "variants:")
    summary = variants.get("summary", {}) if isinstance(variants, dict) else {}
    result = []
    if isinstance(summary, dict) and summary:
        for variant_name, details in summary.items():
            if not isinstance(details, dict) or details.get("in_stock") is not True:
                continue
            try:
                quantity = float(details.get("quantity") or 0)
            except (TypeError, ValueError):
                quantity = 0
            if quantity <= 0:
                continue
            variant_name = clean_text(variant_name)
            color_part = re.split(r"\s*[|/]\s*", variant_name)[0].strip()
            variant_price = price
            listed = details.get("price_list")
            if isinstance(listed, (int, float)) and listed > 0:
                variant_price = float(listed)
            barcode = clean_text(details.get("barcode"))
            item_meta = infer(f"{name} - {variant_name}", "Filament Marketim", brand)
            item_meta["color"] = normalized_color("", color_part) or color_part
            result.append({
                **item_meta, "source": "Filament Marketim",
                "external_id": barcode or f"{product_id}:{variant_name}",
                "name": f"{name} - {variant_name}", "price": variant_price,
                "old_price": old_price if variant_price == price else None, "in_stock": 1,
                "url": f"{url}#varyant={quote(variant_name)}",
                "image": variant_images.get(variant_name.casefold()) or variant_images.get(color_part.casefold()) or default_image,
            })
        return result

    # Varyantsız bir ürün ancak yapılandırılmış veri açıkça stokta diyorsa eklenir.
    in_stock = False
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text() or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for product in walk_json(payload):
            offers = product.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if str(offers.get("availability") or "").casefold().endswith("instock"):
                in_stock = True
    if not in_stock:
        return []
    return [{
        **base_meta, "source": "Filament Marketim", "external_id": product_id,
        "name": name, "price": price, "old_price": old_price, "in_stock": 1,
        "url": url, "image": default_image,
    }]


def filamentmarketim_products(source: dict) -> list[dict]:
    """12+ kategori sayfasını ve her ana ürünün stoklu renklerini eksiksiz tarar."""
    session = fm_session()
    first_url = urljoin(source["url"], "/filament")
    first_html = fm_html(session, first_url)
    first_soup = BeautifulSoup(first_html, "html.parser")
    pages = [1]
    for link in first_soup.select("a.page-link[href]"):
        match = re.search(r"[?&]sayfa=(\d+)", str(link.get("href") or ""))
        if match:
            pages.append(int(match.group(1)))
    last_page = max(pages)

    urls, category_failures = set(), 0
    for page in range(1, last_page + 1):
        try:
            html = first_html if page == 1 else fm_html(session, f"{first_url}?sayfa={page}")
            cards = BeautifulSoup(html, "html.parser").select(".card-product")
            if not cards:
                category_failures += 1
                continue
            for card in cards:
                link = card.select_one("a.c-p-i-link[href]")
                if link:
                    product_url = urljoin(FM_BASE + "/", link.get("href"))
                    if urlsplit(product_url).netloc.removeprefix("www.") == "filamentmarketim.com":
                        urls.add(product_url.split("?", 1)[0].split("#", 1)[0])
            print(f"Filament Marketim kategori {page}/{last_page}: {len(cards)} kart", flush=True)
        except requests.RequestException as error:
            category_failures += 1
            print(f"Filament Marketim kategori {page} alınamadı: {error}", flush=True)
    if category_failures or not urls:
        raise RuntimeError("FilamentMarketim kategori taraması eksik; mevcut kayıtlar korunuyor")

    result, failures = [], 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        jobs = {pool.submit(fm_product, url): url for url in sorted(urls)}
        for job in concurrent.futures.as_completed(jobs):
            try:
                result.extend(job.result())
            except Exception as error:
                failures += 1
                print(f"Filament Marketim ürün alınamadı ({jobs[job]}): {error}", flush=True)
    if failures / len(urls) > 0.05:
        raise RuntimeError(f"FilamentMarketim ürün taraması eksik ({failures}/{len(urls)}); mevcut kayıtlar korunuyor")

    unique = {}
    for item in result:
        unique[(item["external_id"], item["name"])] = item
    result = list(unique.values())
    if not result:
        raise RuntimeError("FilamentMarketim stoklu ürün döndürmedi; mevcut kayıtlar korunuyor")
    print(f"Filament Marketim: {len(urls)} ana ürün, {len(result)} stoklu varyant", flush=True)
    return result


def sitemap_products(source: dict) -> list[dict]:
    base = source["url"].rstrip("/") + "/"
    base_host = urlsplit(base).netloc.removeprefix("www.")
    headers = {"User-Agent": "Mozilla/5.0 FilamentKarsilastirici/2.0"}
    first = xml_locs(requests.get(urljoin(base, "sitemap.xml"), timeout=25, headers=headers).content)
    sitemap_urls = [u for u in first if "sitemap" in u.casefold() or urlsplit(u).path.casefold().endswith(".xml")]
    page_urls = [] if sitemap_urls else first
    for sitemap_url in sitemap_urls:
        if not re.search(r"product|urun", sitemap_url, re.I):
            continue
        try: page_urls.extend(xml_locs(requests.get(sitemap_url, timeout=25, headers=headers).content))
        except requests.RequestException: continue
    if not page_urls:
        soup = BeautifulSoup(requests.get(base, timeout=25, headers=headers).text, "html.parser")
        page_urls = [urljoin(base, a.get("href")) for a in soup.select("a[href]")]
    candidates = []
    seen = set()
    for url in page_urls:
        host = urlsplit(url).netloc.removeprefix("www.")
        path = urlsplit(url).path
        if host != base_host or not URL_MATERIAL_TERMS.search(path) or re.search(r"\.(?:jpe?g|png|webp|gif|svg)$", path, re.I) or url in seen:
            continue
        seen.add(url); candidates.append(url)
    result = []
    def fetch(url):
        try: return product_from_html(source, url)
        except requests.RequestException: return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for items in pool.map(fetch, candidates): result.extend(items)
    return result


def jsonld_products(source: dict) -> list[dict]:
    response = requests.get(source["url"], timeout=25, headers={"User-Agent": "Mozilla/5.0 FilamentKarsilastirici/1.0"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    result, seen = [], set()
    for script in soup.select('script[type="application/ld+json"]'):
        try: payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError): continue
        for product in walk_json(payload):
            name = clean_text(product.get("name"))
            if not name or name in seen or "filament" not in name.casefold(): continue
            seen.add(name)
            offers = product.get("offers") or {}
            if isinstance(offers, list): offers = offers[0] if offers else {}
            brand = product.get("brand") or ""
            if isinstance(brand, dict): brand = brand.get("name", "")
            meta = infer(name, source["name"], brand)
            image = product.get("image")
            if isinstance(image, list): image = image[0] if image else None
            if isinstance(image, dict): image = image.get("url")
            result.append({**meta, "source": source["name"], "external_id": str(product.get("sku") or product.get("productID") or name),
                "name": name, "price": price_number(offers.get("price") or offers.get("lowPrice")), "old_price": None,
                "in_stock": int("OutOfStock" not in str(offers.get("availability", ""))),
                "url": urljoin(source["url"], offers.get("url") or product.get("url") or ""), "image": urljoin(source["url"], image) if image else None})
    return result


def save_products(items: list[dict]) -> int:
    items = [clean for item in items if (clean := validated_product(item)) is not None]
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        for p in items:
            conn.execute("""INSERT INTO products(source,external_id,name,brand,material,color,weight_g,price,old_price,in_stock,url,image,updated_at)
              VALUES(:source,:external_id,:name,:brand,:material,:color,:weight_g,:price,:old_price,:in_stock,:url,:image,:updated_at)
              ON CONFLICT(source,external_id,name,color) DO UPDATE SET brand=excluded.brand,material=excluded.material,
              weight_g=excluded.weight_g,price=excluded.price,old_price=excluded.old_price,in_stock=excluded.in_stock,
              url=excluded.url,image=excluded.image,updated_at=excluded.updated_at""", {**p, "updated_at": now})
    return len(items)


def update_all() -> None:
    if not update_lock.acquire(blocking=False): return
    update_state.update(running=True, message="Kaynaklar güncelleniyor…")
    total, errors = 0, []
    try:
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
        active_sources = [source["name"] for source in sources if source.get("enabled", True)]
        if active_sources:
            placeholders = ",".join("?" for _ in active_sources)
            with db() as conn:
                conn.execute(f"DELETE FROM products WHERE source NOT IN ({placeholders})", active_sources)
        for source in sources:
            if not source.get("enabled", True): continue
            try:
                if source["name"] == "Filament Marketim": items = filamentmarketim_products(source)
                elif source["kind"] == "shopify": items = shopify_products(source)
                elif source["kind"] == "sitemap": items = sitemap_products(source)
                else: items = jsonld_products(source)
                items = [p for p in items if p.get("price") is not None and p.get("in_stock")]
                if source["name"] == "Filament Marketim" and not items:
                    raise RuntimeError("FilamentMarketim boş sonuç döndürdü; mevcut kayıtlar korunuyor")
                with db() as conn:
                    conn.execute("DELETE FROM products WHERE source = ?", (source["name"],))
                total += save_products(items)
                print(f"{source['name']}: {len(items)} stokta filament", flush=True)
            except Exception as exc:
                errors.append(f"{source['name']}: {type(exc).__name__}")
                print(f"{source['name']}: {type(exc).__name__}", flush=True)
        stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
        update_state.update(message=f"{total} kayıt işlendi" + (f" · {len(errors)} kaynak erişilemedi" if errors else ""), updated_at=stamp)
    finally:
        update_state["running"] = False
        update_lock.release()


@app.get("/")
def index(): return render_template("index.html")


@app.get("/api/products")
def products():
    q = request.args.get("q", "").strip()
    filters, params = [], []
    for field in ("brand", "material", "color", "source"):
        value = request.args.get(field, "").strip()
        if value: filters.append(f"{field} = ?"); params.append(value)
    if q:
        filters.append("(name LIKE ? OR brand LIKE ? OR material LIKE ? OR color LIKE ?)")
        params += [f"%{q}%"] * 4
    filters.append("in_stock = 1")
    sql = "SELECT *, CASE WHEN weight_g > 0 THEN price * 1000.0 / weight_g END AS kg_price FROM products"
    if filters: sql += " WHERE " + " AND ".join(filters)
    order = {"price_asc":"price ASC", "price_desc":"price DESC", "new":"id DESC", "discount":"CASE WHEN old_price > price THEN (old_price-price)/old_price ELSE 0 END DESC, price ASC"}.get(request.args.get("sort"), "price ASC")
    sql += f" ORDER BY {order} LIMIT 5000"
    with db() as conn: rows = [dict(r) for r in conn.execute(sql, params)]
    return jsonify(rows)


@app.get("/api/filters")
def filters():
    selected_source = request.args.get("source", "").strip()
    where = " WHERE in_stock = 1"
    params = []
    if selected_source:
        where += " AND source = ?"
        params.append(selected_source)
    with db() as conn:
        values = {}
        for field in ("brand", "material", "color"):
            sql = f"SELECT DISTINCT {field} FROM products{where} AND {field} IS NOT NULL AND {field} != '' ORDER BY {field}"
            values[field] = [row[0] for row in conn.execute(sql, params)]
        values["source"] = [row[0] for row in conn.execute("SELECT DISTINCT source FROM products WHERE in_stock = 1 ORDER BY source")]
        values["count"] = conn.execute(f"SELECT COUNT(*) FROM products{where}", params).fetchone()[0]
    return jsonify(values)


@app.post("/api/update")
def update():
    threading.Thread(target=update_all, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/status")
def status(): return jsonify(update_state)


if __name__ == "__main__":
    init_db()
    with db() as conn:
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            threading.Thread(target=update_all, daemon=True).start()
    port = int(os.getenv("PORT", "9277"))
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    if not os.getenv("PORT"):
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:9277/")).start()
    serve(app, host=host, port=port, threads=6)
