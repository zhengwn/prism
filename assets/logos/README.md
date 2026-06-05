# Prism — Logos & Icons

> Single source of truth for the Prism wordmark and the Tauri app icon set.

## Files

| Path | Purpose |
|---|---|
| `prism-logo.svg` | Master vector source (1024×1024 viewBox). Edit this, regenerate everything else. |
| `../../src-tauri/icons/` | Generated icon set consumed by `tauri.conf.json` `bundle.icon`. |

## Design intent

A geometric, app-store-readable mark that says **"refract"** without literal science-class imagery.

- **Rounded dark backdrop** (deep navy → indigo) — the prism housing, also a stable
  surface for macOS rounded-corner masks and Windows taskbar tiles.
- **White equilateral triangle** — the prism itself. Stroked, semi-transparent fill so
  the spectrum beams visually pass *through* it.
- **One white incident beam** entering from the upper-left.
- **Five spectrum beams** fanning out to the right:
  `#A855F7` (violet) → `#3B82F6` (blue) → `#10B981` (green) → `#F59E0B` (amber) → `#EF4444` (red).
  Slightly muted, not neon — readable at 16×16.
- **Glowing dots** at the tip of each spectrum beam give 32×32 a recognizable silhouette.

Constraints respected:

- 1024×1024 square viewBox, safe area ~10% on all sides.
- Survives down-scaling to 16×16 (single dominant shape + colored dots).
- Self-drawn, no third-party logo or font dependency.

## How to regenerate

The icon set under `src-tauri/icons/` is **generated**, hand-edited files will be
overwritten. To refresh after editing `prism-logo.svg`:

```bash
# 1. Cross-platform PNG/ICO/ICNS + macOS Square / StoreLogo / Android / iOS
npx --yes @tauri-apps/cli@latest icon assets/logos/prism-logo.svg -o src-tauri/icons/

# 2. Tauri CLI does not emit 16/48/256/512 PNGs, so re-render those from icon.png
uv run --with pillow --no-project python3 -c "
from PIL import Image
img = Image.open('src-tauri/icons/icon.png')
for s in [16, 48, 256, 512]:
    img.resize((s, s), Image.LANCZOS).save(f'src-tauri/icons/{s}x{s}.png', 'PNG', optimize=True)
"
```

Tools used by this commit:

- **`npx @tauri-apps/cli@latest icon`** — official Tauri 2 icon generator. Outputs the
  full desktop + mobile set (PNG, ICO, ICNS, Windows Store Square*/StoreLogo, iOS
  AppIcon-*, Android mipmap-*).
- **`uv run --with pillow`** — fills in the four extra PNG sizes
  (`16x16.png`, `48x48.png`, `256x256.png`, `512x512.png`) that the Tauri CLI does
  not emit. Source = `icon.png` (512×512, LANCZOS resample).

No ImageMagick / librsvg / Pillow ICO/ICNS libraries required.

## When to refresh

- Any change to `prism-logo.svg` (color, geometry, layout) → run the regeneration block above.
- New Tauri release with new required icon sizes → rerun step 1 and audit step 2.
- Brand refresh → edit `BRAND.md` first, then update `prism-logo.svg` to match.

## Licensing

Original work by the Prism maintainers. No third-party trademarks, fonts, or logos
are incorporated. Free to use, modify, and redistribute within the Prism project.
