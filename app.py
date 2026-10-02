from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

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


COLOR_MAPPING = [
    (r"(?=.*stone)(?=.*mercan)", "Kırmızı"),
    (r"star|simli|glitter|sparkle", "Mavi"),
    (r"mavi|blue|lacivert|navy|bebek\s*mavisi|bebek\s*mavi|turkuaz|aqua", "Mavi"),
    (r"pembe|pink|magenta|fuchsia|fuşya|fusya", "Pembe"),
    (r"beyaz|white|ral\s*9003", "Beyaz"),
    (r"kahverengi|kahve|tuğla|mocha|brown|chocolate", "Kahverengi"),
    (r"mor|purple|violet|lila", "Mor"),
    (r"gümüş|silver", "Gümüş"),
    (r"army", "Army"),
    (r"bakır|copper", "Bakır"),
    (r"natural|naturel|şeffaf|seffaf|transparent|clear", "Şeffaf"),
    (r"wood", "Wood"),
    (r"ten|somon|salmon|skin|flesh|latte", "Ten"),
    (r"su\s*yeşili|su\s*yesili|yeşil|yesil|green|matcha|haki|khaki|olive|zeytin|mint|nane", "Yeşil"),
    (r"color\s*shift|renk\s*geçiş|renk\s*gecis|transition|rainbow|dual[- ]?color", "Renk Geçişi"),
    (r"sarı|sari|yellow|limon|lemon|mustard|hardal", "Sarı"),
    (r"turuncu|orange|amber|kehribar", "Turuncu"),
    (r"siyah|black|dark|gece|obsidian|antrasit", "Siyah"),
    (r"gri|grey|gray|stone|kil|granit|rock|metal|titanium|titanyum", "Gri"),
    (r"bordo|burgundy|maroon", "Bordo"),
    (r"kırmızı|kirmizi|red|crimson|ruby|sakura|mercan", "Kırmızı"),
    (r"altın|altin|gold|bronz|bronze", "Altın"),
    (r"bej|beige", "Bej")
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

EXCLUDE_TERMS = [
    "nozzle", "hotend", "extruder", "kurutucu", "dryer", "dry box", "vakum", "poşet",
    "reçine", "resin", "3d kalem", "spatula", "sprey", "rulman", "soğutucu", "fan",
    "step motor", "baskı tablası", "peı", "tabla", "sensör", "yazıcı", "printer",
    "somun", "vida", "kayış", "kasnak", "fişek", "ısıtıcı", "termistör", "yapıştırıcı"
]

KNOWN_BRANDS = [
    "Microzey", "Porima", "Filamix", "Beta Filament", "R3D", "Creality", "Anycubic", 
    "Sunlu", "eSUN", "Polymaker", "Elegoo", "Spectrum", "Formfutura", "Bambu Lab", "Fibromast", "Robotistan"
]


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
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


def is_valid_filament(name: str) -> bool:
    name_lower = name.lower()
    if any(term in name_lower for term in EXCLUDE_TERMS):
        return False
    return True


def detect_color(text: str) -> str:
    text_lower = text.lower()
    for pattern, normalized_color in COLOR_MAPPING:
        if re.search(pattern, text_lower, re.I):
            return normalized_color
    return "Mavi"


def detect_material(name: str) -> str:
    upper = name.upper()
    for label, pattern in MATERIAL_PATTERNS:
        if re.search(pattern, upper, re.I):
            return label
    return "PLA"


def detect_brand(name: str, source: str) -> str:
    for b in KNOWN_BRANDS:
        if b.lower() in name.lower():
            return b
    return source


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

            image_url = p.get("image", "")
            product_url = p.get("url", "")
            source = p.get("source", "")
            
            brand = detect_brand(name, source)
            material = detect_material(name)
            color = detect_color(name)
            
            try:
                conn.execute("""INSERT INTO products(source, external_id, name, brand, material, color, weight_g, price, old_price, in_stock, url, image, updated_at)
                  VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(source, url) DO UPDATE SET 
                  price=excluded.price, image=excluded.image, in_stock=excluded.in_stock, color=excluded.color, brand=excluded.brand, updated_at=excluded.updated_at""", 
                  (source, str(p.get("external_id", "")), name, brand, material, color, 1000, price, p.get("old_price"), 1, product_url, image_url, now))
                saved += 1
            except Exception:
                pass
    return saved


def scrape_porima() -> list[dict]:
    items = []
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    })

    for page in range(1, 10):
        url = f"https://porima3d.com/collections/filamentler/products.json?page={page}&limit=250"
        try:
            res = session.get(url, timeout=12)
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

                    price = price_number(v.get("price"))
                    if price and price > 0:
                        v_img = v.get("featured_image", {}).get("src") if v.get("featured_image") else default_img
                        if v_img and v_img.startswith("//"):
                            v_img = "https:" + v_img

                        items.append({
                            "source": "Porima 3D",
                            "external_id": str(v.get("id")),
                            "name": full_name,
                            "price": price,
                            "old_price": price_number(v.get("compare_at_price")),
                            "in_stock": 1,
                            "url": f"https://porima3d.com/products/{p.get('handle')}?variant={v.get('id')}",
                            "image": v_img
                        })
        except Exception as e:
            print(f"[Porima 3D] Hata: {e}", flush=True)
            break

    return items


def scrape_robotistan() -> list[dict]:
    items = []
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }

    base_url = "https://www.robotistan.com"

    for page in range(1, 15):
        target_url = f"{base_url}/3d-filament?sayfa={page}"
        try:
            res = session.get(target_url, headers=headers, timeout=12)
            if res.status_code != 200:
                break

            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select(".product-item, .showProductScheme, .productItem, [class*='product-box']")
            if not cards:
                cards = soup.find_all("div", class_=lambda c: c and "product" in str(c).lower())

            if not cards:
                break

            found = 0
            for card in cards:
                title_elem = card.select_one(".product-title, .productName, h3, a.title, .p-name, [class*='title']")
                link_elem = card.find("a", href=True)
                price_elem = card.select_one(".product-price, .current-price, .price, .p-price, [class*='price']")
                
                if not title_elem or not link_elem:
                    continue

                title = title_elem.get_text(strip=True)
                if not is_valid_filament(title):
                    continue

                prod_url = link_elem["href"]
                if not prod_url.startswith("http"):
                    prod_url = base_url + ("/" + prod_url.lstrip("/"))

                price = price_number(price_elem.get_text(strip=True)) if price_elem else None
                if not price or price <= 0:
                    continue

                raw_img = ""
                img_elem = card.find("img")
                if img_elem:
                    raw_img = (
                        img_elem.get("data-original") or 
                        img_elem.get("data-src") or 
                        img_elem.get("data-lazy") or 
                        img_elem.get("src") or ""
                    )
                    if raw_img.startswith("//"):
                        raw_img = "https:" + raw_img
                    elif raw_img and not raw_img.startswith("http"):
                        raw_img = base_url + ("/" + raw_img.lstrip("/"))

                img_url = f"https://wsrv.nl/?url={raw_img}" if raw_img else ""

                items.append({
                    "source": "Robotistan",
                    "external_id": prod_url.rstrip("/").split("/")[-1],
                    "name": title,
                    "price": price,
                    "old_price": None,
                    "in_stock": 1,
                    "url": prod_url,
                    "image": img_url
                })
                found += 1

            if found == 0:
                break

        except Exception as e:
            print(f"[Robotistan] Hata: {e}", flush=True)
            break

    return items


def update_all() -> None:
    if not update_lock.acquire(blocking=False): return
    
    update_state.update(running=True, message="Güncelleniyor…")
    print(">>> FİLAMAN TARAMA SÜRECİ BAŞLADI <<<", flush=True)
    
    try:
        # 1. Porima
        try:
            p_items = scrape_porima()
            p_count = save_products(p_items)
            print(f"[Porima 3D] -> {p_count} adet tam filament eklendi.", flush=True)
        except Exception as e:
            print(f"[Porima 3D] Hata: {e}", flush=True)

        # 2. Robotistan
        try:
            r_items = scrape_robotistan()
            r_count = save_products(r_items)
            print(f"[Robotistan] -> {r_count} adet tam filament eklendi.", flush=True)
        except Exception as e:
            print(f"[Robotistan] Hata: {e}", flush=True)

        print(">>> TARAMA TAMAMLAMDI. <<<", flush=True)
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
    filters, params = ["in_stock = 1"], []
    
    for field in ("brand", "material", "color", "source"):
        value = request.args.get(field, "").strip()
        if value: 
            filters.append(f"{field} = ?")
            params.append(value)
            
    if q:
        filters.append("(name LIKE ? OR brand LIKE ? OR material LIKE ?)")
        params += [f"%{q}%"] * 3
    
    sql = "SELECT *, CASE WHEN weight_g > 0 THEN price * 1000.0 / weight_g END AS kg_price FROM products WHERE " + " AND ".join(filters) + " ORDER BY price ASC LIMIT 1000"
    
    with db() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]
    return jsonify(rows)


@app.get("/api/filters")
def filters():
    with db() as conn:
        values = {}
        for field in ("brand", "material", "color", "source"):
            sql = f"SELECT DISTINCT {field} FROM products WHERE {field} IS NOT NULL AND {field} != '' AND in_stock = 1 ORDER BY {field}"
            values[field] = [row[0] for row in conn.execute(sql)]
    return jsonify(values)


@app.post("/api/update")
def update():
    threading.Thread(target=update_all, daemon=True).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    threading.Thread(target=update_all, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    serve(app, host="0.0.0.0", port=port)
