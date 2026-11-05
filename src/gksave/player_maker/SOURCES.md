# Player Studio assets

`seasons.json` records the collection time, official season metadata, source URLs,
and SHA-256 hashes for the locally stored Nexon card backgrounds and season emblems.
`nations.json` records country names/IDs from the official data center and the
source URLs/hashes for each flag. These are public visual assets, not player or
account data. Nexon/EA retain ownership of their artwork.

The four preferred designs are 25 UCL (856), PTG (863), ICON THE MOMENT (100),
and ICON (101). All other collected designs are available in the same selector.
Source artwork uses 340 × 400 card backgrounds. Card PNGs export at 680 × 800;
full sheets export at 1400 px wide with a content-dependent height. Preview and both exports share the same card
canvas, so names, portraits and other card elements stay aligned.

`card-details.json` records the official FCOAllSans/CruyffSans font files, club
crests, platinum enhancement artwork, and the fixed FC icon league logo taken
from the supplied ICON THE MOMENT Henry reference. Their URLs and hashes accompany
the originals. Nexon/EA retain ownership of these fonts and visual assets.
The card follows the official 340 × 400 layout: a 180 × 180 default portrait
inside the 180 × 240 image region; a separate salary hexagon and enhancement badge;
and country / league / club icons under the centered emblem and player name.
Portrait scaling preserves aspect ratio, and position/zoom controls still allow
manual adjustments. Official season colors apply separately to rating, name,
salary outline and salary fill. Card fonts are served from this site's own assets.

`vendor/` contains unmodified MediaPipe Tasks Vision 0.10.32 JavaScript/WASM and
the version-1 float16 Selfie Segmenter model. See `vendor/LICENSE` and
`vendor/sources.json` for the license, package integrity and source hashes.
The runtime and model are served locally and loaded only when a photo is selected.
No image is uploaded or persisted. The version-1 float16 BlazeFace Short Range model
first locates the largest face. A square region around that face is segmented, so
scenery left in a wide photo cannot shrink the person into a corner of the card.
If no face is detected, the original image is segmented and manual size/position
controls remain available. The person mask removes the background, then the
transparent portrait is cropped and composited over the card artwork. The face
model's source URL and SHA-256 are also recorded in `vendor/sources.json`.
SIMD and non-SIMD WASM builds are included. The site's CSP allows WASM compilation
with `wasm-unsafe-eval`; JavaScript `unsafe-eval` remains disallowed.

The radar chart has six independently editable name/value pairs. Each entered
value is used directly, with no hidden sub-abilities, averaging or position-specific
conversion. The full PNG shows these values in the radar chart only, without a
separate numeric list. OVR always comes from the user's input.

`traits.json` records all 48 available trait icons found
when checking official CDN codes 0 through 199, including source URLs and SHA-256 hashes. Their normal/new
classification uses the same six-point background-color rule as the repository's
`scripts/classify_new_traits.py`: 31 gray normal icons and 17 gold new icons.
All 48 original names are matched by numeric icon code against the trait catalog
in the official [training coach/trait helper](https://fconline.nexon.com/datacenter/coachhelper)
[JavaScript bundle](https://ssl.nexon.com/s1/fc/online/obt/coachHelper/app.ybFnFD3C.js).
Code 0 is `선호 포지션 고집`, rather than a blank base icon. The manifest records
the name source URL, bundle SHA-256 and collection time. Users can name, rename and remove each
chosen trait independently, including reusing an icon with a different name.
Selected traits appear below the preview and prominently below the card/chart in
the full PNG, using 96 px icons and 36 px names. The full sheet starts at 1140 px
high without traits and grows to fit the trait rows. The standalone card PNG keeps
its original dimensions.

To refresh assets, use the source URLs recorded in the manifests, validate PNG
content, and update their SHA-256 hashes. Keep originals and attribution together.
Do not use the private match database or environment files to collect these assets.
