from __future__ import annotations

import re
import sqlite3
import requests
from pathlib import Path
from urllib.parse import urljoin
from datetime import datetime
from bs4 import BeautifulSoup

def data_dir() -> Path:
    root = Path(__file__).resolve().parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root

DB_PATH = data_dir() / "filaments_v9.db"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7'
}

# Varsayılan USD Kuru (Robitshop USD Fiyatlarını TL'ye Çevirmek İçin)
USD_RATE = 35.0

EXCLUDE_TERMS = [
    "tutucu", "holder", "destek", "ayak", "kolu", "stent", "aparat", "model", "yedek parça",
    "hub", "splitter", "buffer", "feeder", "cutter", "tube", "replacement", "ptfe", "kesici", "borusu", "bıçak", "makas",
    "nozzle", "hotend", "extruder", "kurutucu", "dryer", "dry box", "vakum", "poşet", "reçine", "resin", "3d kalem", 
    "spatula", "sprey", "rulman", "soğutucu", "fan", "step motor", "baskı tablası", "peı", "tabla", "sensör", "sensor", 
    "yazıcı", "printer", "somun", "vida", "kayış", "kasnak", "fişek", "ısıtıcı", "termistör", "yapıştırıcı", "modül", "coupler"
]

COLOR_MAPPING = [
    (r"mavi|blue|lacivert|navy|bebek\s*mavisi|buz|ice|sky|gök|turkuaz|teal|cyan", "Mavi"),
    (r"pembe|pink|magenta|fuşya", "Pembe"),
    (r"beyaz|white|ral\s*9003", "Beyaz"),
    (r"kahverengi|kahve|tuğla|mocha|brown", "Kahverengi"),
    (r"mor|purple|violet|lila|lavanta", "Mor"),
    (r"gümüş|silver", "Gümüş"),
    (r"bakır|copper", "Bakır"),
    (r"şeffaf|seffaf|transparent|clear|natural|naturel", "Şeffaf"),
    (r"wood|ahşap|ahsap", "Wood"),
    (r"ten|somon|salmon|skin|flesh|latte", "Ten"),
    (r"yeşil|yesil|green|haki|mint|nane|fıstık|lime|çim|grass", "Yeşil"),
    (r"sarı|sari|yellow|limon|mustard|hardal", "Sarı"),
    (r"turuncu|orange|amber|kehribar", "Turuncu"),
    (r"siyah|black|dark|gece|antrasit", "Siyah"),
    (r"gri|grey|gray|stone|kil|granit|titanium|titanyum", "Gri"),
    (r"kırmızı|kirmizi|red|ruby|sakura|mercan", "Kırmızı"),
    (r"altın|altin|gold|bronz|bronze", "Altın")
]

MATERIAL_PATTERNS = [
    ("PETG-CF", r"\bPETG[- ]?CF\b"),
    ("PET-CF", r"\bPET[- ]?CF\d*\b"),
    ("PA-CF", r"\bPA\d*[- ]?CF\d*|NYLON[- ]?CF\d*"),
    ("PETG", r"\bPETG\b"),
    ("ABS", r"\bABS\b"),
    ("PLA-CF", r"\bPLA[- ]?CF\b"), 
    ("ASA", r"\bASA\b"),
    ("TPU", r"\bTPU\d*(?:[- ]?HF)?\b"),
    ("PVA", r"\bPVA\b"),
    ("PC", r"\bPC\b"),
    ("PLA", r"\bPLA(?:\+|[- ]?PLUS|[- ]?PRO|[- ]?BASIC|[- ]?HS)?\b")
]

KNOWN_BRANDS = ["eSUN", "Esun", "Creality", "Filenta", "Kingroon", "Sunlu", "Anycubic", "Microzey", "Porima"]

def clean_text(value) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(str(value or ""), "html.parser").get_text(" ")).strip()

def parse_price(text: str) -> float | None:
    if not text:
        return None
    
    is_usd = "usd" in text.lower() or "$" in text
    
    # Metindeki sayısal değeri yakala
    match = re.search(r"(\d+[\d\.,]*)", text)
    if not match:
        return None
        
    digits = match.group(1)
    if "," in digits and "." in digits:
        digits = digits.replace(".", "").replace(",", ".")
    elif "," in digits:
        digits = digits.replace(",", ".")
        
    try:
        val = float(digits)
        if val <= 0:
            return None
            
        # USD ise KDV ekleyip TL'ye çevir
        if is_usd:
            val = val * 1.20 * USD_RATE
            
        return round(val, 2)
    except ValueError:
        return None

def is_robitshop_filament(name: str) -> bool:
    name_lower = name.lower()
    if any(term in name_lower for term in EXCLUDE_TERMS):
        return False
    if "filament" in name_lower:
        return True
    materials = ["pla", "petg", "abs", "tpu", "asa", "pva", "nylon", "carbon", "pc", "hips", "basic"]
    for mat in materials:
        if re.search(rf"\b{mat}\b", name_lower):
            return True
    return False

def infer(name: str) -> dict:
    upper = name.upper()
    material = next((label for label, pattern in MATERIAL_PATTERNS if re.search(pattern, upper, re.I)), "PLA")
    color = "Mavi"
    for pattern, normalized_color in COLOR_MAPPING:
        if re.search(pattern, name.lower(), re.I):
            color = normalized_color
            break
    brand = next((b for b in KNOWN_BRANDS if b.casefold() in name.casefold()), "Robitshop")
    return {"brand": brand, "material": material, "color": color, "weight_g": 1000}

def scrape_robitshop() -> list[dict]:
    items = []
    # GERÇEK VE DOĞRULANMIŞ ROBITSHOP URL HEDEFLERİ
    targets = [
        "https://www.robitshop.com/kategori/filament",
        "https://www.robitshop.com/kategori/pla-filamentler",
        "https://www.robitshop.com/kategori/pet-g-filament",
        "https://www.robitshop.com/kategori/abs-filamentler",
        "https://www.robitshop.com/kategori/tpu-flex-filament",
        "https://www.robitshop.com/kategori/ozel-filamentler",
        "https://www.robitshop.com/marka/esun",        # DOĞRU MARKA LINKI
        "https://www.robitshop.com/marka/creality"    # DOĞRU MARKA LINKI
    ]

    print(">>> ROBITSHOP TAM VERİ TARAMASI BAŞLADI <<<", flush=True)

    for base_url in targets:
        for page in range(1, 15):
            target_url = base_url if page == 1 else f"{base_url}?sayfa={page}"

            try:
                res = requests.get(target_url, headers=HEADERS, timeout=12)
                if res.status_code != 200:
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                product_elements = soup.find_all("div", class_=re.compile(r"product-item|showcase|ItemOrj", re.I))

                if not product_elements:
                    break

                added_in_page = 0
                for elem in product_elements:
                    elem_html = str(elem).lower()
                    if "stokta yok" in elem_html or "tukendi" in elem_html or "tükendi" in elem_html:
                        continue

                    title_elem = elem.find(["a", "div", "span"], class_=re.compile(r"product-title|productName|title", re.I)) or elem.find("a")
                    price_elem = elem.find(["span", "div"], class_=re.compile(r"price|fiyat|discountPrice", re.I))
                    img_elem = elem.find("img")

                    if title_elem and price_elem:
                        name = clean_text(title_elem.get_text(strip=True))
                        if not is_robitshop_filament(name):
                            continue

                        price = parse_price(price_elem.get_text(strip=True))
                        if not price:
                            continue

                        href = title_elem.get("href") or (elem.find("a").get("href") if elem.find("a") else "")
                        prod_url = urljoin("https://www.robitshop.com", href) if href else target_url

                        img_src = ""
                        if img_elem:
                            img_src = img_elem.get("data-src") or img_elem.get("src") or img_elem.get("data-original") or ""
                            if img_src and not img_src.startswith("http"):
                                img_src = urljoin("https://www.robitshop.com", img_src)

                        if not any(p["url"] == prod_url for p in items):
                            items.append({
                                "source": "Robitshop",
                                "external_id": str(hash(prod_url)),
                                "name": name,
                                "price": price,
                                "url": prod_url,
                                "image": img_src
                            })
                            added_in_page += 1

                if added_in_page == 0 and page > 1:
                    break

            except Exception as e:
                print(f"Hata ({target_url}): {e}", flush=True)
                break

    print(f">>> ROBITSHOP TARAMASI TAMAMLANDI: TOPLAM {len(items)} ADET FİLAMAN BULUNDU. <<<", flush=True)
    return items

def save_to_db(items: list[dict]):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT, name TEXT NOT NULL,
        brand TEXT, material TEXT, color TEXT, weight_g INTEGER, price REAL, old_price REAL,
        in_stock INTEGER DEFAULT 1, url TEXT NOT NULL, image TEXT, updated_at TEXT,
        UNIQUE(source, url)
    )""")
    
    now = datetime.now().isoformat(timespec="seconds")
    saved = 0
    for p in items:
        meta = infer(p["name"])
        try:
            cursor.execute("""INSERT INTO products(source, external_id, name, brand, material, color, weight_g, price, old_price, in_stock, url, image, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, url) DO UPDATE SET 
                price=excluded.price, in_stock=excluded.in_stock, color=excluded.color, image=excluded.image, updated_at=excluded.updated_at""", 
                (p["source"], p["external_id"], p["name"], meta["brand"], meta["material"], meta["color"], meta["weight_g"], p["price"], None, 1, p["url"], p["image"], now))
            saved += 1
        except Exception:
            pass
    conn.commit()
    conn.close()
    print(f">>> {saved} ADET ROBITSHOP ÜRÜNÜ VERİTABANINA BAŞARIYLA KAYDEDİLDİ. <<<", flush=True)

if __name__ == "__main__":
    products = scrape_robitshop()
    if products:
        save_to_db(products)
