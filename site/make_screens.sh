#!/usr/bin/env bash
# Phone screenshots -> the site's screens/ folder, at a size a web page wants.
#
# The originals are 1206x2622 PNGs straight off the phone, and one of them is
# 3.2 MB because it contains a photograph of a desk. The site shows them in a
# column a few hundred points wide, so 900px is already retina for that slot.
#
# PNG is kept when it is already small -- the build-guide shot carries the
# camp plan, which is line art that JPEG blurs exactly where the labels are.
# Everything else goes to JPEG at a quality checked by eye against the
# chattiest screen: at 900px, q85 leaves the text crisp and roughly halves
# the file. Resampling a PNG is what makes it BIGGER, not smaller --
# interpolation invents colours, and these screenshots came off the phone
# already flat.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:-~/Snails/screenshots}"
OUT="${DIR}/screens"
WIDTH=900
JPEG_OVER=320000
QUALITY=85

# source:destination. sync.png keeps its existing image -- there is no new
# screenshot of the Sync screen, and a stale shot is better than none.
PAIRS=(
  "IMG_1277.PNG:app.png"      # the Lucy tab, mid-conversation
  "IMG_1278.PNG:people.png"   # Snails, the collapsed list
  "IMG_1279.PNG:note.png"     # Memory, viewfinder open
  "IMG_1280.PNG:info.png"     # Info, now three documents
  "IMG_1276.PNG:build.png"    # the build guide and the camp plan
)

for pair in "${PAIRS[@]}"; do
    src="${SRC}/${pair%%:*}"
    name="${pair##*:}"
    [ -f "${src}" ] || { echo "missing ${src}" >&2; exit 1; }

    png="${OUT}/${name}"
    cp "${src}" "${png}"
    sips --resampleWidth "${WIDTH}" "${png}" >/dev/null

    size=$(stat -f%z "${png}")
    if [ "${size}" -gt "${JPEG_OVER}" ]; then
        jpg="${OUT}/${name%.png}.jpg"
        sips -s format jpeg -s formatOptions "${QUALITY}" \
             "${png}" --out "${jpg}" >/dev/null
        rm -f "${png}"
        printf '%-12s %7s KB  ->  %s  %s KB (jpeg)\n' \
            "${pair%%:*}" "$((size / 1024))" "$(basename "${jpg}")" \
            "$(( $(stat -f%z "${jpg}") / 1024 ))"
    else
        printf '%-12s %7s KB  ->  %s (png, kept lossless)\n' \
            "${pair%%:*}" "$((size / 1024))" "${name}"
    fi
done

echo
ls -la "${OUT}"
