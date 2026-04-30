# algo-nft-grid

Generate a square NFT wall image from any Algorand wallet address or NFD.

![Example 5×5 NFT grid](example.png)

---

## Features

- **NFD support** — pass `pippo.algo` instead of a raw address
- **ARC-3 / ARC-19 / ARC-69** metadata resolution
- **5 IPFS gateways** with automatic fallback (no more 403s)
- **Auto-filters fungible tokens** — only true NFTs end up in the grid
- Configurable grid size, cell size, gap and output file

---

## Requirements

```
Python 3.10+
Pillow
requests
base58
```

Install with:

```bash
pip install Pillow requests base58
```

---

## Usage

```bash
python nft_grid.py <address_or_nfd> [options]
```

### Examples

```bash
# From a raw Algorand address (5×5 grid, default)
python nft_grid.py RWBL6NFN53EH5X3U7LNZW73HNJY5UCSLB2MCCYU2FEX76HEVWTFGBXNYMQ

# From an NFD
python nft_grid.py famverse.algo

# 4×4 grid with larger cells
python nft_grid.py pippo.algo --size 4 --cell 600 --out my_wall.png

# 3×3 grid with a wider gap between cells
python nft_grid.py pippo.algo --size 3 --gap 8
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--size N` | `5` | Grid side length (N×N NFTs) |
| `--cell PX` | `500` | Cell size in pixels |
| `--gap PX` | `4` | Gap between cells in pixels |
| `--out FILE` | `nft_grid.png` | Output filename |
| `--delay SEC` | `0.3` | Delay between IPFS requests |

---

## How it works

1. **Resolve the address** — if the input ends in `.algo`, it's resolved via the [NFD API](https://api.nf.domains).
2. **Fetch wallet assets** — via the [Algonode](https://algonode.io) mainnet API.
3. **Filter NFTs** — assets with `total ≤ 100` and `decimals == 0` are treated as NFTs; fungible tokens are skipped.
4. **Resolve images** — handles all three main Algorand NFT standards:
   - **ARC-3**: metadata JSON on IPFS → `image` field
   - **ARC-19**: CID derived from the `reserve` address → metadata JSON → image
   - **ARC-69**: image URL stored directly in the ASA `url` parameter
5. **Download & compose** — images are fetched with gateway fallback, resized, and arranged in an N×N grid.

---

## Supported ARC standards

| Standard | Method | Notes |
|----------|--------|-------|
| ARC-3 | `ipfs://CID#arc3` or `https://ipfs.io/ipfs/CID` | Fetches JSON metadata |
| ARC-19 | `template-ipfs://{ipfscid:0\|1:…:reserve:sha2-256}` | Derives CID from reserve address |
| ARC-69 | Direct URL in ASA params | Image URL used as-is |

---

## IPFS Gateways

The script tries these gateways in order, stopping at the first successful response:

1. `https://ipfs.io/ipfs/`
2. `https://dweb.link/ipfs/`
3. `https://nftstorage.link/ipfs/`
4. `https://w3s.link/ipfs/`
5. `https://gateway.pinata.cloud/ipfs/`

---

## Contributing

PRs welcome. Ideas for improvement:

- [ ] `--label` flag to overlay NFT names on each cell
- [ ] `--exclude ASA_ID` to skip specific assets
- [ ] Multi-page PDF output for large collections
- [ ] Optional Vestige / Allo metadata fallback

---

## License

MIT
