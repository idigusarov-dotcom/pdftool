"""Проверки PdfTool.exe на уровне Windows: самопроверка в длинном пути, на сетевом пути, от обычного
пользователя (без прав администратора), с пометкой «скачано из интернета»; подпись, манифест.

    python qa/win_system.py <PdfTool.exe> <папка отчёта>
"""
import json
import os
import shutil
import subprocess
import sys
import time

EXE, OUT = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
os.makedirs(OUT, exist_ok=True)
R = {}


def selftest(label, out_dir, exe=EXE, timeout=240):
    os.makedirs(out_dir, exist_ok=True)
    t = time.monotonic()
    try:
        p = subprocess.run([exe, "--self-test", out_dir], timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    js = os.path.join(out_dir, "selftest.json")
    res = {"exit": rc, "sec": round(time.monotonic() - t, 1), "dir_len": len(out_dir)}
    if os.path.exists(js):
        j = json.load(open(js, encoding="utf-8"))
        res["ok"] = j.get("ok")
        res["failed"] = {k: v.get("error", "")[-400:] for k, v in j.get("checks", {}).items() if not v.get("ok")}
    else:
        res["ok"] = False
        res["failed"] = "selftest.json не создан"
    R[label] = res
    print(label, json.dumps(res, ensure_ascii=False), flush=True)


# 0. манифест: longPathAware
data = open(EXE, "rb").read()
R["manifest_longPathAware"] = b"longPathAware" in data
R["manifest_dpiAware"] = (b"dpiAware" in data, b"PerMonitorV2" in data)
try:
    import winreg
    k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem")
    R["LongPathsEnabled_registry"] = winreg.QueryValueEx(k, "LongPathsEnabled")[0]
except OSError as e:
    R["LongPathsEnabled_registry"] = f"нет ({e})"

# 1. обычная самопроверка
base = os.path.join(os.environ["RUNNER_TEMP"], "Самопроверка")
selftest("обычная папка", os.path.join(base, "обычная"))
# 2. длинный путь (>260)
d = os.path.join(base, "длинный")
while len(d) < 270:
    d = os.path.join(d, "очень длинное имя папки")
try:
    selftest("длинный путь >260", d)
except Exception as e:  # noqa: BLE001
    R["длинный путь >260"] = f"не удалось подготовить: {e}"
# 3. сетевой путь
unc = os.environ.get("QA_UNC")
if unc:
    selftest("сетевой путь UNC", os.path.join(unc, "самопроверка по сети"))
# 4. пометка «скачано из интернета» (Zone.Identifier = 3)
motw = os.path.join(base, "скачанный", "PdfTool.exe")
os.makedirs(os.path.dirname(motw), exist_ok=True)
shutil.copy(EXE, motw)
with open(motw + ":Zone.Identifier", "w") as f:
    f.write("[ZoneTransfer]\nZoneId=3\nHostUrl=https://github.com/\n")
selftest("с пометкой «из интернета»", os.path.join(base, "motw"), exe=motw)
# 5. обычный пользователь без прав администратора
try:
    import win32con
    import win32event
    import win32process
    user, pw = "qa_user", "Qa-Пароль-2026!x"
    subprocess.run(["net", "user", user, pw, "/add"], capture_output=True)
    pub = os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "PdfTool QA")
    shutil.rmtree(pub, ignore_errors=True)
    os.makedirs(pub)
    subprocess.run(["icacls", pub, "/grant", "Everyone:(OI)(CI)F"], capture_output=True)
    exe_pub = os.path.join(pub, "PdfTool.exe")
    shutil.copy(EXE, exe_pub)
    outd = os.path.join(pub, "самопроверка обычного пользователя")
    si = win32process.STARTUPINFO()
    t = time.monotonic()
    hp, ht, pid, tid = win32process.CreateProcessWithLogonW(
        user, ".", pw, win32con.LOGON_WITH_PROFILE, exe_pub, f'"{exe_pub}" --self-test "{outd}"',
        0, None, pub, si)
    win32event.WaitForSingleObject(hp, 240000)
    rc = win32process.GetExitCodeProcess(hp)
    js = os.path.join(outd, "selftest.json")
    res = {"exit": rc, "sec": round(time.monotonic() - t, 1)}
    groups = subprocess.run(["net", "user", user], capture_output=True, text=True, errors="replace").stdout
    res["groups"] = [ln.strip() for ln in groups.splitlines() if "Group" in ln or "Групп" in ln]
    if os.path.exists(js):
        j = json.load(open(js, encoding="utf-8"))
        res["ok"] = j.get("ok")
        res["failed"] = {k: v.get("error", "")[-400:] for k, v in j.get("checks", {}).items() if not v.get("ok")}
    else:
        res["ok"] = False
    R["обычный пользователь (не админ)"] = res
    print("non-admin", json.dumps(res, ensure_ascii=False), flush=True)
except Exception:  # noqa: BLE001
    import traceback
    R["обычный пользователь (не админ)"] = "ошибка подготовки: " + traceback.format_exc()[-600:]

json.dump(R, open(os.path.join(OUT, "win_system.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(R, ensure_ascii=False, indent=1))
