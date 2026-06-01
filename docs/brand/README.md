# Gideon brand assets

The Gideon mark is a stylized **gideon** painted in the brand's coral→amber gradient
(`#c85a48 → #ff6b5b → #ff9a7a → #ffb454`). The live app renders it theme-aware via
`web/src/ui/GideonMark.tsx`; the source vector is `web/public/gideon.svg`.

| File | What it is | Where it's used |
|---|---|---|
| `gideon-mark.svg` | Vector mark, coral→amber gradient, transparent | source of truth for the mark |
| `gideon-mark.png` | 512×512 mark, transparent | README header logo |
| `avatar.png` | 1024×1024, cream gideon on coral fill | **GitHub org avatar** (Settings → Profile) |
| `social-preview.png` | 1280×640 card | **repo social preview** (Settings → Social preview) |

Regenerate from `desktop/icon.png` (the gideon alpha mask) + the gradient stops above; keep
the gradient and geometry identical to `gideon.svg` so every surface stays consistent.
