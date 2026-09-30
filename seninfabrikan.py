import re
import sqlite3
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path

# Veritabanı Yolu (app.py ile tam aynı veritabanını kullanır)
DB_PATH = Path(__file__).resolve().parent / "data" / "filaments_v9.db"

# Engellenecek Ekstra Parçalar
EXCLUDE_TERMS = [
    "nozzle", "hotend", "extruder", "kurutucu", "dryer", "dry box", "vakum", "poşet",
    "reçine", "resin", "3d kalem", "spatula", "sprey", "rulman", "soğutucu", "fan",
    "step motor", "baskı tablası", "peı", "tabla", "sensör", "yazıcı", "printer",
    "somun", "vida", "kayış", "kasnak", "fişek", "ısıtıcı", "termistör", "yapıştırıcı", "naylonu"
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
    ("HIPS", r"\bHIPS\b"),
    ("Naylon/PA", r"\bNYLON\b|\bPA(?:6|12)?\b"),
    ("PC", r"\bPC\b"),
    ("PLA", r"\bPLA(?:\+|[- ]?PLUS|[- ]?PRO|[- ]?BASIC|[- ]?HS)?\b")
]

COLOR_MAPPING = [
    (r"mavi|blue|lacivert|navy|turkuaz|aqua|pus mavisi", "Mavi"),
    (r"pembe|pink|magenta|fuşya", "Pembe"),
    (r"beyaz|white|süt beyazı|kemik beyazı|soğuk beyaz", "Beyaz"),
    (r"kahverengi|brown|mocha|açık kahverengi|tuğla", "Kahverengi"),
    (r"mor|purple|violet|lila|very peri", "Mor"),
    (r"gümüş|silver", "Gümüş"),
    (r"yeşil|yesil|green|haki|mint|matcha|zeytin|çim|çam|yeşim", "Yeşil"),
    (r"sarı|sari|yellow|badem sarısı|hardal", "Sarı"),
    (r"turuncu|orange|mandalina|mercal", "Turuncu"),
    (r"siyah|black|antrasit", "Siyah"),
    (r"gri|grey|gray|beton grisi", "Gri"),
    (r"kırmızı|kirmizi|red|itfaiye kırmızısı", "Kırmızı"),
    (r"altın|gold|pirinç", "Altın"),
    (r"bej|beige|kayısı", "Bej")
]

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS products (
      id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT, name TEXT NOT NULL,
      brand TEXT, material TEXT, color TEXT, weight_g INTEGER, price REAL, old_price REAL,
      in_stock INTEGER DEFAULT 1, url TEXT NOT NULL, image TEXT, updated_at TEXT,
      UNIQUE(source, url)
    )""")
    conn.close()

def clean_price(price_str: str) -> float:
    if not price_str:
        return 0.0
    cleaned = re.sub(r"[^\d,.]", "", price_str)
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def detect_material(name: str) -> str:
    for label, pattern in MATERIAL_PATTERNS:
        if re.search(pattern, name, re.I):
            return label
    return "PLA"

def detect_color(name: str) -> str:
    name_lower = name.lower()
    for pattern, color in COLOR_MAPPING:
        if re.search(pattern, name_lower, re.I):
            return color
    return "Mavi"

def scrape_senin_fabrikan():
    print(">>> [Senin Fabrikan] Özel HTML Tarayıcısı Başlatıldı...", flush=True)
    
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept-Language': 'tr-TR,tr;q=0.9'
    }

    base_url = "https://seninfabrikan.com"
    target_urls = [
        f"{base_url}/uc-boyutlu-yazici/filament/",
        f"{base_url}/uc-boyutlu-yazici/filament/?sayfa=2",
        f"{base_url}/uc-boyutlu-yazici/filament/?sayfa=3",
        f"{base_url}/uc-boyutlu-yazici/filament/?sayfa=4"
    ]

    items = []

    for target_url in target_urls:
        try:
            res = session.get(target_url, headers=headers, timeout=12)
            if res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            
            # Ürün Kartlarını Yakala
            cards = soup.find_all(["div", "article"], class_=lambda c: c and ("product" in c.lower() or "item" in c.lower()))
            if not cards:
                cards = soup.select(".product-item, .product-card, .item, .showProductScheme")

            for card in cards:
                title_elem = card.find(["a", "h2", "h3", "div"], class_=lambda c: c and ("title" in c.lower() or "name" in c.lower() or "product" in c.lower()))
                link_elem = card.find("a", href=True)
                price_elem = card.find(["span", "div", "p"], class_=lambda c: c and ("price" in c.lower() or "fiyat" in c.lower()))
                img_elem = card.find("img")

                if not title_elem or not link_elem:
                    continue

                title = title_elem.get_text(strip=True)
                title_lower = title.lower()

                # Parça/aksesuar engelleme
                if any(term in title_lower for term in EXCLUDE_TERMS):
                    continue

                if "filament" not in title_lower and not any(m[0].lower() in title_lower for m in MATERIAL_PATTERNS):
                    continue

                prod_url = link_elem["href"]
                if not prod_url.startswith("http"):
                    prod_url = base_url + ("/" + prod_url.lstrip("/"))

                price = clean_price(price_elem.get_text(strip=True)) if price_elem else 0.0
                if price <= 0:
                    continue

                img_url = ""
                if img_elem:
                    img_url = img_elem.get("data-src") or img_elem.get("src") or ""
                    if img_url.startswith("//"):
                        img_url = "https:" + img_url
                    elif img_url and not img_url.startswith("http"):
                        img_url = base_url + ("/" + img_url.lstrip("/"))

                brand = "eSUN" if "esun" in title_lower else ("Filenta" if "filenta" in title_lower else "Senin Fabrikan")

                items.append({
                    "source": "Senin Fabrikan",
                    "external_id": prod_url.split("/")[-2] if len(prod_url.split("/")) > 2 else "",
                    "name": title,
                    "brand": brand,
                    "material": detect_material(title),
                    "color": detect_color(title),
                    "weight_g": 1000,
                    "price": price,
                    "old_price": None,
                    "in_stock": 1,
                    "url": prod_url,
                    "image": img_url
                })

        except Exception as e:
            print(f"Hata ({target_url}): {e}")

    # Veritabanına Kaydet
    init_db()
    saved_count = 0
    now = datetime.now().isoformat(timespec="seconds")

    conn = sqlite3.connect(DB_PATH)
    for item in items:
        try:
            conn.execute("""
                INSERT INTO products(source, external_id, name, brand, material, color, weight_g, price, old_price, in_stock, url, image, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, url) DO UPDATE SET 
                price=excluded.price, in_stock=excluded.in_stock, color=excluded.color, image=excluded.image, updated_at=excluded.updated_at
            """, (
                item["source"], item["external_id"], item["name"], item["brand"],
                item["material"], item["color"], item["weight_g"], item["price"],
                item["old_price"], item["in_stock"], item["url"], item["image"], now
            ))
            saved_count += 1
        except Exception:
            pass

    conn.commit()
    conn.close()
    
    print(f">>> [Senin Fabrikan] Tamamlandı! Toplam {saved_count} ürün veritabanına eklendi/güncellendi.", flush=True)

if __name__ == "__main__":
    scrape_senin_fabrikan()
