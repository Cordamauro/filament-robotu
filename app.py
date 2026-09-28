from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request
from waitress import serve


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def data_dir() -> Path:
    root = Path(__file__).resolve().parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


APP_DIR = Path(__file__).resolve().parent
DB_PATH = data_dir() / "filaments_v9.db"
SOURCES_PATH = APP_DIR / "sources.json" if (APP_DIR / "sources.json").exists() else resource_path("sources.json")

templates_dir = APP_DIR / "templates"
if not templates_dir.exists():
    templates_dir = resource_path("templates")

app = Flask(__name__, template_folder=str(templates_dir))
update_lock = threading.Lock()
update_state = {"running": False, "message": "Hazır", "updated_at": None}


@app.after_request
def disable_browser_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


COLOR_WORDS = ["siyah", "beyaz", "kırmızı", "mavi", "yeşil", "sarı", "turuncu", "mor", "pembe", "gri", "gümüş", "altın", "kahve", "bej", "mint", "turkuaz", "lila", "şeffaf", "doğal", "naturel", "natural", "bordo", "lacivert", "antrasit"]
MATERIAL_PATTERNS = [
    ("PLA+", r"\bPLA\s*(?:\+|PLUS|PRO)\b"), ("PETG-CF", r"\bPETG[- ]?CF\b"),
    ("PETG", r"\bPETG\b"), ("ABS", r"\bABS\b"), ("PLA-CF", r"\bPLA[- ]?CF\b"), 
    ("ASA", r"\bASA\b"), ("TPU", r"\bTPU\d*(?:[- ]?HF)?\b"), ("PVA", r"\bPVA\b"),
    ("HIPS", r"\bHIPS\b"), ("PC", r"\bPC\b"), ("PLA", r"\bPLA\b")
]
KNOWN_BRANDS = ["Microzey", "Porima", "Filamix", "Beta Filament", "R3D", "Creality", "Anycubic", "Sunlu", "eSUN", "Polymaker", "Elegoo"]


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
          UNIQUE(source, external_id, name)
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


def infer(name: str, source: str) -> dict:
    upper = name.upper()
    material = next((label for label, pattern in MATERIAL_PATTERNS if re.search(pattern, upper, re.I)), "PLA")
    color = next((c.title() for c in COLOR_WORDS if c in name.casefold()), "Belirtilmemiş")
    brand = next((b for b in KNOWN_BRANDS if b.casefold() in name.casefold()), source)
    return {"brand": brand, "material": material, "color": color, "weight_g": 1000}


def save_products(items: list[dict]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    saved = 0
    with db() as conn:
        for p in items:
            name = clean_text(p.get("name"))
            if not name: continue
            meta = infer(name, p.get("source", ""))
            
            try:
                conn.execute("""INSERT INTO products(source, external_id, name, brand, material, color, weight_g, price, old_price, in_stock, url, image, updated_at)
                  VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(source, external_id, name) DO UPDATE SET 
                  price=excluded.price, in_stock=excluded.in_stock, url=excluded.url, image=excluded.image, updated_at=excluded.updated_at""", 
                  (p.get("source"), str(p.get("external_id", "")), name, meta["brand"], meta["material"], meta["color"], 1000, p.get("price"), p.get("old_price"), p.get("in_stock", 1), p.get("url"), p.get("image"), now))
                saved += 1
            except Exception as e:
                print(f"Kayıt Hatası: {e}", flush=True)
    return saved


def update_all() -> None:
    if not update_lock.acquire(blocking=False): return
    update_state.update(running=True, message="Güncelleniyor…")
    print(">>> VERİ GÜNCELLEME BAŞLADI <<<", flush=True)
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    try:
        if SOURCES_PATH.exists():
            sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
            for source in sources:
                if not source.get("enabled", True): continue
                print(f"Tarama yapılıyor: {source['name']}", flush=True)
                
                # Shopify Mağazaları Taraması
                if source.get("kind") == "shopify":
                    try:
                        res = requests.get(source["url"], headers=headers, timeout=10)
                        if res.status_code == 200:
                            data = res.json().get("products", [])
                            items = []
                            for p in data:
                                for v in p.get("variants", []):
                                    items.append({
                                        "source": source["name"],
                                        "external_id": str(v.get("id")),
                                        "name": f"{p.get('title')} {v.get('title') if v.get('title') != 'Default Title' else ''}",
                                        "price": price_number(v.get("price")),
                                        "old_price": price_number(v.get("compare_at_price")),
                                        "in_stock": 1 if v.get("available") else 0,
                                        "url": f"{source['url'].replace('/products.json', '')}/products/{p.get('handle')}",
                                        "image": (p.get("images") or [{}])[0].get("src")
                                    })
                            count = save_products(items)
                            print(f"{source['name']} -> {count} ürün kaydedildi.", flush=True)
                    except Exception as e:
                        print(f"Hata ({source['name']}): {e}", flush=True)

        update_state.update(message="Güncellendi", updated_at=datetime.now().strftime("%d.%m.%Y %H:%M"))
    finally:
        update_state["running"] = False
        update_lock.release()
        print(">>> VERİ GÜNCELLEME BİTTİ <<<", flush=True)


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
    sql += " ORDER BY price ASC LIMIT 500"
    
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
