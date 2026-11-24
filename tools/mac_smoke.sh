#!/bin/bash
# Проверка готового .dmg на macOS-раннере так, как это сделает пользователь:
# открыть dmg → скопировать программу из образа → запустить через Launch Services. Плюс самопроверка и снимки экрана.
#   tools/mac_smoke.sh dist/PdfTool-...-arm64.dmg arm64 smoke
set -uo pipefail
DMG="$1"; ARCH="$2"
mkdir -p "$3" || exit 1
OUT="$(cd "$3" && pwd -P)" || exit 1
fail() { echo "ОШИБКА: $*"; echo "$*" >> "$OUT/errors.txt"; FAILED=1; }
FAILED=0
with_timeout() { perl -e 'alarm shift; exec @ARGV' "$@"; }

echo "== открыть dmg"
TEMP_ROOT="$(mktemp -d)" || exit 1
TEMP_ROOT="$(cd "$TEMP_ROOT" && pwd -P)" || exit 1
MNT="$TEMP_ROOT/dmg"
APP="$TEMP_ROOT/Applications/PdfTool.app"
BIN="$APP/Contents/MacOS/PdfTool"
mounted=0
original_theme=""
set_dark_mode() {
  osascript -e "tell application \"System Events\" to tell appearance preferences to set dark mode to $1"
}
test_pid() {
  /usr/sbin/lsof -a -d txt -t "$BIN" 2>/dev/null
}
cleanup() {
  if [ -n "$original_theme" ]; then set_dark_mode "$original_theme" >/dev/null || true; fi
  local pid
  for pid in $(test_pid); do kill "$pid" 2>/dev/null || true; done
  if [ "$mounted" = 1 ]; then hdiutil detach "$MNT" || return 1; fi
  rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT
mkdir -p "$MNT" "$TEMP_ROOT/Applications" || exit 1
hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MNT" || { fail "dmg не открылся"; exit 1; }
mounted=1
ls -la "$MNT" | tee "$OUT/dmg_contents.txt"
[ -d "$MNT/PdfTool.app" ] || fail "в dmg нет PdfTool.app"
[ "$(readlink "$MNT/Программы")" = "/Applications" ] || fail "в dmg нет ярлыка «Программы» → /Applications"

echo "== копирование приложения из образа"
ditto "$MNT/PdfTool.app" "$APP" || { fail "не удалось скопировать приложение"; exit 1; }
hdiutil detach "$MNT" || { fail "не удалось отключить образ"; exit 1; }
mounted=0

echo "== архитектура, подпись, Info.plist"
lipo -archs "$BIN" | tee "$OUT/archs.txt"
grep -qw "$ARCH" "$OUT/archs.txt" || fail "исполняемый файл не для $ARCH"
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tee "$OUT/codesign.txt" || fail "подпись не прошла проверку"
spctl --assess --type execute -vv "$APP" > "$OUT/gatekeeper.txt" 2>&1 || true   # без нотаризации — ожидаемо «rejected»
plutil -p "$APP/Contents/Info.plist" > "$OUT/Info.plist.txt" || fail "не читается Info.plist"
/usr/bin/python3 - "$APP" "$OUT" "$ARCH" <<'PY' || fail "часть библиотек требует macOS новее 12.0"
import json, os, subprocess, sys
app, out, arch = sys.argv[1:]
worst, rows = (0, 0), []
for root, _, files in os.walk(app):
    for f in files:
        p = os.path.join(root, f)
        if os.path.islink(p):
            continue
        with open(p, "rb") as fh:
            magic = fh.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"):
            continue
        txt = subprocess.run(["otool", "-arch", arch, "-l", p], capture_output=True, text=True, check=True).stdout
        vers = []
        for block in txt.split("Load command"):
            words = block.split()
            if "LC_BUILD_VERSION" in words and "minos" in words:
                vers.append(words[words.index("minos") + 1])
            elif "LC_VERSION_MIN_MACOSX" in words and "version" in words:
                vers.append(words[words.index("version") + 1])
        if not vers:
            raise ValueError(f"{p}: нет минимальной версии macOS для {arch}")
        for v in vers:
            t = tuple(int(x) for x in v.split("."))
            rows.append((t, os.path.relpath(p, app)))
            worst = max(worst, t)
rows.sort(reverse=True)
json.dump({"max_minos": ".".join(map(str, worst)), "top": [[".".join(map(str, t)), p] for t, p in rows[:15]]},
          open(os.path.join(out, "min_macos.json"), "w"), indent=1, ensure_ascii=False)
print("наибольшая минимальная версия macOS среди библиотек:", worst)
sys.exit(0 if rows and worst <= (12, 0, 0) else 1)
PY

echo "== самопроверка собранной программы (путь с кириллицей и пробелами)"
ST="$OUT/Самопроверка PdfTool"
mkdir -p "$ST"
with_timeout 300 "$BIN" --self-test "$ST"; CODE=$?
echo "код выхода: $CODE"
cat "$ST/selftest.json" || fail "selftest.json не создан"
[ "$CODE" = "0" ] || fail "самопроверка: код $CODE"
/usr/bin/python3 -c "import json,sys; r=json.load(open(sys.argv[1])); sys.exit(0 if r['ok'] and r['frozen'] and r['platform'] == 'darwin' and r['machine'] == sys.argv[2] else 1)" \
  "$ST/selftest.json" "$ARCH" || fail "самопроверка не подтвердила запуск собранной программы для $ARCH"

echo "== обычный запуск через Finder (open) с PDF из папки с кириллицей"
PDF="$ST/Проверка путей с пробелами/Акты кв 1-3.pdf"
[ -s "$PDF" ] || { fail "нет тестового PDF"; exit 1; }
open -n -a "$APP" "$PDF" || { fail "Launch Services не запустила тестовую копию"; exit 1; }
opened=0
for _ in {1..45}; do
  pid="$(test_pid | head -n 1)"
  if [ -n "$pid" ] && /usr/sbin/lsof -a -p "$pid" -t "$PDF" >/dev/null 2>&1; then
    opened=1
    break
  fi
  sleep 1
done
[ "$opened" = 1 ] || fail "тестовая копия не открыла переданный PDF"
if [ "${GITHUB_ACTIONS:-}" = true ]; then
  original_theme="$(osascript -e 'tell application "System Events" to get dark mode of appearance preferences' 2>/dev/null)" || original_theme=""
fi
if [ "$original_theme" = true ] || [ "$original_theme" = false ]; then
  for theme in light dark; do
    dark=false
    [ "$theme" = dark ] && dark=true
    if set_dark_mode "$dark"; then
      sleep 2
      screencapture -x "$OUT/macos_screen_$theme.png" || echo "screencapture недоступен"
    fi
  done
else
  screencapture -x "$OUT/macos_screen.png" || echo "screencapture недоступен"
fi
LOG="$HOME/Library/Application Support/PdfTool/log.txt"
if [ -f "$LOG" ]; then tail -40 "$LOG" > "$OUT/log_tail.txt"; grep -q "Запуск" "$LOG" || fail "в журнале нет записи о запуске"
else fail "нет журнала в ~/Library/Application Support/PdfTool"; fi
cp "$ST"/selftest*.png "$OUT/" 2>/dev/null || true
echo "FAILED=$FAILED"
exit $FAILED
