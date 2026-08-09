# Copyright (c) 2026 juhan20500-create. All rights reserved.
# 개인 사용만 허용. 재배포·공유·판매 금지. 자세한 내용은 LICENSE 참고.
# Personal use only. Redistribution prohibited. See LICENSE.
import os
import glob
import json
import re
import shutil
import subprocess
from fractions import Fraction

# =========================================================
# 설정
# =========================================================
DOWNLOAD_DIR_NAME = "다운받은 영상"
MAX_FILENAME_LEN = 140
# 쿠키는 선택 사항. 로그인 상태를 쓰려면 환경변수로 브라우저를 지정 (예: "chrome" 또는 "chrome:Profile 2")
COOKIES_BROWSER = os.environ.get("YTDLP_COOKIES_BROWSER", "").strip()
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

def _cookie_args():
    return ["--cookies-from-browser", COOKIES_BROWSER] if COOKIES_BROWSER else []

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
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=METADATA_TIMEOUT_SEC
        )
        title = proc.stdout.strip()
        if not title:
            return "video"
        return truncate_filename(sanitize_filename(title))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
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
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=METADATA_TIMEOUT_SEC
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
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
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
    if "po_token" in lower or "po token" in lower:
        logs.append("⚠️ 외부 인증 토큰(PO Token) 누락으로 고화질 세션이 거부되었습니다.")
    return list(set(logs))

# =========================================================
# 다운로드 실행
# =========================================================
def download_video(url: str, folder: str, title: str):
    cmd = build_download_command(url, folder, title)
    process = subprocess.run(cmd, capture_output=True, text=True)
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