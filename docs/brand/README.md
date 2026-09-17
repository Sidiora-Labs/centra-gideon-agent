# Gideon identity assets

The active mark is a geometric G in a blue-to-teal gradient. Its vector source is [apps/console/public/gideon.svg](../../apps/console/public/gideon.svg). The filled application icon is [apps/console/public/icons/icon.svg](../../apps/console/public/icons/icon.svg), with a dark background.

| Asset | What it is | Where it is used |
| --- | --- | --- |
| [gideon-mark.svg](gideon-mark.svg) | The transparent vector mark | The repository README header, and any surface that needs the mark without a background |
| [gideon-mark.png](gideon-mark.png) | The 512 x 512 application icon on a dark navy background | Icon slots that need a bitmap at full size |
| [gideon-mark-192.png](gideon-mark-192.png) | The same icon at 192 x 192 | Small icon slots that cannot take the 512 pixel file |
| [avatar.png](avatar.png) | A 512 x 512 copy of the application icon | Avatar slots, such as a profile picture or a bot avatar |

Keep these four filenames. The mobile store-asset build and the repository README link them by name, so a rename breaks a consumer. That build script is `apps/mobile/scripts/generate_store_assets.py`.

The navy on these PNGs is `#0f1724`, the same background the mobile launcher icon uses. These are local assets: their presence does not imply publication to an organization profile, a public site, or a release service.
