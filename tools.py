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


def ffmpeg_path():
    p = os.path.join(_bundle_dir(), _exe("ffmpeg"))
    if os.path.exists(p):
        return p
    return shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_path():
    p = os.path.join(_bundle_dir(), _exe("ffprobe"))
    if os.path.exists(p):
        return p
    return shutil.which("ffprobe") or "ffprobe"


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
