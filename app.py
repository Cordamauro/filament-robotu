def scrape_shopify(source: dict) -> list[dict]:
    items = []
    page = 1
    
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'tr-TR,tr;q=0.9'
    })

    raw_url = source["url"].rstrip("/")
    base_url = raw_url.split("/collections")[0]

    # Porima URL'si zaten /collections/filamentler içerdiği için doğrudan /products.json ekliyoruz
    if "/collections/" in raw_url:
        json_endpoint = f"{raw_url}/products.json"
    else:
        json_endpoint = f"{base_url}/products.json"

    while page <= 10:
        req_url = f"{json_endpoint}?page={page}&limit=250"
        try:
            res = session.get(req_url, timeout=15)
            if res.status_code != 200: 
                break
                
            products = res.json().get("products", [])
            if not products: 
                break
            
            for p in products:
                title = p.get('title', '')
                images = p.get("images") or []
                img_map = {img.get("id"): img.get("src") for img in images if img.get("id") and img.get("src")}
                default_img = images[0].get("src") if images else ""

                for v in p.get("variants", []):
                    if not v.get("available"):
                        continue

                    v_title = v.get('title', '')
                    full_name = f"{title} {v_title if v_title != 'Default Title' else ''}"
                    
                    if not is_valid_filament(full_name): 
                        continue

                    v_img_src = None
                    v_img_id = v.get("image_id")
                    if v_img_id:
                        v_img_src = img_map.get(v_img_id)

                    if not v_img_src and v.get("featured_image"):
                        v_img_src = v.get("featured_image", {}).get("src")

                    if not v_img_src:
                        v_img_src = default_img

                    if v_img_src and v_img_src.startswith("//"):
                        v_img_src = "https:" + v_img_src

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
        except Exception as e:
            print(f"[{source['name']}] Hata: {e}", flush=True)
            break
    return items
