# -*- coding: utf-8 -*-
"""Google Flow 자동화 브리지 — VEO Automation 확장과 스튜디오를 '파일'로 연결.
   ★ 무협/야담/경제학 TTS 파서·엔진을 전혀 건드리지 않는 별도 모듈(읽기 전용 재사용).

   흐름:
     1) 대본에서 장면별 [영어 이미지 프롬프트] / [영상 프롬프트] 추출
     2) 확장이 먹는 포맷(.txt, 빈 줄로 프롬프트 구분)으로 내보내기 (+ 내보낸 장면 순서 기록)
     3) 확장이 생성·다운로드한 001,002,003… 파일을 '내보낸 순서'대로 장면번호에 되돌려
        카테고리 폴더(이미지/·클립/)에 배치

   추가비용 0(구독만) · 실행 시 외부 호출 0 · 전부 로컬.
"""
import os, re, json, shutil

# ── 대본 마커(무협 시각화 양식과 동일) ──
SCENE_RE = re.compile(r'^\[\s*장면\s*(\d+)\s*\]')
KOR_RE   = re.compile(r'^\[\s*한국어\s*(?:번역|원문)\s*\]')
IMG_RE   = re.compile(r'^\[\s*영어\s*이미지\s*프롬프트\s*\]\s*(.*)$')
VID_RE   = re.compile(r'^\[\s*영상\s*프롬프트\s*\]\s*(.*)$')
MARK_RE  = re.compile(r'^\[\s*(?:CTA|반전|클라이맥스|climax)\s*\]\s*$', re.IGNORECASE)
ANY_MARK = re.compile(r'^\[')   # 그 외 대괄호 시작 줄(마커) → 프롬프트 누적 종료 신호

def extract_prompts(script_text):
    """대본 → [{'seq','image','video'}, ...] (장면 순서).
       [영어 이미지 프롬프트]/[영상 프롬프트] 는 여러 줄이어도 다음 마커 전까지 전부 합침."""
    scenes, cur, mode = [], None, None

    def flush():
        nonlocal cur
        if cur is not None:
            cur["image"] = " ".join(cur["image"]).strip()
            cur["video"] = " ".join(cur["video"]).strip()
            scenes.append(cur)
        cur = None

    for raw in (script_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        ms = SCENE_RE.match(line)
        if ms:
            flush()
            cur = {"seq": f"{int(ms.group(1)):03d}", "image": [], "video": []}
            mode = None
            continue
        mi = IMG_RE.match(line)
        if mi:
            mode = "image"
            if cur is not None and mi.group(1).strip():
                cur["image"].append(mi.group(1).strip())
            continue
        mv = VID_RE.match(line)
        if mv:
            mode = "video"
            if cur is not None and mv.group(1).strip():
                cur["video"].append(mv.group(1).strip())
            continue
        if KOR_RE.match(line) or MARK_RE.match(line):   # TTS/위치 마커 → 프롬프트 아님
            mode = None
            continue
        if ANY_MARK.match(line):                        # 알 수 없는 [..] 마커 → 누적 중단
            mode = None
            continue
        if cur is not None and mode in ("image", "video"):
            cur[mode].append(line)                      # 이어지는 줄 누적
    flush()
    return scenes

# ── 영상(I2V) 프롬프트 자동 생성 — 이미지 프롬프트에서 파생 (지침 v11.1 규칙) ──
# 규칙: 카메라 1개 + 움직임 1개 + 환경요소 + 색톤, 인물 외형 재묘사 금지, 콘텐츠필터, 20~50단어.
_CF = [("bloodshed", "carnage of battle"), ("blood", "dark crimson"), ("killing", "striking down"),
       ("kill", "strike down"), ("corpse", "fallen figure"), ("massacre", "overwhelming force"),
       ("stab", "blade arc"), ("slashing", "blade arcs"), ("slash", "blade arc"),
       ("wound", "torn fabric with dark stain"), ("execution", "final judgment")]
def _cfilter(s):
    for a, b in _CF:
        s = re.sub(r"\b" + a + r"\b", b, s, flags=re.I)
    return s

_DYN = [("mote", "dust drifts"), ("dust", "dust drifts"), ("ember", "embers rise"),
        ("mist", "thin mist drifts"), ("candle", "candle flames flicker"), ("snow", "fine snow drifts"),
        ("banner", "banners ripple"), ("lantern", "lantern lights flicker"), ("smoke", "smoke curls"),
        ("rain", "rain falls softly"), ("petal", "petals drift"), ("spark", "sparks scatter")]
def _dyn_elems(img):
    low = img.lower(); found = []
    for kw, phrase in _DYN:
        if kw in low and phrase not in found:
            found.append(phrase)
    return found[:2] or ["dust drifts softly"]

def _tone_colors(img):
    m = (re.search(r"color palette of (.+?) accents", img, re.I)
         or re.search(r"Color palette:\s*(.+?)(?:,? with|\.)", img, re.I))
    if not m:
        return "crimson and black"
    parts, cleaned = re.split(r",|\band\b", m.group(1)), []
    for p in parts:
        p = re.sub(r"\b(deep|cold|bold|sickly|antique|faint|pale|natural|gradient|transitions|accents)\b",
                   "", p, flags=re.I).strip()
        p = re.sub(r"\s+", "-", p.strip())
        if p and p not in cleaned:
            cleaned.append(p)
    return " and ".join(cleaned[:2]) if cleaned else "crimson and black"

def _cut_type(img):
    low = img.lower()
    if "no people" in low:
        return "SCENE"
    if ("high-angle" in low or "kneeling" in low or "crawling" in low or "prostrat" in low
            or ("warriors" in low and "ground" in low) or "crowd" in low):
        return "CROWD"
    return "CHAR"

def _shot(img):
    low = img.lower()
    for k in ("extreme close-up", "close-up", "medium shot", "medium", "wide establishing",
              "wide shot", "low-angle", "high-angle"):
        if k in low:
            return k
    return "medium"

def gen_video_prompt(image_prompt):
    """이미지 프롬프트 1개 → I2V 영상 프롬프트 1개(지침 규칙). 무료·로컬·결정적."""
    if not image_prompt:
        return ""
    img = image_prompt
    ct = _cut_type(img); dyn = " and ".join(_dyn_elems(img)); tone_c = _tone_colors(img)
    tone = f"Dark manhwa style, cel-shading, {tone_c} tones, cinematic wuxia mood."
    low = img.lower()
    if ct == "SCENE":
        cam = "Slow crane rising over the scene" if ("wide" in low or "establishing" in low) else "Slow dolly in across the space"
        body = f"{cam}. The architecture and structures stay fixed as {dyn}."
    elif ct == "CROWD":
        cam = "Slow high-angle crane descending over the crowd"
        body = f"{cam}. The bowed figures stay still and low as {dyn}."
    else:  # CHAR
        if any(w in low for w in ("smile", "smirk", "grin")):
            motion = "parts the lips into a faint expression"
        elif any(w in low for w in ("shout", "mouth wide", "screaming", "agape")):
            motion = "draws one sharp breath as the shoulders rise"
        elif any(w in low for w in ("blade", "sword", "strike", "palm", "fist", "weapon")):
            motion = "completes one restrained motion of the hand while the body stays fixed"
        else:
            motion = "makes one small deliberate motion"
        shot = "Static close-up shot" if "close-up" in low else "Slow dolly in"
        body = f"{shot}. The figure {motion} while everything else stays still. {dyn.capitalize()}."
    return f"{_cfilter(body)} {tone}"   # 콘텐츠필터는 움직임 본문에만(색톤 보존)

def fill_video_auto(scenes):
    """video가 비어있는 장면을 이미지 프롬프트에서 자동 생성해 채움. 이미 있으면 유지."""
    for s in scenes:
        if not s.get("video") and s.get("image"):
            s["video"] = gen_video_prompt(s["image"])
    return scenes

def assign_video_manual(scenes, video_text):
    """수동으로 붙여넣은 영상 프롬프트(빈 줄 구분)를 장면 순서대로 배정. 반환: (scenes, 블록수)."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", (video_text or "").strip()) if b.strip()]
    for i, s in enumerate(scenes):
        s["video"] = blocks[i] if i < len(blocks) else ""
    return scenes, len(blocks)

# ── 확장 업로드용 .txt 내보내기 (빈 줄로 프롬프트 구분) ──
def export_prompt_files(scenes, out_dir, kind="image", only_seqs=None):
    """kind='image'|'video'. only_seqs 지정 시 그 장면만(없으면 프롬프트 있는 전체).
       반환: (파일경로, [내보낸 seq 순서]). 순서 기록이 다운로드 되돌리기의 핵심."""
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for s in scenes:
        if only_seqs is not None and s["seq"] not in only_seqs:
            continue
        p = s.get(kind, "").strip()
        if p:
            rows.append((s["seq"], p))
    fname = "이미지프롬프트.txt" if kind == "image" else "영상프롬프트.txt"
    path = os.path.join(out_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(p for _, p in rows))        # 빈 줄로 구분
    order = [seq for seq, _ in rows]
    # 되돌리기용 순서 기록
    json.dump(order, open(os.path.join(out_dir, f".flow_order_{kind}.json"), "w",
                          encoding="utf-8"), ensure_ascii=False)
    return path, order

# ── 확장이 생성한 001,002,003… 다운로드 → 장면번호로 되돌려 배치 ──
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VID_EXTS = (".mp4", ".mov", ".webm", ".mkv")

def _lead_num(name):
    m = re.match(r'\s*0*(\d+)', os.path.basename(name))
    return int(m.group(1)) if m else None

def import_downloads(download_dir, dest_dir, order, kind="image"):
    """download_dir의 생성물(001,002,003…)을 order[i-1] 장면번호로 dest_dir에 복사.
       확장이 파일명 앞에 001,002… 를 붙여 순서대로 저장하므로 앞 숫자로 정렬.
       반환: [(다운로드파일, 배정된 seq), ...] + 누락/잉여 진단."""
    exts = IMG_EXTS if kind == "image" else VID_EXTS
    files = [os.path.join(download_dir, f) for f in os.listdir(download_dir)
             if f.lower().endswith(exts)]
    # 앞 숫자 우선 정렬, 없으면 수정시각
    files.sort(key=lambda p: (_lead_num(p) if _lead_num(p) is not None else 10**9,
                              os.path.getmtime(p)))
    os.makedirs(dest_dir, exist_ok=True)
    placed, report = [], []
    for i, src in enumerate(files):
        if i >= len(order):
            report.append(f"잉여 다운로드(내보낸 장면보다 많음): {os.path.basename(src)}")
            continue
        seq = order[i]
        ext = os.path.splitext(src)[1].lower()
        dst = os.path.join(dest_dir, f"{seq}{ext}")
        shutil.copy2(src, dst)
        placed.append((os.path.basename(src), f"{seq}{ext}"))
    if len(files) < len(order):
        missing = order[len(files):]
        report.append(f"누락(생성 안 됨) 장면: {', '.join(missing)}")
    return placed, report
