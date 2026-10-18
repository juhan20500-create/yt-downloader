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


def _has_impersonation(path):
    """이 yt-dlp 가 브라우저 흉내를 낼 수 있는지. 틱톡에 꼭 필요하다."""
    try:
        proc = subprocess.run([path, "--list-impersonate-targets"],
                              capture_output=True, text=True, timeout=30)
        return any("unavailable" not in ln and "curl_cffi" in ln
                   for ln in (proc.stdout or "").splitlines())
    except (OSError, subprocess.SubprocessError):
        return False


def _ytdlp_version(path):
    """'2026.08.19' 같은 버전을 비교할 수 있게 숫자 묶음으로. 모르면 (0,)."""
    try:
        out = subprocess.run([path, "--version"], capture_output=True,
                             text=True, timeout=60).stdout.strip()
        return tuple(int(x) for x in out.split("."))
    except (OSError, subprocess.SubprocessError, ValueError):
        return (0,)


def ensure_ytdlp():
    if os.environ.get("YT_DLP_PATH"):
        return os.environ["YT_DLP_PATH"]
    name = ("yt-dlp.exe" if sys.platform == "win32"
            else "yt-dlp_macos" if sys.platform == "darwin" else "yt-dlp")
    local = os.path.join(data_dir(), name)
    # 1) 빌드에 함께 포장된 것 — 인터넷 없이도 바로 쓴다.
    #    포장본은 실행할 때마다 임시 폴더에 새로 풀리므로, 거기서 -U 로 업데이트해도
    #    끄면 사라진다. 그래서 사용자 폴더로 복사해 그 복사본을 쓰고 업데이트한다.
    #    복사본이 포장본보다 낡았을 때(업데이트가 막혔던 경우)만 다시 덮어쓴다.
    for n in (_exe("yt-dlp"), "yt-dlp"):
        p = os.path.join(_bundle_dir(), n)
        if os.path.exists(p):
            _mark_exec(p)
            try:
                if not os.path.exists(local) or _ytdlp_version(local) < _ytdlp_version(p):
                    shutil.copy2(p, local)
                    _mark_exec(local)
                return local
            except OSError:
                return p
    # 2) 컴퓨터에 설치된 것. 단 브라우저 흉내를 못 내면 틱톡이 통째로 안 되므로,
    #    그럴 때는 그 기능이 들어 있는 단독 실행본을 대신 쓴다.
    #    (홈브루로 깐 yt-dlp 에는 curl_cffi 가 빠져 있다)
    found = shutil.which("yt-dlp")
    if found and _has_impersonation(found):
        return found
    # 3) 이미 받아둔 것
    if os.path.exists(local):
        return local
    if found:
        print("설치된 yt-dlp 는 틱톡을 받지 못해, 기능이 들어 있는 것을 내려받습니다.")
    # 4) 마지막으로 내려받기 (백신이나 네트워크가 막으면 여기서 실패한다)
    print("yt-dlp를 처음 한 번 내려받는 중… (약 30MB)")
    url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{name}"
    urllib.request.urlretrieve(url, local)
    _mark_exec(local)
    print("yt-dlp 준비 완료.")
    return local


def update_ytdlp_bg(path):
    try:
        subprocess.run([path, "-U"], capture_output=True, timeout=60)
    except Exception:
        pass
