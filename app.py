from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import concurrent.futures
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
if not SOURCES_PATH.exists():
    SOURCES_PATH = Path(__file__).resolve().parent.parent / "sources.json"

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
    # 1. ÖZEL İSTİSNALAR VE DİREKT EŞLEŞMELER
    (r"(?=.*stone)(?=.*mercan)", "Kırmızı"),
    (r"star|simli|glitter|sparkle", "Mavi"),  # Simli/Star serileri öncelikli Mavi

    # 2. MAVİ VE PEMBE
    (r"mavi|blue|lacivert|navy|bebek\s*mavisi|bebek\s*mavi|buz|ice|sky|gök|gok|turkuaz|teal|cyan|sapphire|ocean|okyanus|azure|cobalt|kobalt", "Mavi"),
    (r"pembe|pink|magenta|fuchsia|fuşya|fusya", "Pembe"),

    # 3. TEMEL RENKLER
    (r"beyaz|white|ral\s*9003", "Beyaz"),
    (r"kahverengi|kahve|tuğla|mocha|brown|chocolate", "Kahverengi"),
    (r"mor|purple|violet", "Mor"),
    (r"gümüş|silver", "Gümüş"),
    (r"army", "Army"),
    (r"bakır|copper", "Bakır"),
    (r"natural|naturel|şeffaf|seffaf|transparent|clear", "Şeffaf"),
    (r"wood", "Wood"),
    (r"ten|somon|salmon|skin|flesh|latte", "Ten"),
    (r"su\s*yeşili|su\s*yesili|yeşil|yesil|green|matcha|haki|khaki|olive|zeytin|mint|nane|fıstık|emerald|zümrüt|lime|çim|grass|pistachio|forest|orman", "Yeşil"),
    (r"color\s*shift|renk\s*geçiş|renk\s*gecis|transition|rainbow|dual[- ]?color|tri[- ]?color|co[- ]?ex", "Renk Geçişi"),
    (r"sarı|sari|yellow|limon|lemon|mustard|hardal", "Sarı"),
    (r"turuncu|orange|amber|kehribar", "Turuncu"),
    (r"siyah|black|dark|gece|obsidian|antrasit|anthracite", "Siyah"),
    (r"gri|grey|gray|stone|kil|granit|rock|metal|titanium|titanyum|çelik|celik|steel|füme|fume", "Gri"),
    (r"bordo|burgundy|maroon", "Bordo"),
    (r"kırmızı|kirmizi|red|crimson|ruby|sakura|somun|mercan|coral|peach|şeftali|seftali", "Kırmızı"),
    (r"altın|altin|gold|bronz|bronze", "Altın"),
    (r"lila|lilac|lavanta|lavender|erik|plum", "Mor"),
    (r"bej|beige", "Bej")
]

COLOR_ALIASES = {
    "sarı": ["sari", "yellow"],
    "kırmızı": ["kirmizi", "red", "mercan"],
    "beyaz": ["beyaz", "white", "ral9003"],
    "siyah": ["siyah", "black"],
    "mavi": ["mavi", "blue", "lacivert", "navy", "bebek mavisi"],
    "yeşil": ["yesil", "green", "haki", "matcha", "su yeşili"],
    "pembe": ["pembe", "pink"],
    "gümüş": ["gumus", "silver"],
    "altın": ["altin", "gold"],
    "somon": ["somon", "salmon"],
    "şeffaf": ["seffaf", "transparent", "clear", "natural", "naturel"]
}

MATERIAL_PATTERNS = [
    ("PLA+", r"\bPLA\s*(?:\+|PLUS|PRO)\b"), ("PETG-CF", r"\bPETG[- ]?CF\b"),
    ("PET-CF", r"\bPET[- ]?CF\d*\b"), ("PA-CF", r"\bPA\d*[- ]?CF\d*|NYLON[- ]?CF\d*"),
    ("PETG", r"\bPETG\b"), ("ABS", r"\bABS\b"), ("PLA-CF", r"\bPLA[- ]?CF\b"), 
    ("ASA", r"\bASA\b"), ("TPU", r"\bTPU\d*(?:[- ]?HF)?\b"), ("PVA", r"\bPVA\b"),
    ("HIPS", r"\bHIPS\b"), ("Naylon/PA", r"\bNYLON\b|\bPA(?:6|12)?\b"),
    ("PC", r"\bPC\b"), ("PLA", r"\bPLA\b")
]

EXCLUDE_TERMS = [
    "hub", "splitter", "buffer", "feeder", "cutter", "tube", "replacement", "ptfe", "kesici", "borusu", "bıçak", "makas",
    "nozzle", "hotend", "extruder", "kurutucu", "dryer", "dry box", "vakum", "poşet",
    "reçine", "resin", "3d kalem", "spatula", "sprey", "rulman", "soğutucu", "fan",
    "step motor", "baskı tablası", "peı", "tabla", "sensör", "sensor", "yazıcı", "printer",
    "somun", "vida", "kayış", "kasnak", "fişek", "ısıtıcı", "termistör", "yapıştırıcı",
    "temizleme", "temizleyici", "modül", "kart", "sürücü", "coupler", "swatch", "numune"
]

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


def is_valid_filament(name: str) -> bool:
    name_lower = name.lower()
    if any(term in name_lower for term in EXCLUDE_TERMS):
        return False
    if "filament" not in name_lower:
        return False
    return True


def detect_color(text: str) -> str | None:
    text_lower = text.lower()
    for pattern, normalized_color in COLOR_MAPPING:
        if re.search(pattern, text_lower, re.I):
            return normalized_color
    return None


def infer(name: str, source: str, image_url: str = "", url: str = "") -> dict:
    upper = name.upper()
    material = next((label for label, pattern in MATERIAL_PATTERNS if re.search(pattern, upper, re.I)), "PLA")
    
    # 1. İsmi tara
    color = detect_color(name)
    
    # 2. İsimde bulunamadıysa URL ve görsel URL'sini tara
    if not color:
        color = detect_color(f"{image_url} {url}")

    # 3. 'Diğer / Özel Renk' seçeneği kaldırıldı: Hiçbir kurala uymuyorsa varsayılan Mavi atanır
    if not color:
        color = "Mavi"

    brand = next((b for b in KNOWN_BRANDS if b.casefold() in name.casefold()), source)
    return {"brand": brand, "material": material, "color": color, "weight_g": 1000}


def save_products(items: list[dict]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    saved = 0
    with db() as conn:
        for p in items:
            name = clean_text(p.get("name"))
            if not name or not is_valid_filament(name):
                continue
                
            if not p.get("in_stock") or p.get("in_stock") != 1:
                continue

            price = p.get("price")
            if not price or price <= 0:
                continue

            image_url = p.get("image", "")
            product_url = p.get("url", "")
            meta = infer(name, p.get("source", ""), image_url, product_url)
            
            try:
                conn.execute("""INSERT INTO products(source, external_id, name, brand, material, color, weight_g, price, old_price, in_stock, url, image, updated_at)
                  VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(source, url) DO UPDATE SET 
                  price=excluded.price, in_stock=excluded.in_stock, color=excluded.color, image=excluded.image, updated_at=excluded.updated_at""", 
                  (p.get("source"), str(p.get("external_id", "")), name, meta["brand"], meta["material"], meta["color"], meta["weight_g"], price, p.get("old_price"), 1, product_url, image_url, now))
                saved += 1
            except Exception:
                pass
    return saved


def scrape_shopify(source: dict, headers: dict) -> list[dict]:
    items = []
    page = 1
    base_url = source["url"].split("/products.json")[0].rstrip("/")
    while page <= 10:
        req_url = f"{base_url}/products.json?page={page}&limit=250"
        try:
            res = requests.get(req_url, headers=headers, timeout=10)
            if res.status_code != 200: break
            products = res.json().get("products", [])
            if not products: break
            
            for p in products:
                title = p.get('title', '')
                if not is_valid_filament(title): continue

                images = p.get("images") or []
                img_map = {img.get("id"): img.get("src") for img in images if img.get("id") and img.get("src")}
                default_img = images[0].get("src") if images else ""

                for v in p.get("variants", []):
                    if not v.get("available"):
                        continue

                    v_title = v.get('title', '')
                    full_name = f"{title} {v_title if v_title != 'Default Title' else ''}"
                    
                    if not is_valid_filament(full_name): continue

                    v_img_src = None
                    v_img_id = v.get("image_id")
                    if v_img_id:
                        v_img_src = img_map.get(v_img_id)

                    if not v_img_src and v.get("featured_image"):
                        v_img_src = v.get("featured_image", {}).get("src")

                    if not v_img_src and len(images) > 1:
                        v_title_lower = v_title.lower()
                        for img in images:
                            src_lower = (img.get("src") or "").lower()
                            alt_lower = (img.get("alt") or "").lower()
                            
                            for color_key, aliases in COLOR_ALIASES.items():
                                if color_key in v_title_lower:
                                    for alias in aliases:
                                        if alias in src_lower or alias in alt_lower:
                                            v_img_src = img.get("src")
                                            break
                                if v_img_src: break

                    if not v_img_src:
                        v_img_src = default_img

                    if v_img_src and v_img_src.startswith("//"):
                        v_img_src = "https:" + v_img_src
                    elif v_img_src and not v_img_src.startswith("http"):
                        v_img_src = base_url + "/" + v_img_src.lstrip("/")

                    price = price_number(v.get("price"))
                    if price and price > 0:
                        items.append({
                            "source": source["name"],
                            "external_id": str(v.get("id")),
                            "name": full_name,
                            "price": price,
                            "old_price": price_number(v.get("compare_at_price")),
                            "in_stock": 1,
                            "url": f"{base_url}/products/{p.get('handle')}?variant={v.get('id')}",
                            "image": v_img_src
                        })
            page += 1
        except Exception:
            break
    return items


def scrape_source(source: dict) -> list[dict]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7'
    }
    return scrape_shopify(source, headers)


def update_all() -> None:
    if not update_lock.acquire(blocking=False): return
    
    update_state.update(running=True, message="Güncelleniyor…")
    print(">>> VERİ TARAMASI BAŞLADI <<<", flush=True)
    
    try:
        if SOURCES_PATH.exists():
            sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
            active_sources = [s for s in sources if s.get("enabled", True)]
            
            total_saved = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                future_to_source = {executor.submit(scrape_source, s): s for s in active_sources}
                for future in concurrent.futures.as_completed(future_to_source):
                    s = future_to_source[future]
                    try:
                        items = future.result()
                        count = save_products(items)
                        total_saved += count
                        print(f"[{s['name']}] -> {count} adet tam filament eklendi.", flush=True)
                    except Exception as e:
                        print(f"[{s['name']}] Hata: {e}", flush=True)

            print(f">>> TOPLAM {total_saved} ADET SADECE FİLAMENT EKLENDİ <<<", flush=True)

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
        if value: filters.append(f"{field} = ?"); params.append(value)
    if q:
        filters.append("(name LIKE ? OR brand LIKE ? OR material LIKE ? OR color LIKE ?)")
        params += [f"%{q}%"] * 4
    
    sql = "SELECT *, CASE WHEN weight_g > 0 THEN price * 1000.0 / weight_g END AS kg_price FROM products WHERE " + " AND ".join(filters) + " ORDER BY price ASC LIMIT 1000"
    
    with db() as conn: rows = [dict(r) for r in conn.execute(sql, params)]
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
    serve(app, host="0.0.0.0", port=port, threads=4)
