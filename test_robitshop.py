import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

EXCLUDE_TERMS = [
    "hub", "splitter", "buffer", "feeder", "cutter", "tube", "replacement", "ptfe", "kesici", "borusu", "bıçak", "makas",
    "nozzle", "hotend", "extruder", "kurutucu", "dryer", "dry box", "vakum", "poşet", "reçine", "resin", "3d kalem", 
    "spatula", "sprey", "rulman", "soğutucu", "fan", "step motor", "baskı tablası", "peı", "tabla", "sensör", "sensor", 
    "yazıcı", "printer", "somun", "vida", "kayış", "kasnak", "fişek", "ısıtıcı", "termistör", "yapıştırıcı", "modül"
]

def is_valid_filament(name: str) -> bool:
    name_lower = name.lower()
    if any(term in name_lower for term in EXCLUDE_TERMS):
        return False
    if "filament" in name_lower:
        return True
    materials = ["pla", "petg", "abs", "tpu", "asa", "pva", "nylon", "carbon", "pc", "hips"]
    for mat in materials:
        if re.search(rf"\b{mat}\b", name_lower):
            return True
    return False

def parse_price(text: str) -> float | None:
    if not text:
        return None
    # USD veya TL cinsinden sayısal değeri çek
    digits = re.sub(r"[^\d,.]", "", text)
    if not digits:
        return None
    if "," in digits and "." in digits:
        digits = digits.replace(".", "").replace(",", ".")
    elif "," in digits:
        digits = digits.replace(",", ".")
    try:
        val = float(digits)
        return val if val > 0 else None
    except ValueError:
        return None

def run_test():
    print(">>> ROBITSHOP TESTİ BAŞLATILIYOR... <<<\n")
    
    # Robitshop Filament Kategorileri
    categories = [
        "https://www.robitshop.com/kategori/filament-1",
        "https://www.robitshop.com/kategori/pla-filamentler",
        "https://www.robitshop.com/kategori/abs-filamentler",
        "https://www.robitshop.com/kategori/pet-g-filament",
        "https://www.robitshop.com/kategori/tpu-flex-filament",
        "https://www.robitshop.com/kategori/ozel-filamentler"
    ]
    
    found_products = {}
    esun_count = 0

    for cat_url in categories:
        page = 1
        while page <= 10:
            target_url = f"{cat_url}?sayfa={page}" if page > 1 else cat_url
            try:
                res = requests.get(target_url, headers=HEADERS, timeout=10)
                if res.status_code != 200:
                    break

                soup = BeautifulSoup(res.text, "html.parser")
                # Ticimax ürün kartlarını bul
                items = soup.find_all("div", class_=re.compile(r"product-item|showcase|ItemOrj", re.I))
                
                if not items:
                    break

                added_in_page = 0
                for item in items:
                    item_html = str(item).lower()
                    
                    # Stok Kontrolü
                    if "stokta yok" in item_html or "tukendi" in item_html or "tükendi" in item_html:
                        continue

                    title_elem = item.find(["a", "div", "span"], class_=re.compile(r"product-title|productName|title", re.I)) or item.find("a")
                    price_elem = item.find(["span", "div"], class_=re.compile(r"price|fiyat|discountPrice", re.I))
                    
                    if title_elem and price_elem:
                        name = title_elem.get_text(strip=True)
                        if not is_valid_filament(name):
                            continue

                        price = parse_price(price_elem.get_text(strip=True))
                        if not price:
                            continue

                        href = title_elem.get("href") or (item.find("a").get("href") if item.find("a") else "")
                        prod_url = urljoin("https://www.robitshop.com", href) if href else target_url

                        if prod_url not in found_products:
                            found_products[prod_url] = {"name": name, "price": price}
                            added_in_page += 1
                            if "esun" in name.lower():
                                esun_count += 1
                                print(f"[eSun Bulundu] {name} -> {price} TL/USD")

                if added_in_page == 0 and page > 1:
                    break

                page += 1

            except Exception as e:
                print(f"Hata ({target_url}): {e}")
                break

    print("\n" + "="*50)
    print(f"TOPLAM BULUNAN STOKLU FİLAMAN: {len(found_products)} ADET")
    print(f"BULUNAN eSUN FİLAMAN SAYISI : {esun_count} ADET")
    print("="*50)

    # eSun Soğuk Beyaz Kontrolü
    target_found = any("soğuk beyaz" in p["name"].lower() or "soguk beyaz" in p["name"].lower() for p in found_products.values())
    print(f"\nAranan 'Esun Soğuk Beyaz PLA' Listedemı? -> {'EVET (BAŞARILI)' if target_found else 'HAYIR (BAŞARISIZ)'}")

if __name__ == "__main__":
    run_test()
