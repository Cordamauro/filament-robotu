import re
import sqlite3
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path

# Veritabanı Yolu (app.py ile tam aynı veritabanını kullanır)
DB_PATH = Path(__file__).resolve().parent / "data" / "filaments_v9.db"

# Engellenecek Ekstra Aksesuar/Parça Terimleri
EXCLUDE_TERMS = [
    "nozzle", "hotend", "extruder", "kurutucu", "dryer", "dry box", "vakum", "poşet",
    "reçine", "resin", "3d kalem", "spatula", "sprey", "rulman", "soğutucu", "fan",
    "step motor", "baskı tablası", "peı", "tabla", "sensör", "yazıcı", "printer",
    "somun", "vida", "kayış", "kasnak", "fişek", "ısıtıcı", "termistör", "yapıştırıcı",
    "kalem", "temizleme", "borusu", "kesici", "modül", "kart", "sürücü", "numune"
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
    (r"mavi|blue|lacivert|navy|turkuaz|aqua", "Mavi"),
    (r"pembe|pink|magenta|fuşya", "Pembe"),
    (r"beyaz|white", "Beyaz"),
    (r"kahverengi|brown|mocha|tuğla", "Kahverengi"),
    (r"mor|purple|violet|lila", "Mor"),
    (r"gümüş|silver", "Gümüş"),
    (r"yeşil|yesil|green|haki|mint|matcha|zeytin", "Yeşil"),
    (r"sarı|sari|yellow|badem|hardal", "Sarı"),
    (r"turuncu|orange", "Turuncu"),
    (r"siyah|black|antrasit", "Siyah"),
    (r"gri|grey|gray", "Gri"),
    (r"kırmızı|kirmizi|red", "Kırmızı"),
    (r"altın|gold", "Altın"),
    (r"bej|beige", "Bej")
]

KNOWN_BRANDS = ["eSUN", "Creality", "Porima", "Microzey", "Sunlu", "Anycubic", "Elegoo", "Bambu Lab", "Robotistan"]

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

def detect_brand(name: str) -> str:
    for b in KNOWN_BRANDS:
        if b.lower() in name.lower():
            return b
    return "Robotistan"

def scrape_robotistan():
    print(">>> [Robotistan] Tarama Başladı...", flush=True)
    
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept-Language': 'tr-TR,tr;q=0.9'
    }

    base_url = "https://www.robotistan.com"
    items = []

    # Robotistan Filament Kategorisi Sayfaları
    for page in range(1, 10):
        target_url = f"{base_url}/3d-printer-filamentleri?sayfa={page}"
        try:
            res = session.get(target_url, headers=headers, timeout=12)
            if res.status_code != 200:
                break

            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select(".product-item, .showProductScheme, .productItem")
            if not cards:
                cards = soup.find_all("div", class_=lambda c: c and "product" in c.lower())

            if not cards:
                break

            for card in cards:
                title_elem = card.select_one(".product-title, .productName, h3, a.title")
                link_elem = card.find("a", href=True)
                price_elem = card.select_one(".product-price, .current-price, .price")
                img_elem = card.find("img")

                if not title_elem or not link_elem:
                    continue

                title = title_elem.get_text(strip=True)
                title_lower = title.lower()

                # Parça / aksesuar engelleme
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

                items.append({
                    "source": "Robotistan",
                    "external_id": prod_url.rstrip("/").split("/")[-1],
                    "name": title,
                    "brand": detect_brand(title),
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
            break

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
    
    print(f">>> [Robotistan] Tamamlandı! Toplam {saved_count} ürün veritabanına eklendi/güncellendi.", flush=True)

if __name__ == "__main__":
    scrape_robotistan()
