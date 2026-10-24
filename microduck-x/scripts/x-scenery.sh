#!/bin/sh
# Download the hangar's scenery: photographed worlds and real space imagery (~95 MB).
#
#   microduck-x/scripts/x-scenery.sh        # fetch what is missing, then rebuild the dashboard
#   microduck-x/scripts/x-scenery.sh --no-build
#
# Viewer-only images — none of it is physics (docs/HANGAR.md, "Scenery"). Every source is
# public domain or CC; the credits are shown in the dashboard and listed in CREDITS.txt.
# Idempotent: files already present are kept.
set -eu
. "$(dirname "$0")/x-common.sh"

BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0
DIR="$X_ROOT/frontend/public/scenery"
mkdir -p "$DIR/thumbs" "$DIR/space"
command -v curl >/dev/null 2>&1 || die "curl is required"

fetch() { # url dest
    [ -s "$2" ] && return 0
    printf '   %s\n' "${2#"$DIR"/}"
    curl -fsSL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2" || { rm -f "$2.part"; warn "could not download $1"; }
}

# Poly Haven (CC0): an 8K tonemapped panorama to look at, and a 1K HDR to light the duck with.
PH="spruit_sunrise umhlanga_sunrise snowy_park_01 goegap kloofendal_48d_partly_cloudy shanghai_bund venice_sunset dikhololo_night"
say "photographed worlds from Poly Haven (CC0), ~8 MB each"
for id in $PH; do
    fetch "https://dl.polyhaven.org/file/ph-assets/HDRIs/extra/Tonemapped%20JPG/$id.jpg" "$DIR/$id.jpg"
    fetch "https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/${id}_1k.hdr" "$DIR/${id}_1k.hdr"
    fetch "https://cdn.polyhaven.com/asset_img/thumbs/$id.png?width=480&height=240" "$DIR/thumbs/$id.png"
done

say "space: NASA Blue Marble, Black Marble, clouds and LRO Moon (public domain), ESO Milky Way (CC BY 4.0)"
fetch https://eoimages.gsfc.nasa.gov/images/imagerecords/73000/73909/world.topo.bathy.200412.3x5400x2700.jpg "$DIR/space/earth_day.jpg"
fetch https://eoimages.gsfc.nasa.gov/images/imagerecords/57000/57747/cloud_combined_2048.jpg "$DIR/space/earth_clouds.jpg"
fetch https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/lroc_color_2k.jpg "$DIR/space/moon.jpg"
fetch https://cdn.eso.org/images/large/eso0932a.jpg "$DIR/space/milky_way.jpg"
if [ ! -s "$DIR/space/earth_night.jpg" ]; then
    # 13500 px is wider than most GPUs take; 5400 matches the day map
    fetch https://eoimages.gsfc.nasa.gov/images/imagerecords/144000/144898/BlackMarble_2016_3km.jpg "$DIR/space/black_marble_full.jpg"
    if [ -s "$DIR/space/black_marble_full.jpg" ] && command -v ffmpeg >/dev/null 2>&1; then
        ffmpeg -loglevel error -y -i "$DIR/space/black_marble_full.jpg" -vf scale=5400:2700 -q:v 3 "$DIR/space/earth_night.jpg"
        rm -f "$DIR/space/black_marble_full.jpg"
    elif [ -s "$DIR/space/black_marble_full.jpg" ]; then
        mv "$DIR/space/black_marble_full.jpg" "$DIR/space/earth_night.jpg"
    fi
fi
if command -v ffmpeg >/dev/null 2>&1; then
    [ -s "$DIR/thumbs/orbit.jpg" ] || ffmpeg -loglevel error -y -i "$DIR/space/milky_way.jpg" -vf "crop=iw/2:ih/2:iw/4:ih/4,scale=480:240" "$DIR/thumbs/orbit.jpg" || true
fi

cat > "$DIR/CREDITS.txt" <<'EOF'
Scenery used by the Microduck X hangar (viewer only, never physics).

Poly Haven, CC0 (https://polyhaven.com): spruit_sunrise, umhlanga_sunrise, snowy_park_01, goegap,
  kloofendal_48d_partly_cloudy, shanghai_bund, venice_sunset, dikhololo_night — by Greg Zaal,
  Andreas Mischok, Sergej Majboroda and the Poly Haven team (see each asset's page for its author).
NASA Earth Observatory, public domain: Blue Marble (Reto Stöckli), Black Marble 2016, cloud cover.
NASA Scientific Visualization Studio, public domain: CGI Moon Kit (LRO LROC colour map).
ESO/S. Brunier, CC BY 4.0: "The Milky Way panorama" (eso0932a), https://www.eso.org/public/images/eso0932a/
EOF

du -sh "$DIR" 2>/dev/null | sed 's/^/== scenery: /'
if [ "$BUILD" = 1 ] && [ -d "$X_ROOT/frontend/node_modules" ]; then
    say "rebuilding the dashboard so it carries the scenery"
    (cd "$X_ROOT/frontend" && npm run build --silent)
fi
