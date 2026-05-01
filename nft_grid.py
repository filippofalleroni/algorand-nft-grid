#!/usr/bin/env python3
"""
nft_grid.py — Algorand NFT Wall Generator

Generates a square NxN grid image from any Algorand wallet address or NFD.

Dependencies:
    pip install Pillow requests base58

Usage:
    python nft_grid.py [address_or_nfd] [--size N] [--cell PX] [--gap PX] [--out FILE]

Examples:
    python nft_grid.py gloot.algo
    python nft_grid.py gloot.algo --size 5
    python nft_grid.py RWBL6NFN53EH5X3U7LNZW73HNJY5UCSLB2MCCYU2FEX76HEVWTFGBXNYMQ
"""

import argparse
import base64
import os
import re
import sys
import time
from io import BytesIO

import base58
import requests
from PIL import Image, ImageDraw, ImageFont

# ── Constants ─────────────────────────────────────────────────────────────────

ALGONODE_IDX  = "https://mainnet-idx.algonode.cloud/v2"
ALGONODE_API  = "https://mainnet-api.algonode.cloud/v2"
NFD_API       = "https://api.nf.domains/nfd"

IPFS_GATEWAYS = [
    "https://ipfs.algonode.xyz/ipfs/",   # Algonode — best for Algorand NFTs
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
    "https://nftstorage.link/ipfs/",
    "https://w3s.link/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
]

HEADERS = {"User-Agent": "AlgoNFTGrid/1.0"}
TIMEOUT = 25
SKIP_UNIT_NAMES = {"ALGO", "USDC", "USDT", "goUSD", "goETH", "goBTC", "wALGO", "VEST"}

# ── IPFS / CID utilities ──────────────────────────────────────────────────────

def _algo_addr_to_pk(addr: str) -> bytes:
    padding = "=" * (-len(addr) % 8)
    return base64.b32decode(addr + padding)[:32]

def cid_v0(reserve: str) -> str:
    pk = _algo_addr_to_pk(reserve)
    return base58.b58encode(bytes([0x12, 0x20]) + pk).decode()

def cid_v1(reserve: str) -> str:
    pk = _algo_addr_to_pk(reserve)
    raw = bytes([0x01, 0x55, 0x12, 0x20]) + pk
    return "b" + base64.b32encode(raw).decode().lower().rstrip("=")

def fetch_url(url: str, as_json: bool = False, timeout: int = TIMEOUT):
    if url.startswith("ipfs://") or "/ipfs/" in url:
        cid = (url[7:] if url.startswith("ipfs://") else url.split("/ipfs/", 1)[-1])
        cid = cid.split("#")[0].rstrip("/")
        for gw in IPFS_GATEWAYS:
            try:
                r = requests.get(gw + cid, headers=HEADERS, timeout=timeout)
                if r.status_code == 200:
                    return r.json() if as_json else r.content
            except Exception:
                pass
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json() if as_json else r.content
    except Exception:
        pass
    return None

# ── NFD resolution ────────────────────────────────────────────────────────────

def resolve_nfd(nfd: str) -> str:
    print(f"  Looking up {nfd} ...")
    try:
        r = requests.get(f"{NFD_API}/{nfd}", headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            addr = data.get("depositAccount") or data.get("caAlgo", [None])[0]
            if addr:
                print(f"  Found wallet address.")
                return addr
    except Exception:
        pass
    sys.exit(f"\n  Oops! We couldn't find a wallet for '{nfd}'. Please check and try again.")

# ── Wallet assets ─────────────────────────────────────────────────────────────

def get_wallet_assets(address: str) -> list[dict]:
    print(f"  Connecting to the Algorand blockchain ...")
    r = requests.get(
        f"{ALGONODE_API}/accounts/{address}?exclude=none",
        headers=HEADERS, timeout=TIMEOUT
    )
    r.raise_for_status()
    assets = r.json().get("assets", [])
    return [a for a in assets if a.get("amount", 0) > 0]

# ── Asset metadata ────────────────────────────────────────────────────────────

def fetch_asset_params(asset_id: int) -> dict:
    r = requests.get(f"{ALGONODE_IDX}/assets/{asset_id}", headers=HEADERS, timeout=TIMEOUT)
    if r.status_code == 200:
        return r.json().get("asset", {}).get("params", {})
    return {}

def is_nft(params: dict) -> bool:
    total    = params.get("total", 0)
    decimals = params.get("decimals", 1)
    unit     = params.get("unit-name", "").upper()
    if unit in SKIP_UNIT_NAMES:
        return False
    return total <= 100 and decimals == 0

# ── Image URL resolution ──────────────────────────────────────────────────────

def resolve_image_url(params: dict) -> str | None:
    url     = params.get("url", "")
    reserve = params.get("reserve", "")

    if "template-ipfs" in url and reserve:
        cid  = cid_v1(reserve) if "ipfscid:1" in url else cid_v0(reserve)
        meta = fetch_url(f"ipfs://{cid}", as_json=True)
        if meta:
            img = meta.get("image") or meta.get("image_url") or meta.get("animation_url") or ""
            return img if img else None
        return None

    if url.startswith("ipfs://") and "#arc3" in url:
        meta = fetch_url(url, as_json=True)
        if meta:
            img = meta.get("image") or meta.get("image_url") or ""
            return img if img else None
        return None

    if url.startswith("https://ipfs.io/ipfs/") and url.endswith("e"):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                meta = r.json()
                img = meta.get("image") or meta.get("image_url") or ""
                return img if img else url
        except Exception:
            pass
        return url

    if url.startswith("ipfs://"):
        return url

    if url.startswith("https://"):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                ct = r.headers.get("content-type", "")
                if "json" in ct:
                    meta = r.json()
                    img = meta.get("image") or meta.get("image_url") or ""
                    return img if img else None
                elif ct.startswith("image/"):
                    return url
        except Exception:
            pass
        return url

    return None

# ── Image download ────────────────────────────────────────────────────────────

def download_image(image_url: str) -> Image.Image | None:
    data = fetch_url(image_url)
    if data:
        try:
            return Image.open(BytesIO(data)).convert("RGBA")
        except Exception:
            pass
    return None

# ── Placeholder ───────────────────────────────────────────────────────────────

def make_placeholder(size: int = 500) -> Image.Image:
    img  = Image.new("RGB", (size, size), (18, 18, 18))
    draw = ImageDraw.Draw(img)

    border, dash, gap = 12, 18, 10
    col_border = (60, 60, 60)

    def dashed_line(x0, y0, x1, y1):
        if x0 == x1:
            y = y0
            while y < y1:
                draw.line([(x0, y), (x0, min(y + dash, y1))], fill=col_border, width=2)
                y += dash + gap
        else:
            x = x0
            while x < x1:
                draw.line([(x, y0), (min(x + dash, x1), y0)], fill=col_border, width=2)
                x += dash + gap

    dashed_line(border, border, size - border, border)
    dashed_line(border, size - border, size - border, size - border)
    dashed_line(border, border, border, size - border)
    dashed_line(size - border, border, size - border, size - border)
    for cx, cy in [(border, border), (size-border, border), (border, size-border), (size-border, size-border)]:
        draw.rectangle([cx-3, cy-3, cx+3, cy+3], fill=(80, 80, 80))

    font = None
    for fp in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Courier.ttc",
        "/Library/Fonts/Courier New.ttf",
    ]:
        if os.path.exists(fp):
            font = ImageFont.truetype(fp, max(size // 10, 18))
            break
    if font is None:
        font = ImageFont.load_default()

    lines  = ["ALGORAND", "NFT GRID"]
    line_h = size // 8
    y      = (size - len(lines) * line_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw   = bbox[2] - bbox[0]
        draw.text(((size - tw) // 2, y), line, fill=(70, 70, 70), font=font)
        y += line_h

    return img

# ── Grid composition ──────────────────────────────────────────────────────────

def make_grid(images: list[Image.Image], names: list[str], cols: int,
              cell_size: int, gap: int, bg: tuple = (15, 15, 15)) -> Image.Image:
    rows   = (len(images) + cols - 1) // cols
    W      = cols * cell_size + (cols + 1) * gap
    H      = rows * cell_size + (rows + 1) * gap
    canvas = Image.new("RGB", (W, H), bg)
    for idx, img in enumerate(images):
        thumb = img.convert("RGB").resize((cell_size, cell_size), Image.LANCZOS)
        col   = idx % cols
        row   = idx // cols
        x     = gap + col * (cell_size + gap)
        y     = gap + row * (cell_size + gap)
        canvas.paste(thumb, (x, y))
    return canvas

# ── Grid size picker ──────────────────────────────────────────────────────────

def pick_grid_size(total_nfts: int, forced: int | None, wallet_input: str = "") -> int:
    OPTIONS = list(range(2, 11))

    if forced is not None:
        forced = max(2, min(10, forced))
        return forced

    print(f"\n  This wallet contains {total_nfts} NFTs.")
    print("  Choose the size of your NFT wall.\n")
    print("  If the wallet has fewer NFTs than the grid,")
    print("  the empty slots will show a placeholder image.\n")

    for i, s in enumerate(OPTIONS, 1):
        needed = s * s
        note = "  (some slots will use placeholder)" if needed > total_nfts else ""
        print(f"  [{i:2d}]  {s}x{s}  =  {needed:3d} images{note}")

    print(f"\n  Tip: to skip this question next time, run:")
    print(f"       python3 nft_grid.py {wallet_input} --size 5\n")

    while True:
        try:
            choice = input(f"  Your choice [1-{len(OPTIONS)}]: ").strip()
            n = int(choice)
            if 1 <= n <= len(OPTIONS):
                return OPTIONS[n - 1]
            print(f"  Please enter a number between 1 and {len(OPTIONS)}")
        except (ValueError, KeyboardInterrupt):
            print("  Please enter a valid number")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a square NFT wall image from any Algorand wallet or NFD."
    )
    parser.add_argument("wallet",  nargs="?", default=None,
                        help="Algorand address or NFD (e.g. gloot.algo). You will be asked if omitted.")
    parser.add_argument("--size",  type=int,   default=None,
                        help="Grid size (e.g. 5 for a 5x5 wall). You will be asked if omitted.")
    parser.add_argument("--cell",  type=int,   default=500,
                        help="Image quality in pixels per cell (default: 500)")
    parser.add_argument("--gap",   type=int,   default=4,
                        help="Space between images in pixels (default: 4)")
    parser.add_argument("--out",   default=None,
                        help="Output file path (default: saves to your Desktop)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Delay between downloads in seconds (default: 0.3)")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════╗")
    print("║    Algorand NFT Wall Generator       ║")
    print("╚══════════════════════════════════════╝\n")

    # 1. Ask for wallet if not provided
    if args.wallet:
        wallet_input = args.wallet.strip()
    else:
        wallet_input = input("  Enter your wallet address or name (e.g. gloot.algo): ").strip()
        if not wallet_input:
            sys.exit("\n  No wallet entered. Please try again.")

    print()

    # 2. Resolve NFD to address if needed
    wallet = wallet_input
    if wallet.endswith(".algo") or (len(wallet) < 58 and "." in wallet):
        wallet = resolve_nfd(wallet_input)

    wallet_label = wallet_input

    # 3. Fetch wallet assets
    wallet_assets = get_wallet_assets(wallet)
    print(f"  Scanning for NFTs ...")

    # 4. Filter NFT candidates
    nft_candidates = []
    for item in wallet_assets:
        aid    = item["asset-id"]
        params = fetch_asset_params(aid)
        if params and is_nft(params):
            nft_candidates.append((aid, params))
        time.sleep(0.05)

    total_nfts = len(nft_candidates)

    if not nft_candidates:
        sys.exit("\n  No NFTs found in this wallet.")

    # 5. Pick grid size
    grid_size = pick_grid_size(total_nfts, args.size, wallet_input)
    max_nfts  = grid_size * grid_size
    cell_size = args.cell

    # 6. Resolve image URLs
    print(f"\n  Getting image links for your NFTs ...")
    nfts_resolved = []
    for aid, params in nft_candidates:
        img_url = resolve_image_url(params)
        if img_url:
            nfts_resolved.append({
                "id":        aid,
                "name":      params.get("name", f"#{aid}"),
                "image_url": img_url,
            })
            if len(nfts_resolved) >= max_nfts:
                break
        time.sleep(0.1)

    # Fill remaining slots with placeholders if needed
    while len(nfts_resolved) < max_nfts:
        nfts_resolved.append({"id": None, "name": "—", "image_url": None})

    to_render = nfts_resolved[:max_nfts]
    print(f"  Ready! Downloading {min(len([n for n in to_render if n['image_url']]), max_nfts)} images ...\n")

    # 7. Download images
    images, names = [], []
    total = len(to_render)
    for i, nft in enumerate(to_render, 1):
        print(f"  Downloading image {i} of {total} ...", end="\r")
        if nft["image_url"]:
            img = download_image(nft["image_url"])
        else:
            img = None
        if img:
            images.append(img)
        else:
            images.append(make_placeholder(cell_size))
        names.append(nft["name"])
        time.sleep(args.delay)

    print(f"  All images downloaded!              \n")

    # 8. Compose grid
    print(f"  Building your {grid_size}x{grid_size} NFT wall ...")
    grid = make_grid(images, names, cols=grid_size, cell_size=cell_size, gap=args.gap)

    # 9. Save output
    if args.out:
        out_path = args.out
    else:
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", wallet_label)[:30]
        desktop   = os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(desktop, exist_ok=True)
        out_path  = os.path.join(desktop, f"nft_grid_{safe_name}.png")

    grid.save(out_path, "PNG", optimize=True)
    w, h = grid.size

    # 10. Save 1080x1080 social version
    social_path = out_path.replace(".png", "_1080.png")
    social = grid.resize((1080, 1080), Image.LANCZOS)
    social.save(social_path, "PNG", optimize=True)

    print(f"\n  ✅  Done! Your NFT wall has been saved to your Desktop.")
    print(f"\n  Full resolution:  {os.path.basename(out_path)}  ({w}×{h} px)")
    print(f"  Social (1080px):  {os.path.basename(social_path)}  — ready for Instagram & X\n")


if __name__ == "__main__":
    main()
