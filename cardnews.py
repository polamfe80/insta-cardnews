#!/usr/bin/env python3
"""인스타그램 카드뉴스 자동 발행 — 단일 파일 버전.

  python cardnews.py --stage render    카드 이미지 생성 (published/날짜/)
  python cardnews.py --stage publish   인스타그램에 캐러셀 발행

설정은 전부 환경변수로 받습니다 (GitHub Secrets / Variables).
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import mimetypes
import os
import pathlib
import random
import shutil
import sys
import time
import zoneinfo

import requests
from playwright.sync_api import sync_playwright


# ── 설정 ─────────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).resolve().parent

# ── 계정 설정 (여기만 고치면 됩니다) ─────────────────────────────────────────
BRAND_HANDLE = os.getenv("BRAND_HANDLE", "3분 재테크")   # 카드 좌상단 문구 (핸들이 바뀌어도 안 낡도록 브랜드명 사용)
POSTS_PER_DAY = 1                                          # 하루 발행 수
TIMEZONE = "Asia/Seoul"

# ── GitHub Secrets 로 넣는 값들 ──────────────────────────────────────────────
IG_USER_ID = os.getenv("IG_USER_ID", "")           # 인스타그램 비즈니스 계정 ID (숫자)
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")  # 페이지 액세스 토큰 (만료 없음)
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# 이미지 공개 URL 조립용 — GitHub Actions 가 자동으로 채웁니다
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")   # "owner/repo"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

GRAPH_VERSION = "v21.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"

# ── 경로 ─────────────────────────────────────────────────────────────────────
BANK_PATH = ROOT / "content" / "bank.json"
STATE_PATH = ROOT / "content" / "state.json"
PUBLISHED_DIR = ROOT / "published"
BUILD_DIR = ROOT / "build"


def raw_url(relative_path: str) -> str:
    """저장소에 커밋된 파일의 공개 raw URL. 저장소가 public 이어야 합니다."""
    if not GITHUB_REPOSITORY:
        raise RuntimeError("GITHUB_REPOSITORY 환경변수가 없습니다.")
    rel = str(relative_path).lstrip("/")
    return f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{GITHUB_BRANCH}/{rel}"


def require(name: str, value: str):
    if not value:
        raise SystemExit(
            f"[설정 오류] {name} 가 비어 있습니다. "
            f"GitHub 저장소 Settings → Secrets and variables → Actions 에서 등록하세요."
        )


# ── 사진 (Pexels) ───────────────────────────────────────────────────
API = "https://api.pexels.com/v1/search"
TIMEOUT = 25


def _pick(photos: list, used: set):
    """이미 쓴 사진은 피해서 고릅니다."""
    for p in photos:
        if p["id"] not in used:
            used.add(p["id"])
            return p
    return random.choice(photos) if photos else None


def fetch_photos(queries: list, out_dir: pathlib.Path, count: int) -> list:
    """검색어 목록으로 사진 count 장을 받아 로컬에 저장하고 경로 목록을 돌려줍니다."""
    require("PEXELS_API_KEY", PEXELS_API_KEY)
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": PEXELS_API_KEY}
    used, paths = set(), []

    qi = 0
    while len(paths) < count:
        q = queries[qi % len(queries)]
        qi += 1
        try:
            r = requests.get(
                API,
                headers=headers,
                params={"query": q, "orientation": "portrait", "per_page": 30,
                        "size": "large", "locale": "en-US"},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            photos = r.json().get("photos", [])
        except Exception as e:
            print(f"  [경고] '{q}' 검색 실패: {e}")
            photos = []

        if not photos:
            if qi > len(queries) * 3:
                raise SystemExit("[오류] Pexels 에서 사진을 찾지 못했습니다. 검색어를 확인하세요.")
            continue

        pick = _pick(photos, used)
        url = pick["src"].get("large2x") or pick["src"]["large"]
        name = hashlib.md5(url.encode()).hexdigest()[:12] + ".jpg"
        dest = out_dir / name
        if not dest.exists():
            img = requests.get(url, timeout=TIMEOUT)
            img.raise_for_status()
            dest.write_bytes(img.content)
        paths.append(dest)
        print(f"  사진 확보 [{q}] {dest.name}")

    return paths


# ── 카드 렌더링 ──────────────────────────────────────────────────────
W, H = 1080, 1350

# 팔레트: 슬라이드 종류별 포인트 컬러
ACCENT = "#c8912f"        # 골드 (커버/스텝 배지)
PANEL_BG = "#f7f3ea"      # 크림 패널
INK = "#14110c"           # 본문 검정
INK_SOFT = "#4c4539"      # 본문 회색
KICKER = "#b8862f"

GRAIN = (
    "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='240'"
    "%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'"
    "/%3E%3C/filter%3E%3Crect width='240' height='240' filter='url(%23n)'/%3E%3C/svg%3E\")"
)

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
html,body { width:1080px; height:1350px; }
body { font-family:'Pretendard','Noto Sans KR','Noto Sans CJK KR',sans-serif;
       overflow:hidden; position:relative; background:#0b1a2e; }

.photo { position:absolute; top:0; left:0; right:0; height:60%;
         background-size:cover; background-position:center; }
.photo::after { content:''; position:absolute; inset:0;
  background:linear-gradient(to bottom, rgba(0,0,0,.42) 0%, rgba(0,0,0,.12) 45%, rgba(0,0,0,.34) 100%); }
.grain { position:absolute; top:0; left:0; right:0; height:60%; opacity:.13;
         mix-blend-mode:overlay; background-image:GRAINURL; }

.panel { position:absolute; left:0; right:0; bottom:0; height:40%;
  background:PANELBG; padding:70px 84px;
  display:flex; flex-direction:column; justify-content:center; }

.handle { position:absolute; top:74px; left:84px; z-index:5;
  color:rgba(255,255,255,.9); font-size:29px; font-weight:700; letter-spacing:.01em;
  text-shadow:0 2px 12px rgba(0,0,0,.5); }
.pageno { position:absolute; top:74px; right:84px; z-index:5;
  color:rgba(255,255,255,.7); font-size:29px; font-weight:600;
  text-shadow:0 2px 12px rgba(0,0,0,.5); }

.badge { position:absolute; left:84px; bottom:calc(40% + 44px); z-index:5;
  background:ACCENTC; color:#fff; font-size:27px; font-weight:800; letter-spacing:.08em;
  padding:13px 28px; border-radius:100px; box-shadow:0 6px 24px rgba(0,0,0,.28); }

.kicker { font-size:30px; font-weight:800; letter-spacing:.2em; color:KICKERC; margin-bottom:24px; }
.t-cover { font-size:94px; font-weight:900; line-height:1.14; letter-spacing:-.04em; color:INKC; }
.t-body  { font-size:70px; font-weight:900; line-height:1.2;  letter-spacing:-.038em; color:INKC; }
.t-small { font-size:58px; font-weight:900; line-height:1.24; letter-spacing:-.036em; color:INKC; }
.sub { margin-top:24px; font-size:37px; font-weight:600; color:#6b6252; letter-spacing:-.02em; }
.lead { margin-top:12px; font-size:32px; font-weight:700; color:ACCENTC; letter-spacing:-.01em; }
.text { margin-top:24px; font-size:33px; font-weight:400; line-height:1.62;
        color:INKSOFT; letter-spacing:-.018em; }
.cta { display:inline-block; margin-top:32px; padding:16px 36px; border:3px solid ACCENTC;
       color:ACCENTC; border-radius:100px; font-size:31px; font-weight:800; align-self:flex-start; }
"""


def _css():
    return (CSS.replace("GRAINURL", GRAIN).replace("PANELBG", PANEL_BG)
               .replace("ACCENTC", ACCENT).replace("KICKERC", KICKER)
               .replace("INKSOFT", INK_SOFT).replace("INKC", INK))


def _data_uri(path: pathlib.Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _br(t: str) -> str:
    return (t or "").replace("\n", "<br>")


def _title_class(title: str) -> str:
    """제목 길이에 따라 폰트 크기 자동 조절 — 패널 밖으로 넘치는 걸 막습니다."""
    longest = max((len(l) for l in title.split("\n")), default=0)
    lines = title.count("\n") + 1
    if lines >= 3 or longest >= 14:
        return "t-small"
    if longest >= 10:
        return "t-body"
    return "t-body"


def slide_html(slide: dict, photo_path: pathlib.Path, index: int, total: int) -> str:
    kind = slide.get("type", "body")
    photo = _data_uri(photo_path)

    if kind == "cover":
        panel = (f'<div class="kicker">{slide.get("kicker","")}</div>'
                 f'<div class="t-cover">{_br(slide["title"])}</div>'
                 f'<div class="sub">{_br(slide.get("sub",""))}</div>')
        badge = ""
    elif kind == "outro":
        panel = (f'<div class="t-body">{_br(slide["title"])}</div>'
                 f'<div class="text">{_br(slide.get("body",""))}</div>'
                 f'<div class="cta">{slide.get("cta","저장하기")}</div>')
        badge = ""
    else:
        cls = _title_class(slide["title"])
        lead = f'<div class="lead">{slide["lead"]}</div>' if slide.get("lead") else ""
        panel = (f'<div class="{cls}">{_br(slide["title"])}</div>'
                 f'{lead}'
                 f'<div class="text">{_br(slide.get("body",""))}</div>')
        badge = (f'<div class="badge">{slide["badge"]}</div>' if slide.get("badge") else "")

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_css()}</style></head><body>"
        f'<div class="photo" style="background-image:url({photo})"></div>'
        '<div class="grain"></div>'
        f'<div class="handle">{BRAND_HANDLE}</div>'
        f'<div class="pageno">{index} / {total}</div>'
        f'{badge}'
        f'<div class="panel">{panel}</div>'
        "</body></html>"
    )


def render_post(slides: list, photos: list, out_dir: pathlib.Path) -> list:
    """슬라이드 목록 + 사진 목록 → JPEG 파일 경로 목록."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    total = len(slides)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for i, slide in enumerate(slides):
            photo = photos[i % len(photos)]
            html_path = out_dir / f"_slide_{i+1:02d}.html"
            html_path.write_text(slide_html(slide, photo, i + 1, total), encoding="utf-8")
            page.goto(html_path.as_uri())
            page.wait_for_timeout(200)
            out = out_dir / f"card_{i+1:02d}.jpg"
            page.screenshot(path=str(out), type="jpeg", quality=90)
            html_path.unlink(missing_ok=True)
            paths.append(out)
            print(f"  렌더 완료 {out.name}")
        browser.close()
    return paths


# ── 인스타그램 발행 ──────────────────────────────────────────────────
TIMEOUT = 40


def _post(path: str, data: dict) -> dict:
    data = dict(data, access_token=IG_ACCESS_TOKEN)
    r = requests.post(f"{GRAPH}/{path}", data=data, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise SystemExit(f"[인스타그램 API 오류] {r.status_code}\n{r.text}")
    return r.json()


def _get(path: str, params: dict) -> dict:
    params = dict(params, access_token=IG_ACCESS_TOKEN)
    r = requests.get(f"{GRAPH}/{path}", params=params, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise SystemExit(f"[인스타그램 API 오류] {r.status_code}\n{r.text}")
    return r.json()


def wait_public(urls: list, tries: int = 12, delay: int = 10):
    """이미지가 실제로 공개 URL 에서 열리는지 확인합니다.
    GitHub raw 는 커밋 직후 몇 초~몇 분 캐시 지연이 있을 수 있습니다."""
    for url in urls:
        for attempt in range(1, tries + 1):
            try:
                r = requests.head(url, timeout=20, allow_redirects=True)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            print(f"  이미지 대기 중 ({attempt}/{tries}) {url}")
            time.sleep(delay)
        else:
            raise SystemExit(f"[오류] 이미지가 공개되지 않았습니다: {url}\n"
                             f"저장소가 private 이면 raw URL 이 열리지 않습니다. public 으로 바꿔주세요.")


def _wait_container(container_id: str, tries: int = 20, delay: int = 6):
    """미디어 컨테이너가 FINISHED 될 때까지 대기."""
    for _ in range(tries):
        info = _get(container_id, {"fields": "status_code,status"})
        code = info.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise SystemExit(f"[오류] 미디어 처리 실패: {info}")
        time.sleep(delay)
    raise SystemExit("[오류] 미디어 처리 시간 초과")


def publish_carousel(image_urls: list, caption: str) -> str:
    """이미지 공개 URL 목록 + 캡션 → 게시물 ID"""
    require("IG_USER_ID", IG_USER_ID)
    require("IG_ACCESS_TOKEN", IG_ACCESS_TOKEN)

    if not 2 <= len(image_urls) <= 10:
        raise SystemExit(f"[오류] 캐러셀은 2~10장이어야 합니다 (현재 {len(image_urls)}장).")

    wait_public(image_urls)

    # 1) 자식 컨테이너 생성
    children = []
    for url in image_urls:
        res = _post(f"{IG_USER_ID}/media", {"image_url": url, "is_carousel_item": "true"})
        children.append(res["id"])
        print(f"  컨테이너 생성 {res['id']}")

    for cid in children:
        _wait_container(cid)

    # 2) 캐러셀 부모 컨테이너
    parent = _post(f"{IG_USER_ID}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "caption": caption,
    })
    _wait_container(parent["id"])
    print(f"  캐러셀 컨테이너 {parent['id']}")

    # 3) 발행
    published = _post(f"{IG_USER_ID}/media_publish", {"creation_id": parent["id"]})
    print(f"  발행 완료 media_id={published['id']}")
    return published["id"]


def check_quota() -> dict:
    """24시간 발행 한도 확인 (계정당 100건)."""
    try:
        return _get(f"{IG_USER_ID}/content_publishing_limit",
                    {"fields": "config,quota_usage"})
    except SystemExit:
        return {}


# ── 실행 ─────────────────────────────────────────────────────────────
MANIFEST = BUILD_DIR / "manifest.json"


def today_str() -> str:
    return dt.datetime.now(zoneinfo.ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_post(bank: list, state: dict):
    done = set(state.get("published_ids", []))
    for post in bank:
        if post["id"] not in done:
            return post
    return None


def build_caption(post: dict) -> str:
    parts = [post["caption"].strip()]
    tags = post.get("hashtags", [])
    if tags:
        parts += ["", " ".join(f"#{t}" for t in tags)]
    return "\n".join(parts)


# ── 1단계: 렌더 ──────────────────────────────────────────────────────────────
def stage_render(args):
    bank = load_json(BANK_PATH, [])
    state = load_json(STATE_PATH, {"published_ids": [], "log": []})
    if not bank:
        sys.exit("[오류] content/bank.json 이 비어 있습니다.")

    if args.id:
        post = next((p for p in bank if p["id"] == args.id), None)
        if not post:
            sys.exit(f"[오류] id={args.id} 를 찾을 수 없습니다.")
    else:
        post = pick_post(bank, state)

    if post is None:
        print("::warning::발행할 콘텐츠가 남지 않았습니다. content/bank.json 을 채워주세요.")
        sys.exit(78)      # 78 = 뱅크 소진 (실패와 구분)

    date = today_str()
    print(f"[{date}] 오늘의 게시물: {post['id']} — {post['topic']}")

    out_dir = PUBLISHED_DIR / date
    if out_dir.exists():
        shutil.rmtree(out_dir)
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)


    slides = post["slides"]
    photos = fetch_photos(post.get("photo_queries", ["finance", "money"]),
                          BUILD_DIR / "photos", len(slides))
    cards = render_post(slides, photos, out_dir)

    save_json(MANIFEST, {
        "date": date,
        "id": post["id"],
        "topic": post["topic"],
        "caption": build_caption(post),
        "files": [str(p.relative_to(ROOT)) for p in cards],
    })
    print(f"카드 {len(cards)}장 생성 완료 → {out_dir}")
    if args.dry_run:
        print("[dry-run] 발행 단계는 실행하지 않습니다.")


# ── 2단계: 발행 ──────────────────────────────────────────────────────────────
def stage_publish(args):
    manifest = load_json(MANIFEST, None)
    if not manifest:
        sys.exit("[오류] build/manifest.json 이 없습니다. render 단계를 먼저 실행하세요.")

    urls = [raw_url(f) for f in manifest["files"]]
    print("공개 URL:")
    for u in urls:
        print("  " + u)

    media_id = publish_carousel(urls, manifest["caption"])

    state = load_json(STATE_PATH, {"published_ids": [], "log": []})
    state.setdefault("published_ids", []).append(manifest["id"])
    state.setdefault("log", []).append({
        "date": manifest["date"], "id": manifest["id"],
        "topic": manifest["topic"], "media_id": media_id,
    })
    save_json(STATE_PATH, state)
    print(f"발행 완료 — {manifest['topic']} (media_id={media_id})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["render", "publish"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--id", help="특정 게시물 id 지정")
    args = ap.parse_args()
    (stage_render if args.stage == "render" else stage_publish)(args)


if __name__ == "__main__":
    main()

