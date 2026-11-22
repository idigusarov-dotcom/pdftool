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
    import win32event
    import win32process
    user, pw = "qa_user", "Qa-Passw0rd-2026!x"   # только ASCII: кириллица в «net user» портится кодировкой консоли
    subprocess.run(["net", "user", user, "/delete"], capture_output=True)
    add = subprocess.run(["net", "user", user, pw, "/add"], capture_output=True, text=True, errors="replace")
    if add.returncode != 0:
        raise OSError(f"net user /add: {add.returncode} {add.stdout} {add.stderr}")
    pub = os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "PdfTool QA")
    shutil.rmtree(pub, ignore_errors=True)
    os.makedirs(pub)
    subprocess.run(["icacls", pub, "/grant", "Everyone:(OI)(CI)F"], capture_output=True)
    exe_pub = os.path.join(pub, "PdfTool.exe")
    shutil.copy(EXE, exe_pub)
    outd = os.path.join(pub, "самопроверка обычного пользователя")
    import ctypes
    from ctypes import wintypes

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()
    cmd = ctypes.create_unicode_buffer(f'"{exe_pub}" --self-test "{outd}"')
    t = time.monotonic()
    ok = ctypes.windll.advapi32.CreateProcessWithLogonW(user, ".", pw, 1, exe_pub, cmd, 0, None, pub,
                                                        ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise OSError(f"CreateProcessWithLogonW: {ctypes.GetLastError()}")
    win32event.WaitForSingleObject(int(pi.hProcess), 240000)
    rc = win32process.GetExitCodeProcess(int(pi.hProcess))
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
