#!/usr/bin/env python3
"""
nft_grid.py — Algorand NFT Wall Generator

Generates a square NxN grid image from any Algorand wallet address or NFD.

Dependencies:
    pip install Pillow requests base58

Usage:
    python nft_grid.py [address_or_nfd] [--size N] [--cell PX] [--gap PX] [--out FILE]

Examples:
    python nft_grid.py RWBL6NFN53EH5X3U7LNZW73HNJY5UCSLB2MCCYU2FEX76HEVWTFGBXNYMQ
    python nft_grid.py pippo.algo --size 4 --cell 600 --out my_wall.png
    python nft_grid.py famverse.algo --size 5 --cell 500 --gap 6
"""

import argparse
import base64
import json
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
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
    "https://nftstorage.link/ipfs/",
    "https://w3s.link/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
]

HEADERS = {"User-Agent": "AlgoNFTGrid/1.0"}
TIMEOUT = 25

# Known fungible tokens to exclude (extend as needed)
SKIP_UNIT_NAMES = {"ALGO", "USDC", "USDT", "goUSD", "goETH", "goBTC", "wALGO", "VEST"}

# ── IPFS / CID utilities ──────────────────────────────────────────────────────

def _algo_addr_to_pk(addr: str) -> bytes:
    """Decode an Algorand address to its 32-byte public key."""
    padding = "=" * (-len(addr) % 8)
    return base64.b32decode(addr + padding)[:32]

def cid_v0(reserve: str) -> str:
    """ARC-19 template ipfscid:0:dag-pb → CIDv0 (Qm…)"""
    pk = _algo_addr_to_pk(reserve)
    return base58.b58encode(bytes([0x12, 0x20]) + pk).decode()

def cid_v1(reserve: str) -> str:
    """ARC-19 template ipfscid:1:raw → CIDv1 (bafy…)"""
    pk = _algo_addr_to_pk(reserve)
    raw = bytes([0x01, 0x55, 0x12, 0x20]) + pk
    return "b" + base64.b32encode(raw).decode().lower().rstrip("=")

def fetch_url(url: str, as_json: bool = False, timeout: int = TIMEOUT):
    """Fetch a URL, trying all IPFS gateways if needed."""
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
    """Resolve an NFD (e.g. pippo.algo) to its Algorand address."""
    print(f"[NFD] Resolving {nfd} …")
    try:
        r = requests.get(f"{NFD_API}/{nfd}", headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            addr = data.get("depositAccount") or data.get("caAlgo", [None])[0]
            if addr:
                print(f"[NFD] → {addr}")
                return addr
    except Exception:
        pass
    sys.exit(f"[ERROR] NFD '{nfd}' could not be resolved.")

# ── Wallet assets ─────────────────────────────────────────────────────────────

def get_wallet_assets(address: str) -> list[dict]:
    """Return all assets with amount > 0 from the wallet."""
    print(f"[Wallet] Fetching assets for {address[:20]}…")
    r = requests.get(
        f"{ALGONODE_API}/accounts/{address}?exclude=none",
        headers=HEADERS, timeout=TIMEOUT
    )
    r.raise_for_status()
    assets = r.json().get("assets", [])
    return [a for a in assets if a.get("amount", 0) > 0]

# ── Asset metadata ────────────────────────────────────────────────────────────

def fetch_asset_params(asset_id: int) -> dict:
    r = requests.get(
        f"{ALGONODE_IDX}/assets/{asset_id}",
        headers=HEADERS, timeout=TIMEOUT
    )
    if r.status_code == 200:
        return r.json().get("asset", {}).get("params", {})
    return {}

def is_nft(params: dict) -> bool:
    """Filter out fungible tokens: NFT = total <= 100 and decimals == 0."""
    total    = params.get("total", 0)
    decimals = params.get("decimals", 1)
    unit     = params.get("unit-name", "").upper()
    if unit in SKIP_UNIT_NAMES:
        return False
    return total <= 100 and decimals == 0

# ── Image URL resolution ──────────────────────────────────────────────────────

def resolve_image_url(params: dict) -> str | None:
    """
    Returns the image URL for an NFT, handling:
    - ARC-3  (ipfs://CID or https://ipfs.io/ipfs/CID  →  JSON metadata  →  image)
    - ARC-19 (template-ipfs://  →  CID from reserve  →  JSON metadata  →  image)
    - ARC-69 (direct image URL in the ASA url parameter)
    """
    url     = params.get("url", "")
    reserve = params.get("reserve", "")

    # ARC-19: template URL with reserve-derived CID
    if "template-ipfs" in url and reserve:
        cid  = cid_v1(reserve) if "ipfscid:1" in url else cid_v0(reserve)
        meta = fetch_url(f"ipfs://{cid}", as_json=True)
        if meta:
            img = meta.get("image") or meta.get("image_url") or meta.get("animation_url") or ""
            return img if img else None
        return None

    # ARC-3: metadata JSON on IPFS (url ends with #arc3)
    if url.startswith("ipfs://") and "#arc3" in url:
        meta = fetch_url(url, as_json=True)
        if meta:
            img = meta.get("image") or meta.get("image_url") or ""
            return img if img else None
        return None

    # HTTPS URL pointing to IPFS JSON metadata
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

    # Direct IPFS image URL (with or without fragment)
    if url.startswith("ipfs://"):
        return url

    # Direct HTTPS URL — could be metadata JSON or image
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

# ── Placeholder for missing images ───────────────────────────────────────────

def make_placeholder(size: int = 500) -> Image.Image:
    """Dark placeholder with dashed border and ALGORAND NFT GRID text."""
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
        print(f"  [{idx+1:02d}/{len(images)}] {names[idx]}")

    return canvas

# ── Grid size picker ──────────────────────────────────────────────────────────

def pick_grid_size(total_nfts: int, forced: int | None) -> int:
    """
    Offers grids from 2×2 up to 10×10, capped by available NFTs.
    If --size is passed via CLI it is used directly.
    """
    MAX_SIDE = 10
    max_side = min(MAX_SIDE, int(total_nfts ** 0.5))

    if forced is not None:
        capped = min(forced, max_side)
        if capped != forced:
            print(f"[Info] --size {forced} capped to {capped} (only {total_nfts} NFTs available)")
        return capped

    options = list(range(2, max_side + 1))

    print(f"\n[Grid] {total_nfts} NFTs found in wallet.")
    print("       Choose grid size:\n")
    for i, s in enumerate(options, 1):
        bar = "+" * s
        print(f"  [{i:2d}]  {s}x{s}  =  {s*s:3d} NFTs   {bar}")
    print(f"\n  [ 0]  Custom size (2-{max_side})")

    while True:
        try:
            choice = input("\nChoice: ").strip()
            n = int(choice)
            if 1 <= n <= len(options):
                return options[n - 1]
            elif n == 0:
                custom = int(input(f"Enter grid side (2-{max_side}): ").strip())
                if 2 <= custom <= max_side:
                    return custom
                print(f"  Out of range (2-{max_side})")
            else:
                print("  Invalid choice")
        except (ValueError, KeyboardInterrupt):
            print("  Please enter a valid number")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate a square NFT wall image from any Algorand wallet or NFD."
    )
    parser.add_argument("wallet",  nargs="?", default=None,
                        help="Algorand address (58 chars) or NFD (e.g. pippo.algo). Prompted interactively if omitted.")
    parser.add_argument("--size",  type=int,   default=None,
                        help="Grid side length (e.g. 5 → 5x5 = 25 NFTs). Interactive menu if omitted.")
    parser.add_argument("--cell",  type=int,   default=500,
                        help="Cell size in pixels (default: 500)")
    parser.add_argument("--gap",   type=int,   default=4,
                        help="Gap between cells in pixels (default: 4)")
    parser.add_argument("--out",   default=None,
                        help="Output file path (default: ~/Desktop/nft_grid_<wallet>.png)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Delay between IPFS requests in seconds (default: 0.3)")
    args = parser.parse_args()

    # 1. Ask for wallet if not provided via CLI
    if args.wallet:
        wallet = args.wallet.strip()
    else:
        print("╔══════════════════════════════════════╗")
        print("║       Algorand NFT Grid Generator    ║")
        print("╚══════════════════════════════════════╝\n")
        wallet = input("Enter Algorand address or NFD (e.g. pippo.algo): ").strip()
        if not wallet:
            sys.exit("[ERROR] No wallet provided.")

    if wallet.endswith(".algo") or (len(wallet) < 58 and "." in wallet):
        wallet = resolve_nfd(wallet)

    # Label for the output filename (NFD or first 8 chars of address)
    wallet_label = args.wallet.strip() if args.wallet else wallet[:8]

    # 2. Fetch wallet assets
    wallet_assets = get_wallet_assets(wallet)
    print(f"[Wallet] {len(wallet_assets)} assets found")

    # 3. Quick scan — filter NFT candidates (no IPFS calls yet)
    print("[Metadata] Scanning wallet for NFTs…")
    nft_candidates = []
    for item in wallet_assets:
        aid    = item["asset-id"]
        params = fetch_asset_params(aid)
        if params and is_nft(params):
            nft_candidates.append((aid, params))
        time.sleep(0.05)

    total_nfts = len(nft_candidates)
    print(f"[NFT] {total_nfts} NFTs found in wallet")

    if not nft_candidates:
        sys.exit("[ERROR] No NFTs found in this wallet.")

    # 4. Pick grid size (interactive or from CLI)
    grid_size = pick_grid_size(total_nfts, args.size)
    max_nfts  = grid_size * grid_size

    # 5. Resolve image URLs — early stop once we have enough NFTs
    print(f"\n[Metadata] Resolving image URLs (target: {max_nfts} NFTs)…")
    nfts_resolved = []
    for aid, params in nft_candidates:
        img_url = resolve_image_url(params)
        if img_url:
            nfts_resolved.append({
                "id":        aid,
                "name":      params.get("name", f"ASA {aid}"),
                "image_url": img_url,
            })
            print(f"  ✓ [{len(nfts_resolved):03d}/{max_nfts}] {params.get('name', aid)}")
            if len(nfts_resolved) >= max_nfts:
                print(f"  … early stop: {max_nfts} NFTs reached")
                break
        else:
            print(f"  - {params.get('name', aid)} (no image URL)")
        time.sleep(0.1)

    print(f"\n[NFT] {len(nfts_resolved)} NFTs ready")

    to_render = nfts_resolved[:max_nfts]
    print(f"[Grid] Rendering {len(to_render)} NFTs in {grid_size}x{grid_size} grid…\n")

    # 6. Download images
    images, names = [], []
    for nft in to_render:
        print(f"  ↓ Downloading: {nft['name']}")
        img = download_image(nft["image_url"])
        if img:
            images.append(img)
            names.append(nft["name"])
        else:
            print(f"    ⚠ Could not download {nft['name']} — using placeholder")
            images.append(make_placeholder(args.cell))
            names.append(nft["name"])
        time.sleep(args.delay)

    # 7. Compose grid
    print(f"\n[Grid] Composing {grid_size}x{grid_size} grid ({args.cell}px per cell)…")
    grid = make_grid(images, names, cols=grid_size, cell_size=args.cell, gap=args.gap)

    # Output path — Desktop by default
    if args.out:
        out_path = args.out
    else:
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", wallet_label)[:30]
        desktop   = os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(desktop, exist_ok=True)
        out_path  = os.path.join(desktop, f"nft_grid_{safe_name}.png")

    grid.save(out_path, "PNG", optimize=True)
    w, h = grid.size
    print(f"\n✅  Saved: {out_path}  ({w}×{h} px, {len(images)} NFTs)")


if __name__ == "__main__":
    main()
