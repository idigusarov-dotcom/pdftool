#!/bin/bash
# Проверка готового .dmg на macOS-раннере так, как это сделает пользователь:
# открыть dmg → скопировать программу в «Программы» → запустить. Плюс самопроверка и снимки экрана.
#   tools/mac_smoke.sh dist/PdfTool-...-arm64.dmg arm64 smoke
set -uo pipefail
DMG="$1"; ARCH="$2"; OUT="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
mkdir -p "$OUT"
fail() { echo "ОШИБКА: $*"; echo "$*" >> "$OUT/errors.txt"; FAILED=1; }
FAILED=0
with_timeout() { perl -e 'alarm shift; exec @ARGV' "$@"; }

echo "== открыть dmg"
MNT="$(mktemp -d)/dmg"
mkdir -p "$MNT"
hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MNT" || { fail "dmg не открылся"; exit 1; }
ls -la "$MNT" | tee "$OUT/dmg_contents.txt"
[ -d "$MNT/PdfTool.app" ] || fail "в dmg нет PdfTool.app"
[ "$(readlink "$MNT/Программы")" = "/Applications" ] || fail "в dmg нет ярлыка «Программы» → /Applications"

echo "== «перетащить в Программы»"
rm -rf /Applications/PdfTool.app
ditto "$MNT/PdfTool.app" /Applications/PdfTool.app || fail "не скопировалось в /Applications"
hdiutil detach "$MNT" || true
APP=/Applications/PdfTool.app
BIN="$APP/Contents/MacOS/PdfTool"

echo "== архитектура, подпись, Info.plist"
lipo -archs "$BIN" | tee "$OUT/archs.txt"
grep -qw "$ARCH" "$OUT/archs.txt" || fail "исполняемый файл не для $ARCH"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tee "$OUT/codesign.txt" || fail "подпись не прошла проверку"
spctl --assess --type execute -vv "$APP" > "$OUT/gatekeeper.txt" 2>&1 || true   # без нотаризации — ожидаемо «rejected»
plutil -p "$APP/Contents/Info.plist" > "$OUT/Info.plist.txt"
/usr/bin/python3 - "$APP" "$OUT" <<'PY' || fail "часть библиотек требует macOS новее 12.0"
import json, os, subprocess, sys
app, out = sys.argv[1], sys.argv[2]
worst, rows = (0, 0), []
for root, _, files in os.walk(app):
    for f in files:
        p = os.path.join(root, f)
        if os.path.islink(p):
            continue
        with open(p, "rb") as fh:
            magic = fh.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"):
            continue
        txt = subprocess.run(["otool", "-l", p], capture_output=True, text=True).stdout
        vers = []
        for block in txt.split("Load command"):
            words = block.split()
            if "LC_BUILD_VERSION" in words and "minos" in words:
                vers.append(words[words.index("minos") + 1])
            elif "LC_VERSION_MIN_MACOSX" in words and "version" in words:
                vers.append(words[words.index("version") + 1])
        for v in vers:
            t = tuple(int(x) for x in v.split(".")[:2])
            rows.append((t, os.path.relpath(p, app)))
            worst = max(worst, t)
rows.sort(reverse=True)
json.dump({"max_minos": ".".join(map(str, worst)), "top": [[".".join(map(str, t)), p] for t, p in rows[:15]]},
          open(os.path.join(out, "min_macos.json"), "w"), indent=1, ensure_ascii=False)
print("наибольшая минимальная версия macOS среди библиотек:", worst)
sys.exit(0 if worst <= (12, 0) else 1)
PY

echo "== самопроверка собранной программы (путь с кириллицей и пробелами)"
ST="$OUT/Самопроверка PdfTool"
mkdir -p "$ST"
with_timeout 300 "$BIN" --self-test "$ST"; CODE=$?
echo "код выхода: $CODE"
cat "$ST/selftest.json" || fail "selftest.json не создан"
[ "$CODE" = "0" ] || fail "самопроверка: код $CODE"
/usr/bin/python3 -c "import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if r['ok'] and r['frozen'] else 1)" \
  "$ST/selftest.json" || fail "selftest.json: ok/frozen не true"

echo "== обычный запуск через Finder (open) с PDF из папки с кириллицей"
PDF="$(ls "$ST"/Проверка*/Акты*.pdf | head -1)"
shot() {   # $1 — light|dark
  open -a "$APP" "$PDF" || fail "open -a не запустил программу"
  sleep 12
  pgrep -f "PdfTool.app/Contents/MacOS/PdfTool" > /dev/null || fail "программа закрылась сама после запуска ($1)"
  screencapture -x "$OUT/macos_screen_$1.png" || echo "screencapture не сработал (на CI бывает без прав)"
  osascript -e 'tell application id "ru.akvatehstroy.pdftool" to quit' > /dev/null 2>&1 || true
  sleep 3
  pkill -f "PdfTool.app/Contents/MacOS/PdfTool" || true
  sleep 1
}
shot light
if osascript -e 'tell application "System Events" to tell appearance preferences to set dark mode to true' 2>/dev/null; then
  shot dark
  osascript -e 'tell application "System Events" to tell appearance preferences to set dark mode to false' || true
else
  echo "не удалось включить тёмное оформление системы (нет прав на CI) — снимок тёмной темы есть в самопроверке"
fi
LOG="$HOME/Library/Application Support/PdfTool/log.txt"
if [ -f "$LOG" ]; then tail -40 "$LOG" > "$OUT/log_tail.txt"; grep -q "Запуск" "$LOG" || fail "в журнале нет записи о запуске"
else fail "нет журнала в ~/Library/Application Support/PdfTool"; fi
cp "$ST"/selftest*.png "$OUT/" 2>/dev/null || true
echo "FAILED=$FAILED"
exit $FAILED
