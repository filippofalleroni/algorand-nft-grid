# Algorand NFT Wall Generator

## Quick start

**First time only — install:**
```bash
bash install.sh
```

**Every time after:**
```bash
cd algorand-nft-grid
./start.sh
```

---

Generate a beautiful square image with all the NFTs from any Algorand wallet.

![Example 5×5 NFT grid](example.png)

---

## What it does

You give it an Algorand wallet address or name (like `gloot.algo`) and it downloads all the NFT images and assembles them into a square grid — saved directly to your Desktop, ready to share on Instagram or X.

---

## Installation (do this once)

### Step 1 — Download the project

Click the green **Code** button on this page → **Download ZIP** → unzip it on your computer.

Or if you have Git:
```bash
git clone https://github.com/filippofalleroni/algorand-nft-grid.git
cd algorand-nft-grid
```

### Step 2 — Install

**Mac / Linux** — open Terminal, go to the folder and run:
```bash
bash install.sh
```

**Windows** — open Command Prompt in the folder and run:
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

That's it. You only need to do this once.

---

## How to use it

Every time you want to generate a wall, open Terminal in the project folder and run:

```bash
./start.sh
```

On **Windows**:
```
venv\Scripts\activate
python nft_grid.py
```

The tool will ask you:
1. **Which wallet?** — enter an address or a name like `gloot.algo`
2. **How big?** — choose from 2×2 (4 images) up to 10×10 (100 images)

Then it downloads everything and saves two files to your Desktop:
- **Full resolution** — best for printing or zooming in
- **1080×1080 px** — ready to post on Instagram or X

---

## Options (for advanced users)

You can skip the questions by passing everything directly:

```bash
./start.sh gloot.algo --size 5
./start.sh gloot.algo --size 5 --out ~/Pictures/my_wall.png
```

| Option | Default | Description |
|--------|---------|-------------|
| `--size N` | asked interactively | Grid side (e.g. 5 = 5×5 = 25 images) |
| `--cell PX` | `500` | Image quality per cell in pixels |
| `--gap PX` | `4` | Space between images |
| `--out FILE` | Desktop | Where to save the output |

---

## Supported wallet formats

- **NFD** — human-readable names like `gloot.algo`, `famverse.algo`
- **Algorand address** — the long 58-character string starting from your wallet app

---

## How it works (technical)

1. Resolves NFD names via the [NFD API](https://api.nf.domains)
2. Fetches wallet assets via [Algonode](https://algonode.io)
3. Filters out fungible tokens — only true NFTs (ARC-3, ARC-19, ARC-69)
4. Downloads images via 5 IPFS gateways with automatic fallback
5. Composes the grid and exports full-res + 1080px social version

---

## Troubleshooting

**"No NFTs found"** — the wallet may only contain fungible tokens (currencies), not NFTs.

**Some images show "ALGORAND NFT GRID"** — those NFT images could not be downloaded. This can happen when the image is no longer available online.

**Script won't start** — make sure you ran `install.sh` first and that you're in the right folder.

---

## Requirements

- Python 3.10 or higher
- Internet connection
- Algorand wallet address or NFD

---

## License

MIT — free to use, modify and share.
