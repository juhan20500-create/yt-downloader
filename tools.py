# Copyright (c) 2026 juhan20500-create. All rights reserved.
# 개인 사용만 허용. 재배포·공유·판매 금지. 자세한 내용은 LICENSE 참고.
# Personal use only. Redistribution prohibited. See LICENSE.
"""외부 도구(yt-dlp, ffmpeg) 경로 관리.

- yt-dlp: PATH에 있으면 그걸, 없으면 최신 실행파일을 사용자 폴더에 자동 다운로드.
          (표준 빌드에 impersonation 포함 → 유튜브·틱톡 모두 처리)
- ffmpeg/ffprobe: 빌드에 함께 포장된 것을 우선 사용(_MEIPASS), 없으면 PATH.
"""
import os
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile

APP_NAME = "yt-downloader"


def data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.path.expanduser("~/.local/share")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _bundle_dir():
    """PyInstaller로 포장된 경우 임시 추출 폴더, 아니면 이 파일 폴더."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _exe(name):
    return name + ".exe" if sys.platform == "win32" else name


def _mark_exec(p):
    if sys.platform != "win32" and os.path.exists(p):
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)


def _ensure_from(name):
    """ffmpeg 또는 ffprobe 정적 실행파일을 확보한다.
    우선순위: 포장본 > PATH > 자동 다운로드(정적 빌드)."""
    exe = _exe(name)
    # 1) 빌드에 포장된 것
    p = os.path.join(_bundle_dir(), exe)
    if os.path.exists(p):
        return p
    # 2) 이미 받아둔 것
    local = os.path.join(data_dir(), exe)
    if os.path.exists(local):
        return local
    # 3) PATH
    found = shutil.which(name)
    if found:
        return found
    # 4) 정적 빌드 다운로드
    try:
        _download_static(name, local)
        _mark_exec(local)
        if os.path.exists(local):
            return local
    except Exception as e:
        print(f"[경고] {name} 자동 준비 실패: {e}")
    return name  # 최후: PATH 이름 그대로 (실패 시 에러 메시지 유도)


def _download_static(name, dest):
    """OS별 정적 ffmpeg/ffprobe를 내려받아 dest에 놓는다."""
    tmp = dest + ".download"
    if sys.platform == "win32":
        # BtbN 정적 빌드(zip) — bin/ 안에 ffmpeg.exe, ffprobe.exe
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        zpath = os.path.join(data_dir(), "_ff_win.zip")
        if not os.path.exists(zpath):
            print("ffmpeg를 처음 한 번 내려받는 중… (약 80MB)")
            urllib.request.urlretrieve(url, zpath)
        with zipfile.ZipFile(zpath) as z:
            member = next(m for m in z.namelist() if m.endswith(f"bin/{name}.exe"))
            with z.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:
        # macOS/리눅스: evermeet 정적 빌드(zip) — 개별 바이너리
        print(f"{name}를 처음 한 번 내려받는 중…")
        url = f"https://evermeet.cx/ffmpeg/getrelease/{name}/zip"
        urllib.request.urlretrieve(url, tmp)
        with zipfile.ZipFile(tmp) as z:
            member = next(m for m in z.namelist() if os.path.basename(m) == name)
            with z.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
        os.remove(tmp)


def ffmpeg_path():
    return _ensure_from("ffmpeg")


def ffprobe_path():
    return _ensure_from("ffprobe")


def ensure_ytdlp():
    if os.environ.get("YT_DLP_PATH"):
        return os.environ["YT_DLP_PATH"]
    found = shutil.which("yt-dlp")
    if found:
        return found
    name = ("yt-dlp.exe" if sys.platform == "win32"
            else "yt-dlp_macos" if sys.platform == "darwin" else "yt-dlp")
    local = os.path.join(data_dir(), name)
    if not os.path.exists(local):
        print("yt-dlp를 처음 한 번 내려받는 중… (약 30MB)")
        url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{name}"
        urllib.request.urlretrieve(url, local)
        if sys.platform != "win32":
            os.chmod(local, os.stat(local).st_mode | stat.S_IEXEC)
        print("yt-dlp 준비 완료.")
    return local


def update_ytdlp_bg(path):
    try:
        subprocess.run([path, "-U"], capture_output=True, timeout=60)
    except Exception:
        pass
