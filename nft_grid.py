#!/usr/bin/env python3
"""
nft_grid.py — Algorand NFT Wall Generator
Genera un'immagine griglia quadrata (NxN) con gli NFT di un wallet Algorand.
Accetta indirizzo Algorand oppure NFD (es. pippo.algo).

Dipendenze:
    pip install Pillow requests base58

Uso:
    python nft_grid.py <address_or_nfd> [--size 5] [--cell 500] [--gap 4] [--out grid.png]

Esempi:
    python nft_grid.py RWBL6NFN53EH5X3U7LNZW73HNJY5UCSLB2MCCYU2FEX76HEVWTFGBXNYMQ
    python nft_grid.py pippo.algo --size 4 --cell 600 --out mio_wallet.png
    python nft_grid.py famverse.algo --size 5 --cell 500 --gap 6
"""

import argparse
import base64
import json
import os
import sys
import time
from io import BytesIO

import base58
import requests
from PIL import Image

# ── Costanti ──────────────────────────────────────────────────────────────────

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

# Token fungibili noti da escludere (puoi estendere la lista)
SKIP_UNIT_NAMES = {"ALGO", "USDC", "USDT", "goUSD", "goETH", "goBTC", "wALGO", "VEST"}

# ── Utilità IPFS / CID ────────────────────────────────────────────────────────

def _algo_addr_to_pk(addr: str) -> bytes:
    """Decodifica un indirizzo Algorand nei 32 byte della chiave pubblica."""
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

def ipfs_url(cid_or_ipfs: str, gateway: str = IPFS_GATEWAYS[0]) -> str:
    """Converte ipfs://CID  →  gateway/CID"""
    if cid_or_ipfs.startswith("ipfs://"):
        cid_or_ipfs = cid_or_ipfs[7:]
    cid_or_ipfs = cid_or_ipfs.split("#")[0]  # rimuove frammenti (#arc3, #i …)
    return gateway + cid_or_ipfs

def fetch_url(url: str, as_json: bool = False, timeout: int = TIMEOUT):
    """Scarica un URL provando tutti i gateway IPFS se necessario."""
    # Se è IPFS, estrai il CID e prova tutti i gateway
    if url.startswith("ipfs://") or "/ipfs/" in url:
        if url.startswith("ipfs://"):
            cid = url[7:].split("#")[0].rstrip("/")
        else:
            cid = url.split("/ipfs/", 1)[-1].split("#")[0].rstrip("/")
        for gw in IPFS_GATEWAYS:
            try:
                r = requests.get(gw + cid, headers=HEADERS, timeout=timeout)
                if r.status_code == 200:
                    return r.json() if as_json else r.content
            except Exception:
                pass
        return None

    # URL normale
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json() if as_json else r.content
    except Exception:
        pass
    return None

# ── Risoluzione NFD → indirizzo ───────────────────────────────────────────────

def resolve_nfd(nfd: str) -> str:
    """Risolve un NFD (es. pippo.algo) nell'indirizzo Algorand corrispondente."""
    print(f"[NFD] Risoluzione {nfd} …")
    try:
        r = requests.get(f"{NFD_API}/{nfd}", headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            addr = data.get("depositAccount") or data.get("caAlgo", [None])[0]
            if addr:
                print(f"[NFD] → {addr}")
                return addr
    except Exception as e:
        pass
    sys.exit(f"[ERRORE] NFD '{nfd}' non trovato o non risolvibile.")

# ── Recupero asset dal wallet ─────────────────────────────────────────────────

def get_wallet_assets(address: str) -> list[dict]:
    """Restituisce gli asset (amount > 0) del wallet."""
    print(f"[Wallet] Recupero asset per {address[:20]}…")
    r = requests.get(
        f"{ALGONODE_API}/accounts/{address}?exclude=none",
        headers=HEADERS, timeout=TIMEOUT
    )
    r.raise_for_status()
    assets = r.json().get("assets", [])
    return [a for a in assets if a.get("amount", 0) > 0]

# ── Recupero metadati asset ───────────────────────────────────────────────────

def fetch_asset_params(asset_id: int) -> dict:
    r = requests.get(
        f"{ALGONODE_IDX}/assets/{asset_id}",
        headers=HEADERS, timeout=TIMEOUT
    )
    if r.status_code == 200:
        return r.json().get("asset", {}).get("params", {})
    return {}

def is_nft(params: dict) -> bool:
    """Filtra i token fungibili: NFT = total == 1, decimals == 0."""
    total    = params.get("total", 0)
    decimals = params.get("decimals", 1)
    unit     = params.get("unit-name", "").upper()
    if unit in SKIP_UNIT_NAMES:
        return False
    # ARC-3/ARC-69/ARC-19: supply unitaria con 0 decimali
    return total <= 100 and decimals == 0

# ── Risoluzione URL immagine ──────────────────────────────────────────────────

def resolve_image_url(params: dict) -> str | None:
    """
    Restituisce l'URL dell'immagine dell'NFT gestendo:
    - ARC-3  (ipfs://CID o https://ipfs.io/ipfs/CID  →  metadata JSON  →  image)
    - ARC-19 (template-ipfs://  →  CID da reserve  →  metadata JSON  →  image)
    - ARC-69 (immagine diretta nell'url del parametro)
    - URL IPFS diretti con frammento #i
    """
    url     = params.get("url", "")
    reserve = params.get("reserve", "")

    # ── ARC-19: URL con placeholder template ──
    if "template-ipfs" in url and reserve:
        cid = cid_v1(reserve) if "ipfscid:1" in url else cid_v0(reserve)
        meta = fetch_url(f"ipfs://{cid}", as_json=True)
        if meta:
            img = meta.get("image") or meta.get("image_url") or meta.get("animation_url") or ""
            return img if img else None
        return None

    # ── ARC-3 via metadata JSON (url termina con #arc3 o punta a JSON) ──
    if url.startswith("ipfs://") and "#arc3" in url:
        meta = fetch_url(url, as_json=True)
        if meta:
            img = meta.get("image") or meta.get("image_url") or ""
            return img if img else None
        return None

    # ── URL HTTPS che punta a JSON IPFS ──
    if url.startswith("https://ipfs.io/ipfs/") and url.endswith("e"):
        # potrebbe essere metadata (es. bafkrei…) — prova come JSON
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.headers.get("content-type","").startswith("application/json"):
                meta = r.json()
                img = meta.get("image") or meta.get("image_url") or ""
                return img if img else url
        except Exception:
            pass
        return url

    # ── Immagine diretta IPFS (con o senza frammento) ──
    if url.startswith("ipfs://"):
        return url  # fetch_url gestirà il gateway

    # ── URL HTTPS diretta ──
    if url.startswith("https://"):
        # Potrebbe essere metadata JSON (es. Yieldling Yields)
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

# ── Download immagine ─────────────────────────────────────────────────────────

def download_image(image_url: str) -> Image.Image | None:
    data = fetch_url(image_url)
    if data:
        try:
            return Image.open(BytesIO(data)).convert("RGBA")
        except Exception:
            pass
    return None

# ── Composizione griglia ──────────────────────────────────────────────────────

def make_grid(images: list[Image.Image], names: list[str], cols: int,
              cell_size: int, gap: int, bg: tuple = (15, 15, 15)) -> Image.Image:
    rows = (len(images) + cols - 1) // cols
    W = cols * cell_size + (cols + 1) * gap
    H = rows * cell_size + (rows + 1) * gap
    canvas = Image.new("RGB", (W, H), bg)

    for idx, img in enumerate(images):
        thumb = img.convert("RGB").resize((cell_size, cell_size), Image.LANCZOS)
        col = idx % cols
        row = idx // cols
        x = gap + col * (cell_size + gap)
        y = gap + row * (cell_size + gap)
        canvas.paste(thumb, (x, y))
        print(f"  [{idx+1:02d}/{len(images)}] {names[idx]}")

    return canvas

# ── Main ──────────────────────────────────────────────────────────────────────

def pick_grid_size(total_nfts: int, forced: int | None) -> int:
    """
    Offre griglie da 2x2 fino a 10x10, limitato dagli NFT disponibili.
    Se --size e passato da CLI lo usa direttamente.
    """
    MAX_SIDE = 10
    max_side = min(MAX_SIDE, int(total_nfts ** 0.5))

    if forced is not None:
        capped = min(forced, max_side)
        if capped != forced:
            print(f"[Info] --size {forced} ridotto a {capped} (solo {total_nfts} NFT disponibili)")
        return capped

    options = list(range(2, max_side + 1))

    print(f"\n[Grid] {total_nfts} NFT trovati nel wallet.")
    print("       Scegli la dimensione della griglia:\n")
    for i, s in enumerate(options, 1):
        bar = "+" * s
        print(f"  [{i:2d}]  {s}x{s}  =  {s*s:3d} NFT   {bar}")
    print(f"\n  [ 0]  Dimensione personalizzata (2-{max_side})")

    while True:
        try:
            choice = input("\nScelta: ").strip()
            n = int(choice)
            if 1 <= n <= len(options):
                return options[n - 1]
            elif n == 0:
                custom = int(input(f"Inserisci lato griglia (2-{max_side}): ").strip())
                if 2 <= custom <= max_side:
                    return custom
                print(f"  Valore fuori range (2-{max_side})")
            else:
                print("  Scelta non valida")
        except (ValueError, KeyboardInterrupt):
            print("  Inserisci un numero valido")


def main():
    parser = argparse.ArgumentParser(
        description="Genera un'immagine griglia con gli NFT di un wallet Algorand."
    )
    parser.add_argument("wallet",  nargs="?", default=None, help="Indirizzo Algorand (58 char) o NFD (es. pippo.algo). Se omesso viene chiesto interattivamente.")
    parser.add_argument("--size",  type=int, default=None, help="Lato della griglia (es. 5 → 5x5=25 NFT). Se omesso viene chiesto interattivamente.")
    parser.add_argument("--cell",  type=int, default=500,  help="Dimensione cella in px (default: 500)")
    parser.add_argument("--gap",   type=int, default=4,    help="Gap tra celle in px (default: 4)")
    parser.add_argument("--out",   default="nft_grid.png", help="File di output (default: nft_grid.png)")
    parser.add_argument("--delay", type=float, default=0.3, help="Pausa tra richieste IPFS in secondi (default: 0.3)")
    args = parser.parse_args()

    # 1. Chiedi wallet se non passato da CLI
    if args.wallet:
        wallet = args.wallet.strip()
    else:
        print("╔══════════════════════════════════════╗")
        print("║       Algorand NFT Grid Generator    ║")
        print("╚══════════════════════════════════════╝\n")
        wallet = input("Inserisci indirizzo Algorand o NFD (es. pippo.algo): ").strip()
        if not wallet:
            sys.exit("[ERRORE] Nessun wallet inserito.")
    if wallet.endswith(".algo") or (len(wallet) < 58 and "." in wallet):
        wallet = resolve_nfd(wallet)

    # 2. Recupera gli asset del wallet
    wallet_assets = get_wallet_assets(wallet)
    print(f"[Wallet] {len(wallet_assets)} asset trovati totali")

    # 3. Prima passata veloce: conta quanti NFT ci sono (solo params base, no IPFS)
    print("[Metadata] Scansione NFT nel wallet…")
    nft_candidates = []
    for item in wallet_assets:
        aid = item["asset-id"]
        params = fetch_asset_params(aid)
        if params and is_nft(params):
            nft_candidates.append((aid, params))
        time.sleep(0.05)

    total_nfts = len(nft_candidates)
    print(f"[NFT] {total_nfts} NFT trovati nel wallet")

    if not nft_candidates:
        sys.exit("[ERRORE] Nessun NFT trovato nel wallet.")

    # 4. Scegli dimensione griglia (interattivo o da CLI)
    grid_size = pick_grid_size(total_nfts, args.size)
    max_nfts  = grid_size * grid_size

    # 5. Risolvi le immagini — early stop appena abbiamo abbastanza NFT
    print(f"\n[Metadata] Risoluzione immagini (target: {max_nfts} NFT)…")
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
                print(f"  … early stop: raggiunti {max_nfts} NFT")
                break
        else:
            print(f"  - {params.get('name', aid)} (nessuna immagine)")
        time.sleep(0.1)

    print(f"\n[NFT] {len(nfts_resolved)} NFT con immagine pronti")

    # Prendi i primi max_nfts
    to_render = nfts_resolved[:max_nfts]
    print(f"[Grid] Renderizzazione {len(to_render)} NFT in griglia {grid_size}x{grid_size}…\n")

    # 6. Scarica le immagini
    images, names = [], []
    for nft in to_render:
        print(f"  ↓ Download: {nft['name']}")
        img = download_image(nft["image_url"])
        if img:
            images.append(img)
            names.append(nft["name"])
        else:
            print(f"    ⚠ Impossibile scaricare {nft['name']}, sostituito con placeholder")
            images.append(Image.new("RGBA", (100, 100), (40, 40, 40)))
            names.append(nft["name"])
        time.sleep(args.delay)

    # 7. Componi la griglia
    print(f"\n[Grid] Composizione griglia {grid_size}x{grid_size} ({args.cell}px/cella)…")
    grid = make_grid(images, names, cols=grid_size, cell_size=args.cell, gap=args.gap)

    grid.save(args.out, "PNG", optimize=True)
    w, h = grid.size
    print(f"\n✅  Salvato: {args.out}  ({w}×{h} px, {len(images)} NFT)")


if __name__ == "__main__":
    main()
