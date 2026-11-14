#!/bin/bash
# Подписывает собранный dist/PdfTool.app «на месте» (ad-hoc, без сертификата Apple) и упаковывает в .dmg,
# в окне которого программу перетаскивают в «Программы».
#   tools/make_dmg.sh dist/PdfTool-1.4.0-mac-arm64.dmg
set -euo pipefail
OUT="$1"
APP="dist/PdfTool.app"
VOL="PDF-помощник"
[ -d "$APP" ] || { echo "нет $APP — сначала pyinstaller pdftool.spec"; exit 1; }

echo "== подпись (ad-hoc)"
xattr -cr "$APP"
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Signature|Format" || true

echo "== dmg"
rm -f "$OUT"
if python -m dmgbuild -s tools/dmg_settings.py -D app="$APP" -D icon=app/ui/assets/PdfTool.icns "$VOL" "$OUT"; then
  echo "dmgbuild: ok"
else
  echo "dmgbuild не сработал — делаю простой dmg через hdiutil"
  STAGE="$(mktemp -d)"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Программы"
  hdiutil create -volname "$VOL" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$OUT"
  rm -rf "$STAGE"
fi
hdiutil verify "$OUT"
ls -la "$OUT"
