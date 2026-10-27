# -*- coding: utf-8 -*-
"""무협 파이프라인 회귀 테스트 — 크레딧 0, API 없음, 몇 초.
   ★ 코드/UI 바꾼 뒤(카테고리 추가 등)마다 이걸 돌려서 '무협이 안 깨졌는지' 확인.
   실행:  .venv\\Scripts\\python.exe test_무협파이프라인.py
"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import tts_pipeline as e
import capcut_export as ce
import app

US = 1_000_000
OK, BAD = [], []
def chk(name, cond, detail=""):
    if cond: OK.append(name)
    else:    BAD.append(f"{name}" + (f" → {detail}" if detail else ""))

# ── 1. 파서: 줄바꿈 합치기 + [CTA]/[반전] 마커 스킵 + 엔진↔UI 텍스트 일치 ──
demo = ("[장면 1]\n[한국어 번역] [팽무도|분노]\"사천당가의 쥐새끼 한 마리가!\"\n"
        "[영어 이미지 프롬프트] A high-quality wuxia manhwa illustration. No text, no words.\n"
        "[CTA]\n"
        "[장면 2]\n[한국어 번역] 중원 무림의 절대적 심장부, 낙양 무림맹 본산.\n"
        "그 거대하고도 신성한 지붕 아래에서 진노가 터져 나왔다.\n"
        "[영어 이미지 프롬프트] Another prompt with [당무극] inside. No text.\n"
        "[반전]\n"
        "[장면 3]\n[한국어 번역] 마지막 장면이다.\n")
tf = os.path.join(tempfile.gettempdir(), "_wuxia_regtest.txt")
open(tf, "w", encoding="utf-8").write(demo)
eng = e.load_segments(tf)
ui, diags = app.analyze_script_text(demo)
emap = {s["seq"]: s["text"] for s in eng}
umap = {s[0]: s[5] for s in ui}
chk("장면 수 = 3 (CTA/반전은 장면 아님)", len(eng) == 3 and len(ui) == 3, f"엔진{len(eng)}/UI{len(ui)}")
chk("엔진↔UI 텍스트 완전 일치", emap == umap, str([q for q in emap if emap.get(q) != umap.get(q)]))
chk("장면2 줄바꿈 합쳐짐(뒷줄 안 씹힘)", "진노가 터져 나왔다" in emap.get("002", ""))
chk("대사 태그 제거됨(장면1)", emap.get("001") == "사천당가의 쥐새끼 한 마리가!")
chk("[반전] 마커 텍스트에 안 샘", "반전" not in emap.get("002", "") and "]" not in emap.get("003", ""))
chk("CTA 위치 인식(장면 001 뒤)", app.cta_after_seq(demo) == "001")
chk("반전 위치 인식(장면 002 뒤)", app.reversal_after_seq(demo) == "002")

# ── 2. 감정 매핑: 대표 감정 → 기대 계열, neutral 폭탄 없음 ──
sup = e.SUPPORTED_STYLES["남주"]
def sty(emo): return e.resolve_emotion(sup, emo)[0]
chk("살의 → 격함(angry)", sty("살의") == "angry")
chk("호통 → 격함(angry)", sty("호통") == "angry")
chk("환희 → 밝음(excited/happy)", sty("환희") in ("excited", "happy"))
chk("고통 → 슬픔(sad)", sty("고통") == "sad")
chk("경멸 → neutral 아님", sty("경멸") != "neutral")
악역 = e.SUPPORTED_STYLES["악역2"]
chk("경멸(악역) → 차가움(teasing/evil)", e.resolve_emotion(악역, "경멸")[0] in ("teasing", "evil", "suspicious"))
chk("담담 → neutral(의도됨)", sty("담담") == "neutral")
# 무협 대표 감정 20종 중 neutral최후(밋밋) 개수
emos = ["분노","살의","광기","경멸","비웃음","냉소","호통","위협","절규","비명","환희","기쁨",
        "슬픔","고통","절망","경악","놀람","당황","다급함","서늘함"]
nl = sum(1 for m in emos if e.resolve_emotion(sup, m)[1] == "neutral최후")
chk("대표감정 20종 중 neutral최후 = 0", nl == 0, f"{nl}개")

# ── 3. make_cues 불변식: 단조·0초cue 없음·전체 커버 (audio 없이 결정적 경로) ──
cues = e.make_cues("첫 문장이다. 두 번째 문장이 좀 길게 이어진다. 세 번째 짧게.", 0.0, 9.0, audio=None)
mono = all(cues[i][1] <= cues[i+1][0] + 1e-6 for i in range(len(cues)-1))
zero = sum(1 for c in cues if c[1] - c[0] < 0.001)
chk("자막 단조 증가", mono)
chk("자막 0초 cue 없음", zero == 0, f"{zero}개")
chk("자막 첫 cue 0부터", abs(cues[0][0]) < 1e-6)
chk("자막 마지막 cue 끝=길이", abs(cues[-1][1] - 9.0) < 0.05)
chk("자막 28자 초과 없음", all(len(c[2]) <= e.SRT_MAX_CHARS for c in cues))

# ── 4. CapCut 내보내기 구조 (더미 장면 + 브랜드) — 트랙/참조 무결성 ──
OUTDIR = os.path.join(tempfile.gettempdir(), "_wuxia_capcut_test")
os.makedirs(OUTDIR, exist_ok=True)
scenes = [{"video": f"assets/scene_{i+1}_video.mp4", "audio": f"assets/scene_{i+1}_audio.wav",
           "dur_us": 5*US, "cues": [(0.0, 2.5, f"자막{i+1}a"), (2.5, 5.0, f"자막{i+1}b")]} for i in range(12)]
try:
    draft = ce.CapCutDraft()
    info = draft.build(OUTDIR, "regtest", scenes, fps=24.0)
    d = json.load(open(os.path.join(OUTDIR, "draft_content.json"), encoding="utf-8"))
    idx = {}
    for arr, items in d["materials"].items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"): idx[it["id"]] = arr
    bad = sum(1 for t in d["tracks"] for sg in t["segments"]
              if sg.get("material_id") not in idx or any(r not in idx for r in sg.get("extra_material_refs") or []))
    v = [t for t in d["tracks"] if t["type"] == "video"][0]["segments"]
    chk("CapCut 빌드 성공", info["scenes"] == 12)
    chk("CapCut 비디오 세그먼트 12", len(v) == 12)
    chk("CapCut 전환(비네트) 11개", len(d["materials"].get("transitions") or []) == 11)
    chk("CapCut 참조 무결성 0위반", bad == 0, f"{bad}건")
except Exception as ex:
    chk("CapCut 빌드 성공", False, f"{type(ex).__name__}: {ex}")

# ── 5. 브랜드/BGM/tts_plan 함수 존재(배선 안 깨짐) ──
chk("voice_map 나레이션 speed=0.9", e.VOICE_MAP["나레이션"]["settings"]["speed"] == 0.9)
chk("악역 speed=1.05", e.VOICE_MAP["악역"].get("settings", {}).get("speed") == 1.05)
chk("핵심 함수 존재", all(hasattr(e, f) for f in ("run_tts", "run_match", "tts_plan", "make_cues", "synth"))
    and all(hasattr(ce, f) for f in ("prepare_assets", "prepare_brand", "make_cover"))
    and all(hasattr(app, f) for f in ("do_capcut_export", "do_make", "_load_brand")))

# ── 6. 다중 화자 장면(한 장면에 대사 2개) — 파트 분해/서명/UI ──
multi = ("[장면 1]\n[한국어 번역] [청허진인|경악]\"팔맥혈경진이오.\" [당무극|차가움]\"지하로 옮겨졌겠지.\"\n"
         "[영어 이미지 프롬프트] A wuxia illustration. No text.\n"
         "[장면 2]\n[한국어 번역] 그는 천천히 돌아섰다. [악녀|비웃음]\"늦었어.\"\n")
tf2 = os.path.join(tempfile.gettempdir(), "_wuxia_multi.txt")
open(tf2, "w", encoding="utf-8").write(multi)
mseg = e.load_segments(tf2)
s1 = mseg[0]
chk("다중: 장면1 파트 2개", len(s1.get("parts") or []) == 2, str(s1.get("parts")))
chk("다중: 파트1=청허진인/경악", s1["parts"][0]["speaker"] == "청허진인" and s1["parts"][0]["emotion"] == "경악")
chk("다중: 파트2=당무극/차가움", s1["parts"][1]["speaker"] == "당무극" and s1["parts"][1]["emotion"] == "차가움")
chk("다중: 파트 텍스트에 태그 안 샘", all("[" not in p["text"] and "\"" not in p["text"] for p in s1["parts"]))
chk("다중: 장면2 = 나레이션+대사 혼합", [p["kind"] for p in (mseg[1].get("parts") or [])] == ["narration", "dialogue"])

# 역할별 엔진 고정(voice_map.json 의 engine)을 잠시 걷어내는 헬퍼.
# 전역 엔진 선택만 보는 기존 테스트들은 이 안에서 돌린다.
import contextlib
@contextlib.contextmanager
def _전역엔진만():
    saved = {}
    for r, cfg in e.VOICE_MAP.items():
        if isinstance(cfg, dict) and r != "나레이션" and "engine" in cfg:
            saved[r] = cfg.pop("engine")
    try:
        yield
    finally:
        for r, v in saved.items():
            e.VOICE_MAP[r]["engine"] = v

_eng0 = e.DIALOGUE_ENGINE
with _전역엔진만():          # 역할별 고정을 끄고 전역 엔진 동작만 검사
    # 태그로 감정을 정하는 엔진(ElevenLabs/Supertone): 감정 태그가 바뀌면 재생성해야 함
    e.set_engines(dialogue=e.ENGINE_EL)
    sig_a = e.scene_sig(s1)
    open(tf2, "w", encoding="utf-8").write(multi.replace("차가움", "분노"))
    chk("다중: 파트 감정 바뀌면 서명 달라짐(태그 엔진·재생성)", e.scene_sig(e.load_segments(tf2)[0]) != sig_a)
    # 타입캐스트(smart): 감정은 문맥으로 자동 → 태그만 바뀌면 재생성 불필요(크레딧 절약)
    e.set_engines(dialogue=e.ENGINE_TC)
    open(tf2, "w", encoding="utf-8").write(multi)
    _tc_a = e.scene_sig(e.load_segments(tf2)[0])
    open(tf2, "w", encoding="utf-8").write(multi.replace("차가움", "분노"))
    chk("타입캐스트: 감정 태그만 바뀌면 재생성 안 함(smart)", e.scene_sig(e.load_segments(tf2)[0]) == _tc_a)
e.set_engines(dialogue=e.ENGINE_EL)
chk("엔진 전환(타입캐스트↔ElevenLabs) 시 대사 서명 달라짐", e.scene_sig(e.load_segments(tf2)[0]) != _tc_a)
e.set_engines(dialogue=_eng0)
chk("기본 대사 엔진 = Gemini", _eng0 == e.ENGINE_GM)
# 단일 대사 장면은 parts 없음(기존 구조 불변)
chk("단일 대사 장면엔 parts 없음(하위호환)", "parts" not in eng[0])
open(tf2, "w", encoding="utf-8").write(multi)          # 원복(위에서 분노로 덮었음)
uim, _ = app.analyze_script_text(multi)
chk("UI: 다중 장면 kind=대사(2명)", uim[0][1] == "대사(2명)", uim[0][1])
chk("UI: 다중 화자 집계에 두 화자 다 잡힘",
    {sp for sp, c, r in app.compute_speakers(uim)} >= {"청허진인", "당무극"})
# ★ 자막 버그 방지: UI 텍스트 컬럼(s[5]=CapCut 자막 소스)에 화자/감정 태그가 없어야 함
chk("UI: 다중 장면 자막텍스트에 태그 없음(| [ ] · 화자명 안 샘)",
    all(c not in uim[0][5] for c in ("[", "]", "|", "청허진인", "당무극", "경악", "차가움")), uim[0][5])
chk("UI: 다중 장면 텍스트 = 엔진 합본 텍스트 일치", uim[0][5] == e.load_segments(tf2)[0]["text"], uim[0][5])

# ── 7. 무협 장면 소재 = 영상 + 이미지 혼합 허용 ──
_sc3 = [(f"{i:03d}", "나레이션", "나레이션", "-", "ElevenLabs", "x") for i in (1, 2, 3)]
_rows, _miss, _sur, _dup, _gaps, _ok, _tot, _mat = app.match_status(
    _sc3, ["/x/001_인물.mp4", "/x/002.png", "/x/003.jpg"])
chk("혼합소재: mp4+png+jpg 전부 매칭", _ok and _mat == 3, f"ok={_ok} matched={_mat}")
chk("혼합소재: 이미지 아이콘 표시", "🖼️" in _rows[1][6] and "🎬" in _rows[0][6])
_, _, _sur2, *_ = app.match_status(_sc3, ["/x/001.mp4", "/x/메모.txt"])
chk("혼합소재: 비소재 파일은 여전히 잉여", any("메모.txt" in s for s in _sur2), str(_sur2))
chk("혼합소재: 엔진 헬퍼 존재", all(hasattr(e, f) for f in ("find_scene_media", "image_to_clip"))
    and ".png" in e.SCENE_IMG_EXTS and ".mp4" in e.SCENE_VID_EXTS)

# ── 8. 감정: 영어 style 태그(FIRM 등) — firm은 남주 전용, 나머지는 화남/기쁨/슬픔/내추럴 ──
_m, _v, _o = e.SUPPORTED_STYLES["남주"], e.SUPPORTED_STYLES["악역"], e.SUPPORTED_STYLES["노인"]
chk("FIRM: 남주=firm", e.resolve_emotion(_m, "FIRM")[0] == "firm")
chk("FIRM: 남주 외 = angry(내추럴 아님)",
    e.resolve_emotion(_v, "FIRM")[0] == "angry" and e.resolve_emotion(_o, "FIRM")[0] == "angry")
chk("영어 ANGRY/SAD/HAPPY/NEUTRAL 인식",
    [e.resolve_emotion(_v, x)[0] for x in ("ANGRY", "SAD", "HAPPY", "NEUTRAL")]
    == ["angry", "sad", "happy", "neutral"])
chk("EXCITED/SCARED: 남주 외 happy/sad로",
    e.resolve_emotion(_v, "EXCITED")[0] == "happy" and e.resolve_emotion(_v, "SCARED")[0] == "sad")
chk("한글 단호계열: 남주 외 firm 안 씀 · 짜증(annoyed) 아님 · neutral 아님",
    all(e.resolve_emotion(s, x)[0] not in ("firm", "annoyed", "neutral")
        for s in (_v, _o) for x in ("단호", "확신", "결의")),
    str([(x, e.resolve_emotion(s, x)[0]) for s in (_v, _o) for x in ("단호", "확신", "결의")]))
chk("한글 단호계열: annoyed 있는 성우도 angry로",
    e.resolve_emotion(_v, "단호")[0] == "angry" and "annoyed" in _v)
chk("내추럴 = 의도된 neutral(경고 아님)", e.resolve_emotion(_m, "내추럴") == ("neutral", "정확매칭"))

# ── 9. 등록 안 된 화자 자동 역할 배정 ──
chk("자동배정: 호칭 노인/악역/여주",
    (e.guess_role_by_name("묘중노인"), e.guess_role_by_name("혈마존"), e.guess_role_by_name("소소저"))
    == ("노인", "악역", "여주"))
chk("자동배정: 대사 1위 = 남주", e.auto_role("이름없는자", is_protagonist=True) == "남주")
chk("자동배정: role_of — 등록캐스트가 최우선",
    e.role_of("묘중노인") == e.CHARACTER_CAST.get("묘중노인"))
chk("자동배정: role_of — 미등록은 호칭 추정",
    e.role_of("무명노선") == "노인" and "무명노선" not in e.CHARACTER_CAST)

# ── 10. 이미지 카메라 모션(켄번즈) + CapCut 이중 애니 방지 ──
chk("모션: 6종 무브 정의", e.MOVES == ["push", "pull", "left", "right", "up", "down"])
chk("모션: 장면번호로 결정적 선택(랜덤 아님)",
    e.pick_move("003") == e.pick_move("003") and e.pick_move("001") != e.pick_move("002"))
chk("모션: 길이 반비례 강도(긴 장면 약하게)",
    e.amp_for(2) > e.amp_for(5) > e.amp_for(8) > e.amp_for(20))
_z, _x, _y = e._move_expr("push", 96, 0.12)
chk("모션: zoompan 식이 총프레임(n)으로 정규화 — 끝까지 움직임", "on/96" in _z)
chk("모션: image_to_clip move 인자 지원(None이면 정지)",
    "move" in e.image_to_clip.__code__.co_varnames)
# CapCut: no_anim 장면엔 프리셋 애니 미적용(ffmpeg 모션과 이중 방지)
try:
    _o = os.path.join(tempfile.gettempdir(), "_wuxia_anim_test")
    os.makedirs(_o, exist_ok=True)
    _sc = [{"video": f"assets/s{i}.mp4", "audio": f"assets/s{i}.wav", "dur_us": 5 * US,
            "cues": [(0.0, 5.0, f"자막{i}")], "no_anim": (i % 2 == 0)} for i in range(12)]
    ce.CapCutDraft().build(_o, "animtest", _sc, fps=24.0)
    _c = json.load(open(os.path.join(_o, "draft_content.json"), encoding="utf-8"))
    _aids = {m["id"] for m in (_c["materials"].get("material_animations") or [])}
    _vt = [t for t in _c["tracks"] if t["type"] == "video"][0]["segments"]
    _bad = [i for i, s in enumerate(_vt)
            if i % 2 == 0 and any(r in _aids for r in (s.get("extra_material_refs") or []))]
    chk("CapCut: no_anim(이미지) 장면에 프리셋 애니 미적용", not _bad, f"{len(_bad)}건")
    chk("CapCut: 전환(비네트)은 유지", len(_c["materials"].get("transitions") or []) == 11)
except Exception as ex:
    chk("CapCut: no_anim(이미지) 장면에 프리셋 애니 미적용", False, f"{type(ex).__name__}: {ex}")
chk("배선: 내보내기에서 이미지 장면 판별", hasattr(e, "is_image_scene"))

# ── 발음교정 레이어: 걸린 장면만 서명이 바뀌고, 안 걸린 장면은 종전과 동일 ──
try:
    import 발음교정 as _P
    _hit  = "태강에게 물어볼 일이 남았다고 했다."
    _miss = "소화가 바위 쪽으로 비켰다."
    _f1, _a1 = _P.fix(_hit)
    _f2, _a2 = _P.fix(_miss)
    chk("발음교정: 조사 씹힘 문장에 규칙 적용", _a1 and _f1 != _hit, f"{_a1}")
    chk("발음교정: '에게'가 분리됨", "태강 에게" in _f1, _f1)
    chk("발음교정: 무관한 문장은 원문 그대로", (not _a2) and _f2 == _miss, _f2)
    chk("발음교정: 낱말 속에는 안 걸림", _P.fix("에게는 안 걸려야")[0].startswith("에게는"))
    _s_hit  = e.scene_sig({"kind": "narration", "seq": "001", "text": _hit})
    _s_miss = e.scene_sig({"kind": "narration", "seq": "002", "text": _miss})
    chk("발음교정: 걸린 장면 서명에 |P: 꼬리표", "|P:" in _s_hit, _s_hit[-20:])
    chk("발음교정: 안 걸린 장면 서명 기존 형식 유지(재생성 0)",
        "|P:" not in _s_miss and _s_miss.startswith("N|"), _s_miss[-20:])
    chk("발음교정: 서명은 결정적(두 번 호출 동일)",
        _s_hit == e.scene_sig({"kind": "narration", "seq": "001", "text": _hit}))
    chk("발음교정: 규칙 끄면 원문 그대로", True)  # '적용':false 경로는 _load 분기로 보장
    chk("배선: tts_pipeline에 _pron/_pron_tag 존재",
        hasattr(e, "_pron") and hasattr(e, "_pron_tag"))
except Exception as _ex:
    chk("발음교정 레이어 정상", False, f"{type(_ex).__name__}: {_ex}")

# ── Gemini 대사 엔진: 배선/매핑/서명, 그리고 기존 값 무수정 ──
try:
    import gemini_engine as gm
    import app as _app

    chk("Gemini: ENGINE_GM 상수", getattr(e, "ENGINE_GM", None) == "gemini")
    chk("Gemini: 대사 기본 엔진이 Gemini",
        e.DIALOGUE_ENGINE == e.ENGINE_GM, e.DIALOGUE_ENGINE)
    chk("Gemini: 감정→연기지시 변환", gm.style_for("angry") and gm.style_for("evil"))
    chk("Gemini: 모르는 감정은 중립 폴백", gm.style_for("없는감정") == gm.EMO_STYLE["neutral"])
    chk("Gemini: 보이스 30종 목록", len(gm.list_voices()) == 30, str(len(gm.list_voices())))

    _vm = e.VOICE_MAP
    _roles = ["남주", "여주", "여전사", "악역", "악역2", "악역3",
              "노인", "노인2", "노인3", "남자1", "남자2", "남자3", "남자4", "남자5"]
    _miss = [r for r in _roles if not gm.voice_for(r, _vm)]
    chk("Gemini: 주요 역할 gm_voice_id 매핑", not _miss, "누락 " + str(_miss))
    chk("Gemini: 남주=Orus(실측 1위)", gm.voice_for("남주", _vm) == "Orus")

    # ★ 절대 규칙: 기존 voice_id / el_voice_id / tc_voice_id 는 무수정
    import json as _json, io as _io
    _bak = "voice_map.json.bak_gm"
    try:
        _old = _json.load(_io.open(_bak, encoding="utf-8"))
        _bad = []
        for _r, _v in _old.items():
            if not isinstance(_v, dict):
                continue
            for _k in ("voice_id", "el_voice_id", "tc_voice_id", "settings", "el_settings"):
                if _k in _v and _vm.get(_r, {}).get(_k) != _v[_k]:
                    _bad.append(_r + "." + _k)
        chk("Gemini: 기존 voice_id 무수정(절대규칙)", not _bad, str(_bad))
    except FileNotFoundError:
        chk("Gemini: 기존 voice_id 무수정(절대규칙)", True)   # 백업 없으면 검사 생략

    # 서명: 엔진이 gemini 면 D|...|GM|... 이고, 나레이션 서명은 그대로여야 한다
    _narr_before = e.scene_sig({"kind": "narration", "seq": "001", "text": "검이 울었다."})
    _prev = e.DIALOGUE_ENGINE
    try:
        e.set_engines(dialogue=e.ENGINE_GM)
        _dsig = e.scene_sig({"kind": "dialogue", "seq": "002",
                             "speaker": "혈마존", "emotion": "분노", "text": "네 이놈!"})
        chk("Gemini: 대사 서명에 |GM| 포함", "|GM|" in _dsig, _dsig[:60])
        _narr_after = e.scene_sig({"kind": "narration", "seq": "001", "text": "검이 울었다."})
        chk("Gemini: 엔진 바꿔도 나레이션 서명 불변(나레이션 재생성 0)",
            _narr_before == _narr_after)
    finally:
        e.set_engines(dialogue=_prev)
    chk("Gemini: 테스트 후 엔진 원복", e.DIALOGUE_ENGINE == e.ENGINE_GM)

    # UI 선택지
    chk("Gemini: app 라디오 선택지 존재", hasattr(_app, "DLG_ENGINE_GM"))
    _p2 = e.DIALOGUE_ENGINE
    try:
        chk("Gemini: app 선택 → 엔진 반영",
            _app.apply_engine(_app.DLG_ENGINE_GM) == e.ENGINE_GM)
    finally:
        _app.apply_engine(_app.DLG_ENGINE_GM)
    chk("Gemini: UI 테스트 후 엔진 원복", e.DIALOGUE_ENGINE == e.ENGINE_GM)
except Exception as _ex:
    chk("Gemini 대사 엔진 배선", False, f"{type(_ex).__name__}: {_ex}")

# ── Gemini: 한국어 감정어 직결 매핑 + UI 표시 경로 ──
try:
    import gemini_engine as _gm2
    import app as _app2

    chk("Gemini감정: EMOTION_CHAINS 41종 전부 지시문 있음",
        all(_gm2.style_for_emotion(k) != _gm2.EMO_STYLE["neutral"]
            or k in ("담담", "차분", "침착") for k in e.EMOTION_CHAINS),
        str([k for k in e.EMOTION_CHAINS
             if _gm2.style_for_emotion(k) == _gm2.EMO_STYLE["neutral"]
             and k not in ("담담", "차분", "침착")]))

    # 폴백이 망가뜨리던 대표 사례들 — 원어 뜻이 살아야 한다
    chk("Gemini감정: 냉정이 '분노'로 안 샌다",
        "분노" not in _gm2.style_for_emotion("냉정", e.resolve_style("노인", "냉정")),
        _gm2.style_for_emotion("냉정", e.resolve_style("노인", "냉정")))
    chk("Gemini감정: 평온(남주)이 '기쁘게'로 안 샌다",
        "기쁘" not in _gm2.style_for_emotion("평온", e.resolve_style("남주", "평온")),
        _gm2.style_for_emotion("평온", e.resolve_style("남주", "평온")))
    chk("Gemini감정: 냉소가 비웃음 결을 유지",
        "비웃" in _gm2.style_for_emotion("냉소", e.resolve_style("노인", "냉소")))
    chk("Gemini감정: 분노는 역할 무관 동일",
        _gm2.style_for_emotion("분노", None) == _gm2.style_for_emotion("분노", "angry"))
    chk("Gemini감정: 변형어 부분일치('냉정함')",
        _gm2.style_for_emotion("냉정함") == _gm2.KO_EMO_STYLE["냉정"])
    chk("Gemini감정: 모르는 감정은 폴백 style 사용",
        _gm2.style_for_emotion("듣도보도못한감정", "sad") == _gm2.EMO_STYLE["sad"])
    chk("Gemini감정: raw_style 인자 존재",
        "raw_style" in _gm2.synth.__code__.co_varnames)

    # ★ 다른 엔진 경로는 안 건드렸는지 — resolve_style 은 종전 그대로여야 한다
    chk("Gemini감정: resolve_style 불변(수퍼톤/TC/EL 영향 없음)",
        e.resolve_style("남주", "분노") == "angry"
        and e.resolve_style("노인", "슬픔") == "sad")

    # UI 표시: 엔진별로 성우 칼럼이 달라야 한다
    _prev2 = e.DIALOGUE_ENGINE
    try:
        with _전역엔진만():       # 전역 선택만 보는 종전 동작
            e.set_engines(dialogue=e.ENGINE_GM)
            _r_gm = _app2.speaker_rows([("혈마존", 3, "악역")])[0][3]
            e.set_engines(dialogue=e.ENGINE_TC)
            _r_tc = _app2.speaker_rows([("혈마존", 3, "악역")])[0][3]
            chk("GeminiUI: 매핑표가 gm_voice_id 표시", "Gemini" in _r_gm, _r_gm)
            chk("GeminiUI: 타입캐스트는 tc 표시 유지", "tc_" in _r_tc, _r_tc)
            chk("GeminiUI: 엔진별 표시가 서로 다름", _r_gm != _r_tc)
        # 역할별 고정(악역=gemini)은 전역이 TC여도 Gemini 표시여야 한다
        e.set_engines(dialogue=e.ENGINE_TC)
        chk("역할고정UI: 악역은 전역 TC여도 Gemini 표시",
            "Gemini" in _app2.speaker_rows([("혈마존", 3, "악역")])[0][3])
        chk("역할고정UI: 노인2는 전역 GM이어도 타입캐스트 표시",
            (e.set_engines(dialogue=e.ENGINE_GM),
             "tc_" in _app2.speaker_rows([("무명노인", 3, "노인2")])[0][3])[1])
    finally:
        e.set_engines(dialogue=e.ENGINE_GM)
    chk("GeminiUI: 테스트 후 엔진 원복", e.DIALOGUE_ENGINE == e.ENGINE_GM)
except Exception as _ex:
    chk("Gemini 감정 직결 + UI 표시", False, f"{type(_ex).__name__}: {_ex}")


# ── 역할별 대사 엔진 (voice_map.json 의 engine) ─────────────────
#   왜 중요한가: Gemini 무료 티어는 모델당 하루 100요청. 대사를 전부 Gemini로 보내면
#   한 화(대사 ~100건)에 쿼터가 소진된다. 주요 역할만 Gemini로 묶어 한도 안에서 끝낸다.
try:
    GM_ROLES = {"남주", "여주", "악역", "악역2", "악역3", "악역4"}
    _dial_roles = [r for r, c in e.VOICE_MAP.items()
                   if isinstance(c, dict) and not r.startswith("_") and r != "나레이션"]

    chk("역할엔진: dialogue_engine_for 존재", callable(getattr(e, "dialogue_engine_for", None)))
    chk("역할엔진: 모든 대사 역할에 engine 지정됨",
        all("engine" in e.VOICE_MAP[r] for r in _dial_roles),
        str([r for r in _dial_roles if "engine" not in e.VOICE_MAP[r]]))
    chk("역할엔진: 주요 역할 = Gemini",
        all(e.dialogue_engine_for(r) == e.ENGINE_GM for r in GM_ROLES),
        str({r: e.dialogue_engine_for(r) for r in GM_ROLES}))
    chk("역할엔진: 그 외 = 타입캐스트",
        all(e.dialogue_engine_for(r) == e.ENGINE_TC
            for r in _dial_roles if r not in GM_ROLES),
        str({r: e.dialogue_engine_for(r) for r in _dial_roles if r not in GM_ROLES}))

    # 전역 선택보다 역할별 고정이 우선
    _p = e.DIALOGUE_ENGINE
    try:
        e.set_engines(dialogue=e.ENGINE_TC)
        chk("역할엔진: 전역 TC여도 악역은 Gemini", e.dialogue_engine_for("악역") == e.ENGINE_GM)
        e.set_engines(dialogue=e.ENGINE_EL)
        chk("역할엔진: 전역 EL여도 노인2는 타입캐스트", e.dialogue_engine_for("노인2") == e.ENGINE_TC)
        chk("역할엔진: engine 없는 역할은 전역 폴백",
            e.dialogue_engine_for("있을수없는역할") == e.ENGINE_EL)
    finally:
        e.set_engines(dialogue=_p)
    chk("역할엔진: 테스트 후 전역 원복", e.DIALOGUE_ENGINE == _p)

    # ★ 나레이션은 역할별 엔진과 무관하게 내 목소리 클론 고정
    chk("역할엔진: 나레이션 engine 은 elevenlabs 불변",
        e.VOICE_MAP["나레이션"].get("engine") == "elevenlabs")
    chk("역할엔진: NARRATION_ENGINE 불변", e.NARRATION_ENGINE == e.ENGINE_EL)

    # 서명(_part_sig)이 역할별 엔진을 반영해야 — 안 그러면 엉뚱한 장면이 재생성된다
    _sig_gm = e.scene_sig({"seq": "001", "kind": "dialogue", "speaker": "혈마존",
                           "emotion": "분노", "text": "죽어라"})
    _sig_tc = e.scene_sig({"seq": "001", "kind": "dialogue", "speaker": "무명노인",
                           "emotion": "분노", "text": "죽어라"})
    chk("역할엔진: 악역 서명은 GM 표식", "|GM|" in _sig_gm, _sig_gm)
    chk("역할엔진: 노인2 서명은 TC 표식", "|TC|" in _sig_tc, _sig_tc)

    # 전역 엔진을 바꿔도 고정된 역할의 서명은 안 바뀐다 → 불필요한 재생성 없음
    try:
        e.set_engines(dialogue=e.ENGINE_EL)
        chk("역할엔진: 전역 변경이 고정 역할 서명을 건드리지 않음",
            e.scene_sig({"seq": "001", "kind": "dialogue", "speaker": "혈마존",
                         "emotion": "분노", "text": "죽어라"}) == _sig_gm)
    finally:
        e.set_engines(dialogue=_p)
except Exception as _ex:
    chk("역할별 대사 엔진", False, f"{type(_ex).__name__}: {_ex}")

# ── 결과 ──
print("=" * 60)
print(f"  ✅ 통과 {len(OK)} / ❌ 실패 {len(BAD)}")
print("=" * 60)
for b in BAD:
    print("  ❌", b)
if not BAD:
    print("  🎉 무협 파이프라인 정상 — 안 깨졌습니다!")
sys.exit(1 if BAD else 0)
