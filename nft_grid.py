#!/usr/bin/env python3
"""
nft_grid.py — Algorand NFT Wall Generator

Generates a square NxN grid image from any Algorand wallet address or NFD.
Supports ARC-3, ARC-19, and ARC-69 NFT standards.

Usage:
    python nft_grid.py [address_or_nfd] [--size N] [--cell PX] [--gap PX] [--out FILE]
"""

import argparse
import base64
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import base58
import requests
from PIL import Image, ImageDraw, ImageFont
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Constants ─────────────────────────────────────────────────────────────────

ALGONODE_IDX = "https://mainnet-idx.algonode.cloud/v2"
ALGONODE_API = "https://mainnet-api.algonode.cloud/v2"
NFD_API      = "https://api.nf.domains/nfd"

# Gateways ordered by typical reliability for Algorand NFTs.
# algonode.xyz first (optimized for Algorand), then well-known public gateways.
IPFS_GATEWAYS = [
    "https://ipfs.algonode.xyz/ipfs/",   # Best for Algorand NFTs
    "https://ipfs.io/ipfs/",
    "https://nftstorage.link/ipfs/",
    "https://w3s.link/ipfs/",
    "https://dweb.link/ipfs/",
]

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT_SHORT = 4   # for metadata JSON fetches (per gateway)
TIMEOUT_LONG  = 6   # for image downloads (per gateway)
MAX_WORKERS   = 12

SKIP_UNIT_NAMES = {"ALGO", "USDC", "USDT", "goUSD", "goETH", "goBTC", "wALGO", "VEST"}

# Persistent session with connection pooling and retry logic
def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    retry = Retry(
        total=2,
        backoff_factor=0.3,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

SESSION = _make_session()

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

def extract_cid(ipfs_url: str) -> str:
    """Extract the CID from an ipfs:// or /ipfs/ URL, stripping any fragment or path."""
    if ipfs_url.startswith("ipfs://"):
        rest = ipfs_url[7:]
    elif "/ipfs/" in ipfs_url:
        rest = ipfs_url.split("/ipfs/", 1)[-1]
    else:
        return ipfs_url
    # Strip fragment (#i, #v, #arc3, etc) and trailing slashes
    return rest.split("#")[0].split("?")[0].rstrip("/")

# ── HTTP fetch with multi-gateway IPFS fallback ───────────────────────────────

def fetch_ipfs_bytes(cid: str, timeout: int = TIMEOUT_LONG) -> bytes | None:
    """Try every gateway, return raw bytes from the first one that gives a valid response."""
    for gw in IPFS_GATEWAYS:
        try:
            r = SESSION.get(gw + cid, timeout=timeout)
            # Reject 4xx errors and empty responses
            if r.status_code == 200 and len(r.content) > 100:
                return r.content
        except Exception:
            pass
    return None

def fetch_ipfs_json(cid: str, timeout: int = TIMEOUT_SHORT) -> dict | None:
    """Try every gateway, return parsed JSON. Returns None if no gateway returns valid JSON."""
    for gw in IPFS_GATEWAYS:
        try:
            r = SESSION.get(gw + cid, timeout=timeout)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    pass  # Not JSON — try next gateway
        except Exception:
            pass
    return None

def head_ipfs_content_type(cid: str, timeout: int = TIMEOUT_SHORT) -> str | None:
    """Return the content-type of a CID via HEAD request, or None if unreachable."""
    for gw in IPFS_GATEWAYS:
        try:
            r = SESSION.head(gw + cid, timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return r.headers.get("content-type", "")
        except Exception:
            pass
    return None

# ── NFD resolution ────────────────────────────────────────────────────────────

def resolve_nfd(nfd: str) -> str:
    print(f"  Looking up {nfd} ...")
    try:
        r = SESSION.get(f"{NFD_API}/{nfd}", timeout=TIMEOUT_SHORT)
        if r.status_code == 200:
            data = r.json()
            addr = data.get("depositAccount") or data.get("caAlgo", [None])[0]
            if addr:
                print(f"  Found wallet address.")
                return addr
    except Exception:
        pass
    sys.exit(f"\n  Oops! We couldn't find a wallet for '{nfd}'. Please check and try again.")

# ── Wallet & asset metadata ───────────────────────────────────────────────────

def get_wallet_assets(address: str) -> list[dict]:
    print(f"  Connecting to the Algorand blockchain ...")
    r = SESSION.get(f"{ALGONODE_API}/accounts/{address}?exclude=none", timeout=TIMEOUT_SHORT)
    r.raise_for_status()
    return [a for a in r.json().get("assets", []) if a.get("amount", 0) > 0]

def fetch_asset_params(asset_id: int) -> dict:
    try:
        r = SESSION.get(f"{ALGONODE_IDX}/assets/{asset_id}", timeout=TIMEOUT_SHORT)
        if r.status_code == 200:
            return r.json().get("asset", {}).get("params", {})
    except Exception:
        pass
    return {}

def is_nft(params: dict) -> bool:
    """An NFT has total <= 100, decimals == 0, and is not a known fungible unit."""
    if params.get("decimals", 1) != 0:
        return False
    if params.get("total", 0) > 100:
        return False
    if params.get("unit-name", "").upper() in SKIP_UNIT_NAMES:
        return False
    return True

# ── Image URL resolution (ARC-3 / ARC-19 / ARC-69) ────────────────────────────

def resolve_image_url(params: dict) -> str | None:
    """
    Returns a downloadable URL for an NFT's image, handling ARC-3, ARC-19, and ARC-69.
    Returns None only if no URL can be constructed.
    """
    url     = params.get("url", "")
    reserve = params.get("reserve", "")

    # ── ARC-19: template URL with reserve-derived CID ──
    if "template-ipfs" in url and reserve:
        cid = cid_v1(reserve) if "ipfscid:1" in url else cid_v0(reserve)

        # Most ARC-19 NFTs have JSON metadata at the CID
        meta = fetch_ipfs_json(cid)
        if meta:
            img = meta.get("image") or meta.get("image_url") or meta.get("animation_url") or ""
            return img if img else None

        # Some ARC-19 NFTs store the image directly at the CID — verify via HEAD
        ct = head_ipfs_content_type(cid)
        if ct and ct.startswith("image/"):
            return f"ipfs://{cid}"

        # Last resort: assume it's an image and let the downloader try
        return f"ipfs://{cid}"

    # ── ARC-3 with explicit #arc3 fragment ──
    if url.startswith("ipfs://") and "#arc3" in url:
        cid  = extract_cid(url)
        meta = fetch_ipfs_json(cid)
        if meta:
            img = meta.get("image") or meta.get("image_url") or ""
            return img if img else None
        return None

    # ── HTTPS metadata JSON ──
    if url.startswith("https://") and ("ipfs" in url or url.endswith(".json")):
        try:
            r = SESSION.get(url, timeout=TIMEOUT_SHORT)
            if r.status_code == 200:
                ct = r.headers.get("content-type", "")
                if "json" in ct or url.endswith(".json"):
                    try:
                        meta = r.json()
                        img = meta.get("image") or meta.get("image_url") or ""
                        return img if img else url
                    except Exception:
                        return url
                return url
        except Exception:
            pass

    # ── Direct IPFS or HTTPS URL ──
    if url.startswith(("ipfs://", "https://", "http://")):
        return url

    return None

# ── Image download ────────────────────────────────────────────────────────────

def download_image(image_url: str) -> Image.Image | None:
    """Download an image from any IPFS or HTTPS URL with multi-gateway fallback."""
    data = None

    if image_url.startswith("ipfs://") or "/ipfs/" in image_url:
        cid  = extract_cid(image_url)
        data = fetch_ipfs_bytes(cid, timeout=TIMEOUT_LONG)
    else:
        try:
            r = SESSION.get(image_url, timeout=TIMEOUT_LONG)
            if r.status_code == 200 and len(r.content) > 100:
                data = r.content
        except Exception:
            pass

    if data:
        try:
            img = Image.open(BytesIO(data))
            img.load()  # Force decoding to detect corrupt images
            return img.convert("RGBA")
        except Exception:
            pass
    return None

# ── Placeholder for missing images ───────────────────────────────────────────

def make_placeholder(size: int = 500) -> Image.Image:
    """Dark placeholder with dashed border and 'ALGORAND NFT GRID' text."""
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

    for cx, cy in [(border, border), (size-border, border),
                   (border, size-border), (size-border, size-border)]:
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

def make_grid(images: list[Image.Image], cols: int, cell_size: int,
              gap: int, bg: tuple = (15, 15, 15)) -> Image.Image:
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
        return max(2, min(10, forced))

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

# ── Main pipeline ─────────────────────────────────────────────────────────────

def fetch_and_resolve_nft(item: dict) -> dict | None:
    """Combined: fetch params + resolve image URL. One unit of parallel work."""
    aid    = item["asset-id"]
    params = fetch_asset_params(aid)
    if not params or not is_nft(params):
        return None
    return {
        "id":        aid,
        "name":      params.get("name", f"#{aid}"),
        "image_url": resolve_image_url(params),
    }

def main():
    parser = argparse.ArgumentParser(
        description="Generate a square NFT wall image from any Algorand wallet or NFD."
    )
    parser.add_argument("wallet",  nargs="?", default=None,
                        help="Algorand address or NFD (e.g. gloot.algo). Asked interactively if omitted.")
    parser.add_argument("--size",  type=int,   default=None, help="Grid size (e.g. 5 for 5x5).")
    parser.add_argument("--cell",  type=int,   default=500,  help="Image quality in pixels per cell (default: 500).")
    parser.add_argument("--gap",   type=int,   default=4,    help="Space between images in pixels (default: 4).")
    parser.add_argument("--out",   default=None, help="Output file path (default: ~/Desktop/nft_grid_<wallet>.png).")
    parser.add_argument("--debug", action="store_true", help="Show detailed debug info for each NFT.")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════╗")
    print("║    Algorand NFT Wall Generator       ║")
    print("╚══════════════════════════════════════╝\n")

    # 1. Wallet input
    wallet_input = args.wallet.strip() if args.wallet else input(
        "  Enter your wallet address or name (e.g. gloot.algo): "
    ).strip()
    if not wallet_input:
        sys.exit("\n  No wallet entered. Please try again.")

    print()

    # 2. Resolve NFD if needed
    wallet = wallet_input
    if wallet.endswith(".algo") or (len(wallet) < 58 and "." in wallet):
        wallet = resolve_nfd(wallet_input)

    # 3. Fetch wallet assets
    wallet_assets = get_wallet_assets(wallet)

    # 4. Parallel scan + resolve all NFTs
    print(f"  Fetching NFT data ...")
    all_nfts: list[dict] = []
    total_assets = len(wallet_assets)
    done_count   = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_and_resolve_nft, item): item for item in wallet_assets}
        for future in as_completed(futures):
            done_count += 1
            print(f"  Fetching NFT data ... {done_count}/{total_assets}", end="\r")
            try:
                result = future.result()
                if result:
                    all_nfts.append(result)
            except Exception:
                pass

    total_nfts = len(all_nfts)
    print(f"  Found {total_nfts} NFTs.                          ")

    if not all_nfts:
        sys.exit("\n  No NFTs found in this wallet.")

    # 5. Pick grid size
    grid_size = pick_grid_size(total_nfts, args.size, wallet_input)
    max_nfts  = grid_size * grid_size
    cell_size = args.cell

    # 6. Split into resolved (have URL) and unresolved
    resolved_pool = [n for n in all_nfts if n["image_url"]]
    no_url        = [n["name"] for n in all_nfts if not n["image_url"]]

    if no_url:
        print(f"\n  ⚠️  No image link found for:")
        for name in no_url:
            print(f"      - {name}")

    # 7. Download images in parallel — with reserve pool for failed downloads
    print(f"\n  Downloading {min(len(resolved_pool), max_nfts)} images ...\n")

    download_results: dict[int, Image.Image | None] = {}
    failed_names: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(download_image, n["image_url"]): i
                   for i, n in enumerate(resolved_pool)}
        completed = 0
        for future in as_completed(futures):
            idx = futures[future]
            try:
                img = future.result()
            except Exception:
                img = None
            download_results[idx] = img
            if img is None:
                failed_names.append(resolved_pool[idx]["name"])
            completed += 1
            print(f"  Downloaded {completed}/{len(resolved_pool)}", end="\r")

    print(f"  All downloads finished.              \n")

    # 8. Build the image list, filling slots from successful downloads in order
    images: list[Image.Image] = []
    for nft in resolved_pool:
        idx = resolved_pool.index(nft)
        img = download_results.get(idx)
        if img is not None:
            images.append(img)
            if len(images) >= max_nfts:
                break

    # Fill remaining slots with placeholders
    while len(images) < max_nfts:
        images.append(make_placeholder(cell_size))

    successful = max_nfts - sum(1 for img in images if img.size[0] != cell_size or False)
    n_failed = len(failed_names)
    n_filler = max_nfts - len(resolved_pool) + n_failed
    n_filler = max(0, n_filler)

    if failed_names:
        print(f"  ⚠️  {len(failed_names)} image(s) could not be downloaded (replaced with placeholder):")
        for name in failed_names:
            print(f"      - {name}")
        print()

    if max_nfts > total_nfts:
        missing = max_nfts - total_nfts
        print(f"  ℹ️  Wallet has {total_nfts} NFTs but grid needs {max_nfts}.")
        print(f"      {missing} slot(s) will show a placeholder.\n")

    # 9. Compose grid
    print(f"  Building your {grid_size}x{grid_size} NFT wall ...")
    grid = make_grid(images, cols=grid_size, cell_size=cell_size, gap=args.gap)

    # 10. Save outputs
    if args.out:
        out_path = args.out
    else:
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", wallet_input)[:30]
        desktop   = os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(desktop, exist_ok=True)
        out_path  = os.path.join(desktop, f"nft_grid_{safe_name}.png")

    grid.save(out_path, "PNG", optimize=True)
    w, h = grid.size

    social_path = out_path.replace(".png", "_1080.png")
    grid.resize((1080, 1080), Image.LANCZOS).save(social_path, "PNG", optimize=True)

    print(f"\n  ✅  Done! Your NFT wall has been saved to your Desktop.\n")
    print(f"  Full resolution:  {os.path.basename(out_path)}  ({w}×{h} px)")
    print(f"  Social (1080px):  {os.path.basename(social_path)}  — ready for Instagram & X\n")


if __name__ == "__main__":
    main()
