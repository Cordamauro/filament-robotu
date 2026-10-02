from __future__ import annotations

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

app = Flask(__name__, template_folder=str(APP_DIR / "templates"))
update_lock = threading.Lock()


@app.after_request
def disable_browser_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


COLOR_MAPPING = [
    (r"mavi|blue|lacivert|navy|turkuaz|aqua", "Mavi"),
    (r"pembe|pink|magenta|fuşya", "Pembe"),
    (r"beyaz|white", "Beyaz"),
    (r"kahverengi|brown|mocha|tuğla", "Kahverengi"),
    (r"mor|purple|violet|lila", "Mor"),
    (r"gümüş|silver", "Gümüş"),
    (r"yeşil|yesil|green|haki|mint|zeytin", "Yeşil"),
    (r"sarı|sari|yellow|hardal", "Sarı"),
    (r"turuncu|orange", "Turuncu"),
    (r"siyah|black|antrasit", "Siyah"),
    (r"gri|grey|gray", "Gri"),
    (r"kırmızı|kirmizi|red|mercan", "Kırmızı"),
    (r"altın|gold|bronz", "Altın"),
    (r"şeffaf|seffaf|clear|natural", "Şeffaf")
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
    ("PC", r"\bPC\b"),
    ("PLA", r"\bPLA(?:\+|[- ]?PLUS|[- ]?PRO|[- ]?BASIC|[- ]?HS)?\b")
]

# Filament dışındaki cihaz ve aksesuarları engelleyen genişletilmiş filtre
EXCLUDE_TERMS = [
    "yıkama", "kürleme", "tarayıcı", "tarayici", "lazer", "gravür", "gravur", "turntable",
    "makinesi", "makine", "bundle", "scan", "scanner", "wash", "cure", "laser", "engraver",
    "printer", "yazıcı", "yazici", "nozzle", "hotend", "extruder", "kurutucu", "dryer", 
    "dry box", "vakum", "poşet", "reçine", "resin", "3d kalem", "spatula", "sprey", "rulman", 
    "soğutucu", "fan", "step motor", "baskı tablası", "peı", "tabla", "sensör", "somun", 
    "vida", "kayış", "kasnak", "fişek", "ısıtıcı", "termistör", "yapıştırıcı", "modül", "kart"
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


def clean_price(val) -> float | None:
    if not val: return None
    txt = re.sub(r"[^\d,.]", "", str(val))
    if not txt: return None
    if "," in txt and "." in txt: txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt: txt = txt.replace(",", ".")
    try: return float(txt)
    except ValueError: return None


def is_valid(name: str) -> bool:
    n_lower = name.lower()
    
    # 1. Cihaz/aksesuar terimi geçiyorsa ele
    if any(t in n_lower for t in EXCLUDE_TERMS):
        return False
        
    # 2. İsmi filament mi veya tanımlı bir filament materyali içeriyor mu?
    if "filament" in n_lower or "filaman" in n_lower:
        return True
        
    for _, pattern in MATERIAL_PATTERNS:
        if re.search(pattern, name, re.I):
            return True
            
    return False


def detect_color(name: str) -> str:
    n_lower = name.lower()
    for pattern, col in COLOR_MAPPING:
        if re.search(pattern, n_lower, re.I):
            return col
    return "Mavi"


def detect_material(name: str) -> str:
    upper = name.upper()
    for label, pattern in MATERIAL_PATTERNS:
        if re.search(pattern, upper, re.I):
            return label
    return "PLA"


def detect_brand(name: str, fallback: str) -> str:
    for b in KNOWN_BRANDS:
        if b.lower() in name.lower():
            return b
    return fallback


def save_items(items: list[dict]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    count = 0
    with db() as conn:
        for p in items:
            name = p.get("name", "").strip()
            price = p.get("price")
            if not name or not price or price <= 0 or not is_valid(name):
                continue

            brand = detect_brand(name, p.get("source", ""))
            material = detect_material(name)
            color = detect_color(name)
            
            try:
                conn.execute("""
                    INSERT INTO products(source, external_id, name, brand, material, color, weight_g, price, old_price, in_stock, url, image, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, url) DO UPDATE SET 
                    price=excluded.price, image=excluded.image, in_stock=excluded.in_stock, color=excluded.color, brand=excluded.brand, updated_at=excluded.updated_at
                """, (
                    p.get("source"), str(p.get("external_id", "")), name, brand, material, color, 1000,
                    price, p.get("old_price"), 1, p.get("url"), p.get("image", ""), now
                ))
                count += 1
            except Exception:
                pass
    return count


def fetch_porima() -> list[dict]:
    items = []
    sess = requests.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    })

    for p_num in range(1, 8):
        url = f"https://porima3d.com/products.json?page={p_num}&limit=250"
        try:
            res = sess.get(url, timeout=10)
            if res.status_code != 200: break
            prods = res.json().get("products", [])
            if not prods: break

            for p in prods:
                title = p.get('title', '')
                imgs = p.get("images") or []
                def_img = imgs[0].get("src") if imgs else ""

                for v in p.get("variants", []):
                    if not v.get("available"): continue
                    v_title = v.get('title', '')
                    full_name = f"{title} {v_title if v_title != 'Default Title' else ''}"
                    
                    if not is_valid(full_name):
                        continue

                    price = clean_price(v.get("price"))
                    if price:
                        v_img = v.get("featured_image", {}).get("src") if v.get("featured_image") else def_img
                        if v_img and v_img.startswith("//"): v_img = "https:" + v_img

                        items.append({
                            "source": "Porima 3D",
                            "external_id": str(v.get("id")),
                            "name": full_name,
                            "price": price,
                            "old_price": clean_price(v.get("compare_at_price")),
                            "url": f"https://porima3d.com/products/{p.get('handle')}?variant={v.get('id')}",
                            "image": v_img
                        })
        except Exception:
            break
    return items


def fetch_robotistan() -> list[dict]:
    items = []
    sess = requests.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    })

    base_url = "https://www.robotistan.com"
    for page in range(1, 15):
        try:
            res = sess.get(f"{base_url}/3d-filament?sayfa={page}", timeout=10)
            if res.status_code != 200: break

            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select(".product-item, .showProductScheme, .productItem, [class*='product-box']")
            if not cards: break

            found = 0
            for card in cards:
                title_el = card.select_one(".product-title, .productName, h3, a.title, .p-name")
                link_el = card.find("a", href=True)
                price_el = card.select_one(".product-price, .current-price, .price, .p-price")

                if not title_el or not link_el: continue
                title = title_el.get_text(strip=True)
                
                if not is_valid(title):
                    continue

                price = clean_price(price_el.get_text(strip=True)) if price_el else None

                if price and price > 0:
                    prod_url = link_el["href"]
                    if not prod_url.startswith("http"):
                        prod_url = base_url + ("/" + prod_url.lstrip("/"))

                    img_el = card.find("img")
                    raw_img = ""
                    if img_el:
                        raw_img = img_el.get("data-original") or img_el.get("data-src") or img_el.get("src") or ""
                        if raw_img.startswith("//"): raw_img = "https:" + raw_img
                        elif raw_img and not raw_img.startswith("http"): raw_img = base_url + ("/" + raw_img.lstrip("/"))

                    img_url = f"https://wsrv.nl/?url={raw_img}" if raw_img else ""

                    items.append({
                        "source": "Robotistan",
                        "external_id": prod_url.rstrip("/").split("/")[-1],
                        "name": title,
                        "price": price,
                        "old_price": None,
                        "url": prod_url,
                        "image": img_url
                    })
                    found += 1
            if found == 0: break
        except Exception:
            break
    return items


def run_update():
    if not update_lock.acquire(blocking=False): return
    print(">>> TARAMA BAŞLADI <<<", flush=True)
    try:
        # Eski hatalı verileri veritabanından temizle
        with db() as conn:
            for term in EXCLUDE_TERMS:
                conn.execute("DELETE FROM products WHERE LOWER(name) LIKE ?", (f"%{term}%",))
        
        p_items = fetch_porima()
        p_c = save_items(p_items)
        print(f"[Porima 3D] -> {p_c} ürün eklendi.", flush=True)

        r_items = fetch_robotistan()
        r_c = save_items(r_items)
        print(f"[Robotistan] -> {r_c} ürün eklendi.", flush=True)
        print(">>> TARAMA TAMAMLAMDI <<<", flush=True)
    finally:
        update_lock.release()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/products")
def products():
    q = request.args.get("q", "").strip()
    filters, params = ["in_stock = 1"], []
    
    for field in ("brand", "material", "color", "source"):
        val = request.args.get(field, "").strip()
        if val:
            filters.append(f"{field} = ?")
            params.append(val)

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
    threading.Thread(target=run_update, daemon=True).start()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    threading.Thread(target=run_update, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    serve(app, host="0.0.0.0", port=port)
