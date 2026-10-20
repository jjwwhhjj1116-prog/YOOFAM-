# app.py — 무협 영상+TTS+자막 변환 스튜디오 (Gradio 웹 대시보드)
# STEP 3 · PART 1: 대사 화자↔역할 수동 오버라이드 + character_cast.json 저장 + 게이트 연동.
#   PART A(매칭 검증) / PART B(2단계 제작) 유지. ★ tts_pipeline.py는 절대 수정하지 않고 import만.
import os, re, glob, shutil, json, base64, subprocess, tempfile
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)
WORK_SCRIPT = os.path.join(PROJECT_DIR, "_작업대본.txt")
ASSETS_DIR  = os.path.join(PROJECT_DIR, "assets")
OUT_BUILD   = "완성본"        # 병합 산출물(전체영상.mp4 / 전체오디오.mp3 / 전체자막.srt)

# CapCut 초안(소재) 폴더 — 이 밑에 화 제목 하위폴더를 만들어 소재만 복사(기존 프로젝트 무수정)
CAPCUT_DIR = r"C:\Users\7500F\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"

def banner_html():
    """assets/banner.(png|jpg)가 있으면 폭 100%(최대 1024) 가운데 정렬 배너 HTML, 없으면 None."""
    for name, mime in (("banner.png", "image/png"), ("banner.jpg", "image/jpeg"),
                       ("banner.jpeg", "image/jpeg")):
        p = os.path.join(ASSETS_DIR, name)
        if os.path.exists(p):
            b64 = base64.b64encode(open(p, "rb").read()).decode()
            return (f"<div style='text-align:center;margin:0 0 8px'>"
                    f"<img src='data:{mime};base64,{b64}' "
                    f"style='width:100%;max-width:1024px;height:auto;border-radius:10px'/></div>")
    return None

import gradio as gr
import tts_pipeline as engine
import capcut_export as capcut          # CapCut 초안(draft) 프로젝트 생성기
import yadam_pipeline as yadam          # 야담&민담(무료 edge-tts / Vrew 오디오) — 무협과 격리
import keizai_pipeline as keizai        # 일본 경제학(ElevenLabs / edge-tts JA) — 무협과 격리
import flow_pipeline as flow            # Google Flow 자동생성 브리지(VEO Automation 확장 연동) — 무협과 격리

HEADERS     = ["No.", "종류", "화자", "감정", "엔진", "대본(TTS텍스트)", "영상"]
DRY_HEADERS = ["장면번호", "화자", "역할", "요청감정", "실제 style", "매핑종류", "엔진"]
SPK_HEADERS = ["캐릭터", "대사횟수", "배정 역할", "성우 (현재 엔진)"]
# 대사 역할(나레이션·설명 제외) — voice_map.json 순서 그대로
ROLES = [k for k in engine.VOICE_MAP if k != "나레이션" and not k.startswith("_")]
# 역할별 나이대(Supertone 성우 메타데이터) — 드롭다운 표시용. 성별은 역할명으로 자명.
ROLE_AGE = {
    "남주": "청년(20~30대)", "여주": "청년(20~30대)", "여주2": "청년(20~30대)",
    "노인": "노년", "노인2": "노년",
    "남자1": "중년(40대)", "남자2": "청년(20~30대)", "남자3": "중년(40대)", "남자4": "청년(20~30대)",
    "여전사": "청년(20~30대)",
    "여주3": "청년(20~30대)·여", "남자5": "청년(20~30대)", "노인3": "노년", "노인4": "노년",
    "악역": "중년(40대)", "악역2": "중년(40대)", "악역3": "노년", "악역4": "중년(40대)",
}
# (표시라벨, 실제값) — 값은 역할 키 그대로 유지
def _role_label(r):
    tc = (engine.VOICE_MAP.get(r) or {}).get("_tc")
    if tc:                                   # 타입캐스트 성우 이름으로 표시
        return f"{r} · {tc}"
    return f"{r} · {ROLE_AGE[r]}" if r in ROLE_AGE else r
ROLE_CHOICES = [(_role_label(r), r) for r in ROLES]

CONFIRM_HINT = "🔧 대사 화자별 역할을 확인/변경한 뒤 **💾 매핑 확정**을 누르세요. (확정해야 ② 실제 제작이 열립니다)"
# ② 실제 제작 버튼 라벨 — 활성/비활성이 한눈에 보이게 문구도 바뀜
DLG_ENGINE_TC = "① 타입캐스트 (감정 자동)"
DLG_ENGINE_EL = "② ElevenLabs (감정 태그)"
DLG_ENGINE_GM = "③ Gemini (기본 · 감정 연기 지시)"
DLG_ENGINE_ST = "④ Supertone (2026-08-31 종료 · 사용불가)"

def apply_engine(choice):
    """UI 선택 → 엔진 반영. 반환: 엔진명(로그용)."""
    eng = {DLG_ENGINE_TC: engine.ENGINE_TC, DLG_ENGINE_EL: engine.ENGINE_EL,
           DLG_ENGINE_GM: engine.ENGINE_GM,
           DLG_ENGINE_ST: engine.ENGINE_ST}.get(choice, engine.ENGINE_GM)
    engine.set_engines(dialogue=eng)
    return eng

MAKE_BTN_OFF = "🔒 ② 실제 제작 (잠김 — ① 미리보기 먼저)"
MAKE_BTN_ON  = "▶ ② 실제 제작 시작 (크레딧 소모)"

def log(msg):
    print(msg, flush=True)

# ── 입력 정규화: 전각/유사 특수문자 → 표준 기호 ──────
_NORMALIZE_MAP = {
    "｜": "|", "¦": "|", "∣": "|", "│": "|", "❘": "|", "⏐": "|", "ǀ": "|",
    "［": "[", "］": "]",
}
def normalize_input(text):
    text = text or ""
    n = 0
    for k, v in _NORMALIZE_MAP.items():
        c = text.count(k)
        if c:
            n += c
            text = text.replace(k, v)
    return text, n

# ── 유연한 형식 감지 (표시/매칭용) ───────────────────
SCENE_FLEX = re.compile(r'^\[?\s*(?:장면|씬|scene)\s*0*(\d+)', re.IGNORECASE)
KOR_MARK   = re.compile(r'^\[\s*(?:한국어\s*번역|한국어\s*원문|한국어|한글\s*번역|한글|번역|원문)\s*\]\s*')
ENG_MARK   = re.compile(r'^\[\s*(?:영어\s*이미지\s*프롬프트|영어\s*프롬프트|이미지\s*프롬프트|이미지|삽화|그림|영어|english|image\s*prompt|prompt)\s*\]', re.IGNORECASE)
CTA_MARK   = re.compile(r'^\[\s*CTA\s*\]\s*$', re.IGNORECASE)   # 후킹 끝 → CTA 삽입 지점
REVERSAL_MARK = re.compile(r'^\[\s*(?:반전|클라이맥스|climax)\s*\]\s*$', re.IGNORECASE)  # 반전 BGM 시작

def _marker_after_seq(text, mark_re):
    """마커 직전 [장면 N]의 seq('003' 등)를 반환. 없으면 None."""
    last = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if mark_re.match(line):
            return last
        m = SCENE_FLEX.match(line)
        if m:
            last = f"{int(m.group(1)):03d}"
    return None

def cta_after_seq(text):
    """[CTA] 마커가 있으면 그 직전 장면 seq. 없으면 None. → 이 장면 뒤에 CTA 영상."""
    return _marker_after_seq(text, CTA_MARK)

def reversal_after_seq(text):
    """[반전] 마커가 있으면 그 직전 장면 seq. 없으면 None. → 이 장면부터 반전 BGM(선택)."""
    return _marker_after_seq(text, REVERSAL_MARK)

def _is_english_prompt(line):
    if ENG_MARK.match(line):
        return True
    hangul = len(re.findall(r'[가-힣]', line))
    latin  = len(re.findall(r'[A-Za-z]', line))
    return latin >= 15 and latin > hangul * 2

def _looks_english_fragment(line):
    """[영어 프롬프트]가 여러 줄로 이어질 때의 '순수 영어 조각'인지. 한글이 없고 라틴문자만 있으면 프롬프트 잔여로 간주."""
    hangul = len(re.findall(r'[가-힣]', line))
    latin  = len(re.findall(r'[A-Za-z]', line))
    return hangul == 0 and latin >= 3

def analyze_script_text(text):
    """[장면 N] 마커 단위로 '블록'을 만들어, 마커~다음 마커 사이의 한국어 줄을 한 장면으로 합친다.
       (한 장면의 나레이션/대사가 여러 줄로 줄바꿈돼 있어도 한 장면으로 인식 — 장면 수 부풀림 방지)"""
    scenes, diags = [], []
    raw_lines = (text or "").splitlines()
    has_marker = any(SCENE_FLEX.match(r.strip()) for r in raw_lines if r.strip())

    # 1) 줄 → 블록(장면). 블록 = {num, lineno, parts[]}
    blocks, cur = [], None
    for lineno, raw in enumerate(raw_lines, 1):
        line = raw.strip()
        if not line:
            continue
        m = SCENE_FLEX.match(line)
        if m:                                        # 새 장면 시작
            if cur is not None:
                blocks.append(cur)
            cur = {"num": int(m.group(1)), "lineno": lineno, "parts": []}
            continue
        if CTA_MARK.match(line) or REVERSAL_MARK.match(line):   # [CTA]/[반전] 마커: 장면 아님, 위치만
            continue
        if _is_english_prompt(line) or _looks_english_fragment(line):
            continue                                 # 영어 이미지 프롬프트(여러 줄 포함) 건너뜀
        content = KOR_MARK.sub('', line).strip()
        if not content:
            continue
        if has_marker:                               # 블록 모드: 현재 장면에 누적
            if cur is None:                          # 첫 마커 이전의 본문
                cur = {"num": None, "lineno": lineno, "parts": []}
            cur["parts"].append(content)
        else:                                        # 마커 없는 대본: 줄 = 장면(기존 동작)
            blocks.append({"num": None, "lineno": lineno, "parts": [content]})
    if cur is not None:
        blocks.append(cur)

    # 2) 블록 → 장면(번호 확정 + 대사/나레이션 분류)
    last, used = 0, {}
    for b in blocks:
        content = " ".join(b["parts"]).strip()
        if not content:
            continue
        lineno = b["lineno"]
        if b["num"] is not None:
            num = b["num"]
        else:
            num = last + 1
            if not scenes:
                diags.append(f"줄 {lineno}: [장면 N] 헤더를 못 찾아 자동 {num:03d} 부여 — 장면 헤더 형식을 확인하세요.")
            else:
                diags.append(f"줄 {lineno}: 직전에 [장면 N]이 없어 자동 {num:03d} 부여.")
        last = num
        seq = f"{num:03d}"
        if seq in used:
            diags.append(f"줄 {lineno}: 장면 {seq} 번호 중복(줄 {used[seq]}과 동일) — 뒤 내용으로 덮어씀.")
        used[seq] = lineno
        try:
            parts = engine._split_parts(content)
        except Exception as ex:
            diags.append(f"줄 {lineno}: 파싱 오류 → {ex}")
            parts = [{"kind": "narration", "speaker": "나레이션", "emotion": None, "text": content}]
        if len(parts) == 1:                              # 단일(기존 동작 그대로)
            p = parts[0]
            if p["kind"] == "dialogue":
                scenes.append((seq, "대사", p["speaker"], p["emotion"] or "-", "Supertone", p["text"]))
            else:
                if engine.TAG_LEAK_RE.search(p["text"]):
                    diags.append(f"줄 {lineno}: 나레이션에 [ ] 또는 | 포함 — 마커/태그 오타 의심 → “{p['text'][:24]}”")
                scenes.append((seq, "나레이션", "나레이션", "-", "ElevenLabs", p["text"]))
        else:                                            # 다중 화자 장면 → 한 행으로 표시
            dlgs = [p for p in parts if p["kind"] == "dialogue"]
            sp_label  = "·".join(dict.fromkeys(p["speaker"] for p in dlgs)) or "나레이션"
            emo_label = "·".join((p["emotion"] or "-") for p in dlgs) or "-"
            # 텍스트 컬럼(s[5])은 자막/매칭에 그대로 쓰이므로 화자·감정 태그 없는 '깨끗한 합본'이어야 함.
            combined = " ".join(p["text"] for p in parts)   # 엔진 seg["text"]와 동일 → 자막 정상
            scenes.append((seq, f"대사({len(dlgs)}명)", sp_label, emo_label, "Supertone", combined))
            diags.append(f"✅ 장면 {seq}: 한 장면에 대사 {len(dlgs)}개(화자 {sp_label}) — 순서대로 이어붙여 생성됩니다.")
    seqset = {s[0] for s in scenes}
    rev = reversal_after_seq(text)
    if rev:
        diags.insert(0, (f"✅ [반전] 마커 인식 — 장면 {rev} 뒤부터 반전 BGM (내보내기 시 자동)."
                         if rev in seqset else f"⚠️ [반전] 마커의 직전 장면 {rev}을(를) 못 찾음."))
    cta = cta_after_seq(text)
    if cta:
        diags.insert(0, (f"✅ [CTA] 마커 인식 — 장면 {cta} 뒤에 CTA 영상 삽입 지점 (내보내기 시 자동)."
                         if cta in seqset else f"⚠️ [CTA] 마커가 있으나 직전 장면 {cta}을(를) 못 찾음 — 위치 확인 필요."))
    return scenes, diags

def _fmt_diags(diags):
    if not diags:
        return "🩺 **진단: 경고 없음** — 모든 줄을 정상 인식했습니다."
    head = diags[:40]
    body = "\n".join("- " + d for d in head)
    if len(diags) > 40:
        body += f"\n- …외 {len(diags) - 40}건"
    return f"🩺 **진단 / 경고 {len(diags)}건**\n\n{body}"

def _num_key(path):
    m = re.match(r'\s*0*(\d+)', os.path.basename(path))
    return f"{int(m.group(1)):03d}" if m else None

def _fail_rows(reason):
    return [["—", "분석 실패", "-", "-", "-", reason, "-"]]

def _cut(lst, n=50):
    return ", ".join(lst[:n]) + (f" …외 {len(lst) - n}개" if len(lst) > n else "")

# ── PART 1: 대사 화자별 집계 ─────────────────────────
def compute_speakers(scenes):
    """→ [(speaker, 대사횟수, 자동배정역할), ...] 대사 횟수 내림차순."""
    counts = {}
    for (seq, kind, sp, emo, eng, text) in scenes:
        if kind.startswith("대사"):                       # "대사" 또는 "대사(N명)"
            for one in sp.split("·"):                     # 다중 화자면 화자별로 집계
                one = one.strip()
                if one and one != "나레이션":
                    counts[one] = counts.get(one, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: -x[1])   # 대사 많은 순
    # 대사 1위 = 주인공(남주) 자동. 이름에 노인/악역/여주 호칭 있으면 그게 우선.
    return [(sp, c, engine.auto_role(sp, is_protagonist=(i == 0)))
            for i, (sp, c) in enumerate(ranked)]

def speaker_rows(speakers):
    def _voice(role):
        cfg = engine.VOICE_MAP.get(role, {}) or {}
        if engine.DIALOGUE_ENGINE == engine.ENGINE_TC:
            return f"{cfg.get('_tc', '-')}  ({cfg.get('tc_voice_id', '-')})"
        if engine.DIALOGUE_ENGINE == engine.ENGINE_EL:
            return f"{cfg.get('_el', '-')}  ({cfg.get('el_voice_id', '-')})"
        if engine.DIALOGUE_ENGINE == engine.ENGINE_GM:
            return f"{cfg.get('gm_voice_id', '-')}  (Gemini)"
        return cfg.get("voice_id", "-")
    return [[sp, cnt, auto, _voice(auto)] for sp, cnt, auto in speakers]

# ── PART A: 매칭 검증 ────────────────────────────────
def match_status(scenes, files):
    seqs = [s[0] for s in scenes]
    seqset = set(seqs)
    seq2files, surplus = {}, []
    _OK_EXTS = engine.SCENE_VID_EXTS + engine.SCENE_IMG_EXTS   # 영상 + 이미지 둘 다 허용
    for p in (files or []):
        base = os.path.basename(p)
        if not base.lower().endswith(_OK_EXTS):
            surplus.append(f"{base} (영상·이미지 아님)"); continue
        k = _num_key(p)
        if k and k in seqset:
            seq2files.setdefault(k, []).append(base)
        else:
            surplus.append(f"{base} ({('번호 '+k+' → 해당 장면 없음') if k else '앞 숫자 없음'})")
    rows, dup = [], []
    for (seq, kind, sp, emo, eng, text) in scenes:
        fl = seq2files.get(seq, [])
        if not fl:
            vid = "❌ 소재 없음"
        elif len(fl) == 1:
            _ic = "🖼️" if fl[0].lower().endswith(engine.SCENE_IMG_EXTS) else "🎬"
            vid = f"✅ {_ic} 매칭됨 ({fl[0]})"
        else:
            vid = f"⚠️ 번호 중복 ({', '.join(fl)})"; dup.append(seq)
        rows.append([seq, kind, sp, emo, eng, text, vid])
    missing = [s for s in seqs if s not in seq2files]
    nums = sorted(int(s) for s in seqs)
    gaps = [f"{n:03d}" for n in range(nums[0], nums[-1] + 1) if n not in set(nums)] if nums else []
    matched = len(seqset & set(seq2files))
    ok = bool(scenes) and not missing and not surplus and not dup
    return rows, missing, surplus, dup, gaps, ok, len(scenes), matched

def match_report(scenes, files):
    if not scenes:
        return [], "⬆️ 대본을 분석하면 매칭 검증이 표시됩니다.", False
    rows, missing, surplus, dup, gaps, ok, total, matched = match_status(scenes, files)
    if ok:
        md = (f"<div style='padding:8px 12px;border-radius:8px;background:#e7f7ee;"
              f"color:#12a150;font-weight:700;font-size:15px'>✅ {matched}/{total} 완벽 매칭 — 영상 제작 가능</div>")
        return rows, md, True
    lines = [f"**매칭 현황: {matched}/{total} 장면 매칭됨**"]
    if missing:  lines.append(f"❌ **영상 누락 장면**: {_cut(missing)}")
    if surplus:  lines.append(f"⚠️ **잉여/번호 불일치 영상**: {_cut(surplus)}")
    if dup:      lines.append(f"⚠️ **한 장면에 영상 2개+ (번호 충돌)**: {_cut(dup)}")
    if gaps:     lines.append(f"⚠️ **장면 번호 불연속(빠진 번호)**: {_cut(gaps)}")
    head = ("<div style='padding:8px 12px;border-radius:8px;background:#fdecec;"
            "color:#e5484d;font-weight:700;font-size:15px'>🚫 매칭 미완료 — 영상 제작 불가</div>")
    return rows, head + "\n\n" + "  \n".join(lines), False

# ── PART B: Dry-run ──────────────────────────────────
DRY_RESET_MSG = "🔄 대본/매칭/매핑이 갱신됨 — **① 미리보기(Dry-run)** 를 다시 실행하세요."

def build_dryrun(script_text):
    script_text, _ = normalize_input(script_text)
    with open(WORK_SCRIPT, "w", encoding="utf-8") as f:
        f.write(script_text or "")
    try:
        segs = engine.load_segments(WORK_SCRIPT)
    except Exception as e:
        return [["—", "분석 실패", f"{type(e).__name__}", "-", "-", "-", "-"]], f"❌ **분석 실패:** {e}", False
    if not segs:
        return [["—", "분석 실패", "장면 0개", "-", "-", "-", "-"]], "❌ 장면을 하나도 못 읽었습니다.", False
    rows, uncast, n_narr = [], set(), 0
    kc = {"정확매칭": 0, "근사폴백": 0, "neutral최후": 0}
    fb_counts, neutral_scenes = {}, []
    _MARK = {"정확매칭": "✅ 정확", "근사폴백": "≈ 근사", "neutral최후": "⚠ neutral최후"}
    n_dial = 0
    for seg in segs:
        for s in engine.iter_parts(seg):                       # 다중 화자 장면은 파트별로
            seq = s["seq"]
            if s["kind"] == "narration":
                n_narr += 1
                rows.append([seq, "나레이션", "-", "-", "-", "-", "ElevenLabs"])
            else:
                n_dial += 1
                speaker, emotion = s["speaker"], s["emotion"]
                role = engine.role_of(speaker)
                if speaker not in engine.CHARACTER_CAST:
                    uncast.add(speaker)
                if engine.DIALOGUE_ENGINE == engine.ENGINE_TC:   # 감정은 문맥으로 자동 판단
                    tcn = (engine.VOICE_MAP.get(role, {}) or {}).get("_tc", "-")
                    rows.append([seq, speaker, role, emotion or "-", "smart 자동", f"🎭 {tcn}", "Typecast"])
                    continue
                if engine.DIALOGUE_ENGINE == engine.ENGINE_GM:  # 감정 → 한국어 연기 지시문
                    import gemini_engine as _gm
                    _st = engine.resolve_style(role, emotion)
                    _vn = (engine.VOICE_MAP.get(role, {}) or {}).get("gm_voice_id", "-")
                    rows.append([seq, speaker, role, emotion or "-",
                                 _gm.style_for_emotion(emotion, _st), f"🎭 {_vn}", "Gemini"])
                    continue
                final, kind = engine.classify(role, emotion)   # 계열 근사 판정
                kc[kind] = kc.get(kind, 0) + 1
                if kind != "정확매칭":
                    fb_counts[emotion] = fb_counts.get(emotion, 0) + 1
                if kind == "neutral최후":
                    neutral_scenes.append(seq)
                rows.append([seq, speaker, role, emotion, final, _MARK[kind], "Supertone"])
    lines = [f"✅ **Dry-run 완료** · 총 {len(segs)}장면 · 대사/나레이션 조각 {n_narr + n_dial}개 (나레이션 {n_narr} / 대사 {n_dial})"]
    if engine.DIALOGUE_ENGINE == engine.ENGINE_TC:
        lines.append("🎭 **대사 = 타입캐스트 · 감정은 앞뒤 장면 문맥으로 자동 판단**(태그 강제 안 함) · 나레이션 = ElevenLabs")
    elif engine.DIALOGUE_ENGINE == engine.ENGINE_GM:
        lines.append("🎭 **대사 = Gemini · 감정을 한국어 연기 지시문으로 전달** · 나레이션 = ElevenLabs")
    else:
        lines.append(f"매핑: 정확 {kc['정확매칭']} · 근사(계열유지) {kc['근사폴백']} · neutral최후 {kc['neutral최후']}")
    if fb_counts:
        lines.append("🔁 **폴백 감정어:** " + ", ".join(f"{e} {c}회" for e, c in sorted(fb_counts.items(), key=lambda x: -x[1])))
    if engine.DIALOGUE_ENGINE not in (engine.ENGINE_TC, engine.ENGINE_GM):
        if neutral_scenes:
            lines.append(f"⚠️ **neutral 최후폴백 장면(밋밋하게 나갈 대사):** {', '.join(neutral_scenes[:60])}")
        else:
            lines.append("🟢 neutral 최후폴백 없음 — 모든 대사가 계열 style로 표현됨")
    if uncast:
        lines.append(f"⚠️ **cast 미등록 화자**(기본 '{engine.DEFAULT_DIALOGUE_ROLE}' 처리): " + ", ".join(sorted(uncast)))
    return rows, "  \n".join(lines), True

# ── PART B: 실제 제작 ────────────────────────────────
def summarize_outputs(scenes):
    mp3s    = glob.glob(os.path.join(engine.OUT_AUDIO, "*.mp3"))
    matched = glob.glob(os.path.join(engine.OUT_MATCH, "*_matched.mp4"))
    srt     = os.path.join(engine.OUT_SRT, "전체.srt")
    final_ts = "-"
    if os.path.exists(srt):
        times = re.findall(r'-->\s*(\d{2}:\d{2}:\d{2},\d{3})', open(srt, encoding="utf-8").read())
        if times:
            final_ts = times[-1]
    failed = [seq for (seq, *_) in scenes
              if not os.path.exists(os.path.join(engine.OUT_MATCH, f"{seq}_matched.mp4"))]
    return (f"✅ **제작 완료**  \n- 생성된 mp3: **{len(mp3s)}개**  \n- matched.mp4: **{len(matched)}개**  \n"
            f"- 전체.srt 최종 시간: **{final_ts}**  \n- 실패한 장면: **{', '.join(failed) if failed else '없음'}**  \n"
            f"- 산출물 위치: `오디오/`, `매칭/`, `자막/`")

def place_videos(scenes, files):
    seq2path = {}
    for p in (files or []):
        k = _num_key(p)
        if k:
            seq2path[k] = p
    os.makedirs(engine.VIDEO_DIR, exist_ok=True)
    n = 0
    for (seq, *_) in scenes:
        src = seq2path.get(seq)
        if src and os.path.exists(src):
            ext = os.path.splitext(src)[1].lower() or ".mp4"      # 영상·이미지 확장자 유지
            for old in (engine.SCENE_VID_EXTS + engine.SCENE_IMG_EXTS):   # 그 장면의 예전 소재 제거
                q = os.path.join(engine.VIDEO_DIR, f"{seq}{old}")
                if old != ext and os.path.exists(q):
                    try: os.remove(q)
                    except OSError: pass
            shutil.copy(src, os.path.join(engine.VIDEO_DIR, f"{seq}{ext}")); n += 1
    return n

# ── 이벤트 핸들러 ────────────────────────────────────
def _reset_pack(ok):
    # (dryrun_btn, make_btn, dryrun_done, dryrun_table, dryrun_summary)
    return (gr.update(interactive=ok), gr.update(interactive=False, value=MAKE_BTN_OFF), False, [], DRY_RESET_MSG)

def on_script(script_text, files):
    raw_len = len(script_text or "")
    script_text, npipe = normalize_input(script_text)
    log(f"[분석시작] 입력 {raw_len}자" + (f" · 특수문자 {npipe}개 자동교정" if npipe else ""))
    empty_tail = ([], [], {}, False, "")   # speakers_state, speaker_table, override_state, cast_confirmed, confirm_md
    try:
        scenes, diags = analyze_script_text(script_text)
        if npipe:
            diags.insert(0, f"전각 파이프/대괄호 등 특수문자 {npipe}개를 표준 기호(|, [ ])로 자동 교정 — 화자|감정 분리 정상화.")
    except Exception as e:
        log(f"[분석오류] {type(e).__name__}: {e}")
        return ([], _fail_rows(f"{type(e).__name__}: {e}"), f"❌ **분석 오류:** {e}", "", "",
                *_reset_pack(False), *empty_tail)
    if not scenes:
        log("[분석결과] 장면 0개")
        return ([], _fail_rows("장면 0개 — 아래 진단 확인"),
                "❌ **장면을 하나도 못 읽었습니다.** 아래 진단을 보세요.",
                _fmt_diags(diags) if diags else "🩺 입력이 비었거나 인식 가능한 줄이 없습니다.",
                "⬆️ 대본을 분석하면 매칭 검증이 표시됩니다.", *_reset_pack(False), *empty_tail)
    rows, match_md_str, ok = match_report(scenes, files)
    n_narr = sum(1 for s in scenes if s[1] == "나레이션")
    n_dial = len(scenes) - n_narr
    summary = f"✅ **총 {len(scenes)}개 장면** (나레이션 {n_narr}개 / 대사 {n_dial}개)"
    speakers = compute_speakers(scenes)
    log(f"[분석완료] 장면 {len(scenes)}개, 대사화자 {len(speakers)}명, 매칭통과={ok}")
    return (list(scenes), rows, summary, _fmt_diags(diags), match_md_str,
            *_reset_pack(ok),
            speakers, speaker_rows(speakers), {}, False, CONFIRM_HINT)

def on_files(files, scenes):
    names = [os.path.basename(p) for p in (files or [])]
    log(f"[업로드됨: {', '.join(names) if names else '(없음)'}]")
    if not scenes:
        return (files, [], "⚠️ 먼저 대본을 분석해 장면을 만든 뒤 업로드하세요.", *_reset_pack(False))
    try:
        rows, match_md_str, ok = match_report(scenes, files)
    except Exception as e:
        log(f"[업로드오류] {type(e).__name__}: {e}")
        return (files, None, f"❌ **업로드 처리 오류:** {type(e).__name__}: {e}", *_reset_pack(False))
    return (files, rows, match_md_str, *_reset_pack(ok))

def confirm_mapping(speakers, overrides):
    if not speakers:
        return False, False, "⚠️ 확정할 대사 화자가 없습니다.", gr.update(interactive=False), speakers, speaker_rows(speakers or [])
    mapping = {sp: (overrides or {}).get(sp, auto) for sp, cnt, auto in speakers}
    full = dict(engine.CHARACTER_CAST); full.update(mapping)
    with open(engine.CAST_PATH, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)
    engine.CHARACTER_CAST = full          # 런타임 반영(파일 무수정; 엔진 dict 갱신)
    log("[매핑확정] " + ", ".join(f"{s}->{r}" for s, r in mapping.items()))
    summary = ("✅ **매핑 확정 완료** · " + ", ".join(f"{s}→{r}" for s, r in mapping.items())
               + "  \n💾 character_cast.json 저장. 이제 **① 미리보기(Dry-run)** 를 실행하면 ②가 활성화됩니다.")
    new_speakers = [(sp, cnt, mapping[sp]) for sp, cnt, auto in speakers]   # 확정 역할로 교체
    return (True, False, summary, gr.update(interactive=False, value=MAKE_BTN_OFF),
            new_speakers, speaker_rows(new_speakers))   # + speakers_state, speaker_table 갱신

def do_dryrun(script_text, cast_confirmed, dlg_engine=None):
    # 매핑 미확정이면 dry-run 자체를 막고 안내(역할 드롭다운 변경이 반영되도록 강제)
    if not cast_confirmed:
        log("[Dry-run 거부] 매핑 미확정")
        return ([], "⚠️ **먼저 💾 매핑 확정을 누르세요.**  \n역할 드롭다운을 바꿨다면 **확정**해야 dry-run·실제 제작에 반영됩니다.",
                False, gr.update(interactive=False))
    if dlg_engine:
        log(f"[엔진] 대사={apply_engine(dlg_engine)}")
    log("[Dry-run 시작] (API 없음)")
    rows, summary, ok = build_dryrun(script_text)     # build_dryrun은 engine.CHARACTER_CAST(확정 반영) 사용
    log(f"[Dry-run 완료] ok={ok}")
    on = ok and cast_confirmed
    if on:
        summary += "  \n🟥 **② 실제 제작 버튼이 활성화됐습니다** (빨간 버튼) — 누르면 크레딧이 소모됩니다."
    return rows, summary, ok, gr.update(interactive=on, value=MAKE_BTN_ON if on else MAKE_BTN_OFF)

def completion_box(scenes):
    """제작 완료 박스(초록=전부성공 / 빨강=실패있음)."""
    total = len(scenes)
    mp3_ok   = [seq for (seq, *_) in scenes if os.path.exists(os.path.join(engine.OUT_AUDIO, f"{seq}.mp3"))]
    match_ok = [seq for (seq, *_) in scenes if os.path.exists(os.path.join(engine.OUT_MATCH, f"{seq}_matched.mp4"))]
    failed   = [seq for (seq, *_) in scenes if seq not in match_ok]
    srt = os.path.join(engine.OUT_SRT, "전체.srt")
    final_ts = "-"
    if os.path.exists(srt):
        times = re.findall(r'-->\s*(\d{2}:\d{2}:\d{2},\d{3})', open(srt, encoding="utf-8").read())
        if times:
            final_ts = times[-1]
    if not failed:
        bg, fg, title, fail_line = "#e7f7ee", "#12a150", "✅ 제작 완료!", "실패 없음"
    else:
        bg, fg, title, fail_line = "#fdecec", "#e5484d", f"⚠️ {len(failed)}개 실패", f"번호: {', '.join(failed[:60])}"
    return (
        f"<div style='padding:16px;border-radius:12px;background:{bg}'>"
        f"<div style='font-size:24px;font-weight:800;color:{fg}'>{title}</div>"
        f"<div style='margin-top:10px;color:#333;font-size:15px;line-height:1.9'>"
        f"• 생성 mp3: <b>{len(mp3_ok)}/{total}</b> 장면<br>"
        f"• matched.mp4: <b>{len(match_ok)}/{total}</b> 장면<br>"
        f"• 전체 재생시간(자막 끝): <b>{final_ts}</b><br>"
        f"• 실패 장면: <b style='color:{fg}'>{fail_line}</b></div>"
        f"<div style='margin-top:8px;color:#888;font-size:12px'>산출물: 오디오/ · 매칭/ · 자막/</div></div>"
    )

def lock_buttons():
    """② 실행 중 ①② 잠금(중복 실행 방지)."""
    return gr.update(interactive=False), gr.update(interactive=False)

def restore_buttons(scenes, files, dryrun_done, cast_confirmed):
    """완료 후 버튼 상태 복원."""
    _, _, ok = match_report(scenes, files)
    on = bool(ok and dryrun_done and cast_confirmed)
    return gr.update(interactive=ok), gr.update(interactive=on, value=MAKE_BTN_ON if on else MAKE_BTN_OFF)

def _busy_box(title, sub=""):
    """생성 중 큰 상태 박스(주황 배경)."""
    subhtml = f"<div style='margin-top:6px;color:#8a5a1a;font-size:15px'>{sub}</div>" if sub else ""
    return (f"<div style='padding:18px 20px;border-radius:12px;background:#fff4e5;border:2px solid #f5a524'>"
            f"<div style='font-size:23px;font-weight:800;color:#c4620a'>⏳ {title}</div>{subhtml}</div>")

def do_make(script_text, scenes, files, dryrun_done, cast_confirmed, dlg_engine=None,
            progress=gr.Progress(track_tqdm=False)):
    # ★ 제너레이터: 각 단계마다 yield로 상태박스를 즉시 갱신(실시간)
    if dlg_engine:
        log(f"[엔진] 대사={apply_engine(dlg_engine)}")
    if not cast_confirmed:
        yield "⚠️ **💾 매핑 확정**을 먼저 완료하세요."; return
    if not dryrun_done:
        yield "⚠️ 먼저 **① 미리보기(Dry-run)** 를 통과하세요."; return
    _, _, ok = match_report(scenes, files)
    if not ok:
        yield "🚫 매칭이 완벽하지 않습니다. 매칭 검증을 먼저 통과하세요."; return
    total = len(scenes)
    log("[실제 제작 시작] (크레딧 소모: run_tts → run_match)")
    progress(0.02, desc=f"준비 중… ({total}장면)")
    yield _busy_box("준비 중…", f"총 {total}장면")
    progress(0.05, desc=f"영상 배치 중… (0/{total})")
    yield _busy_box("영상 배치 중…", f"0/{total}")
    n = place_videos(scenes, files)
    log(f"[영상 배치] 영상클립/에 {n}개 복사")
    progress(0.15, desc="TTS 준비")
    script_text, _ = normalize_input(script_text)
    with open(WORK_SCRIPT, "w", encoding="utf-8") as f:
        f.write(script_text or "")
    plan = engine.tts_plan(WORK_SCRIPT)   # 건너뛸/생성할 개수 미리 계산 → 직관적 표시
    if plan["resume"] and plan["existing"]:
        head = f"TTS 이어서 제작 중… (전체 {total}장면)"
        sub = (f"✅ 이미 만든 <b>{plan['existing']}개</b>는 건너뜀 · "
               f"🆕 <b>{plan['to_generate']}개</b>만 새로 생성 (크레딧은 이 {plan['to_generate']}장면분만) · "
               f"Supertone/ElevenLabs · 시간이 걸립니다")
    else:
        head = f"TTS 생성 중… (전체 {total}장면)"
        sub = (f"🆕 처음부터 <b>{plan['to_generate']}개</b> 전부 생성 · "
               f"Supertone(대사) + ElevenLabs(나레이션) · 시간이 걸립니다")
    progress(0.20, desc=head)
    yield _busy_box(head, sub)
    try:
        engine.run_tts(WORK_SCRIPT)        # ← 실제 API
    except Exception as ex:                # ★ 에러 메시지를 화면에 그대로 보여줌(원인 파악용)
        log(f"[TTS 실패] {type(ex).__name__}: {ex}")
        done = engine.tts_plan(WORK_SCRIPT)
        yield (f"<div style='padding:16px;border-radius:12px;background:#fdecec'>"
               f"<div style='font-size:21px;font-weight:800;color:#e5484d'>❌ TTS 생성 중단</div>"
               f"<div style='margin-top:10px;color:#333;font-size:15px;line-height:1.8'>"
               f"✅ 이미 만든 <b>{done['existing']}개</b>는 그대로 있습니다 · "
               f"남은 <b>{done['to_generate']}개</b>부터 이어서 생성됩니다.<br>"
               f"② 버튼을 다시 누르면 <b>만든 것은 건너뛰고</b> 이어서 진행합니다.<br><br>"
               f"<b>원인:</b> {type(ex).__name__}: {str(ex)[:400]}</div></div>")
        return
    progress(0.60, desc=f"영상 매칭 중… ({total}장면)")
    yield _busy_box(f"영상 속도조절·매칭 중… (전체 {total}장면)", "ffmpeg 처리 중")
    try:
        engine.run_match(WORK_SCRIPT)      # ← ffmpeg 매칭 + 자막
    except Exception as ex:
        log(f"[매칭 실패] {type(ex).__name__}: {ex}")
        yield (f"<div style='padding:16px;border-radius:12px;background:#fdecec'>"
               f"<div style='font-size:21px;font-weight:800;color:#e5484d'>❌ 영상 매칭 중단</div>"
               f"<div style='margin-top:10px;color:#333;font-size:15px'>TTS는 완료됐습니다. "
               f"②를 다시 누르면 TTS는 건너뛰고 매칭부터 이어서 합니다.<br><br>"
               f"<b>원인:</b> {type(ex).__name__}: {str(ex)[:400]}</div></div>")
        return
    progress(0.90, desc="자막 생성·정리 중…")
    yield _busy_box("자막 생성·정리 중…", "")
    progress(1.00, desc="완료")
    log("[실제 제작 완료]")
    yield completion_box(scenes)       # ✅ 제작 완료! (완료 박스로 전환)

# ── [C] 최종 병합 / 내보내기 ─────────────────────────
def _vdur(path):
    """영상 스트림의 실제 길이(초). 컨테이너 duration이 아니라 video 스트림 기준."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip())
    except ValueError:
        return engine.dur(path)

def _concat(list_items, out, copy_args, reencode_args):
    """concat 리스트 파일 만들어 ffmpeg concat. -c copy 실패 시 재인코딩 폴백. → mode."""
    lst = out + ".txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in list_items:
            f.write("file '%s'\n" % os.path.abspath(p).replace("'", r"'\''"))
    r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst] + copy_args + [out],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    mode = "copy"
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        r2 = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst] + reencode_args + [out],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        mode = "reencode"
        if r2.returncode != 0:
            os.remove(lst)
            raise RuntimeError(f"concat 실패: {r2.stderr[-400:]}")
    os.remove(lst)
    return mode

def _unique_dir(base, name):
    """base/name 이 있으면 name_2, name_3 … 으로 충돌 회피(기존 폴더 보호)."""
    target = os.path.join(base, name)
    if not os.path.exists(target):
        return target
    i = 2
    while os.path.exists(f"{target}_{i}"):
        i += 1
    return f"{target}_{i}"

def build_merged(scenes, progress=None):
    """조각 병합 → (paths|None, ok, info_md)."""
    def _p(fr, d):
        if progress:
            progress(fr, desc=d)
    if not scenes:
        return None, False, "⚠️ 먼저 대본 분석 + ② 실제 제작을 완료하세요."
    seqs = sorted(seq for (seq, *_) in scenes)
    total = len(seqs)
    missing = [seq for seq in seqs
               if not os.path.exists(os.path.join(engine.OUT_MATCH, f"{seq}_matched.mp4"))]
    if missing:
        return None, False, (f"❌ **{', '.join(missing[:20])}번 조각 없음 — 병합 불가**  \n"
                             f"(`매칭/{missing[0]}_matched.mp4` 등이 없습니다. ② 실제 제작을 먼저 완료하세요.)")
    os.makedirs(OUT_BUILD, exist_ok=True)
    vout = os.path.join(OUT_BUILD, "전체영상.mp4")
    aout = os.path.join(OUT_BUILD, "전체오디오.mp3")
    sout = os.path.join(OUT_BUILD, "전체자막.srt")
    _p(0.1, f"영상 {total}조각 병합 중… (-c copy)")
    vmode = _concat([os.path.join(engine.OUT_MATCH, f"{s}_matched.mp4") for s in seqs],
                    vout, ["-c", "copy"], ["-c:v", "libx264", "-c:a", "aac"])
    # 오디오는 영상과 같은 소스(매칭 클립)에서 뽑되, 압축본을 잇지 않는다.
    #  · 원본 mp3 병합 → 클립별 프레임 패딩이 빠지고 mp3 인코더 패딩이 쌓여 밀림
    #  · 병합 영상에서 추출 → 조각마다 박힌 AAC 프라이밍이 디코딩 시 무음으로 살아나 밀림
    # 클립별로 PCM 디코딩(조각 단위로 프라이밍 정상 제거) → wav 무손실 연결 → mp3 1회 인코딩.
    _p(0.6, "오디오 추출 중… (영상과 동일 소스)")
    tmpd = tempfile.mkdtemp(prefix="merge_audio_")
    try:
        wavs = []
        for i, s in enumerate(seqs):
            clip = os.path.join(engine.OUT_MATCH, f"{s}_matched.mp4")
            w = os.path.join(tmpd, f"{s}.wav")
            # 각 wav를 그 클립의 '영상 길이'에 정확히 고정: 모자라면 apad, 넘치면 -t 로 자름.
            # (AAC 디코딩은 마지막 프레임 경계로 올림되므로 그냥 뽑으면 클립마다 몇 ms씩 남는다)
            vd = _vdur(clip)
            r = subprocess.run(["ffmpeg", "-y", "-i", clip, "-vn", "-af", "apad",
                                "-t", f"{vd:.6f}", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", w],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or not os.path.exists(w):
                raise RuntimeError(f"{s} 오디오 디코딩 실패: {r.stderr[-300:]}")
            wavs.append(w)
            _p(0.6 + 0.2 * (i + 1) / total, f"오디오 추출 중… ({i+1}/{total})")
        big = os.path.join(tmpd, "_all.wav")
        _concat(wavs, big, ["-c", "copy"], ["-c:a", "pcm_s16le"])
        r = subprocess.run(["ffmpeg", "-y", "-i", big, "-c:a", "libmp3lame", "-q:a", "2", aout],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.exists(aout) or os.path.getsize(aout) == 0:
            raise RuntimeError(f"오디오 인코딩 실패: {r.stderr[-300:]}")
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    _p(0.85, "자막 복사 중…")
    src_srt = os.path.join(engine.OUT_SRT, "전체.srt")
    srt_ok = os.path.exists(src_srt)
    if srt_ok:
        shutil.copy(src_srt, sout)
    _p(0.95, "길이 검증 중…")
    try:
        vdur = engine.dur(vout)          # 병합 영상
        adur = engine.dur(aout)          # 실제 내보낸 오디오 파일
        diff = abs(vdur - adur)
        dur_line = (f"길이 검증: 영상 **{vdur:.2f}s** vs 오디오 **{adur:.2f}s** "
                    f"(차 **{diff*1000:.0f}ms**) → {'✅ 싱크 일치' if diff < 0.1 else '⚠️ 오차 큼'}")
    except Exception as ex:
        dur_line = f"길이 검증 실패: {ex}"
    _p(1.0, "완료")
    info = (f"- 병합 조각: **{total}개** (concat `-c {vmode}`)  \n"
            f"- {dur_line}  \n"
            f"- 자막: {'포함' if srt_ok else '⚠️ 자막/전체.srt 없음'}")
    paths = {"video": os.path.abspath(vout), "audio": os.path.abspath(aout),
             "srt": os.path.abspath(sout) if srt_ok else None}
    return paths, True, info

def do_download(scenes, progress=gr.Progress(track_tqdm=False)):
    paths, ok, info = build_merged(scenes, progress)
    if not ok:
        return info, None
    files = [paths["video"], paths["audio"]] + ([paths["srt"]] if paths["srt"] else [])
    return f"✅ **3종 병합 완료** — 아래에서 다운로드하세요.  \n{info}", files

BRAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "필수")
DISCLAIMER_TEXT = "본영상은 AI를 활용한 창작 컨텐츠입니다."

def _load_brand(script_text, seqs):
    """필수/ 브랜드 소재가 다 있으면 brand dict 반환. 없으면 (None, 없는파일들).
       시작·CTA·마지막 영상 + 로고 + 창작고지 자동 삽입. CTA 위치는 대본의 [CTA] 마커."""
    tpl   = os.path.join(BRAND_DIR, "brand_template.json")
    intro = os.path.join(BRAND_DIR, "시작(무협스튜디오).mp4")
    cta   = os.path.join(BRAND_DIR, "CTA(무협스튜디오).mp4")
    outro = os.path.join(BRAND_DIR, "마지막(무협스튜디오).mp4")
    logo  = os.path.join(BRAND_DIR, "무협스튜디오 로고.png")
    need = {"brand_template.json": tpl, "시작(무협스튜디오).mp4": intro,
            "CTA(무협스튜디오).mp4": cta, "마지막(무협스튜디오).mp4": outro, "무협스튜디오 로고.png": logo}
    missing = [k for k, p in need.items() if not os.path.exists(p)]
    if missing:
        return None, missing
    proto = json.load(open(tpl, encoding="utf-8"))
    cseq = cta_after_seq(script_text)
    cta_after = seqs.index(cseq) if (cseq and cseq in seqs) else None
    brand = {"proto": proto, "cta_after": cta_after,
             "logo": {"path": logo}, "disclaimer": {"text": DISCLAIMER_TEXT},
             "_srcs": {"intro": intro, "cta": cta, "outro": outro}, "_cta_seq": cseq}
    # ── 배경음악(선택): 필수/필수BGM_후킹·본편1·본편2·반전.wav 가 있으면 자동 삽입(-25dB) ──
    def _bgm(name):
        p = os.path.join(BRAND_DIR, name)
        return p if os.path.exists(p) else None
    hook_bgm = _bgm("필수BGM_후킹.wav")
    body_bgm = [p for p in (_bgm("필수BGM_본편1.wav"), _bgm("필수BGM_본편2.wav")) if p]
    rev_bgm  = _bgm("필수BGM_반전.wav")
    if hook_bgm or body_bgm:
        durs = {p: int(round(engine.dur(p) * 1e6))
                for p in ([hook_bgm] + body_bgm + [rev_bgm]) if p}
        brand["bgm"] = {"후킹": hook_bgm, "본편": body_bgm or [hook_bgm], "반전": rev_bgm, "durs": durs}
        rseq = reversal_after_seq(script_text)
        brand["반전_after"] = seqs.index(rseq) if (rseq and rseq in seqs) else None
        brand["_bgm_on"] = True
    return brand, []

def do_capcut_export(script_text, scenes, title, progress=gr.Progress(track_tqdm=False)):
    """★ 진짜 CapCut 프로젝트 생성 — 장면별 영상/오디오/자막이 트랙에 배치된 초안(draft)을 만든다.
       전환(비네트) 자동 + 애니메이션 70%(줌1 80 : 기울기 20) 자동.
       필수/ 브랜드 소재가 있으면 시작·CTA·마지막 영상 + 로고·창작고지 오버레이도 자동 삽입."""
    if not os.path.isdir(CAPCUT_DIR):
        yield f"⚠️ **CapCut 폴더를 찾을 수 없습니다. 경로 확인 필요:**  \n`{CAPCUT_DIR}`"; return
    if not scenes:
        yield "⚠️ 먼저 대본을 분석하세요."; return
    seqs = [s[0] for s in scenes]
    missing = [q for q in seqs
               if not os.path.exists(os.path.join(engine.OUT_MATCH, f"{q}_matched.mp4"))]
    if missing:
        yield (f"❌ **{', '.join(missing[:20])}번 조각 없음 — 내보내기 불가**  \n"
               f"② 실제 제작을 먼저 완료하세요."); return

    total = len(seqs)
    progress(0.05, desc=f"소재 추출 중… ({total}장면)")
    yield _busy_box(f"CapCut 소재 추출 중… ({total}장면 × 2)", "무음 영상 + wav 오디오")
    name = (title or "").strip() or "무협_" + datetime.now().strftime("%Y%m%d")
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "무협_" + datetime.now().strftime("%Y%m%d")
    dest = capcut.__dict__["_unique_dir"](CAPCUT_DIR, name)
    try:
        made = capcut.prepare_assets(os.path.join(dest, "assets"), seqs, engine.OUT_MATCH)
    except Exception as ex:
        yield f"❌ 소재 추출 실패: {type(ex).__name__}: {ex}"; return

    progress(0.6, desc="자막 정렬 + 타임라인 구성 중…")
    yield _busy_box("타임라인 구성 중…", "자막을 실제 발화에 정렬")
    text_by_seq = {s[0]: s[5] for s in scenes}          # scenes: (seq,kind,speaker,emo,engine,text)
    built = []
    for (q, dv, da) in made:
        d = engine.dur(dv)
        built.append({"video": dv, "audio": da, "dur_us": int(round(d * 1e6)),
                      "cues": engine.make_cues(text_by_seq.get(q, ""), 0.0, d,
                                               audio=os.path.join(engine.OUT_AUDIO, f"{q}.mp3")),
                      # 이미지 장면은 ffmpeg으로 카메라 모션을 이미 구웠음 → CapCut 프리셋 애니 제외(이중 방지)
                      "no_anim": engine.is_image_scene(q)})
    brand, miss_brand = _load_brand(script_text, seqs)
    if brand:
        try:
            brand.update(capcut.prepare_brand(os.path.join(dest, "assets"), brand.pop("_srcs")))
        except Exception as ex:
            yield f"❌ 브랜드 소재 추출 실패: {type(ex).__name__}: {ex}"; return
        cs = brand.get("_cta_seq")
        bmsg = (f"브랜드 삽입: 시작·마지막 영상 + 로고·창작고지"
                + (f" + CTA(장면 {cs} 뒤)" if brand.get("cta_after") is not None
                   else " · ⚠️CTA 마커 없음(CTA 미삽입)")
                + (" + BGM(-25dB)" if brand.get("_bgm_on") else ""))
    else:
        bmsg = f"브랜드 소재 없음({', '.join(miss_brand)}) — 본편만 생성"
    progress(0.85, desc="CapCut 초안 생성 중…")
    yield _busy_box("CapCut 프로젝트 생성 중…", bmsg)
    try:
        draft = capcut.CapCutDraft()
        info = draft.build(dest, os.path.basename(dest), built, fps=24.0, brand=brand)
        capcut.make_cover(dest, built[0]["video"])
    except Exception as ex:
        yield f"❌ 프로젝트 생성 실패: {type(ex).__name__}: {ex}"; return
    progress(1.0, desc="완료")
    log(f"[CapCut 내보내기] {dest} · {info} · {bmsg}")
    yield (
        f"<div style='padding:16px;border-radius:12px;background:#e7f7ee'>"
        f"<div style='font-size:23px;font-weight:800;color:#12a150'>✅ CapCut 프로젝트 생성 완료!</div>"
        f"<div style='margin-top:10px;color:#333;font-size:15px;line-height:1.9'>"
        f"• 프로젝트: <b>{os.path.basename(dest)}</b><br>"
        f"• 장면: <b>{info['scenes']}개</b> (영상 {info['video_segs']} / 오디오 {info['audio_segs']} / 자막 {info['text_segs']})<br>"
        f"• 총 길이: <b>{info['duration_us']/6e7:.2f}분</b><br>"
        f"• 전환(비네트) 자동 + 애니메이션 70%(줌1 80:기울기 20) 자동</div>"
        f"<div style='margin-top:10px;color:#555;font-size:13px'>"
        f"CapCut을 실행하면 <b>프로젝트 목록</b>에 바로 뜹니다. 기존 프로젝트는 건드리지 않았습니다.</div>"
        f"<div style='margin-top:6px;color:#888;font-size:12px'>{dest}</div></div>"
    )

# ══════════ 야담 & 민담 파이프라인 (이미지 기반 · Vrew TTS) ══════════
#  무협 파서(analyze_script_text)를 그대로 재사용. 영상 대신 '이미지'를 장면에 매칭.
#  TTS는 우리가 안 함(Vrew가 함) → 화자/역할/제작 단계 없음.
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
VID_EXTS = (".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v")
MEDIA_EXTS = IMG_EXTS + VID_EXTS                          # 후킹=영상, 나머지=이미지 → 둘 다 허용
YADAM_HEADERS = ["No.", "종류", "대본(Vrew TTS 텍스트)", "소재(이미지/영상)"]

def _media_kind(base):
    b = base.lower()
    if b.endswith(VID_EXTS): return "🎬 영상"
    if b.endswith(IMG_EXTS): return "🖼️ 이미지"
    return None

def yadam_match(scenes, files):
    """장면 ↔ 소재(이미지 또는 영상) 매칭(파일명 앞 숫자). → (rows, md, ok).
       후킹은 영상, 나머지는 이미지 — 장면마다 둘 중 아무거나 붙어도 매칭."""
    if not scenes:
        return [], "⬆️ 대본을 분석하면 매칭이 표시됩니다.", False
    seqset = {s[0] for s in scenes}
    seq2files, surplus = {}, []
    for p in (files or []):
        base = os.path.basename(p)
        if not _media_kind(base):
            surplus.append(f"{base} (이미지·영상 아님)"); continue
        k = _num_key(p)
        if k and k in seqset:
            seq2files.setdefault(k, []).append(base)
        else:
            surplus.append(f"{base} ({('번호 '+k+' → 장면없음') if k else '앞 숫자 없음'})")
    rows, dup, missing = [], [], []
    for (seq, kind, sp, emo, eng, text) in scenes:
        fl = seq2files.get(seq, [])
        if not fl:
            cell = "❌ 소재 없음"; missing.append(seq)
        elif len(fl) == 1:
            cell = f"✅ {_media_kind(fl[0])} ({fl[0]})"
        else:
            cell = f"⚠️ 번호 중복 ({', '.join(fl)})"; dup.append(seq)
        rows.append([seq, kind, text, cell])
    matched = len(seqset & set(seq2files))
    ok = bool(scenes) and not missing and not surplus and not dup
    if ok:
        n_v = sum(1 for fl in seq2files.values() if _media_kind(fl[0]) == "🎬 영상")
        md = (f"<div style='padding:8px 12px;border-radius:8px;background:#e7f7ee;color:#12a150;"
              f"font-weight:700;font-size:15px'>✅ {matched}/{len(scenes)} 완벽 매칭 "
              f"(영상 {n_v} · 이미지 {matched - n_v})</div>")
    else:
        lines = [f"**매칭: {matched}/{len(scenes)} 장면**"]
        if missing: lines.append(f"❌ **소재 누락**: {_cut(missing)}")
        if surplus: lines.append(f"⚠️ **잉여/번호 불일치**: {_cut(surplus)}")
        if dup:     lines.append(f"⚠️ **번호 중복(한 장면에 2개+)**: {_cut(dup)}")
        head = ("<div style='padding:8px 12px;border-radius:8px;background:#fdecec;color:#e5484d;"
                "font-weight:700;font-size:15px'>🚫 매칭 미완료</div>")
        md = head + "\n\n" + "  \n".join(lines)
    return rows, md, ok

def on_yadam_script(text):
    text2, npipe = normalize_input(text)
    try:
        scenes, diags = analyze_script_text(text2)
    except Exception as ex:
        log(f"[야담 분석오류] {type(ex).__name__}: {ex}")
        return [], [["—", "분석 실패", f"{type(ex).__name__}: {ex}", "-"]], f"❌ **분석 오류:** {ex}", "", "—"
    if npipe:
        diags.insert(0, f"특수문자 {npipe}개 자동 교정.")
    if not scenes:
        return [], [["—", "분석 실패", "장면 0개 — 진단 확인", "-"]], "❌ **장면을 못 읽었습니다.**", _fmt_diags(diags), "—"
    rows, mmd, ok = yadam_match(scenes, [])
    n = len(scenes)
    log(f"[야담 분석완료] 장면 {n}개")
    return list(scenes), rows, f"✅ **총 {n}개 장면** (이미지 {n}개 필요)", _fmt_diags(diags), mmd

def on_yadam_images(files, scenes):
    if not scenes:
        return files, [], "⚠️ 먼저 대본을 분석하세요."
    rows, mmd, ok = yadam_match(scenes, files)
    return files, rows, mmd

YADAM_TTS_EDGE = "① 무료 edge-tts (자동 생성 · 비용 0)"
YADAM_TTS_VREW = "② Vrew TTS 오디오 첨부 (내가 만든 오디오 업로드)"

def yadam_toggle_tts(choice):
    """TTS 방식 선택 → edge 음성 드롭다운 / Vrew 오디오 업로드 표시 전환."""
    edge = (choice == YADAM_TTS_EDGE)
    return gr.update(visible=edge), gr.update(visible=not edge)

def do_yadam_produce(scenes, media_files, tts_source, edge_voice, vrew_audio, title,
                     progress=gr.Progress(track_tqdm=False)):
    """야담 제작(제너레이터): 오디오(edge-tts 또는 Vrew첨부) → 이미지/영상 클립화 → CapCut."""
    if not scenes:
        yield "⚠️ 먼저 대본을 분석하세요."; return
    rows, mmd, ok = yadam_match(scenes, media_files)
    if not ok:
        yield "🚫 이미지/영상 매칭이 완벽하지 않습니다. 매칭 검증을 먼저 통과하세요."; return
    if not os.path.isdir(CAPCUT_DIR):
        yield f"⚠️ CapCut 폴더 없음: `{CAPCUT_DIR}`"; return
    # scenes: (seq,kind,speaker,emotion,engine,text)
    edge = (tts_source == YADAM_TTS_EDGE)
    try:
        if edge:
            voice = yadam.EDGE_VOICES.get(edge_voice, "ko-KR-SunHiNeural")
            plan = yadam.audio_plan(scenes, "edge")
            yield _busy_box(f"무료 TTS 생성 중… (edge-tts · {edge_voice})",
                            f"이미 만든 {plan['existing']}개 건너뜀 · {plan['to_generate']}개 생성 (비용 0)")
            n_gen, n_skip = yadam.run_edge_tts(scenes, voice, progress=progress)
            audio_note = f"edge-tts {n_gen}개 생성 · {n_skip}개 건너뜀"
        else:
            yield _busy_box("Vrew 오디오 배치 중…", "업로드한 오디오를 장면에 매칭")
            done, miss = yadam.ingest_audio_files(scenes, vrew_audio or [])
            if miss:
                yield (f"❌ Vrew 오디오 누락: **{', '.join(miss[:20])}**  \n"
                       f"파일명 앞 숫자로 장면 매칭됩니다 (예: `001_.mp3`). 누락 장면 오디오를 올리세요."); return
            audio_note = f"Vrew 오디오 {len(done)}개 배치"
    except Exception as ex:
        yield f"❌ 오디오 준비 실패: {type(ex).__name__}: {ex}"; return

    yield _busy_box("소재 준비 중…", "이미지→영상 변환 + 자막 정렬")
    try:
        built, missing = yadam.prepare_scenes(scenes, media_files, progress=progress)
    except Exception as ex:
        yield f"❌ 소재 준비 실패: {type(ex).__name__}: {ex}"; return
    if missing:
        yield f"❌ 소재/오디오 없는 장면: {', '.join(missing[:20])}"; return

    name = (title or "").strip() or "야담_" + datetime.now().strftime("%Y%m%d")
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "야담_" + datetime.now().strftime("%Y%m%d")
    dest = capcut.__dict__["_unique_dir"](CAPCUT_DIR, name)
    yield _busy_box("CapCut 프로젝트 생성 중…", os.path.basename(dest))
    try:
        info = yadam.build_capcut(dest, os.path.basename(dest), built, fps=24.0)
    except Exception as ex:
        yield f"❌ 프로젝트 생성 실패: {type(ex).__name__}: {ex}"; return
    log(f"[야담 제작] {dest} · {info} · {audio_note}")
    yield (
        f"<div style='padding:16px;border-radius:12px;background:#e7f7ee'>"
        f"<div style='font-size:23px;font-weight:800;color:#12a150'>✅ 야담 CapCut 프로젝트 완성!</div>"
        f"<div style='margin-top:10px;color:#333;font-size:15px;line-height:1.9'>"
        f"• 프로젝트: <b>{os.path.basename(dest)}</b><br>"
        f"• 장면: <b>{info['scenes']}개</b> · 총 <b>{info['duration_us']/6e7:.2f}분</b><br>"
        f"• 오디오: {audio_note}<br>"
        f"• 이미지 줌 + 자막 + 전환 자동</div>"
        f"<div style='margin-top:8px;color:#888;font-size:12px'>{dest}</div></div>")

# ══════════ 일본 경제학 핸들러 (야담과 동일 패턴 · 일본어 TTS + 경제학 브랜드) ══════════
def on_keizai_script(text):
    """경제학은 후킹영상+이미지, [CTA] 마커로 후킹 끝 표시(무협과 동일)."""
    return on_yadam_script(text)          # 파싱·매칭 UI 동일(대본 형식 같음)

def on_keizai_media(files, scenes):
    return on_yadam_images(files, scenes)

def do_keizai_produce(scenes, media_files, tts_source, edge_voice, script_text, title,
                      progress=gr.Progress(track_tqdm=False)):
    """경제학 제작: 일본어 나레이션(ElevenLabs/edge-tts) → 이미지/영상 → CapCut(CTA·로고·음악·100%애니)."""
    if not scenes:
        yield "⚠️ 먼저 대본을 분석하세요."; return
    rows, mmd, ok = yadam_match(scenes, media_files)
    if not ok:
        yield "🚫 이미지/영상 매칭이 완벽하지 않습니다. 먼저 매칭을 통과하세요."; return
    if not os.path.isdir(CAPCUT_DIR):
        yield f"⚠️ CapCut 폴더 없음: `{CAPCUT_DIR}`"; return
    seqs = [s[0] for s in scenes]
    cseq = cta_after_seq(script_text)
    cta_after = seqs.index(cseq) if (cseq and cseq in seqs) else 0   # [CTA]없으면 첫 장면 뒤
    brand, miss_b = keizai.load_brand(cta_after)
    if not brand:
        yield f"❌ 경제학 필수 소재 없음: {', '.join(miss_b)}"; return
    try:
        plan = keizai.audio_plan(scenes, tts_source, edge_voice)
        yield _busy_box(f"일본어 나레이션 생성 중… ({tts_source})",
                        f"이미 만든 {plan['existing']}개 건너뜀 · {plan['to_generate']}개 생성")
        n_gen, n_skip = keizai.run_narration(scenes, tts_source, edge_voice, progress=progress)
    except Exception as ex:
        yield f"❌ 나레이션 실패: {type(ex).__name__}: {ex}"; return
    yield _busy_box("소재 준비 중…", "이미지→영상(줌) + 후킹영상 정렬 + 자막")
    try:
        built, missing = keizai.prepare_scenes(scenes, media_files, progress=progress)
    except Exception as ex:
        yield f"❌ 소재 준비 실패: {type(ex).__name__}: {ex}"; return
    if missing:
        yield f"❌ 소재/오디오 없는 장면: {', '.join(missing[:20])}"; return
    name = (title or "").strip() or "경제학_" + datetime.now().strftime("%Y%m%d")
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "경제학_" + datetime.now().strftime("%Y%m%d")
    dest = capcut.__dict__["_unique_dir"](CAPCUT_DIR, name)
    yield _busy_box("CapCut 프로젝트 생성 중…", os.path.basename(dest))
    try:
        info = keizai.build_capcut(dest, os.path.basename(dest), built, brand, fps=24.0)
    except Exception as ex:
        yield f"❌ 프로젝트 생성 실패: {type(ex).__name__}: {ex}"; return
    log(f"[경제학 제작] {dest} · {info}")
    yield (
        f"<div style='padding:16px;border-radius:12px;background:#e7f7ee'>"
        f"<div style='font-size:23px;font-weight:800;color:#12a150'>✅ 경제학 CapCut 프로젝트 완성!</div>"
        f"<div style='margin-top:10px;color:#333;font-size:15px;line-height:1.9'>"
        f"• 프로젝트: <b>{os.path.basename(dest)}</b> · 장면 <b>{info['scenes']}개</b> · {info['duration_us']/6e7:.2f}분<br>"
        f"• 나레이션: {'ElevenLabs 일본어' if tts_source==keizai.TTS_ELEVEN else 'edge-tts 일본어 ('+edge_voice+')'}<br>"
        f"• CTA(후킹 뒤) + 경제학로고(우상) + 음악(-25dB) + 이미지 100% 애니(80줌1/20기울기)</div>"
        f"<div style='margin-top:8px;color:#888;font-size:12px'>{dest}</div></div>")

# ── Google Flow 자동생성 브리지 핸들러 (전부 로컬 · 외부호출 0) ──
FLOW_OUT = os.path.join(PROJECT_DIR, "Flow_내보내기")
FLOW_CAT_DIR = {"① 무협": "무협", "② 야담 & 민담": "야담", "③ 일본 경제학": "경제학"}

def flow_do_export(script_text, category):
    """① 이미지 프롬프트(04) 내보내기 — 대본의 [영어 이미지 프롬프트]만 빈 줄 구분."""
    scenes = flow.extract_prompts(script_text)
    if not scenes:
        return "⚠️ 장면을 못 읽었습니다 — [장면 N] / [영어 이미지 프롬프트] 형식을 확인하세요.", None
    d = os.path.join(FLOW_OUT, FLOW_CAT_DIR.get(category, "기타"))
    ip, io = flow.export_prompt_files(scenes, d, "image")
    return (f"✅ 이미지 프롬프트 **{len(io)}개** 내보냄 → `{d}`  \n"
            f"확장(VEO Automation) *텍스트→이미지* 에 업로드 → 생성 → 001,002… 다운로드.", ip)

def flow_do_video_auto(script_text, category):
    """② 영상 프롬프트(05) 자동 생성 — 이미지 프롬프트에서 파생(지침 규칙·무료·0토큰)."""
    scenes = flow.extract_prompts(script_text)
    if not scenes:
        return "⚠️ [영어 이미지 프롬프트]가 있어야 영상 프롬프트를 자동 생성합니다.", None
    flow.fill_video_auto(scenes)
    d = os.path.join(FLOW_OUT, FLOW_CAT_DIR.get(category, "기타"))
    vp, vo = flow.export_prompt_files(scenes, d, "video")
    return (f"✅ 영상 프롬프트 **{len(vo)}개 자동 생성** → `{d}`  \n"
            f"이미지 프롬프트에서 파생(카메라·움직임·색톤·no외형, 지침 규칙). "
            f"확장 *프레임→비디오* 에 이 txt + 생성 이미지로 생성.", vp)

def flow_do_video_manual(script_text, video_text, category):
    """영상 프롬프트 직접 입력(05 붙여넣기, 빈 줄로 장면 구분) → 저장."""
    scenes = flow.extract_prompts(script_text)
    if not scenes:
        return "⚠️ 먼저 위 대본을 넣어 장면을 인식시키세요.", None
    if not (video_text or "").strip():
        return "⚠️ 영상 프롬프트를 붙여넣으세요 (장면 사이 빈 줄 1개로 구분).", None
    _, n = flow.assign_video_manual(scenes, video_text)
    d = os.path.join(FLOW_OUT, FLOW_CAT_DIR.get(category, "기타"))
    vp, vo = flow.export_prompt_files(scenes, d, "video")
    n_img = len([s for s in scenes if s.get("image")])
    warn = "" if n == n_img else f"  \n⚠️ 붙여넣은 블록 **{n}개** ≠ 장면 **{n_img}개** — 순서·개수 확인."
    return (f"✅ 직접 입력한 영상 프롬프트 **{len(vo)}개** 저장 → `{d}`{warn}", vp)

def flow_do_import(dl_dir, category, kind):
    if not dl_dir or not os.path.isdir(dl_dir):
        return "⚠️ 다운로드 폴더 경로가 올바르지 않습니다.", None
    sub = FLOW_CAT_DIR.get(category, "기타")
    k = "image" if kind == "이미지" else "video"
    order_path = os.path.join(FLOW_OUT, sub, f".flow_order_{k}.json")
    if not os.path.exists(order_path):
        return f"⚠️ 먼저 ①에서 **{category}** 내보내기를 하세요(장면 순서 기록이 없습니다).", None
    order = json.load(open(order_path, encoding="utf-8"))
    dest = os.path.join(FLOW_OUT, sub, "결과")
    placed, rep = flow.import_downloads(dl_dir, dest, order, k)
    files = [os.path.join(dest, p[1]) for p in placed]
    msg = (f"✅ **{len(placed)}개**를 장면번호로 정리 → `{dest}`  \n"
           + ("  \n".join("⚠️ " + r for r in rep) if rep else "누락/잉여 없음 — 완벽 매핑"))
    return msg, files

# ── UI ───────────────────────────────────────────────
STUDIO_CSS = """
.gradio-container{max-width:1180px !important;margin:0 auto !important;
  font-family:'Pretendard','Segoe UI',system-ui,-apple-system,sans-serif;}
/* 상단 워크플로우 스텝바 */
.wf{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 18px;}
.wf .st{flex:1;min-width:130px;display:flex;align-items:center;gap:9px;padding:12px 14px;
  border-radius:14px;background:#fff;border:1px solid #eee;box-shadow:0 1px 4px rgba(0,0,0,.05);
  font-size:13.5px;font-weight:700;color:#334155;}
.wf .st .n{width:26px;height:26px;flex:0 0 auto;border-radius:50%;
  background:linear-gradient(135deg,#f97316,#fb923c);color:#fff;font-weight:800;font-size:13px;
  display:flex;align-items:center;justify-content:center;box-shadow:0 2px 6px rgba(249,115,22,.4);}
.wf .arrow{align-self:center;color:#f9a35c;font-weight:900;font-size:16px;}
/* 섹션 = 번호 카드 헤더 */
.step-h{margin:22px 0 2px !important;}
.step-h h1,.step-h h2,.step-h h3{display:flex !important;align-items:center;gap:11px;
  font-size:19px !important;font-weight:800 !important;color:#1e293b !important;
  padding:13px 18px !important;margin:0 !important;border-radius:13px;
  background:linear-gradient(90deg,#fff7ed 0%,#ffffff 60%);border:1px solid #ffe4c9;
  border-left:5px solid #f97316 !important;box-shadow:0 1px 3px rgba(0,0,0,.05);}
/* 주요(주황) 버튼 */
button.primary,.primary button,button.lg.primary{
  background:linear-gradient(90deg,#f97316,#fb923c) !important;border:none !important;
  color:#fff !important;font-weight:800 !important;box-shadow:0 3px 10px rgba(249,115,22,.35) !important;}
button.primary:hover{filter:brightness(1.05);}
/* ② 실제 제작 = stop(빨강). 활성/비활성이 한눈에 구분되어야 함 */
button.stop,.stop button{
  background:linear-gradient(90deg,#e5484d,#f0666b) !important;border:none !important;
  color:#fff !important;font-weight:800 !important;
  box-shadow:0 3px 12px rgba(229,72,77,.45) !important;}
button.stop:hover{filter:brightness(1.06);}
/* 비활성 버튼 = 확실히 회색·흐림 (활성과 착각 방지) */
button:disabled,button[disabled],.gr-button[disabled]{
  background:#e2e8f0 !important;background-image:none !important;color:#94a3b8 !important;
  box-shadow:none !important;opacity:.7 !important;cursor:not-allowed !important;}
@media (prefers-color-scheme:dark){
  button:disabled,button[disabled]{background:#334155 !important;color:#64748b !important;}
}
/* 입력창 카드감 */
textarea,.gr-box,.block{border-radius:12px !important;}
/* 업로드 파일 목록: 창 크기 고정 + 안에서 스크롤 (500개 올려도 페이지가 안 늘어남) */
.filebox{max-height:340px !important;overflow-y:auto !important;overflow-x:hidden !important;}
.filebox::-webkit-scrollbar{width:10px;}
.filebox::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:6px;}
.filebox::-webkit-scrollbar-thumb:hover{background:#94a3b8;}
.filebox table{font-size:13px;}
/* 파일 개수 배지처럼 보이게 상단 여백 확보 */
.filebox .block{max-height:none !important;}
/* 다크모드 대비 */
@media (prefers-color-scheme:dark){
  .wf .st{background:#1e293b;border-color:#334155;color:#e2e8f0;}
  .step-h h1,.step-h h2,.step-h h3{background:linear-gradient(90deg,#2a2118,#1e293b);
    border-color:#3f2d1a;color:#f8fafc !important;}
}
"""

def wf_bar(steps, active=0):
    """상단 워크플로우 스텝바 HTML. steps=[라벨...], active=강조할 인덱스(0-base)."""
    cells = []
    for i, t in enumerate(steps):
        cells.append(f"<div class='st'><span class='n'>{i+1}</span><span>{t}</span></div>")
    return "<div class='wf'>" + "<span class='arrow'>›</span>".join(cells) + "</div>"

STUDIO_THEME = gr.themes.Soft(primary_hue="orange", secondary_hue="orange",
                              neutral_hue="slate", radius_size="lg")

with gr.Blocks(title="올인원 영상 스튜디오") as demo:
    # ── 좌측 카테고리 사이드바 ──
    with gr.Sidebar(open=True):
        gr.Markdown("## 🎬 올인원 스튜디오")
        gr.Markdown("**카테고리**")
        cat_wuxia_btn = gr.Button("① 무협", variant="primary")
        cat_yadam_btn = gr.Button("② 야담 & 민담", variant="secondary")
        cat_keizai_btn = gr.Button("③ 일본 경제학", variant="secondary")
        gr.Markdown("<span style='color:gray;font-size:12px'>카테고리는 계속 추가됩니다.</span>")

    # 공유 상태(무협 파이프라인)
    scenes_state   = gr.State([])
    files_state    = gr.State([])
    dryrun_done    = gr.State(False)
    speakers_state = gr.State([])
    override_state = gr.State({})
    cast_confirmed = gr.State(False)

    # ══════════ [카테고리 1] 무협 (단계별 위저드 — 한 단계씩만 표시) ══════════
    with gr.Column(visible=True) as wuxia_view:
        _banner = banner_html()
        if _banner:
            gr.HTML(_banner)                                  # assets/banner.png 배너
        else:
            gr.Markdown("# 무협 영상+TTS+자막 변환 스튜디오")   # 배너 없으면 텍스트 제목 폴백

        # 🎬 Flow 프롬프트 생성 — 단계 아님, 필요할 때만 펼치는 선택 유틸(기본 접힘)
        with gr.Accordion("🎬 Google Flow 프롬프트 생성 — 이미지·영상 (선택 · 확장 연동)", open=False):
            gr.Markdown("대본 → 장면별 **이미지/영상 프롬프트**를 확장이 먹는 `.txt`로 내보내고, "
                        "생성·다운로드한 **001,002,003…** 을 장면번호로 자동 정리. 🔒 로컬 · 추가비용 0.")
            flow_category = gr.Radio(["① 무협", "② 야담 & 민담", "③ 일본 경제학"], value="① 무협", label="카테고리")
            flow_script = gr.Textbox(label="대본 붙여넣기 ([장면 N] / [영어 이미지 프롬프트] / [영상 프롬프트])", lines=6,
                                     placeholder="[장면 1]\n[영어 이미지 프롬프트] A wuxia manhwa illustration... No text.\n[영상 프롬프트] Slow cinematic push-in, subtle motion.")
            gr.Markdown("**① 이미지 프롬프트 (04)** — 대본의 영어 이미지 프롬프트만 뽑아 확장용 .txt로")
            flow_export_btn = gr.Button("① 이미지 프롬프트 내보내기 (.txt)", variant="primary")
            flow_export_msg = gr.Markdown()
            flow_img_txt = gr.File(label="이미지프롬프트.txt")
            gr.Markdown("**② 영상 프롬프트 (05)** — 자동 생성(무료) 또는 직접 붙여넣기, 둘 중 아무거나")
            flow_video_auto_btn = gr.Button("② 영상 프롬프트 자동 생성 (이미지에서 · 무료 · 0토큰)", variant="primary")
            flow_video_manual = gr.Textbox(
                label="또는 직접 붙여넣기 (내가 만든 05 영상프롬프트 · 장면 사이 빈 줄 1개)", lines=4,
                placeholder="Slow push-in toward the figure...\n\nSlow pan right over the hall...")
            flow_video_manual_btn = gr.Button("직접 입력한 영상 프롬프트 저장")
            flow_video_msg = gr.Markdown()
            flow_vid_txt = gr.File(label="영상프롬프트.txt")
            gr.Markdown("— 확장에서 생성·다운로드가 끝나면 ↓ —")
            flow_dl_dir = gr.Textbox(label="다운로드 폴더 경로 (확장이 001,002…를 저장한 폴더)",
                                     placeholder=r"C:\Users\7500F\Downloads\천야검가 1화")
            flow_kind = gr.Radio(["이미지", "영상"], value="이미지", label="가져올 종류")
            flow_import_btn = gr.Button("② Flow 결과 가져오기 (장면번호로 자동 정리)", variant="primary")
            flow_import_msg = gr.Markdown()
            flow_result_files = gr.File(label="정리된 결과 (001.png / 001.mp4 …)", file_count="multiple", elem_classes="filebox")

        wuxia_step = gr.State(0)
        _WSTEPS = ["1 · 📝 대본 입력", "2 · 🎞️ 영상 매칭", "3 · 🎙️ TTS 제작", "4 · 🎬 영상 제작"]
        with gr.Row(elem_classes="wizbar"):
            w_stepbtns = [gr.Button(t, variant=("primary" if i == 0 else "secondary"), size="sm")
                          for i, t in enumerate(_WSTEPS)]

        # ───── 1단계 · 대본 입력 & 분석 ─────
        with gr.Column(visible=True) as w_s2:
            gr.Markdown("### 📝 1단계 · 대본 입력 & 분석  <span style='font-weight:400;font-size:14px;color:#64748b'>— ChatGPT/구글독스 시각화 대본 붙여넣기</span>", elem_classes="step-h")
            script_in = gr.Textbox(label="시각화 프롬프트 대본 붙여넣기 (장면 200개+ / 초장문 OK)", lines=12,
                                   placeholder="[장면 1]\n[한국어 번역] [독수마군|분노]\"...\"\n[영어 이미지 프롬프트] Korean dark-fantasy...")
            analyze_btn = gr.Button("🔍 대본 분석", variant="primary")
            gr.Markdown("#### 📋 장면 목록")
            table = gr.Dataframe(headers=HEADERS, label="장면표", wrap=True, interactive=False, max_height=420,
                                 column_widths=["6%", "8%", "10%", "8%", "10%", "33%", "25%"])
            summary_md = gr.Markdown()
            diag_md = gr.Markdown()

        # ───── 2단계 · 영상 매칭 ─────
        with gr.Column(visible=False) as w_s3:
            gr.Markdown("### 🎞️ 2단계 · 영상·이미지 업로드 & 매칭 검증  <span style='font-weight:400;font-size:14px;color:#64748b'>— 파일명 앞 숫자로 자동 매칭 (001_인물.mp4 / 001.png → 장면 001)</span>", elem_classes="step-h")
            video_files = gr.File(file_count="multiple", type="filepath", elem_classes="filebox",
                                  label="영상(mp4·mov…) + 이미지(png·jpg…) 섞어서 한 번에 올리세요 — 이미지는 오디오 길이에 맞춰 자동 클립화")
            gr.Markdown("#### 📊 매칭 검증")
            match_md = gr.Markdown("⬆️ 대본을 분석하면 매칭 검증이 표시됩니다.")

        # ───── 3단계 · TTS 제작 ─────
        with gr.Column(visible=False) as w_s4:
            gr.Markdown("### 🎙️ 3단계 · TTS 제작 (① 미리보기 → ② 실제 제작)", elem_classes="step-h")
            dialogue_engine = gr.Radio(
                [DLG_ENGINE_TC, DLG_ENGINE_EL, DLG_ENGINE_GM, DLG_ENGINE_ST], value=DLG_ENGINE_GM,
                label="대사 성우 엔진  (나레이션은 ElevenLabs 고정)",
                info="기본 = 타입캐스트(감정은 앞뒤 장면 문맥으로 자동). Supertone은 2026-08-31 종료되어 사용 불가. "
                     "엔진을 바꾸면 대사 장면만 새로 생성됩니다(나레이션은 그대로).")
            with gr.Row():
                dryrun_btn = gr.Button("① 미리보기 (Dry-run · 크레딧 소모 없음)", interactive=False)
                make_btn   = gr.Button(MAKE_BTN_OFF, variant="stop", interactive=False)
            gr.Markdown("*<span style='color:gray'>① 은 매칭 통과 시 활성 · ② 는 매칭 통과 + 매핑 확정 + Dry-run 완료 시에만 활성.</span>*")
            make_status = gr.Markdown()
            gr.Markdown("#### 🎭 대사 화자 ↔ 역할 배정 (대사 많은 순 = 주인공이 맨 위)")
            speaker_table = gr.Dataframe(headers=SPK_HEADERS, label="화자 목록", interactive=False, max_height=280,
                                         column_widths=["28%", "14%", "24%", "34%"])
            confirm_md = gr.Markdown()

            @gr.render(inputs=[speakers_state])
            def render_overrides(speakers):
                if not speakers:
                    gr.Markdown("*(대사 화자가 없습니다. 대본을 분석하세요.)*")
                    return
                gr.Markdown("**역할 변경 (드롭다운):**")
                for sp, cnt, auto in speakers:
                    with gr.Row():
                        gr.Markdown(f"**{sp}** · 대사 {cnt}회 · 현재배정 `{auto}`")
                        dd = gr.Dropdown(ROLE_CHOICES, value=auto, label="역할 지정", interactive=True, min_width=170)
                    def _change(new_role, ov, _sp=sp):
                        ov = dict(ov or {}); ov[_sp] = new_role
                        log(f"[역할변경] {_sp} → {new_role}")
                        return (ov, False, False, gr.update(interactive=False, value=MAKE_BTN_OFF),
                                f"🔧 **{_sp} → {new_role}** 변경됨 — 💾 **매핑 확정** 후 ① Dry-run을 다시 실행하세요.")
                    dd.change(_change, inputs=[dd, override_state],
                              outputs=[override_state, cast_confirmed, dryrun_done, make_btn, confirm_md])

            confirm_btn = gr.Button("💾 매핑 확정 (character_cast.json 저장)", variant="primary")
            gr.Markdown("#### ① Dry-run 결과 (역할·감정 style 매핑, API 없음)")
            dryrun_table = gr.Dataframe(headers=DRY_HEADERS, label="dry-run", wrap=True, interactive=False, max_height=360)
            dryrun_summary = gr.Markdown()

        # ───── 4단계 · 영상 제작 (CapCut) ─────
        with gr.Column(visible=False) as w_s5:
            gr.Markdown("### 🎬 4단계 · 영상 제작 — CapCut 내보내기 (TTS 제작 완료 후)", elem_classes="step-h")
            title_in = gr.Textbox(label="화 제목 (CapCut 프로젝트명)", placeholder="예: 만독지존 5화  ·  비우면 무협_오늘날짜")
            with gr.Row():
                capcut_btn   = gr.Button("🎬 CapCut 프로젝트 만들기", variant="primary")
                download_btn = gr.Button("📦 3종 따로 다운로드 (영상/오디오/자막)")
            gr.Markdown("*<span style='color:gray'>🎬 = 장면별 영상·오디오·자막이 트랙에 배치된 **CapCut 프로젝트**를 생성합니다 "
                        "(전환 자동 + 애니메이션 70% 자동). CapCut 실행하면 프로젝트 목록에 바로 뜹니다. &nbsp;·&nbsp; "
                        "📦 = 통합본 3개 파일만 받고 싶을 때.</span>*")
            merge_status = gr.Markdown()
            download_files = gr.File(label="다운로드 (전체영상.mp4 / 전체오디오.mp3 / 전체자막.srt)",
                                    file_count="multiple", interactive=False)

        # ───── 단계 이동 (이전/다음) + 배선 ─────
        with gr.Row():
            w_prev = gr.Button("← 이전 단계")
            w_next = gr.Button("다음 단계 →", variant="primary")
        _w_cols = [w_s2, w_s3, w_s4, w_s5]
        _NW = len(_w_cols)
        def _wuxia_show(i):
            i = max(0, min(_NW - 1, int(i)))
            return ([i] + [gr.update(visible=(k == i)) for k in range(_NW)]
                    + [gr.update(variant=("primary" if k == i else "secondary")) for k in range(_NW)])
        _w_out = [wuxia_step] + _w_cols + w_stepbtns
        for _k, _b in enumerate(w_stepbtns):
            _b.click(lambda _k=_k: _wuxia_show(_k), outputs=_w_out)
        w_prev.click(lambda c: _wuxia_show(c - 1), inputs=[wuxia_step], outputs=_w_out)
        w_next.click(lambda c: _wuxia_show(c + 1), inputs=[wuxia_step], outputs=_w_out)

    # ══════════ [카테고리 2] 야담 & 민담 (이미지 기반) ══════════
    with gr.Column(visible=False) as yadam_view:
        gr.Markdown("# 📜 야담 & 민담 스튜디오")
        gr.Markdown("*시각화 대본 인식 → 장면별 이미지 매칭. "
                    "TTS는 **Vrew**에서 (비용 절감), 후킹만 영상 예정. 내보내기는 다음 단계.*")
        yadam_scenes_state = gr.State([])
        yadam_files_state  = gr.State([])

        gr.HTML(wf_bar(["대본 입력", "장면·매칭", "이미지·영상", "TTS 선택", "제작·CapCut"]))

        gr.Markdown("### 1️⃣ 대본 입력", elem_classes="step-h")
        yadam_script = gr.Textbox(label="시각화 프롬프트 대본 붙여넣기 (무협과 같은 형식)", lines=12,
                                  placeholder="[장면 1]\n[한국어 번역] 옛날 옛적에 한 선비가 살았다.\n[영어 이미지 프롬프트] A Korean folk-tale illustration...")
        yadam_analyze = gr.Button("🔍 대본 분석", variant="primary")

        gr.Markdown("### 2️⃣ 장면 목록", elem_classes="step-h")
        yadam_table = gr.Dataframe(headers=YADAM_HEADERS, label="장면표", wrap=True, interactive=False,
                                   max_height=420, column_widths=["8%", "10%", "60%", "22%"])
        yadam_summary = gr.Markdown()
        yadam_diag = gr.Markdown()

        gr.Markdown("### 3️⃣ 이미지·영상 일괄 업로드  <span style='font-weight:400;font-size:14px;color:#64748b'>— 후킹=영상, 나머지=이미지 (파일명 앞 숫자로 매칭)</span>", elem_classes="step-h")
        yadam_images = gr.File(file_count="multiple", type="filepath", elem_classes="filebox",
                               label="이미지(jpg/png) 또는 영상(mp4) 여러 개 — 한 장면에 아무거나 매칭됩니다")

        gr.Markdown("### 4️⃣ 소재 매칭 검증", elem_classes="step-h")
        yadam_match_md = gr.Markdown("⬆️ 대본을 분석하면 매칭이 표시됩니다.")

        # ── 🔊 TTS 방식 선택 ──
        gr.Markdown("### 5️⃣ 목소리(TTS) 방식 선택", elem_classes="step-h")
        yadam_tts_source = gr.Radio([YADAM_TTS_EDGE, YADAM_TTS_VREW], value=YADAM_TTS_EDGE,
                                    label="어떤 목소리로 만들까요?")
        yadam_edge_voice = gr.Dropdown(list(yadam.EDGE_VOICES.keys()),
                                       value=list(yadam.EDGE_VOICES.keys())[0],
                                       label="edge-tts 음성 (무료)", visible=True)
        yadam_vrew_audio = gr.File(file_count="multiple", type="filepath", visible=False, elem_classes="filebox",
                                   label="Vrew에서 만든 오디오 업로드 (파일명 앞 숫자로 장면 매칭 · 예: 001_.mp3)")

        gr.Markdown("### 6️⃣ 야담 제작 → CapCut", elem_classes="step-h")
        yadam_title = gr.Textbox(label="제목 (CapCut 프로젝트명)", placeholder="예: 자비왕후  ·  비우면 야담_오늘날짜")
        yadam_make_btn = gr.Button("🎬 제작 (오디오 생성 + 이미지 배치 + CapCut)", variant="primary")
        yadam_status = gr.Markdown()
        gr.Markdown("*<span style='color:gray'>edge-tts=완전 무료 자동 · Vrew 첨부=주인님이 Vrew에서 만든 오디오 사용. "
                    "이미지는 자동으로 줌 효과 + 자막이 들어갑니다.</span>*")

        # 야담 이벤트 배선
        yadam_analyze.click(on_yadam_script, inputs=[yadam_script],
                            outputs=[yadam_scenes_state, yadam_table, yadam_summary, yadam_diag, yadam_match_md])
        yadam_script.change(on_yadam_script, inputs=[yadam_script],
                            outputs=[yadam_scenes_state, yadam_table, yadam_summary, yadam_diag, yadam_match_md])
        yadam_images.change(on_yadam_images, inputs=[yadam_images, yadam_scenes_state],
                            outputs=[yadam_files_state, yadam_table, yadam_match_md])
        yadam_tts_source.change(yadam_toggle_tts, inputs=[yadam_tts_source],
                                outputs=[yadam_edge_voice, yadam_vrew_audio])
        yadam_make_btn.click(do_yadam_produce,
                             inputs=[yadam_scenes_state, yadam_files_state, yadam_tts_source,
                                     yadam_edge_voice, yadam_vrew_audio, yadam_title],
                             outputs=[yadam_status])

    # ══════════ [카테고리 3] 일본 경제학 ══════════
    with gr.Column(visible=False) as keizai_view:
        gr.Markdown("# 📈 일본 경제학 스튜디오")
        gr.Markdown("*시각화 대본 → 후킹=영상, 나머지=이미지 → 일본어 나레이션 → CapCut "
                    "(CTA·경제학로고·음악 -25dB·이미지 100% 줌 자동). 후킹 끝에 `[CTA]` 마커.*")
        keizai_scenes_state = gr.State([])
        keizai_files_state  = gr.State([])

        gr.HTML(wf_bar(["대본 입력", "장면·매칭", "이미지·영상", "나레이션", "제작·CapCut"]))

        gr.Markdown("### 1️⃣ 대본 입력", elem_classes="step-h")
        keizai_script = gr.Textbox(label="시각화 프롬프트 대본 붙여넣기 (무협과 같은 형식 · 나레이션은 일본어)", lines=12,
                                   placeholder="[장면 1]\n[한국어 번역] 日本経済の...\n[영어 이미지 프롬프트] Japanese economy infographic...")
        keizai_analyze = gr.Button("🔍 대본 분석", variant="primary")
        gr.Markdown("### 2️⃣ 장면 목록", elem_classes="step-h")
        keizai_table = gr.Dataframe(headers=YADAM_HEADERS, label="장면표", wrap=True, interactive=False,
                                    max_height=420, column_widths=["8%", "10%", "60%", "22%"])
        keizai_summary = gr.Markdown(); keizai_diag = gr.Markdown()
        gr.Markdown("### 3️⃣ 이미지·영상 업로드  <span style='font-weight:400;font-size:14px;color:#64748b'>— 후킹=영상, 나머지=이미지 (파일명 앞 숫자로 매칭)</span>", elem_classes="step-h")
        keizai_media = gr.File(file_count="multiple", type="filepath", elem_classes="filebox",
                               label="이미지(jpg/png) + 후킹 영상(mp4) 한꺼번에")
        gr.Markdown("### 4️⃣ 소재 매칭 검증", elem_classes="step-h")
        keizai_match_md = gr.Markdown("⬆️ 대본을 분석하면 매칭이 표시됩니다.")
        gr.Markdown("### 5️⃣ 일본어 나레이션 방식", elem_classes="step-h")
        keizai_tts_source = gr.Radio([keizai.TTS_ELEVEN, keizai.TTS_EDGE], value=keizai.TTS_EDGE,
                                     label="어떤 목소리로?")
        keizai_edge_voice = gr.Dropdown(list(keizai.EDGE_VOICES_JA.keys()),
                                        value=list(keizai.EDGE_VOICES_JA.keys())[0],
                                        label="edge-tts 일본어 음성 (무료)")
        gr.Markdown("### 6️⃣ 경제학 제작 → CapCut", elem_classes="step-h")
        keizai_title = gr.Textbox(label="제목 (CapCut 프로젝트명)", placeholder="예: 일본경제_금리인상  ·  비우면 경제학_날짜")
        keizai_make_btn = gr.Button("🎬 제작 (나레이션 + 이미지 배치 + CapCut)", variant="primary")
        keizai_status = gr.Markdown()

        keizai_analyze.click(on_keizai_script, inputs=[keizai_script],
                             outputs=[keizai_scenes_state, keizai_table, keizai_summary, keizai_diag, keizai_match_md])
        keizai_script.change(on_keizai_script, inputs=[keizai_script],
                             outputs=[keizai_scenes_state, keizai_table, keizai_summary, keizai_diag, keizai_match_md])
        keizai_media.change(on_keizai_media, inputs=[keizai_media, keizai_scenes_state],
                            outputs=[keizai_files_state, keizai_table, keizai_match_md])
        keizai_make_btn.click(do_keizai_produce,
                              inputs=[keizai_scenes_state, keizai_files_state, keizai_tts_source,
                                      keizai_edge_voice, keizai_script, keizai_title],
                              outputs=[keizai_status])

    # ── 카테고리 전환 (3개) ──
    _CAT_VIEWS = [wuxia_view, yadam_view, keizai_view]
    _CAT_BTNS  = [cat_wuxia_btn, cat_yadam_btn, cat_keizai_btn]
    def _switch(active):
        return ([gr.update(visible=(i == active)) for i in range(3)]
                + [gr.update(variant="primary" if i == active else "secondary") for i in range(3)])
    _cat_out = _CAT_VIEWS + _CAT_BTNS
    cat_wuxia_btn.click(lambda: _switch(0), outputs=_cat_out)
    cat_yadam_btn.click(lambda: _switch(1), outputs=_cat_out)
    cat_keizai_btn.click(lambda: _switch(2), outputs=_cat_out)

    # 🎬 Flow 자동생성 배선 (전부 로컬)
    flow_export_btn.click(flow_do_export, inputs=[flow_script, flow_category],
                          outputs=[flow_export_msg, flow_img_txt])
    flow_video_auto_btn.click(flow_do_video_auto, inputs=[flow_script, flow_category],
                              outputs=[flow_video_msg, flow_vid_txt])
    flow_video_manual_btn.click(flow_do_video_manual, inputs=[flow_script, flow_video_manual, flow_category],
                                outputs=[flow_video_msg, flow_vid_txt])
    flow_import_btn.click(flow_do_import, inputs=[flow_dl_dir, flow_category, flow_kind],
                          outputs=[flow_import_msg, flow_result_files])

    # ── 이벤트 배선 (무협) ──
    _out_script = [scenes_state, table, summary_md, diag_md, match_md,
                   dryrun_btn, make_btn, dryrun_done, dryrun_table, dryrun_summary,
                   speakers_state, speaker_table, override_state, cast_confirmed, confirm_md]
    script_in.change(on_script, inputs=[script_in, files_state], outputs=_out_script)
    analyze_btn.click(on_script, inputs=[script_in, files_state], outputs=_out_script)

    _out_files = [files_state, table, match_md, dryrun_btn, make_btn, dryrun_done, dryrun_table, dryrun_summary]
    video_files.change(on_files, inputs=[video_files, scenes_state], outputs=_out_files)

    confirm_btn.click(confirm_mapping, inputs=[speakers_state, override_state],
                      outputs=[cast_confirmed, dryrun_done, confirm_md, make_btn,
                               speakers_state, speaker_table])
    # 엔진 선택 → 즉시 반영(미리보기·제작 모두 이 값 사용). 바꾸면 Dry-run 다시 필요.
    dialogue_engine.change(
        lambda c: (apply_engine(c), False, gr.update(interactive=False, value=MAKE_BTN_OFF),
                   f"🔧 대사 엔진 **{c}** 선택됨 — **① 미리보기**를 다시 실행하세요.")[1:],
        inputs=[dialogue_engine], outputs=[dryrun_done, make_btn, dryrun_summary])
    dryrun_btn.click(do_dryrun, inputs=[script_in, cast_confirmed, dialogue_engine],
                     outputs=[dryrun_table, dryrun_summary, dryrun_done, make_btn])
    make_btn.click(lock_buttons, outputs=[dryrun_btn, make_btn]).then(
        do_make, inputs=[script_in, scenes_state, files_state, dryrun_done, cast_confirmed,
                         dialogue_engine],
        outputs=[make_status]).then(
        restore_buttons, inputs=[scenes_state, files_state, dryrun_done, cast_confirmed],
        outputs=[dryrun_btn, make_btn])

    capcut_btn.click(do_capcut_export, inputs=[script_in, scenes_state, title_in],
                     outputs=[merge_status])
    download_btn.click(do_download, inputs=[scenes_state], outputs=[merge_status, download_files])

if __name__ == "__main__":
    # 브라우저 자동 오픈은 .bat 한 곳에서만(탭 1개). 여기선 열지 않음.
    demo.launch(server_name="127.0.0.1", server_port=7860, inbrowser=False,
                theme=STUDIO_THEME, css=STUDIO_CSS)
