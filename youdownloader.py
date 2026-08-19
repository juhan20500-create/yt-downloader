# Copyright (c) 2026 juhan20500-create. All rights reserved.
# 개인 사용만 허용. 재배포·공유·판매 금지. 자세한 내용은 LICENSE 참고.
# Personal use only. Redistribution prohibited. See LICENSE.
import os
import glob
import json
import re
import shutil
import sys
import subprocess
import time
from fractions import Fraction

# =========================================================
# 설정
# =========================================================
DOWNLOAD_DIR_NAME = "다운받은 영상"
MAX_FILENAME_LEN = 140
def _chrome_user_data_dir():
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    return os.path.expanduser("~/.config/google-chrome")


def list_chrome_profiles():
    """이 컴퓨터의 크롬 프로필 목록. 크롬이 저장해 둔 표시 이름까지 함께 준다.

    폴더 이름(Default, Profile 1 …)만 보여주면 어느 계정인지 알 수 없어서,
    크롬의 Local State 에 있는 이름과 메일 주소를 같이 보여 준다.
    """
    base = _chrome_user_data_dir()
    if not os.path.isdir(base):
        return []
    names = {}
    try:
        with open(os.path.join(base, "Local State"), encoding="utf-8") as f:
            names = json.load(f).get("profile", {}).get("info_cache", {})
    except (OSError, json.JSONDecodeError):
        pass
    out = []
    for d in sorted(os.listdir(base)):
        if d != "Default" and not d.startswith("Profile "):
            continue
        if not os.path.isdir(os.path.join(base, d, "Network")) and \
           not os.path.exists(os.path.join(base, d, "Cookies")):
            continue                      # 쿠키가 없는 껍데기 폴더는 뺀다
        info = names.get(d) or {}
        out.append({"id": d,
                    "label": info.get("name") or d,
                    "mail": info.get("user_name") or ""})
    return out


def _chrome_last_used():
    """크롬이 마지막으로 쓴 프로필. 사람이 실제로 로그인해 쓰는 것일 확률이 높다."""
    try:
        with open(os.path.join(_chrome_user_data_dir(), "Local State"), encoding="utf-8") as f:
            return json.load(f).get("profile", {}).get("last_used") or ""
    except (OSError, json.JSONDecodeError):
        return ""


def settings_path():
    import tools
    return os.path.join(tools.data_dir(), "settings.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(d):
    with open(settings_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def _auto_cookies_browser():
    """쿠키로 쓸 크롬 프로필을 정한다.

    쿠키가 없으면 유튜브가 "봇이 아님을 확인하라"며 막거나, 샤오홍슈처럼
    로그인이 필요한 곳에서 내용을 안 내준다. 프리미엄 전용 영상도 로그인된
    프로필이라야 본편이 받아진다. 아니면 예고편만 내려온다.

    순서: 환경변수 → 화면에서 고른 값 → 크롬이 마지막에 쓴 프로필 → 첫 프로필.
    예전에는 무조건 Default 를 골랐는데, 프로필이 여럿인 사람은 로그인 안 된
    계정이 걸려 엉뚱한 결과를 받았다.
    """
    want = os.environ.get("YTDLP_COOKIES_BROWSER", "").strip()
    if want:
        return want
    avail = [p["id"] for p in list_chrome_profiles()]
    if not avail:
        return ""
    chosen = load_settings().get("chrome_profile")
    if chosen in avail:
        return f"chrome:{chosen}"
    last = _chrome_last_used()
    if last in avail:
        return f"chrome:{last}"
    return f"chrome:{avail[0]}"


COOKIES_BROWSER = _auto_cookies_browser()


def set_chrome_profile(profile_id):
    """화면에서 고른 프로필을 저장하고 곧바로 반영한다."""
    global COOKIES_BROWSER, COOKIES_DISABLED
    s = load_settings()
    if profile_id:
        s["chrome_profile"] = profile_id
    else:
        s.pop("chrome_profile", None)     # 빈 값이면 자동 선택으로 되돌린다
    save_settings(s)
    COOKIES_DISABLED = False              # 프로필을 바꿨으니 다시 시도해 볼 만하다
    try:                                  # 뽑아둔 쿠키는 옛 계정 것이라 버린다
        os.remove(cookie_file_path())
    except OSError:
        pass
    COOKIES_BROWSER = _auto_cookies_browser()
    return COOKIES_BROWSER
# yt-dlp / ffprobe 경로는 tools가 확보한다(자동 다운로드/포장 포함). 실행 시 주입됨.
YT_DLP_PATH = os.environ.get("YT_DLP_PATH", "yt-dlp")
FFPROBE_PATH = "ffprobe"
FFMPEG_PATH = "ffmpeg"
METADATA_TIMEOUT_SEC = 30
DOWNLOAD_INDEX_FILENAME = ".downloaded_index.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

# 최적 화질 오디오/비디오 병합 포맷 (쇼츠/롱폼 동일하게 최고화질 선택)
FORMAT_SELECTOR = "bv*+ba/b"

# =========================================================
# 유틸
# =========================================================
def is_tiktok(url: str):
    return "tiktok.com" in url.lower()

def get_ytdlp_path(url: str):
    # 단일 yt-dlp(표준 빌드에 impersonation 포함)로 유튜브·틱톡 모두 처리
    return YT_DLP_PATH

# 쿠키를 못 읽어서 실패한 것인지 알아보는 표시들.
# 윈도우에서는 크롬이 켜져 있으면 쿠키 파일이 잠겨 복사되지 않는다.
# 이건 영상 문제가 아니므로, 쿠키 없이 다시 하면 대개 받아진다.
COOKIE_FAIL_SIGNS = (
    "could not copy chrome cookie database",
    "could not copy cookie database",
    "could not find chrome cookies database",
    "could not find cookies database",
    "failed to decrypt",
    "unable to read cookies",
    "cookies from browser",
)

COOKIES_FROM_BROWSER_FAILED = False  # 브라우저에서 직접 못 읽어 파일로 넘어간 상태
COOKIES_DISABLED = False             # 그마저 안 되어 쿠키를 아예 안 쓰는 상태


def cookie_problem(text: str):
    low = (text or "").lower()
    return any(s in low for s in COOKIE_FAIL_SIGNS)


def note_cookie_failure(text: str):
    """쿠키 때문에 실패했으면 한 단계 물러서고 True 를 돌려준다(=다시 해볼 만하다).

    브라우저에서 직접 읽기 → 뽑아 둔 파일 → 쿠키 없음 순으로 내려간다.
    """
    global COOKIES_FROM_BROWSER_FAILED, COOKIES_DISABLED
    if COOKIES_DISABLED or not COOKIES_BROWSER or not cookie_problem(text):
        return False
    if not COOKIES_FROM_BROWSER_FAILED:
        COOKIES_FROM_BROWSER_FAILED = True
        return True
    COOKIES_DISABLED = True
    return True


def strip_cookie_args(cmd):
    """이미 만들어 둔 명령에서 쿠키 관련 부분만 빼낸다."""
    out, skip = [], False
    for a in cmd:
        if skip:
            skip = False
            continue
        if a in ("--cookies-from-browser", "--cookies"):
            skip = True
            continue
        out.append(a)
    return out


def reset_cookie_args(cmd):
    """명령에서 쿠키 부분을 떼고, 지금 상태에 맞는 쿠키 방식으로 다시 붙인다."""
    out = strip_cookie_args(cmd)
    return out[:1] + _cookie_args() + out[1:]


def run_ytdlp(cmd, **kw):
    """yt-dlp 를 돌린다. 쿠키를 못 읽어 실패하면 한 단계 물러서서 다시 해본다."""
    proc = subprocess.run(cmd, **kw)
    while proc.returncode != 0:
        both = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if not note_cookie_failure(both):
            break
        proc = subprocess.run(reset_cookie_args(cmd), **kw)
    return proc


def cookie_file_path():
    """뽑아둔 쿠키를 보관할 파일 위치."""
    import tools
    return os.path.join(tools.data_dir(), "cookies.txt")


def export_cookies(max_age_days=3):
    """크롬 쿠키를 파일로 한 번 뽑아 둔다.

    유튜브는 일부 영상을 로그인한 사람에게만 내준다. 쿠키 없이 받으면
    주소까지는 나오는데 정작 파일을 받을 때 403 으로 막힌다.

    그런데 크롬이 켜져 있으면 쿠키 파일이 잠겨 브라우저에서 직접 읽지
    못한다(윈도우에서는 거의 항상 그렇다). 그래서 읽을 수 있을 때 파일로
    뽑아 두고, 그다음부터는 그 파일을 쓴다. 크롬을 켜 두어도 상관없어진다.

    실패해도 조용히 넘어간다. 이미 뽑아 둔 파일이 있으면 그것을 계속 쓴다.
    """
    if not COOKIES_BROWSER:
        return None
    path = cookie_file_path()
    if os.path.exists(path):
        age = (time.time() - os.path.getmtime(path)) / 86400
        if age < max_age_days:
            return path                      # 아직 쓸 만하다
    try:
        proc = subprocess.run(
            [YT_DLP_PATH, "--cookies-from-browser", COOKIES_BROWSER,
             "--cookies", path, "--skip-download", "--simulate",
             "https://www.youtube.com/watch?v=BaW_jenozKc"],
            capture_output=True, text=True, timeout=90,
        )
        if proc.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return path if os.path.exists(path) else None


def _cookie_args():
    """쿠키를 어디서 가져올지 정한다.

    브라우저에서 직접 읽는 것이 가장 잘 된다. 그때그때 살아 있는 값을
    가져오기 때문이다. 파일로 뽑아 두면 일부 값이 굳어서, 유튜브가
    로그인 확인을 요구하는 영상에서 403 으로 막히는 일이 생긴다.

    그래서 브라우저에서 직접 읽기를 먼저 쓰고, 그게 실패했을 때만
    (윈도우에서 크롬이 켜져 있어 쿠키 파일이 잠긴 경우) 뽑아 둔 파일로
    넘어간다. 아예 못 받는 것보다는 낫기 때문이다.
    """
    if COOKIES_DISABLED:
        return []
    if COOKIES_BROWSER and not COOKIES_FROM_BROWSER_FAILED:
        return ["--cookies-from-browser", COOKIES_BROWSER]
    path = cookie_file_path()
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return ["--cookies", path]
    return []

def _ffmpeg_args():
    # 포장/자동확보된 ffmpeg 위치를 yt-dlp에 알려준다 (병합에 필요)
    return ["--ffmpeg-location", FFMPEG_PATH] if FFMPEG_PATH and FFMPEG_PATH != "ffmpeg" else []

def get_common_ytdlp_args(url: str):
    if is_tiktok(url):
        # 틱톡: 브라우저 impersonation 필수 (없으면 페이지 파싱 실패)
        return [
            *_cookie_args(),
            *_ffmpeg_args(),
            "--impersonate", "chrome",
            "--user-agent", USER_AGENT,
        ]
    return [
        *_cookie_args(),
        *_ffmpeg_args(),
        "--extractor-args",
        "youtube:player_client=web_creator,default",
        "--user-agent", USER_AGENT,
    ]

def ensure_tool_exists(name: str):
    return shutil.which(name) is not None

def sanitize_filename(name: str):
    name = "".join(c for c in name if c not in r'\/:*?"<>|')
    return name.strip().rstrip(".")

def truncate_filename(name: str):
    # 한글은 UTF-8에서 글자당 3바이트라 글자수 대신 바이트 기준으로 잘라야
    # macOS 파일명 제한(APFS 255바이트)을 안 넘김
    encoded = name.strip().encode("utf-8")
    if len(encoded) <= MAX_FILENAME_LEN:
        return name.strip()
    truncated = encoded[:MAX_FILENAME_LEN]
    while truncated:
        try:
            return truncated.decode("utf-8").strip()
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""

def extract_video_id(url: str):
    for pattern in (
        r"shorts/([a-zA-Z0-9_-]{11})",
        r"[?&]v=([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"embed/([a-zA-Z0-9_-]{11})",
    ):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None

def load_download_index(folder: str):
    path = os.path.join(folder, DOWNLOAD_INDEX_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_download_index(folder: str, index: dict):
    path = os.path.join(folder, DOWNLOAD_INDEX_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

def parse_fps(v):
    try:
        return float(Fraction(v))
    except (ValueError, ZeroDivisionError):
        return None

def find_latest_downloaded_file(folder: str):
    files = []
    for ext in ["*.mp4", "*.mkv", "*.webm", "*.mov"]:
        files.extend(glob.glob(os.path.join(folder, ext)))
    
    files = [f for f in files if not f.endswith(".part") and not f.endswith(".ytdl")]
    if not files:
        return None

    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return files[0]

# =========================================================
# ffprobe 분석
# =========================================================
def get_media_info(path: str):
    cmd = [
        FFPROBE_PATH, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate,bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=METADATA_TIMEOUT_SEC
        )
        lines = [x.strip() for x in proc.stdout.splitlines() if x.strip()]
        if len(lines) < 4:
            return None

        codec = lines[0]
        width = int(lines[1])
        height = int(lines[2])
        fps = parse_fps(lines[3])

        bitrate = 0
        if len(lines) >= 5:
            try:
                bitrate = int(lines[4])
            except ValueError:
                pass

        return {"codec": codec, "width": width, "height": height, "fps": fps, "bitrate": bitrate}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, ValueError, IndexError):
        return None

# =========================================================
# 제목 추출
# =========================================================
def get_video_title(url: str):
    cmd = [get_ytdlp_path(url),
    *get_common_ytdlp_args(url),

    "--no-playlist",

    "--get-title",

    url]
    try:
        proc = run_ytdlp(
            cmd, capture_output=True, text=True, timeout=METADATA_TIMEOUT_SEC
        )
        title = proc.stdout.strip() if proc.returncode == 0 else ""
        if not title:
            return "video"
        return truncate_filename(sanitize_filename(title))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "video"

# =========================================================
# 화질 목록 조회
# =========================================================
def get_available_resolutions(url: str):
    cmd = [
        get_ytdlp_path(url),
        *get_common_ytdlp_args(url),
        "--no-playlist",
        "--no-warnings",
        "--dump-json",
        url,
    ]
    try:
        proc = run_ytdlp(
            cmd, capture_output=True, text=True, timeout=METADATA_TIMEOUT_SEC
        )
        data = json.loads(proc.stdout)
        labels = set()
        for f in data.get("formats", []):
            if f.get("vcodec") in (None, "none"):
                continue
            # 세로 영상은 height가 width보다 커서 "Xp" 라벨은 짧은 변 기준이어야 함.
            # yt-dlp가 이미 계산해둔 format_note("1080p" 등)를 우선 신뢰.
            note = f.get("format_note") or ""
            m = re.match(r"(\d+)p", note)
            if m:
                labels.add(int(m.group(1)))
            elif f.get("width") and f.get("height"):
                labels.add(min(f["width"], f["height"]))
        return sorted(labels)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return []

# =========================================================
# 다운로드 명령 생성
# =========================================================
def build_download_command(url: str, folder: str, title: str):
    # yt-dlp가 -o 템플릿에서 %를 필드 참조(%(...)s)로 해석하므로 리터럴 %는 이스케이프해야 함
    safe_title = title.replace("%", "%%")
    output_template = os.path.join(folder, f"{safe_title}.%(ext)s")

    cmd = [
        get_ytdlp_path(url),
        *get_common_ytdlp_args(url),
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        "--format", FORMAT_SELECTOR,
        "--concurrent-fragments", "1",
        "--extractor-retries", "10",
        "--fragment-retries", "10",
        "--retry-sleep", "2",
        "--force-overwrites",
        "-o", output_template,
        url
    ]
    return cmd

# =========================================================
# 로그 분석
# =========================================================
def analyze_output(text: str):
    logs = []
    lower = text.lower()
    if re.search(r"http error 403|403[:\s]+forbidden", lower):
        logs.append("⚠️ 유튜브 서버 권한 거부(403 Forbidden)가 감지되었습니다.")
    if "sabr" in lower:
        logs.append("⚠️ 유튜브의 실시간 스트리밍 제한(SABR 방식)이 감지되어 고화질이 필터링되었습니다.")
    if "nsig" in lower:
        logs.append("⚠️ nsig 서명 제한이 작동하여 특정 포맷 매칭이 방해받았습니다.")
    if "unplayable" in lower:
        logs.append("⚠️ 유튜브가 이 기기에서의 재생을 차단(UNPLAYABLE)하여 우회 경로를 사용했습니다.")
    if "403" in lower and "forbidden" in lower:
        logs.append("⚠️ 유튜브가 접근을 거부했습니다(403). 로그인이 필요한 영상일 수 있습니다 — "
                    "크롬에서 그 영상이 보이는 계정으로 로그인한 뒤, 아래에서 계정을 골라 다시 시도하세요.")
    if "po_token" in lower or "po token" in lower:
        logs.append("⚠️ 외부 인증 토큰(PO Token) 누락으로 고화질 세션이 거부되었습니다.")
    if cookie_problem(lower):
        logs.append("⚠️ 크롬 쿠키를 읽지 못해 쿠키 없이 받았습니다. "
                    "로그인이 필요한 영상이면 크롬을 완전히 끄고 다시 시도하세요.")
    return list(set(logs))

# =========================================================
# 다운로드 실행
# =========================================================
def download_video(url: str, folder: str, title: str):
    cmd = build_download_command(url, folder, title)
    process = run_ytdlp(cmd, capture_output=True, text=True)
    output = (process.stdout or "") + "\n" + (process.stderr or "")
    latest = find_latest_downloaded_file(folder)
    success = process.returncode == 0
    return success, latest, output

# =========================================================
# 메인 가이드 제어 부
# =========================================================
def download_full_video(url: str, folder: str):
    video_id = extract_video_id(url)
    index = load_download_index(folder) if video_id else {}

    if video_id and video_id in index:
        existing_path = os.path.join(folder, index[video_id])
        if os.path.exists(existing_path):
            os.utime(existing_path, None)
            print(f"🔁 원래 있던 영상입니다: {os.path.basename(existing_path)}")
            return

    resolutions = get_available_resolutions(url)
    if resolutions:
        print("📺 사용 가능 화질: " + ", ".join(f"{h}p" for h in resolutions))
    else:
        print("📺 사용 가능 화질: 조회 실패")

    title = get_video_title(url)
    success, saved_file, output = download_video(url, folder, title)

    if not success:
        print("❌ 다운로드 실패")
        for hint in analyze_output(output):
            print(f"   {hint}")
        print("\n--- 에러 로그 (복붙용) ---")
        print(output.strip())
        print("--------------------------")
        return

    if video_id:
        index[video_id] = os.path.basename(saved_file)
        save_download_index(folder, index)

    info = get_media_info(saved_file)
    achieved = min(info["width"], info["height"]) if info else None
    print(f"🎯 선택된 화질: {achieved}p" if achieved else "🎯 선택된 화질: 확인 불가")
    print(f"✅ 다운로드 완료: {os.path.basename(saved_file)}")

    if achieved and resolutions and achieved < max(resolutions):
        print(f"\n⚠️ 최고화질({max(resolutions)}p) 대신 {achieved}p로 저장됨")
        hints = analyze_output(output)
        if hints:
            for hint in hints:
                print(f"   {hint}")
        else:
            print("   정확한 원인 불명 (네트워크 상태 또는 포맷 매칭 문제로 추정)")

# =========================================================
# 시작
# =========================================================
if __name__ == "__main__":
    if not ensure_tool_exists(YT_DLP_PATH):
        print("❌ yt-dlp 미설치")
        exit()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    download_dir = os.path.join(base_dir, DOWNLOAD_DIR_NAME)
    os.makedirs(download_dir, exist_ok=True)

    print("\n🎬 동영상 다운로더")
    print("=" * 60)

    while True:
        try:
            url = input("\n🔗 URL 입력: ").strip()
            if not url:
                continue
            download_full_video(url, download_dir)
        except KeyboardInterrupt:
            print("\n👋 프로그램 종료")
            break
        except Exception as e:
            print(f"\n❌ 시스템 오류: {e}")