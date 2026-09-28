from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request
from waitress import serve


def data_dir() -> Path:
    root = Path(__file__).resolve().parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


APP_DIR = Path(__file__).resolve().parent
DB_PATH = data_dir() / "filaments_v9.db"
SOURCES_PATH = APP_DIR / "sources.json"

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))
update_lock = threading.Lock()
update_state = {"running": False, "message": "Hazır", "updated_at": None}


@app.after_request
def disable_browser_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


COLOR_WORDS = [
    "siyah", "beyaz", "kırmızı", "mavi", "yeşil", "sarı", "turuncu", "mor", "pembe", 
    "gri", "gümüş", "altın", "kahve", "bej", "mint", "turkuaz", "lila", "şeffaf", 
    "doğal", "naturel", "natural", "bordo", "lacivert", "antrasit", "bronz", "bakır"
]

MATERIAL_PATTERNS = [
    ("PLA+", r"\bPLA\s*(?:\+|PLUS|PRO)\b"), ("PETG-CF", r"\bPETG[- ]?CF\b"),
    ("PET-CF", r"\bPET[- ]?CF\d*\b"), ("PA-CF", r"\bPA\d*[- ]?CF\d*|NYLON[- ]?CF\d*"),
    ("PETG", r"\bPETG\b"), ("ABS", r"\bABS\b"), ("PLA-CF", r"\bPLA[- ]?CF\b"), 
    ("ASA", r"\bASA\b"), ("TPU", r"\bTPU\d*(?:[- ]?HF)?\b"), ("PVA", r"\bPVA\b"),
    ("HIPS", r"\bHIPS\b"), ("Naylon/PA", r"\bNYLON\b|\bPA(?:6|12)?\b"),
    ("PC", r"\bPC\b"), ("PLA", r"\bPLA\b")
]

# KESİNLİKLE ENGELENEN AKSESUAR VE PARÇALAR (FİLTRE)
EXCLUDE_PATTERNS = re.compile(
    r"vakum|poşet|saklama|kurutucu|dryer|dry box|sens[oö]r|holder|nozzle|hotend|extruder|"
    r"rulman|fan|kablo|spatula|yapıştırıcı|reçine|resin|3d kalem|yedek parça|modül|kart|"
    r"sürücü|motor|kasnak|kayış|termistör|ısıtıcı|fişek|baskı tablası|peı|yay çeliği|"
    r"temizleme filamenti|temizleyici|sprey|tabla|somun|vida|yay|coupler|yazıcı|printer|hazne", re.I
)

# SADECE VE SADECE GERÇEK FİLAMENT KABUL ET
FILAMENT_MUST_HAVE = re.compile(r"filament|pla|petg|abs|tpu|asa|pva|hips|nylon|pa-cf|pet-cf", re.I)

KNOWN_BRANDS = [
    "Microzey", "Porima", "Filamix", "Beta Filament", "R3D", "Creality", "Anycubic", 
    "Sunlu", "eSUN", "Polymaker", "Elegoo", "Spectrum", "Formfutura", "Bambu Lab"
]


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS products (
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, external_id TEXT, name TEXT NOT NULL,
          brand TEXT, material TEXT, color TEXT, weight_g INTEGER, price REAL, old_price REAL,
          in_stock INTEGER DEFAULT 1, url TEXT NOT NULL, image TEXT, updated_at TEXT,
          UNIQUE(source, url)
        )""")


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(str(value or ""), "html.parser").get_text(" ")).strip()


def price_number(value) -> float | None:
    if value is None: return None
    text = re.sub(r"[^\d,.]", "", str(value))
    if not text: return None
    if "," in text and "." in text: text = text.replace(".", "").replace(",", ".")
    elif "," in text: text = text.replace(",", ".")
    try: return float(text)
    except ValueError: return None


def extract_weight(name: str) -> int:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|gr|g)\b", name, re.I)
    if match:
        val, unit = float(match.group(1)), match.group(2).lower()
        if unit == "kg": return int(val * 1000)
        return int(val)
    return 1000


def is_valid_filament(name: str) -> bool:
    """Aksesuar, reçine ve 3D yazıcı parçalarını kesinlikle eler."""
    if EXCLUDE_PATTERNS.search(name):
        return False
    if not FILAMENT_MUST_HAVE.search(name):
        return False
    return True


def infer(name: str, source: str) -> dict:
    upper = name.upper()
    material = next((label for label, pattern in MATERIAL_PATTERNS if re.search(pattern, upper, re.I)), "PLA")
    color = next((c.title() for c in COLOR_WORDS if c in name.casefold()), "Belirtilmemiş")
    brand = next((b for b in KNOWN_BRANDS if b.casefold() in name.casefold()), source)
    weight_g = extract_weight(name)
    return {"brand": brand, "material": material, "color": color, "weight_g": weight_g}


def save_products(items: list[dict]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    saved = 0
    with db() as conn:
        for p in items:
            name = clean_text(p.get("name"))
            if not name or not is_valid_filament(name):
                continue
                
            price = p.get("price")
            if not price or price <= 0:
                continue

            meta = infer(name, p.get("source", ""))
            
            try:
                conn.execute("""INSERT INTO products(source, external_id, name, brand, material, color, weight_g, price, old_price, in_stock, url, image, updated_at)
                  VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(source, url) DO UPDATE SET 
                  price=excluded.price, in_stock=excluded.in_stock, image=excluded.image, updated_at=excluded.updated_at""", 
                  (p.get("source"), str(p.get("external_id", "")), name, meta["brand"], meta["material"], meta["color"], meta["weight_g"], price, p.get("old_price"), p.get("in_stock", 1), p.get("url"), p.get("image"), now))
                saved += 1
            except Exception:
                pass
    return saved


def scrape_shopify(source: dict, headers: dict) -> list[dict]:
    items = []
    page = 1
    while page <= 25:  # Tüm sayfaları derinlemesine çek
        url = f"{source['url']}?page={page}&limit=250"
        try:
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code != 200: break
            products = res.json().get("products", [])
            if not products: break
            
            base_url = source['url'].replace('/products.json', '')
            for p in products:
                title = p.get('title', '')
                for v in p.get("variants", []):
                    v_title = v.get('title', '')
                    full_name = f"{title} {v_title if v_title != 'Default Title' else ''}"
                    if not is_valid_filament(full_name): continue
                    
                    items.append({
                        "source": source["name"],
                        "external_id": str(v.get("id")),
                        "name": full_name,
                        "price": price_number(v.get("price")),
                        "old_price": price_number(v.get("compare_at_price")),
                        "in_stock": 1 if v.get("available") else 0,
                        "url": f"{base_url}/products/{p.get('handle')}",
                        "image": (p.get("images") or [{}])[0].get("src")
                    })
            page += 1
        except Exception:
            break
    return items


def scrape_sitemap(source: dict, headers: dict) -> list[dict]:
    items = []
    sitemap_url = urljoin(source["url"], "/sitemap.xml")
    try:
        res = requests.get(sitemap_url, headers=headers, timeout=8)
        if res.status_code != 200: return items
        
        root = ET.fromstring(res.content)
        urls = [elem.text for elem in root.iter() if elem.tag.endswith('loc') and elem.text]
        
        # Filament içeren tüm ürün linklerini süz
        product_urls = [u for u in urls if 'filament' in u.lower()]
        
        for p_url in product_urls[:200]:
            try:
                p_res = requests.get(p_url, headers=headers, timeout=5)
                if p_res.status_code != 200: continue
                soup = BeautifulSoup(p_res.text, 'html.parser')
                
                title_tag = soup.find('h1')
                if not title_tag: continue
                title = clean_text(title_tag.text)
                
                if not is_valid_filament(title): continue
                
                price = None
                price_elem = soup.find(class_=re.compile(r'price|fiyat', re.I))
                if price_elem:
                    price = price_number(price_elem.text)
                
                if price:
                    items.append({
                        "source": source["name"],
                        "external_id": p_url,
                        "name": title,
                        "price": price,
                        "in_stock": 1,
                        "url": p_url,
                        "image": ""
                    })
            except Exception:
                continue
    except Exception:
        pass
    return items


def update_all() -> None:
    if not update_lock.acquire(blocking=False): return
    
    update_state.update(running=True, message="Güncelleniyor…")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    try:
        if SOURCES_PATH.exists():
            sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
            for source in sources:
                if not source.get("enabled", True): continue
                
                items = []
                kind = source.get("kind")
                
                if kind == "shopify":
                    items = scrape_shopify(source, headers)
                elif kind in ["sitemap", "jsonld"]:
                    items = scrape_sitemap(source, headers)
                
                save_products(items)
                
        update_state.update(message="Güncellendi", updated_at=datetime.now().strftime("%d.%m.%Y %H:%M"))
    finally:
        update_state["running"] = False
        update_lock.release()


@app.get("/")
def index():
    return render_template("index.html")


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
    
    sql = "SELECT *, CASE WHEN weight_g > 0 THEN price * 1000.0 / weight_g END AS kg_price FROM products"
    if filters: sql += " WHERE " + " AND ".join(filters)
    sql += " ORDER BY price ASC LIMIT 1000"
    
    with db() as conn: rows = [dict(r) for r in conn.execute(sql, params)]
    return jsonify(rows)


@app.get("/api/filters")
def filters():
    with db() as conn:
        values = {}
        for field in ("brand", "material", "color", "source"):
            sql = f"SELECT DISTINCT {field} FROM products WHERE {field} IS NOT NULL AND {field} != '' ORDER BY {field}"
            values[field] = [row[0] for row in conn.execute(sql)]
    return jsonify(values)


@app.post("/api/update")
def update():
    threading.Thread(target=update_all, daemon=True).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    serve(app, host="0.0.0.0", port=port, threads=4)
