import sqlite3
import requests
import re
from datetime import datetime
from pathlib import Path

# Veritabanı Yolunu Belirle (app.py ile aynı veritabanını kullanır)
DB_PATH = Path(__file__).resolve().parent / "data" / "filaments_v9.db"

# Engellenen Ürün Terimleri (Yedek Parça vb.)
EXCLUDE_TERMS = [
    "nozzle", "hotend", "extruder", "kurutucu", "dryer", "dry box", "vakum", "poşet",
    "reçine", "resin", "3d kalem", "spatula", "sprey", "rulman", "soğutucu", "fan",
    "step motor", "baskı tablası", "peı", "tabla", "sensör", "yazıcı", "printer",
    "somun", "vida", "kayış", "kasnak", "fişek", "ısıtıcı", "termistör", "yapıştırıcı"
]

# Materyal Yakalama
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
    (r"mavi|blue|lacivert|navy|turkuaz", "Mavi"),
    (r"pembe|pink|magenta|fuşya", "Pembe"),
    (r"beyaz|white", "Beyaz"),
    (r"kahverengi|brown", "Kahverengi"),
    (r"mor|purple", "Mor"),
    (r"gümüş|silver", "Gümüş"),
    (r"yeşil|yesil|green|haki|mint", "Yeşil"),
    (r"sarı|sari|yellow", "Sarı"),
    (r"turuncu|orange", "Turuncu"),
    (r"siyah|black", "Siyah"),
    (r"gri|grey|gray|antrasit", "Gri"),
    (r"kırmızı|kirmizi|red", "Kırmızı"),
    (r"altın|gold", "Altın")
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

def is_valid_filament(name: str) -> bool:
    name_lower = name.lower()
    if any(term in name_lower for term in EXCLUDE_TERMS):
        return False
    if "filament" in name_lower or "filaman" in name_lower:
        return True
    for _, pattern in MATERIAL_PATTERNS:
        if re.search(pattern, name, re.I):
            return True
    return False

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
    print(">>> [Senin Fabrikan] Tarama Başladı...", flush=True)
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*'
    })

    base_url = "https://seninfabrikan.com"
    page = 1
    items = []

    while page <= 10:
        req_url = f"{base_url}/products.json?page={page}&limit=250"
        try:
            res = session.get(req_url, timeout=12)
            if res.status_code != 200:
                break
            
            products = res.json().get("products", [])
            if not products:
                break

            for p in products:
                title = p.get('title', '')
                images = p.get("images") or []
                default_img = images[0].get("src") if images else ""

                for v in p.get("variants", []):
                    if not v.get("available"):
                        continue

                    v_title = v.get('title', '')
                    full_name = f"{title} {v_title if v_title != 'Default Title' else ''}"

                    if not is_valid_filament(full_name):
                        continue

                    price = float(v.get("price") or 0)
                    if price <= 0:
                        continue

                    v_img_src = v.get("featured_image", {}).get("src") if v.get("featured_image") else default_img
                    if v_img_src and v_img_src.startswith("//"):
                        v_img_src = "https:" + v_img_src

                    items.append({
                        "source": "Senin Fabrikan",
                        "external_id": str(v.get("id")),
                        "name": full_name,
                        "brand": "Senin Fabrikan",
                        "material": detect_material(full_name),
                        "color": detect_color(full_name),
                        "weight_g": 1000,
                        "price": price,
                        "old_price": float(v.get("compare_at_price") or 0) if v.get("compare_at_price") else None,
                        "in_stock": 1,
                        "url": f"{base_url}/products/{p.get('handle')}?variant={v.get('id')}",
                        "image": v_img_src
                    })
            page += 1
        except Exception as e:
            print(f"Hata: {e}")
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
    
    print(f">>> [Senin Fabrikan] Tamamlandı! Toplam {saved_count} ürün veritabanına eklendi/güncellendi.", flush=True)

if __name__ == "__main__":
    scrape_senin_fabrikan()
