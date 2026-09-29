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

# --- VERİTABANI KURULUMU ---
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

# --- RENK VE MATERYAL ALGILAMA DÜZENEĞİ ---
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

# --- SHOPIFY ÇEKİCİ (PORİMA) ---
def scrape_shopify(source_name, base_url):
    products = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        url = base_url if base_url.endswith("/products.json") else f"{base_url.rstrip('/')}/products.json"
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            for p in data.get("products", []):
                title = p.get("title", "")
                if not any(m in title.upper() for m in MATERIALS):
                    continue
                
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
                    prod_url = f"{base_url.replace('/products.json', '')}/products/{p.get('handle')}"
                    
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
        print(f"Shopify Scrape Hatası ({source_name}): {e}")
    return products

# --- MICROZEY HTML KAZIYICI ---
def scrape_microzey():
    products = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    base_url = "https://www.microzey.com"
    category_url = "https://www.microzey.com/filamentler"
    
    try:
        res = requests.get(category_url, headers=headers, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            items = soup.find_all(["div", "li"], class_=re.compile(r"product|item", re.I))
            
            for idx, item in enumerate(items):
                title_elem = item.find(["a", "h2", "h3", "div"], class_=re.compile(r"title|name", re.I)) or item.find("a")
                price_elem = item.find(["span", "div", "p"], class_=re.compile(r"price", re.I))
                img_elem = item.find("img")
                
                if title_elem and price_elem:
                    name = title_elem.get_text(strip=True)
                    if not any(m in name.upper() for m in MATERIALS):
                        continue
                    
                    price_text = re.sub(r"[^\d,.]", "", price_elem.get_text(strip=True)).replace(".", "").replace(",", ".")
                    try:
                        price = float(price_text)
                    except ValueError:
                        continue
                    
                    if price <= 0:
                        continue
                    
                    href = title_elem.get("href") or (item.find("a").get("href") if item.find("a") else "")
                    prod_url = urljoin(base_url, href) if href else base_url
                    
                    img_src = ""
                    if img_elem:
                        img_src = img_elem.get("data-src") or img_elem.get("src") or ""
                        if img_src and not img_src.startswith("http"):
                            img_src = urljoin(base_url, img_src)
                    
                    products.append({
                        "id": f"microzey_{idx}_{hash(name)}",
                        "source": "Microzey",
                        "brand": "Microzey",
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
        print(f"Microzey Scrape Hatası: {e}")
    return products

# --- FILAMIX HTML KAZIYICI ---
def scrape_filamix():
    products = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    base_url = "https://www.filamix.com.tr"
    category_url = "https://www.filamix.com.tr/kategori/filamentler"
    
    try:
        res = requests.get(category_url, headers=headers, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            items = soup.find_all(["div", "li"], class_=re.compile(r"product|item|card", re.I))
            
            for idx, item in enumerate(items):
                title_elem = item.find(["a", "h2", "h3", "div"], class_=re.compile(r"title|name", re.I)) or item.find("a")
                price_elem = item.find(["span", "div", "p"], class_=re.compile(r"price", re.I))
                img_elem = item.find("img")
                
                if title_elem and price_elem:
                    name = title_elem.get_text(strip=True)
                    if not any(m in name.upper() for m in MATERIALS):
                        continue
                    
                    price_text = re.sub(r"[^\d,.]", "", price_elem.get_text(strip=True)).replace(".", "").replace(",", ".")
                    try:
                        price = float(price_text)
                    except ValueError:
                        continue
                    
                    if price <= 0:
                        continue
                    
                    href = title_elem.get("href") or (item.find("a").get("href") if item.find("a") else "")
                    prod_url = urljoin(base_url, href) if href else base_url
                    
                    img_src = ""
                    if img_elem:
                        img_src = img_elem.get("data-src") or img_elem.get("src") or ""
                        if img_src and not img_src.startswith("http"):
                            img_src = urljoin(base_url, img_src)
                    
                    products.append({
                        "id": f"filamix_{idx}_{hash(name)}",
                        "source": "Filamix",
                        "brand": "Filamix",
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
        print(f"Filamix Scrape Hatası: {e}")
    return products

# --- GENEL VERİ GÜNCELLEME SÜRECİ ---
def update_all_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products")
    conn.commit()
    conn.close()

    all_products = []
    
    # 1. Porima 3D (Shopify)
    porima_prods = scrape_shopify("Porima 3D", "https://porima3d.com")
    all_products.extend(porima_prods)
    print(f"[Porima 3D] -> {len(porima_prods)} adet eklendi.")

    # 2. Microzey (HTML Scraper)
    micro_prods = scrape_microzey()
    all_products.extend(micro_prods)
    print(f"[Microzey] -> {len(micro_prods)} adet eklendi.")

    # 3. Filamix (HTML Scraper)
    filamix_prods = scrape_filamix()
    all_products.extend(filamix_prods)
    print(f"[Filamix] -> {len(filamix_prods)} adet eklendi.")

    # Veritabanına Yaz
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

# --- API ENDPOINTHLERİ ---
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
