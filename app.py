from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
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
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
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
    (r"gri|grey|gray|beton", "Gri"),
    (r"kırmızı|kirmizi|red|mercan", "Kırmızı"),
    (r"altın|gold|bronz", "Altın"),
    (r"şeffaf|seffaf|clear|natural", "Şeffaf"),
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
    ("PLA", r"\bPLA(?:\+|[- ]?PLUS|[- ]?PRO|[- ]?BASIC|[- ]?HS)?\b"),
]


EXCLUDE_TERMS = [
    "cutter",
    "tube",
    "replacement",
    "print head",
    "head",
    "kesici",
    "boru",
    "borusu",
    "yıkama",
    "kürleme",
    "tarayıcı",
    "tarayici",
    "lazer",
    "gravür",
    "gravur",
    "turntable",
    "makinesi",
    "makine",
    "bundle",
    "scan",
    "scanner",
    "wash",
    "cure",
    "laser",
    "engraver",
    "printer",
    "yazıcı",
    "yazici",
    "nozzle",
    "hotend",
    "extruder",
    "kurutucu",
    "dryer",
    "dry box",
    "vakum",
    "poşet",
    "reçine",
    "resin",
    "3d kalem",
    "spatula",
    "sprey",
    "rulman",
    "soğutucu",
    "fan",
    "step motor",
    "baskı tablası",
    "peı",
    "tabla",
    "sensör",
    "somun",
    "vida",
    "kayış",
    "kasnak",
    "fişek",
    "ısıtıcı",
    "termistör",
    "yapıştırıcı",
    "modül",
    "kart",
    "baskı kafası",
    "yay",
    "sürücü",
    "güç kaynağı",
    "adaptör",
    "kablo",
]


KNOWN_BRANDS = [
    "Microzey",
    "Porima 3D",
    "Porima",
    "Filamix",
    "Beta Filament",
    "R3D",
    "Creality",
    "Anycubic",
    "Sunlu",
    "eSUN",
    "Polymaker",
    "Elegoo",
    "Spectrum",
    "Formfutura",
    "Bambu Lab",
    "Fibromast",
    "Robotistan",
    "tinylab",
    "Inslogic",
    "Kingroon",
    "Apex",
    "3dcim",
]


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=60.0)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                external_id TEXT,
                name TEXT NOT NULL,
                brand TEXT,
                material TEXT,
                color TEXT,
                weight_g INTEGER,
                price REAL,
                old_price REAL,
                in_stock INTEGER DEFAULT 1,
                url TEXT NOT NULL,
                image TEXT,
                updated_at TEXT,
                UNIQUE(source, url)
            )
            """
        )


def clean_price(value) -> float | None:
    if value is None:
        return None

    text = re.sub(r"[^\d,.]", "", str(value))

    if not text:
        return None

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def is_valid(name: str) -> bool:
    name_lower = name.casefold()

    if any(term.casefold() in name_lower for term in EXCLUDE_TERMS):
        return False

    if (
        "filament" in name_lower
        or "filaman" in name_lower
        or "1.75" in name_lower
        or "1,75" in name_lower
    ):
        return True

    for _, pattern in MATERIAL_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return True

    return False


def detect_color(name: str) -> str:
    name_lower = name.casefold()

    for pattern, color in COLOR_MAPPING:
        if re.search(pattern, name_lower, re.IGNORECASE):
            return color

    return "Gri" if "beton" in name_lower else "Mavi"


def detect_material(name: str) -> str:
    for label, pattern in MATERIAL_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return label

    return "PLA"


def detect_brand(name: str, fallback_source: str) -> str:
    for brand in KNOWN_BRANDS:
        if brand.casefold() in name.casefold():
            return brand

    return fallback_source


def save_items(items: list[dict]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    count = 0

    with db() as connection:
        for product in items:
            name = product.get("name", "").strip()
            price = product.get("price")
            image = product.get("image", "").strip()

            if (
                not name
                or price is None
                or price <= 0
                or not image
                or "blank" in image.lower()
                or not is_valid(name)
            ):
                continue

            source = product.get("source", "").strip()
            brand = detect_brand(name, source)
            material = detect_material(name)
            color = detect_color(name)

            try:
                connection.execute(
                    """
                    INSERT INTO products (
                        source,
                        external_id,
                        name,
                        brand,
                        material,
                        color,
                        weight_g,
                        price,
                        old_price,
                        in_stock,
                        url,
                        image,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, url) DO UPDATE SET
                        external_id = excluded.external_id,
                        name = excluded.name,
                        brand = excluded.brand,
                        material = excluded.material,
                        color = excluded.color,
                        weight_g = excluded.weight_g,
                        price = excluded.price,
                        old_price = excluded.old_price,
                        in_stock = excluded.in_stock,
                        image = excluded.image,
                        updated_at = excluded.updated_at
                    """,
                    (
                        source,
                        str(product.get("external_id", "")),
                        name,
                        brand,
                        material,
                        color,
                        product.get("weight_g", 1000),
                        price,
                        product.get("old_price"),
                        int(product.get("in_stock", 1)),
                        product.get("url"),
                        image,
                        now,
                    ),
                )

                count += 1

            except sqlite3.Error as error:
                print(
                    f"[Veritabanı] {name} kaydedilemedi: {error}",
                    flush=True,
                )

    return count


def fetch_porima() -> list[dict]:
    items = []
    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
    )

    for page_number in range(1, 20):
        url = (
            "https://porima3d.com/products.json"
            f"?page={page_number}&limit=250"
        )

        try:
            response = session.get(url, timeout=20)

            if response.status_code != 200:
                break

            products = response.json().get("products", [])

            if not products:
                break

            for product in products:
                title = product.get("title", "").strip()
                images = product.get("images") or []
                default_image = images[0].get("src") if images else ""

                for variant in product.get("variants", []):
                    if not variant.get("available"):
                        continue

                    variant_title = variant.get("title", "").strip()

                    full_name = title

                    if (
                        variant_title
                        and variant_title.casefold() != "default title"
                    ):
                        full_name = f"{title} {variant_title}"

                    if not is_valid(full_name):
                        continue

                    price = clean_price(variant.get("price"))

                    if price is None or price <= 0:
                        continue

                    featured_image = variant.get("featured_image") or {}
                    variant_image = (
                        featured_image.get("src") or default_image
                    )

                    if (
                        variant_image
                        and variant_image.startswith("//")
                    ):
                        variant_image = "https:" + variant_image

                    items.append(
                        {
                            "source": "Porima 3D",
                            "external_id": str(variant.get("id", "")),
                            "name": full_name,
                            "price": price,
                            "old_price": clean_price(
                                variant.get("compare_at_price")
                            ),
                            "in_stock": 1,
                            "weight_g": 1000,
                            "url": (
                                "https://porima3d.com/products/"
                                f"{product.get('handle')}"
                                f"?variant={variant.get('id')}"
                            ),
                            "image": variant_image,
                        }
                    )

        except (
            requests.RequestException,
            ValueError,
            KeyError,
        ) as error:
            print(
                f"[Porima 3D] Sayfa {page_number} alınamadı: "
                f"{error}",
                flush=True,
            )
            break

    return items


def fetch_robotistan() -> list[dict]:
    items = []
    seen_urls = set()

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Chromium";v="128", "Not=A?Brand";v="24", "Google Chrome";v="128"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    base_url = "https://www.robotistan.com"

    for page_number in range(1, 101):
        try:
            page_url = (
                f"{base_url}/3d-filament?pg={page_number}"
            )

            response = session.get(page_url, timeout=25)
            if response.status_code != 200:
                print(f"[Robotistan] HTTP {response.status_code} alındı, durduruluyor.", flush=True)
                break

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            cards = soup.select(
                ".product-item, "
                ".showProductScheme, "
                ".productItem, "
                "[class*='product-box'], "
                ".product_box, "
                ".ItemOrj"
            )

            if not cards:
                print(
                    f"[Robotistan] Sayfa {page_number}: "
                    "ürün kartı bulunamadı, tarama tamamlandı.",
                    flush=True,
                )
                break

            new_products_on_page = 0

            for card in cards:
                title_element = card.select_one(
                    ".product-title, "
                    ".productName, "
                    "h3, "
                    "a.title, "
                    ".p-name, "
                    ".product-name"
                )

                if not title_element:
                    continue

                title = title_element.get_text(
                    " ",
                    strip=True,
                )

                if not title or not is_valid(title):
                    continue

                if (
                    title_element.name == "a"
                    and title_element.get("href")
                ):
                    link_element = title_element
                else:
                    link_element = title_element.find(
                        "a",
                        href=True,
                    )

                if not link_element:
                    link_element = card.select_one(
                        "a.product-link[href], "
                        "a[href*='filament'], "
                        "a[href]"
                    )

                if not link_element:
                    continue

                raw_url = link_element.get("href", "").strip()

                if not raw_url:
                    continue

                product_url = urljoin(base_url, raw_url)
                normalized_url = product_url.split("?")[0].rstrip("/")

                if normalized_url in seen_urls:
                    continue

                price_element = card.select_one(
                    ".product-price, "
                    ".current-price, "
                    ".price, "
                    ".p-price, "
                    ".product-price-new"
                )

                price = None

                if price_element:
                    price = clean_price(
                        price_element.get_text(
                            " ",
                            strip=True,
                        )
                    )

                if price is None or price <= 0:
                    continue

                image_element = card.find("img")
                image_url = ""

                if image_element:
                    raw_image = (
                        image_element.get("data-original")
                        or image_element.get("data-src")
                        or image_element.get("data-lazy")
                        or image_element.get("src")
                        or ""
                    ).strip()

                    if raw_image:
                        if raw_image.startswith("//"):
                            full_img_url = "https:" + raw_image
                        elif raw_image.startswith("http"):
                            full_img_url = raw_image
                        else:
                            full_img_url = urljoin(base_url, raw_image)

                        image_url = f"https://wsrv.nl/?url={full_img_url}"

                seen_urls.add(normalized_url)

                items.append(
                    {
                        "source": "Robotistan",
                        "external_id": (
                            normalized_url
                            .rstrip("/")
                            .split("/")[-1]
                        ),
                        "name": title,
                        "price": price,
                        "old_price": None,
                        "in_stock": 1,
                        "weight_g": 1000,
                        "url": product_url,
                        "image": image_url,
                    }
                )

                new_products_on_page += 1

            print(
                f"[Robotistan] Sayfa {page_number}: "
                f"{new_products_on_page} yeni filament",
                flush=True,
            )

            if new_products_on_page == 0:
                print(
                    "[Robotistan] Yeni ürün kalmadı, "
                    "tarama tamamlandı.",
                    flush=True,
                )
                break

        except requests.RequestException as error:
            print(
                f"[Robotistan] Sayfa {page_number} alınamadı: "
                f"{error}",
                flush=True,
            )
            break

    print(
        f"[Robotistan] Toplam {len(items)} "
        "benzersiz filament bulundu.",
        flush=True,
    )

    return items


# SITEMAP BAZLI GÜVENİLİR 3DCİM SCRAPER'I
def fetch_3dcim() -> list[dict]:
    items = []
    seen_urls = set()
    base_url = "https://www.3dcim.com"

    sitemap_urls = [
        f"{base_url}/UrunSitemap.xml",
        f"{base_url}/sitemap.xml",
    ]

    target_links = set()

    for sm_url in sitemap_urls:
        try:
            res = curl_requests.get(sm_url, impersonate="chrome", timeout=15)
            if res.status_code == 200:
                urls_found = re.findall(r'<loc>(https?://www\.3dcim\.com/[^<]+)</loc>', res.text)
                for u in urls_found:
                    u_lower = u.lower()
                    if ("filament" in u_lower or "esun" in u_lower or "porima" in u_lower) and not u_lower.endswith(".xml"):
                        target_links.add(u)
                if target_links:
                    break
        except Exception as err:
            print(f"[3dcim Debug] Sitemap Hatası: {err}", flush=True)

    if not target_links:
        print("[3dcim] Sitemap bulunamadı, varsayılan sayfalara geçiliyor.", flush=True)
        categories = [
            "/3d-yazici-filament-cesitleri",
            "/esun-pla-basic-filament",
            "/esun-pla-plus-filamentler",
            "/pla-filamentler",
            "/petg-filamentler",
        ]
        for c in categories:
            target_links.add(f"{base_url}{c}")

    for product_link in list(target_links)[:300]:
        try:
            resp = curl_requests.get(
                product_link,
                impersonate="chrome",
                timeout=12,
                headers={"Accept-Language": "tr-TR,tr;q=0.9"},
            )

            if resp.status_code != 200:
                continue

            html = resp.text

            # 1. Yöntem: Detay Sayfası İçi JS/JSON Objesinden Tüm Varyantları / Ürünü Çekme
            detail_models = re.findall(r'var\040ProductDetailModel\s*=\s*(\{.*?\});', html, re.DOTALL) or \
                            re.findall(r'var\040UrunDetay\s*=\s*(\{.*?\});', html, re.DOTALL)

            if detail_models:
                try:
                    p_data = json.loads(detail_models[0])
                    title = p_data.get("Title") or p_data.get("UrunAdi") or ""
                    price = clean_price(p_data.get("Price") or p_data.get("SatisFiyati"))
                    in_stock = p_data.get("InStock", True) and not p.get("IsOutStock", False)
                    img = p_data.get("DefaultImage") or p_data.get("Resim") or ""

                    norm_url = product_link.split("?")[0].rstrip("/")

                    if title and price and price > 0 and in_stock and norm_url not in seen_urls and is_valid(title):
                        if img:
                            if img.startswith("//"): img = "https:" + img
                            elif not img.startswith("http"): img = urljoin(base_url, img)
                            img = f"https://wsrv.nl/?url={img}"

                        if img and "blank" not in img.lower():
                            seen_urls.add(norm_url)
                            items.append({
                                "source": "3dcim",
                                "external_id": norm_url.split("/")[-1],
                                "name": title,
                                "price": price,
                                "old_price": None,
                                "in_stock": 1,
                                "weight_g": 1000,
                                "url": product_link,
                                "image": img,
                            })
                        continue
                except Exception:
                    pass

            # 2. Yöntem: HTML Parsing (Ürün Kartları ya da Detay Sayfası)
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select(".product-item, .productItem, .ItemOrj, [class*='product-box'], .p-card")

            if not cards:
                # Doğrudan detay sayfası ise
                card_text = soup.get_text(" ", strip=True).lower()
                if "stokta yok" in card_text or "tükendi" in card_text:
                    continue

                title_el = soup.select_one("h1, .product-name, .productTitle")
                price_el = soup.select_one(".product-price, .current-price, .price, .p-price")
                img_el = soup.select_one(".product-image img, #imgUrunResmi, img[data-src]")

                if not title_el or not price_el:
                    continue

                title = title_el.get_text(" ", strip=True)
                price = clean_price(price_el.get_text(" ", strip=True))
                norm_url = product_link.split("?")[0].rstrip("/")

                if not price or price <= 0 or norm_url in seen_urls or not is_valid(title):
                    continue

                img_url = ""
                if img_el:
                    raw_img = img_el.get("data-src") or img_el.get("src") or ""
                    if raw_img and "blank" not in raw_img.lower() and not raw_img.endswith(".svg"):
                        if raw_img.startswith("//"): raw_img = "https:" + raw_img
                        elif not raw_img.startswith("http"): raw_img = urljoin(base_url, raw_img)
                        img_url = f"https://wsrv.nl/?url={raw_img}"

                if not img_url:
                    continue

                seen_urls.add(norm_url)
                items.append({
                    "source": "3dcim",
                    "external_id": norm_url.split("/")[-1],
                    "name": title,
                    "price": price,
                    "old_price": None,
                    "in_stock": 1,
                    "weight_g": 1000,
                    "url": product_link,
                    "image": img_url,
                })
            else:
                for card in cards:
                    card_text = card.get_text(" ", strip=True).lower()
                    if "stokta yok" in card_text or "tükendi" in card_text:
                        continue

                    title_el = card.select_one(".product-title, .productName, h3, a.title, .p-name")
                    link_el = card.find("a", href=True)
                    price_el = card.select_one(".product-price, .current-price, .price, .p-price")

                    if not title_el or not link_el:
                        continue

                    title = title_el.get_text(" ", strip=True)
                    if not title or not is_valid(title):
                        continue

                    raw_href = link_el["href"].strip()
                    p_url = urljoin(base_url, raw_href)
                    norm_url = p_url.split("?")[0].rstrip("/")

                    if norm_url in seen_urls:
                        continue

                    price = clean_price(price_el.get_text(" ", strip=True)) if price_el else None
                    if not price or price <= 0:
                        continue

                    img_el = card.find("img")
                    img_url = ""
                    if img_el:
                        raw_img = (
                            img_el.get("data-original")
                            or img_el.get("data-src")
                            or img_el.get("data-lazy")
                            or img_el.get("src")
                            or ""
                        ).strip()

                        if raw_img and "blank" not in raw_img.lower() and not raw_img.endswith(".svg"):
                            if raw_img.startswith("//"): raw_img = "https:" + raw_img
                            elif not raw_img.startswith("http"): raw_img = urljoin(base_url, raw_img)
                            img_url = f"https://wsrv.nl/?url={raw_img}"

                    if not img_url:
                        continue

                    seen_urls.add(norm_url)
                    items.append({
                        "source": "3dcim",
                        "external_id": norm_url.split("/")[-1],
                        "name": title,
                        "price": price,
                        "old_price": None,
                        "in_stock": 1,
                        "weight_g": 1000,
                        "url": p_url,
                        "image": img_url,
                    })

        except Exception as e:
            continue

    print(f"[3dcim] Toplam {len(items)} stoklu ürün çekildi.", flush=True)
    return items


def run_update():
    if not update_lock.acquire(blocking=False):
        print(
            "Başka bir güncelleme zaten çalışıyor.",
            flush=True,
        )
        return

    print(">>> TARAMA BAŞLADI <<<", flush=True)

    try:
        with db() as connection:
            for term in EXCLUDE_TERMS:
                connection.execute(
                    """
                    DELETE FROM products
                    WHERE LOWER(name) LIKE ?
                    """,
                    (f"%{term.casefold()}%",),
                )

        # 1. Porima Taraması
        porima_items = fetch_porima()
        if porima_items:
            with db() as connection:
                connection.execute("DELETE FROM products WHERE source = 'Porima 3D'")
            porima_count = save_items(porima_items)
            print(f"[Porima 3D] -> {porima_count} ürün kaydedildi.", flush=True)

        # 2. Robotistan Taraması
        robotistan_items = fetch_robotistan()
        if robotistan_items:
            with db() as connection:
                connection.execute("DELETE FROM products WHERE source = 'Robotistan'")
            robotistan_count = save_items(robotistan_items)
            print(f"[Robotistan] -> {robotistan_count} benzersiz ürün kaydedildi.", flush=True)

        # 3. 3dcim Taraması
        tcim_items = fetch_3dcim()
        if tcim_items:
            with db() as connection:
                connection.execute("DELETE FROM products WHERE source = '3dcim'")
            tcim_count = save_items(tcim_items)
            print(f"[3dcim] -> {tcim_count} benzersiz ürün kaydedildi.", flush=True)

        with db() as connection:
            connection.execute(
                "DELETE FROM products WHERE image IS NULL OR image = '' OR image LIKE '%blank%'"
            )

        print(">>> TARAMA TAMAMLANDI <<<", flush=True)

    except Exception as error:
        print(f">>> GÜNCELLEME HATASI: {error} <<<", flush=True)

    finally:
        update_lock.release()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/products")
def products():
    search_query = request.args.get("q", "").strip()
    filters = [
        "in_stock = 1",
        "image IS NOT NULL",
        "image != ''",
        "image NOT LIKE '%blank%'",
    ]
    parameters = []

    for field in ("brand", "material", "color", "source"):
        value = request.args.get(field, "").strip()
        if not value:
            continue

        if field == "source" and "porima" in value.casefold():
            filters.append("LOWER(source) LIKE '%porima%'")
        elif field == "source" and "robotistan" in value.casefold():
            filters.append("LOWER(source) LIKE '%robotistan%'")
        elif field == "source" and "3dcim" in value.casefold():
            filters.append("LOWER(source) LIKE '%3dcim%'")
        else:
            filters.append(f"{field} = ?")
            parameters.append(value)

    if search_query:
        filters.append(
            """
            (
                name LIKE ?
                OR brand LIKE ?
                OR material LIKE ?
                OR color LIKE ?
            )
            """
        )

        query_value = f"%{search_query}%"
        parameters.extend([query_value] * 4)

    sql = """
        SELECT *,
               CASE
                   WHEN weight_g > 0
                   THEN price * 1000.0 / weight_g
               END AS kg_price
        FROM products
        WHERE {}
        ORDER BY price ASC
        LIMIT 5000
    """.format(" AND ".join(filters))

    with db() as connection:
        rows = [dict(row) for row in connection.execute(sql, parameters)]

    return jsonify(rows)


@app.get("/api/filters")
def filters():
    with db() as connection:
        values = {}

        for field in ("brand", "material", "color", "source"):
            sql = f"""
                SELECT DISTINCT {field}
                FROM products
                WHERE {field} IS NOT NULL
                  AND {field} != ''
                  AND in_stock = 1
                  AND image IS NOT NULL AND image != ''
                ORDER BY {field}
            """

            values[field] = [row[0] for row in connection.execute(sql)]

        values["count"] = connection.execute(
            """
            SELECT COUNT(*)
            FROM products
            WHERE in_stock = 1 AND image IS NOT NULL AND image != ''
            """
        ).fetchone()[0]

    return jsonify(values)


@app.post("/api/update")
def update():
    if update_lock.locked():
        return jsonify(
            {
                "ok": False,
                "message": "Güncelleme zaten çalışıyor.",
            }
        )

    threading.Thread(
        target=run_update,
        daemon=True,
    ).start()

    return jsonify(
        {
            "ok": True,
            "message": "Güncelleme başlatıldı.",
        }
    )


@app.get("/api/status")
def status():
    return jsonify({"running": update_lock.locked()})


if __name__ == "__main__":
    init_db()

    threading.Thread(
        target=run_update,
        daemon=True,
    ).start()

    port = int(os.environ.get("PORT", "10000"))

    serve(
        app,
        host="0.0.0.0",
        port=port,
        threads=8,
    )
