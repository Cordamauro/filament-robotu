import os
import re
import json
import sqlite3
import threading
from urllib.parse import urljoin
from pathlib import Path
from flask import Flask, render_template, jsonify, request
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)
DB_PATH = Path("filaments.db")
SOURCES_PATH = Path("sources.json")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            source TEXT,
            brand TEXT,
            name TEXT,
            material TEXT,
            color TEXT,
            price REAL,
            kg_price REAL,
            in_stock INTEGER,
            url TEXT,
            image TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

COLOR_MAP = {
    "Siyah": ["siyah", "black", "nero"],
    "Beyaz": ["beyaz", "white", "blanco"],
    "Kırmızı": ["kırmızı", "kirmizi", "red", "rojo"],
    "Mavi": ["mavi", "blue", "lacivert", "navy"],
    "Yeşil": ["yeşil", "yesil", "green", "fıstık"],
    "Sarı": ["sarı", "sari", "yellow"],
    "Turuncu": ["turuncu", "orange"],
    "Gri": ["gri", "grey", "gray", "gümüş", "silver"],
    "Kahverengi": ["kahverengi", "kahve", "brown", "mocha", "tuğla"],
    "Pembe": ["pembe", "pink"],
    "Mor": ["mor", "purple", "violet"],
    "Ten / Somon": ["ten", "somon", "latte", "skin"],
    "Altın": ["altın", "altin", "gold"],
    "Bakır": ["bakır", "bakir", "copper"],
    "Şeffaf": ["şeffaf", "seffaf", "transparent", "clear"],
    "Ahşap / Wood": ["wood", "ahşap", "ahsap"]
}

MATERIALS = ["PLA", "PETG", "ABS", "TPU", "PVA", "ASA", "PC", "PA", "NYLON", "CARBON"]

def detect_material(name):
    name_upper = name.upper()
    for mat in MATERIALS:
        if re.search(rf"\b{mat}\b", name_upper):
            return mat
    return "PLA"

def detect_color(name):
    name_lower = name.lower()
    for main_color, keywords in COLOR_MAP.items():
        for kw in keywords:
            if kw in name_lower:
                return main_color
    return "Mavi"

# --- PORİMA (LIMIT=250 İLE TEK İSTEKTE 237 ULAŞAN SHOPIFY SCRAPER) ---
def scrape_shopify(source_name, base_url):
    products = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        clean_url = base_url.split('?')[0].rstrip('/')
        url = clean_url if clean_url.endswith("/products.json") else f"{clean_url}/products.json"
        
        # 250 LIMIT PARANETRESİ PORİMA'NIN BÜTÜN URUNLERINI TEK SEFERDE CEKER
        res = requests.get(f"{url}?limit=250", headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            for p in data.get("products", []):
                title = p.get("title", "")
                brand = p.get("vendor") or source_name
                image_url = p["images"][0]["src"] if p.get("images") else ""
                
                for v in p.get("variants", []):
                    if not v.get("available", False):
                        continue
                    
                    price = float(v.get("price", 0))
                    if price <= 0:
                        continue
                    
                    v_title = v.get("title", "")
                    full_name = f"{title} - {v_title}" if v_title and v_title != "Default Title" else title
                    prod_id = f"{source_name}_{v.get('id')}"
                    prod_url = f"{clean_url.replace('/products.json', '')}/products/{p.get('handle')}"
                    
                    products.append({
                        "id": prod_id,
                        "source": source_name,
                        "brand": brand,
                        "name": full_name,
                        "material": detect_material(full_name),
                        "color": detect_color(full_name),
                        "price": price,
                        "kg_price": price,
                        "in_stock": 1,
                        "url": prod_url,
                        "image": image_url
                    })
    except Exception as e:
        print(f"Scrape Hatası ({source_name}): {e}")
    return products

# --- ROBITSHOP SCRAPER ---
def scrape_robitshop():
    products = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    cat_urls = [
        "https://www.robitshop.com/kategori/filament-1",
        "https://www.robitshop.com/kategori/abs-filamentler",
        "https://www.robitshop.com/kategori/pet-g-filament"
    ]

    for cat_url in cat_urls:
        try:
            res = requests.get(cat_url, headers=headers, timeout=15)
            if res.status_code != 200:
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            items = soup.find_all("div", class_=re.compile(r"product-item|showcase|ItemOrj", re.I))

            for idx, item in enumerate(items):
                title_elem = item.find(["a", "div", "span"], class_=re.compile(r"product-title|productName|title", re.I)) or item.find("a")
                price_elem = item.find(["span", "div"], class_=re.compile(r"price|fiyat|discountPrice", re.I))
                img_elem = item.find("img")

                if title_elem and price_elem:
                    name = title_elem.get_text(strip=True)
                    if not name or len(name) < 3:
                        continue

                    raw_price = price_elem.get_text(strip=True)
                    price_digits = re.sub(r"[^\d,.]", "", raw_price).replace(".", "").replace(",", ".")
                    
                    try:
                        price = float(price_digits)
                    except ValueError:
                        continue

                    if price <= 0:
                        continue

                    href = title_elem.get("href") or (item.find("a").get("href") if item.find("a") else "")
                    prod_url = urljoin("https://www.robitshop.com", href) if href else cat_url

                    img_src = ""
                    if img_elem:
                        img_src = img_elem.get("data-src") or img_elem.get("src") or ""
                        if img_src and not img_src.startswith("http"):
                            img_src = urljoin("https://www.robitshop.com", img_src)

                    first_word = name.split()[0] if name.split() else "Robitshop"
                    brand = first_word if first_word.lower() in ["filenta", "esun", "creality", "kingroon", "filamix"] else "Robitshop"

                    prod_id = f"robitshop_{hash(name)}"

                    if not any(p["id"] == prod_id for p in products):
                        products.append({
                            "id": prod_id,
                            "source": "Robitshop",
                            "brand": brand,
                            "name": name,
                            "material": detect_material(name),
                            "color": detect_color(name),
                            "price": price,
                            "kg_price": price,
                            "in_stock": 1,
                            "url": prod_url,
                            "image": img_src
                        })
        except Exception as e:
            print(f"Robitshop Scrape Hatası ({cat_url}): {e}")

    return products

def update_all_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products")
    conn.commit()
    conn.close()

    if not SOURCES_PATH.exists():
        print("sources.json bulunamadı!")
        return

    try:
        sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"sources.json okunurken hata oluştu: {e}")
        return

    all_products = []
    for src in sources:
        if not src.get("enabled", True):
            continue
        
        name = src.get("name")
        url = src.get("url")
        
        if "porima" in name.lower() or "shopify" in src.get("kind", "").lower() or "json" in url:
            prods = scrape_shopify(name, url)
            all_products.extend(prods)
            print(f"[{name}] -> {len(prods)} adet tam filament eklendi.")
        elif "robitshop" in name.lower():
            prods = scrape_robitshop()
            all_products.extend(prods)
            print(f"[{name}] -> {len(prods)} adet filament eklendi.")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for p in all_products:
        cursor.execute("""
            INSERT OR REPLACE INTO products 
            (id, source, brand, name, material, color, price, kg_price, in_stock, url, image)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (p["id"], p["source"], p["brand"], p["name"], p["material"], p["color"], p["price"], p["kg_price"], p["in_stock"], p["url"], p["image"]))
    
    conn.commit()
    conn.close()
    print(f">>> TOPLAM {len(all_products)} ADET FİLAMNET BAŞARIYLA EKLENDİ <<<")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/filters")
def get_filters():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT DISTINCT source FROM products WHERE in_stock = 1 ORDER BY source")
    sources = [r[0] for r in cursor.fetchall() if r[0]]
    
    cursor.execute("SELECT DISTINCT brand FROM products WHERE in_stock = 1 ORDER BY brand")
    brands = [r[0] for r in cursor.fetchall() if r[0]]
    
    cursor.execute("SELECT DISTINCT material FROM products WHERE in_stock = 1 ORDER BY material")
    materials = [r[0] for r in cursor.fetchall() if r[0]]
    
    cursor.execute("SELECT DISTINCT color FROM products WHERE in_stock = 1 ORDER BY color")
    colors = [r[0] for r in cursor.fetchall() if r[0]]
    
    conn.close()
    return jsonify({
        "source": sources,
        "brand": brands,
        "material": materials,
        "color": colors
    })

@app.route("/api/products")
def get_products():
    source = request.args.get("source")
    brand = request.args.get("brand")
    material = request.args.get("material")
    color = request.args.get("color")
    q = request.args.get("q")

    query = "SELECT id, source, brand, name, material, color, price, kg_price, in_stock, url, image FROM products WHERE in_stock = 1"
    params = []

    if source:
        query += " AND source = ?"
        params.append(source)
    if brand:
        query += " AND brand = ?"
        params.append(brand)
    if material:
        query += " AND material = ?"
        params.append(material)
    if color:
        query += " AND color = ?"
        params.append(color)
    if q:
        query += " AND (name LIKE ? OR color LIKE ? OR material LIKE ?)"
        q_wild = f"%{q}%"
        params.extend([q_wild, q_wild, q_wild])

    query += " ORDER BY price ASC"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    products = []
    for r in rows:
        products.append({
            "id": r[0],
            "source": r[1],
            "brand": r[2],
            "name": r[3],
            "material": r[4],
            "color": r[5],
            "price": r[6],
            "kg_price": r[7],
            "in_stock": r[8],
            "url": r[9],
            "image": r[10]
        })

    return jsonify(products)

@app.route("/api/update", methods=["POST"])
def trigger_update():
    threading.Thread(target=update_all_data).start()
    return jsonify({"status": "success", "message": "Güncelleme başlatıldı"})

if __name__ == "__main__":
    update_all_data()
    app.run(host="0.0.0.0", port=5000)
