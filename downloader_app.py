"""동영상 다운로더 — 채널 모니터와 같은 로컬 웹 UI 버전.

기존 CLI(youdownloader.py)의 다운로드 로직을 그대로 재사용하고,
브라우저에서 URL 붙여넣기 → 실시간 진행률 → 결과 카드로 보여준다.
완전 로컬. yt-dlp / ffmpeg.
"""
import json
import os
import re
import subprocess
import sys
import webbrowser
from threading import Timer

from flask import Flask, request, jsonify, Response, send_file

# 기존 CLI 다운로더의 검증된 로직을 그대로 가져다 쓴다.
sys.path.insert(0, "/Users/juhan")
import youdownloader as yd  # noqa: E402

PORT = 5055
DOWNLOAD_DIR = os.path.join("/Users/juhan", yd.DOWNLOAD_DIR_NAME)
PREVIEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview")

app = Flask(__name__)


def open_browser(url):
    """크롬으로 연다. 크롬이 없으면 기본 브라우저로 연다.

    APP_BROWSER 환경변수로 바꿀 수 있다. (chrome / edge / default)
    """
    import os
    import shutil
    import subprocess
    import sys
    import webbrowser

    want = (os.environ.get("APP_BROWSER") or "chrome").strip().lower()
    if want == "default":
        webbrowser.open(url)
        return

    if sys.platform == "darwin":
        names = {"chrome": "Google Chrome", "edge": "Microsoft Edge"}
        app = names.get(want)
        if app and os.path.isdir(f"/Applications/{app}.app"):
            subprocess.Popen(["open", "-a", app, url])
            return
    elif sys.platform == "win32":
        cands = {
            "chrome": [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            ],
            "edge": [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ],
        }.get(want, [])
        for p in cands:
            if os.path.exists(p):
                subprocess.Popen([p, url])
                return
    else:
        exe = shutil.which("google-chrome") or shutil.which("chromium")
        if want == "chrome" and exe:
            subprocess.Popen([exe, url])
            return

    webbrowser.open(url)   # 못 찾으면 기본 브라우저


def build_streaming_command(url, folder, title, audio_only=False):
    """기존 다운로드 명령에 실시간 진행률 출력(newline + progress-template)을 더한다.

    audio_only=True 면 영상 없이 소리만 받아 mp3로 저장한다.
    """
    safe_title = title.replace("%", "%%")
    output_template = os.path.join(folder, f"{safe_title}.%(ext)s")
    cmd = [
        yd.get_ytdlp_path(url),
        *yd.get_common_ytdlp_args(url),
        "--no-playlist",
        "--newline",
        "--progress-template",
        "PROG|%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
    ]
    if audio_only:
        cmd += [
            "--format", "bestaudio/best",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",   # 최고 품질
        ]
    else:
        cmd += [
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
            "--format", yd.FORMAT_SELECTOR,
        ]
    cmd += [
        "--concurrent-fragments", "1",
        "--extractor-retries", "10",
        "--fragment-retries", "10",
        "--retry-sleep", "2",
        "--force-overwrites",
        "-o", output_template,
        url,
    ]
    return cmd


def find_latest_audio(folder):
    """가장 최근에 만들어진 오디오 파일을 찾는다(공용 함수는 영상만 찾아서 별도로 둔다)."""
    import glob
    files = []
    for ext in ("*.mp3", "*.m4a", "*.opus", "*.wav", "*.aac"):
        files.extend(glob.glob(os.path.join(folder, ext)))
    files = [f for f in files if not f.endswith((".part", ".ytdl"))]
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def download_stream(url, audio_only=False):
    """URL 하나를 받아 진행 상황을 이벤트(dict)로 하나씩 흘려보낸다."""
    folder = DOWNLOAD_DIR
    os.makedirs(folder, exist_ok=True)

    # 이미 받은 영상이면 바로 알림 (소리만 받기는 별도 파일이라 중복검사 제외)
    video_id = yd.extract_video_id(url)
    index = yd.load_download_index(folder) if video_id else {}
    if not audio_only and video_id and video_id in index:
        existing = os.path.join(folder, index[video_id])
        if os.path.exists(existing):
            os.utime(existing, None)
            yield {"stage": "dup", "filename": os.path.basename(existing)}
            return

    # 메타(제목 + 화질)
    resolutions = [] if audio_only else yd.get_available_resolutions(url)
    title = yd.get_video_title(url)
    yield {"stage": "meta", "title": title, "resolutions": resolutions,
           "audio": audio_only}

    # 다운로드 (실시간 진행률 파싱). 틱톡은 JS 챌린지가 확률적이라 여러 번 재시도.
    import time
    cmd = build_streaming_command(url, folder, title, audio_only)
    max_tries = 5 if yd.is_tiktok(url) else 2
    started_at = time.time() - 1  # 이번 실행에서 새로 생긴 파일만 성공으로 인정
    saved, output = None, ""
    for attempt in range(1, max_tries + 1):
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        log_lines = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("PROG|"):
                _, pct, speed, eta = (line.split("|") + ["", "", ""])[:4]
                yield {"stage": "progress",
                       "percent": pct.strip(), "speed": speed.strip(), "eta": eta.strip()}
            else:
                log_lines.append(line)
        proc.wait()
        output = "\n".join(log_lines)
        cand = find_latest_audio(folder) if audio_only else yd.find_latest_downloaded_file(folder)
        # 이번 시도에서 실제로 생성/갱신된 파일만 인정 (이전 다운로드 오탐 방지)
        if proc.returncode == 0 and cand and os.path.getmtime(cand) >= started_at:
            saved = cand
            break
        saved = None
        if attempt < max_tries:
            yield {"stage": "progress", "percent": "",
                   "speed": f"재시도 {attempt}/{max_tries - 1}…", "eta": ""}
            time.sleep(2)

    if not saved:
        yield {"stage": "error",
               "hints": yd.analyze_output(output) or ["여러 번 시도했지만 실패 (틱톡 접속 제한일 수 있음 — 잠시 후 재시도)"],
               "log": output.strip()[-2000:]}
        return

    # 소리만 받은 건 영상 목록과 별개이므로 중복 인덱스에 넣지 않는다
    if video_id and not audio_only:
        index[video_id] = os.path.basename(saved)
        yd.save_download_index(folder, index)

    if audio_only:
        size_mb = round(os.path.getsize(saved) / 1048576, 1)
        yield {"stage": "done", "filename": os.path.basename(saved),
               "audio": True, "size": size_mb, "warnings": []}
        return

    info = yd.get_media_info(saved)
    achieved = min(info["width"], info["height"]) if info else None
    maxres = max(resolutions) if resolutions else None
    warnings = []
    if achieved and maxres and achieved < maxres:
        warnings = yd.analyze_output(output) or ["원인 불명 (네트워크 또는 포맷 매칭 문제로 추정)"]
    yield {"stage": "done",
           "filename": os.path.basename(saved),
           "achieved": achieved, "maxres": maxres, "warnings": warnings}


INDEX_HTML = r"""
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%AC%87%EF%B8%8F%3C/text%3E%3C/svg%3E">
<title>동영상 다운로더</title>
<style>
  :root{
    --bg:#0d0f14; --panel:#16191f; --elev:#1d2129; --line:#262b33;
    --txt:#e8eaf0; --muted:#949aa6; --accent:#7c5cff; --accent2:#6366f1;
    --ok:#3ddc84; --danger:#ff6b6b; --warn:#ffb454;
    --r:14px; --ctrl:10px;
  }
  *{ box-sizing:border-box; }
  body{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    background:var(--bg); color:var(--txt); margin:0; padding:0;
    background-image:radial-gradient(1000px 520px at 100% -10%, rgba(124,92,255,.10), transparent 60%); }
  .wrap{ max-width:920px; margin:0 auto; padding:36px 32px 64px; }
  header{ display:flex; align-items:center; gap:14px; margin-bottom:6px; }
  .logo{ width:44px; height:44px; border-radius:12px; display:grid; place-items:center;
    font-size:22px; background:linear-gradient(135deg,var(--accent),var(--accent2));
    box-shadow:0 6px 20px rgba(124,92,255,.35); }
  h1{ font-size:24px; font-weight:750; margin:0; letter-spacing:-.02em; }
  p.sub{ color:var(--muted); margin:10px 0 22px; font-size:14px; }
  .card{ background:var(--panel); border:1px solid var(--line); border-radius:var(--r); padding:18px; }
  textarea{ width:100%; min-height:84px; resize:vertical; background:var(--elev); color:var(--txt);
    border:1px solid var(--line); border-radius:var(--ctrl); padding:12px 14px; font-size:14px;
    outline:none; font-family:inherit; line-height:1.5; transition:.15s; }
  textarea:focus{ border-color:var(--accent); box-shadow:0 0 0 3px rgba(124,92,255,.18); }
  .row{ display:flex; gap:10px; align-items:center; margin-top:12px; }
  button{ color:#fff; border:none; padding:11px 20px; border-radius:var(--ctrl); font-size:14px;
    font-weight:650; cursor:pointer; transition:.15s ease;
    background:linear-gradient(135deg,var(--accent),var(--accent2)); box-shadow:0 4px 14px rgba(124,92,255,.28); }
  button:hover{ filter:brightness(1.08); }
  button:disabled{ opacity:.5; cursor:default; box-shadow:none; }
  .hint{ color:var(--muted); font-size:12.5px; }
  button.audio{ background:var(--elev); color:var(--txt); border:1px solid var(--line); box-shadow:none; }
  button.audio:hover{ border-color:var(--accent); }
  #jobs{ margin-top:22px; display:flex; flex-direction:column; gap:14px; }
  .job{ background:var(--panel); border:1px solid var(--line); border-radius:var(--r); padding:16px 18px; }
  .job .jtitle{ font-size:14.5px; font-weight:650; margin-bottom:4px; word-break:break-all; }
  .job .jurl{ font-size:11.5px; color:var(--muted); word-break:break-all; margin-bottom:10px; }
  .bar{ height:8px; background:var(--elev); border-radius:999px; overflow:hidden; }
  .bar > i{ display:block; height:100%; width:0%; border-radius:999px;
    background:linear-gradient(90deg,var(--accent),var(--accent2)); transition:width .25s ease; }
  .jmeta{ display:flex; gap:14px; flex-wrap:wrap; margin-top:8px; font-size:12.5px; color:var(--muted); }
  .jmeta b{ color:var(--txt); font-weight:600; }
  .badge{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:650; }
  .badge.ok{ background:rgba(61,220,132,.14); color:var(--ok); }
  .badge.err{ background:rgba(255,107,107,.14); color:var(--danger); }
  .badge.dup{ background:rgba(148,154,166,.16); color:var(--muted); }
  .badge.run{ background:rgba(124,92,255,.16); color:var(--accent); }
  .warn{ color:var(--warn); font-size:12.5px; margin-top:8px; }
  .errlog{ margin-top:10px; background:#120d0f; border:1px solid #3a2226; border-radius:8px;
    padding:10px 12px; font-size:11.5px; color:#f0b8bd; white-space:pre-wrap; max-height:180px;
    overflow:auto; font-family:ui-monospace,Menlo,monospace; }
  .foot{ margin-top:22px; font-size:12.5px; color:var(--muted); }
  .foot a{ color:var(--accent); text-decoration:none; }
</style>
</head>
<body>
 <div class="wrap">
  <header style="display:flex;align-items:center;gap:14px;">
    <div class="logo">⬇️</div>
    <h1>동영상 다운로더</h1>
    <a href="/trim" style="margin-left:auto;color:var(--accent);font-size:13.5px;text-decoration:none;font-weight:650;">✂️ 구간 편집 →</a>
  </header>
  <p class="sub">유튜브·틱톡 URL을 붙여넣으면 최고화질로 받아 저장합니다. · 완전 로컬, yt-dlp</p>

  <div class="card">
    <textarea id="urls" placeholder="URL 붙여넣기 (여러 개면 한 줄에 하나씩)"></textarea>
    <div class="row">
      <button id="go">다운로드</button>
      <button id="goAudio" class="audio">🎵 소리만 (mp3)</button>
      <span class="hint" id="hint">유튜브 shorts·롱폼, 틱톡 지원</span>
    </div>
  </div>

  <div id="jobs"></div>

  <div class="foot">저장 위치: <a href="#" id="openFolder">다운받은 영상</a> 폴더</div>
 </div>

<script>
const urlsEl = document.getElementById('urls');
const goBtn = document.getElementById('go');
const jobsEl = document.getElementById('jobs');

function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function jobCard(url){
  const el = document.createElement('div');
  el.className = 'job';
  el.innerHTML = `
    <div class="jtitle">준비 중… <span class="badge run">진행</span></div>
    <div class="jurl">${esc(url)}</div>
    <div class="bar"><i></i></div>
    <div class="jmeta"></div>`;
  jobsEl.prepend(el);
  return el;
}

async function runOne(url, audioOnly){
  const card = jobCard(url);
  const titleEl = card.querySelector('.jtitle');
  const barEl = card.querySelector('.bar > i');
  const metaEl = card.querySelector('.jmeta');
  try{
    const resp = await fetch('/download?url=' + encodeURIComponent(url)
                             + (audioOnly ? '&audio=1' : ''));
    if(!resp.ok) throw new Error('서버 오류 ' + resp.status);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while(true){
      const {value, done} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while((i = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
        if(!line) continue;
        let ev; try{ ev = JSON.parse(line); }catch(_){ continue; }
        applyEvent(ev, {titleEl, barEl, metaEl});
      }
    }
  }catch(err){
    titleEl.innerHTML = `실패 <span class="badge err">오류</span>`;
    metaEl.innerHTML = `<span class="warn">${esc(err.message)}</span>`;
  }
}

function applyEvent(ev, els){
  const {titleEl, barEl, metaEl} = els;
  if(ev.stage === 'meta'){
    titleEl.innerHTML = `${esc(ev.title)} <span class="badge run">진행</span>`;
    const res = ev.audio ? '🎵 소리만 받는 중 (mp3)'
      : ((ev.resolutions && ev.resolutions.length)
          ? '사용가능 ' + ev.resolutions.map(r=>r+'p').join(' · ') : '화질 조회 실패');
    metaEl.innerHTML = `<span>${esc(res)}</span>`;
  } else if(ev.stage === 'progress'){
    const p = parseFloat((ev.percent||'').replace('%','')) || 0;
    barEl.style.width = p + '%';
    let extra = [];
    if(ev.percent) extra.push('<b>'+esc(ev.percent)+'</b>');
    if(ev.speed) extra.push(esc(ev.speed));
    if(ev.eta && ev.eta !== 'Unknown') extra.push('남은시간 ' + esc(ev.eta));
    metaEl.innerHTML = '<span>' + extra.join(' · ') + '</span>';
  } else if(ev.stage === 'done'){
    barEl.style.width = '100%';
    titleEl.innerHTML = `${esc(ev.filename)} <span class="badge ok">완료</span>`;
    let m = [];
    if(ev.audio){
      m.push('🎵 <b>소리만</b> (mp3)');
      if(ev.size) m.push(`${ev.size}MB`);
    } else {
      if(ev.achieved) m.push(`저장화질 <b>${ev.achieved}p</b>`);
      if(ev.maxres) m.push(`최고 ${ev.maxres}p`);
    }
    metaEl.innerHTML = m.map(x=>`<span>${x}</span>`).join('');
    if(ev.warnings && ev.warnings.length){
      const w = document.createElement('div'); w.className='warn';
      w.innerHTML = '⚠️ 최고화질 대신 저장됨<br>' + ev.warnings.map(esc).join('<br>');
      metaEl.after(w);
    }
  } else if(ev.stage === 'dup'){
    barEl.style.width = '100%';
    titleEl.innerHTML = `${esc(ev.filename)} <span class="badge dup">이미 있음</span>`;
    metaEl.innerHTML = `<span>중복 — 다시 받지 않음</span>`;
  } else if(ev.stage === 'error'){
    titleEl.innerHTML = `다운로드 실패 <span class="badge err">오류</span>`;
    let html = '';
    if(ev.hints && ev.hints.length) html += ev.hints.map(h=>`<div class="warn">${esc(h)}</div>`).join('');
    metaEl.innerHTML = html;
    if(ev.log){ const l=document.createElement('div'); l.className='errlog'; l.textContent=ev.log; metaEl.after(l); }
  }
}

const audioBtn = document.getElementById('goAudio');
async function runAll(audioOnly){
  const urls = urlsEl.value.split('\n').map(s=>s.trim()).filter(Boolean);
  if(!urls.length) return;
  goBtn.disabled = true; audioBtn.disabled = true;
  urlsEl.value = '';
  for(const u of urls){ await runOne(u, audioOnly); }   // 하나씩 순차 (충돌 방지)
  goBtn.disabled = false; audioBtn.disabled = false;
}
goBtn.onclick = ()=>runAll(false);
audioBtn.onclick = ()=>runAll(true);
urlsEl.addEventListener('keydown', e=>{
  if(e.key === 'Enter' && (e.metaKey || e.ctrlKey)) goBtn.click();
});
document.getElementById('openFolder').onclick = (e)=>{ e.preventDefault(); fetch('/open_folder',{method:'POST'}); };
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return INDEX_HTML


@app.route("/download")
def download():
    url = (request.args.get("url") or "").strip()
    audio_only = request.args.get("audio") == "1"
    if not url:
        return jsonify({"error": "url 없음"}), 400

    def gen():
        try:
            for ev in download_stream(url, audio_only):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:
            yield json.dumps({"stage": "error", "hints": [f"{type(e).__name__}: {e}"],
                              "log": ""}, ensure_ascii=False) + "\n"

    return Response(gen(), mimetype="application/x-ndjson")


@app.route("/open_folder", methods=["POST"])
def open_folder():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    subprocess.run(["open", DOWNLOAD_DIR])
    return jsonify({"ok": True})


# ============================================================
# 구간 편집: URL 로드 → 브라우저에서 미리보기 → 마우스로 구간 선택 → 정밀 컷 저장
# ============================================================
def _safe_id(url):
    vid = yd.extract_video_id(url)
    if vid:
        return vid
    import hashlib
    return "v" + hashlib.md5(url.encode()).hexdigest()[:12]


def _ffprobe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out)
    except (ValueError, subprocess.TimeoutExpired):
        return 0.0


@app.route("/load", methods=["POST"])
def load_video():
    """URL을 받아 미리보기용으로 영상을 내려받고 메타를 돌려준다."""
    d = request.get_json() or {}
    url = (d.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url 없음"}), 400
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    vid = _safe_id(url)
    target = os.path.join(PREVIEW_DIR, vid + ".mp4")
    if not os.path.exists(target):
        cmd = [yd.get_ytdlp_path(url), *yd.get_common_ytdlp_args(url),
               "--no-playlist", "--merge-output-format", "mp4",
               "--remux-video", "mp4", "--format", yd.FORMAT_SELECTOR,
               "--force-overwrites", "-o", os.path.join(PREVIEW_DIR, vid + ".%(ext)s"), url]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if not os.path.exists(target):
            return jsonify({"error": "영상 로드 실패",
                            "log": ((proc.stderr or "") + (proc.stdout or ""))[-1500:]}), 500
    title = yd.get_video_title(url)
    return jsonify({"id": vid, "title": title,
                    "duration": _ffprobe_duration(target)})


@app.route("/preview/<vid>")
def preview(vid):
    """미리보기 영상 스트리밍 (스크럽 위해 range 요청 지원)."""
    path = os.path.join(PREVIEW_DIR, vid + ".mp4")
    if not os.path.exists(path):
        return "not found", 404
    return send_file(path, mimetype="video/mp4", conditional=True)


@app.route("/cut", methods=["POST"])
def cut():
    """선택 구간을 정밀하게 잘라 저장한다(재인코딩)."""
    d = request.get_json() or {}
    vid = (d.get("id") or "").strip()
    title = (d.get("title") or "clip").strip()
    try:
        start = float(d.get("start"))
        end = float(d.get("end"))
    except (TypeError, ValueError):
        return jsonify({"error": "시작/끝 시간 오류"}), 400
    if end <= start:
        return jsonify({"error": "끝이 시작보다 뒤여야 합니다"}), 400
    src = os.path.join(PREVIEW_DIR, vid + ".mp4")
    if not os.path.exists(src):
        return jsonify({"error": "먼저 영상을 불러오세요"}), 400
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    safe = yd.truncate_filename(yd.sanitize_filename(title))
    def s(t):
        return f"{int(t)//3600:02d}:{int(t)%3600//60:02d}:{t%60:06.3f}"
    out_name = f"{safe} [{int(start)}-{int(end)}s].mp4"
    out_path = os.path.join(DOWNLOAD_DIR, out_name)
    cmd = ["ffmpeg", "-y", "-i", src, "-ss", s(start), "-to", s(end),
           "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(out_path):
        return jsonify({"error": "컷 실패", "log": (proc.stderr or "")[-1500:]}), 500
    return jsonify({"ok": True, "filename": out_name})


TRIM_HTML = r"""
<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%E2%9C%82%EF%B8%8F%3C/text%3E%3C/svg%3E">
<title>구간 편집 다운로드</title>
<style>
  :root{ --bg:#0d0f14; --panel:#16191f; --elev:#1d2129; --line:#262b33; --txt:#e8eaf0;
    --muted:#949aa6; --accent:#7c5cff; --accent2:#6366f1; --ok:#3ddc84; --r:14px; --ctrl:10px; }
  *{ box-sizing:border-box; }
  body{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--txt);
    margin:0; background-image:radial-gradient(1000px 520px at 100% -10%, rgba(124,92,255,.10), transparent 60%); }
  .wrap{ max-width:860px; margin:0 auto; padding:32px 28px 60px; }
  header{ display:flex; align-items:center; gap:12px; }
  .logo{ width:42px; height:42px; border-radius:11px; display:grid; place-items:center; font-size:20px;
    background:linear-gradient(135deg,var(--accent),var(--accent2)); box-shadow:0 6px 20px rgba(124,92,255,.35); }
  h1{ font-size:21px; font-weight:750; margin:0; }
  a.nav{ color:var(--muted); font-size:13px; text-decoration:none; margin-left:auto; }
  a.nav:hover{ color:var(--accent); }
  p.sub{ color:var(--muted); margin:8px 0 18px; font-size:13px; }
  .card{ background:var(--panel); border:1px solid var(--line); border-radius:var(--r); padding:16px; margin-bottom:16px; }
  .row{ display:flex; gap:10px; }
  input#url{ flex:1; background:var(--elev); border:1px solid var(--line); color:var(--txt);
    border-radius:var(--ctrl); padding:11px 14px; font-size:14px; outline:none; }
  input#url:focus{ border-color:var(--accent); box-shadow:0 0 0 3px rgba(124,92,255,.18); }
  button{ color:#fff; border:none; font-weight:650; cursor:pointer; padding:11px 18px; border-radius:var(--ctrl);
    font-size:14px; background:linear-gradient(135deg,var(--accent),var(--accent2)); box-shadow:0 4px 14px rgba(124,92,255,.28); }
  button:disabled{ opacity:.5; cursor:default; box-shadow:none; }
  button.ghost{ background:var(--elev); color:var(--txt); border:1px solid var(--line); box-shadow:none; padding:8px 12px; font-size:13px; }
  video{ width:100%; max-height:46vh; border-radius:10px; background:#000; display:block; object-fit:contain; cursor:pointer; }
  .pbar{ display:flex; align-items:center; gap:12px; margin-top:12px; }
  .pbar .cur{ font-size:14px; color:var(--accent); font-weight:700; font-variant-numeric:tabular-nums; }
  #editor{ display:none; }
  /* 타임라인 */
  .tl{ position:relative; height:44px; margin:16px 0 6px; background:var(--elev);
    border:1px solid var(--line); border-radius:10px; cursor:pointer; user-select:none; }
  .tl .sel{ position:absolute; top:0; bottom:0; background:rgba(124,92,255,.28);
    border-left:2px solid var(--accent); border-right:2px solid var(--accent); }
  .tl .handle{ position:absolute; top:-4px; width:14px; height:52px; margin-left:-7px; border-radius:5px;
    background:var(--accent); cursor:ew-resize; box-shadow:0 2px 8px rgba(0,0,0,.5); }
  .times{ display:flex; gap:14px; align-items:center; flex-wrap:wrap; margin-top:10px; }
  .tbox{ background:var(--elev); border:1px solid var(--line); border-radius:8px; padding:8px 12px; font-size:13px; }
  .tbox b{ color:var(--accent); font-variant-numeric:tabular-nums; }
  input.t{ width:96px; background:transparent; border:none; color:var(--accent); font-size:15px; font-weight:700;
    font-variant-numeric:tabular-nums; outline:none; }
  .len{ color:var(--muted); font-size:13px; }
  #status{ color:var(--accent); font-size:13px; min-height:16px; font-weight:600; margin-top:6px; }
  .hint{ color:var(--muted); font-size:12px; margin-top:8px; }
</style></head><body>
 <div class="wrap">
  <header><div class="logo">✂️</div><h1>구간 편집 다운로드</h1><a class="nav" href="/">← 전체 다운로드</a></header>
  <p class="sub">URL을 넣어 영상을 불러온 뒤, 타임라인을 마우스로 끌어 원하는 구간만 정밀하게 저장합니다. · 완전 로컬</p>

  <div class="card">
    <div class="row">
      <input id="url" placeholder="URL 붙여넣기 (유튜브 · 틱톡)" />
      <button id="loadBtn">불러오기</button>
    </div>
    <div id="status"></div>
  </div>

  <div class="card" id="editor">
    <video id="player" playsinline></video>
    <div class="pbar">
      <button class="ghost" id="playBtn">▶ 재생</button>
      <span class="cur" id="cur">0:00.000</span>
    </div>
    <div class="tl" id="tl">
      <div class="sel" id="sel"></div>
      <div class="handle" id="hStart"></div>
      <div class="handle" id="hEnd"></div>
    </div>
    <div class="times">
      <div class="tbox">시작 <input class="t" id="tStart" value="0:00.000"></div>
      <button class="ghost" id="setStart">▶ 재생위치를 시작으로</button>
      <div class="tbox">끝 <input class="t" id="tEnd" value="0:00.000"></div>
      <button class="ghost" id="setEnd">▶ 재생위치를 끝으로</button>
      <span class="len" id="len"></span>
    </div>
    <div class="times" style="margin-top:14px;">
      <button id="cutBtn">이 구간 저장</button>
      <button class="ghost" id="previewSel">구간 미리듣기</button>
    </div>
    <div class="hint">타임라인의 보라 손잡이를 끌어 구간을 정하거나, 영상을 재생하고 “재생위치를” 버튼으로 지정하세요. 시간 칸은 직접 입력도 됩니다.</div>
  </div>
 </div>

<script>
let dur=0, vidId='', title='', start=0, end=0;
const $=id=>document.getElementById(id);
const player=$('player'), tl=$('tl'), sel=$('sel'), hStart=$('hStart'), hEnd=$('hEnd');

function fmt(t){ if(t<0)t=0; const m=Math.floor(t/60), s=Math.floor(t%60), ms=Math.round((t-Math.floor(t))*1000);
  return m+':'+String(s).padStart(2,'0')+'.'+String(ms).padStart(3,'0'); }
function parse(str){ const m=String(str).trim().match(/^(?:(\d+):)?(\d+)(?:\.(\d+))?$/);
  if(!m)return null; const mm=+(m[1]||0), ss=+m[2], ms=+((m[3]||'0').padEnd(3,'0').slice(0,3)); return mm*60+ss+ms/1000; }

function tlWidth(){ return tl.getBoundingClientRect().width; }
function layout(){
  const w=tlWidth();
  const a=dur?start/dur*w:0, b=dur?end/dur*w:0;
  sel.style.left=a+'px'; sel.style.width=Math.max(0,b-a)+'px';
  hStart.style.left=a+'px'; hEnd.style.left=b+'px';
  $('tStart').value=fmt(start); $('tEnd').value=fmt(end);
  $('len').textContent = dur? ('선택 길이 '+fmt(end-start)) : '';
}
function setStart(t){ start=Math.max(0,Math.min(t,end-0.05)); layout(); }
function setEnd(t){ end=Math.min(dur,Math.max(t,start+0.05)); layout(); }

$('loadBtn').onclick=async()=>{
  const url=$('url').value.trim(); if(!url)return;
  $('status').textContent='불러오는 중… (영상 크기에 따라 시간이 걸립니다)'; $('loadBtn').disabled=true;
  try{
    const r=await fetch('/load',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const d=await r.json();
    if(!r.ok) throw new Error(d.error||'실패');
    vidId=d.id; title=d.title; dur=d.duration||0;
    player.src='/preview/'+vidId; $('editor').style.display='block';
    start=0; end=dur; layout();
    $('status').textContent='불러옴: '+title+' ('+fmt(dur)+')';
  }catch(e){ $('status').textContent='오류: '+e.message; }
  finally{ $('loadBtn').disabled=false; }
};

// 재생바 + 현재시간 갱신 (초록 마커 하나만 사용)
player.addEventListener('timeupdate',()=>{ if(dur){ $('cur').textContent=fmt(player.currentTime); } });
player.addEventListener('loadedmetadata',()=>{ if(!dur){dur=player.duration; end=dur; layout();} });
// 재생/일시정지
function togglePlay(){ if(player.paused){player.play();$('playBtn').textContent='❚❚ 일시정지';} else {player.pause();$('playBtn').textContent='▶ 재생';} }
$('playBtn').onclick=togglePlay;
player.addEventListener('click',togglePlay);
player.addEventListener('ended',()=>{ $('playBtn').textContent='▶ 재생'; });

// 손잡이 드래그
function dragHandle(handle,which){
  handle.addEventListener('mousedown',e=>{ e.preventDefault();
    player.pause(); $('playBtn').textContent='▶ 재생';
    const move=ev=>{ const rect=tl.getBoundingClientRect();
      const t=Math.max(0,Math.min(1,(ev.clientX-rect.left)/rect.width))*dur;
      which==='s'?setStart(t):setEnd(t);
      // 손잡이가 가리키는 지점의 프레임을 화면에 표시
      if(dur) player.currentTime = which==='s'?start:end;
    };
    const up=()=>{ document.removeEventListener('mousemove',move); document.removeEventListener('mouseup',up); };
    document.addEventListener('mousemove',move); document.addEventListener('mouseup',up);
  });
}
dragHandle(hStart,'s'); dragHandle(hEnd,'e');
// 타임라인 클릭 → 재생위치 이동
tl.addEventListener('click',e=>{ if(e.target.classList.contains('handle'))return;
  const rect=tl.getBoundingClientRect(); player.currentTime=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width))*dur; });

$('setStart').onclick=()=>setStart(player.currentTime);
$('setEnd').onclick=()=>setEnd(player.currentTime);
$('tStart').addEventListener('change',()=>{ const t=parse($('tStart').value); if(t!=null)setStart(t); else layout(); });
$('tEnd').addEventListener('change',()=>{ const t=parse($('tEnd').value); if(t!=null)setEnd(t); else layout(); });
$('previewSel').onclick=()=>{ player.currentTime=start; player.play();
  const stop=()=>{ if(player.currentTime>=end){ player.pause(); player.removeEventListener('timeupdate',stop); } };
  player.addEventListener('timeupdate',stop); };

$('cutBtn').onclick=async()=>{
  if(end<=start){ $('status').textContent='끝이 시작보다 뒤여야 합니다'; return; }
  $('status').textContent='구간 저장 중…'; $('cutBtn').disabled=true;
  try{
    const r=await fetch('/cut',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:vidId,title,start,end})});
    const d=await r.json();
    if(!r.ok) throw new Error(d.error||'실패');
    $('status').textContent='✅ 저장 완료: '+d.filename;
  }catch(e){ $('status').textContent='오류: '+e.message; }
  finally{ $('cutBtn').disabled=false; }
};
window.addEventListener('resize',layout);
</script></body></html>
"""


@app.route("/trim")
def trim_page():
    return TRIM_HTML


if __name__ == "__main__":
    if not yd.ensure_tool_exists(yd.YT_DLP_PATH):
        print("❌ yt-dlp 미설치")
        sys.exit(1)
    (None if os.environ.get("NO_BROWSER") else Timer(1.0, lambda: open_browser(f"http://127.0.0.1:{PORT}")).start())
    app.run(port=PORT, debug=False, threaded=True)
