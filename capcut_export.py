# -*- coding: utf-8 -*-
"""CapCut 초안(draft) 프로젝트 생성기.

기존 CapCut 프로젝트를 '템플릿'으로 삼아 draft_content.json / draft_meta_info.json 을
같은 스키마로 생성한다. 세그먼트와 그것이 참조하는 머티리얼 그래프를 통째로 복제하고
경로·길이·ID·자막 텍스트만 교체하므로, CapCut 버전별 필드를 그대로 유지한다.

결과: com.lveditor.draft/<제목>/
        ├─ assets/scene_N_video.mp4 (무음) · scene_N_audio.wav
        ├─ draft_content.json   (video/audio/text 트랙에 장면별 세그먼트 배치)
        ├─ draft_meta_info.json
        └─ draft_cover.jpg
"""
import os, json, uuid, copy, time, subprocess

US = 1_000_000  # 마이크로초
BGM_VOLUME = 0.05623413249850273   # -25dB = 10^(-25/20). 배경음악 기본 볼륨.

# 프로젝트에 동봉한 템플릿(검증본). CapCut이 클라우드로 오프로드해도 안 사라짐.
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capcut_template")

# 세그먼트에서 제외할 머티리얼 종류. 비우면 템플릿의 효과(전환 등)를 그대로 물려받는다.
#   → '비네트 전환'을 매 컷에 자동 적용하려고 일부러 비워둠(주인님이 늘 수동으로 넣던 것).
DROP_REF_KINDS = set()

# ── 장면 애니메이션 자동 배분 ────────────────────────
#   전체 장면의 ANIM_RATIO 만큼만 효과를 넣고, 그 안에서 줌1:기울기 = ANIM_ZOOM_RATIO
ANIM_RATIO      = 0.70      # 전체 장면 중 효과를 넣을 비율 (나머지는 무효과)
ANIM_ZOOM_RATIO = 0.80      # 효과 대상 중 '줌 1' 비율 (나머지 '밖으로 기울기')

ANIM_ZOOM = {   # 줌 1
    "id": "6759078592740594184", "resource_id": "6759078592740594184",
    "third_resource_id": "6759078592740594184", "name": "줌 1",
    "path": "C:/Users/7500F/AppData/Local/CapCut/User Data/Cache/effect/"
            "6759078592740594184/cac9a365aab6a7fe878431abd85d4b5d",
}
ANIM_TILT = {   # 밖으로 기울기
    "id": "6789167998700622350", "resource_id": "6789167998700622350",
    "third_resource_id": "6789167998700622350", "name": "밖으로 기울기",
    "path": "C:/Users/7500F/AppData/Local/CapCut/User Data/Cache/effect/"
            "6789167998700622350/b3b5bbf151c2f6cd6883ca88ab955cd0",
}

def plan_animations(n, ratio=None):
    """장면 n개에 대한 애니메이션 계획 → [None|ANIM_ZOOM|ANIM_TILT, ...]
       ratio 비율만큼 효과, 그 중 80% 줌1 / 20% 기울기. 고르게 흩뿌림.
       (무협=0.70 기본 · 경제학 이미지=1.0 전체적용)"""
    if ratio is None:
        ratio = ANIM_RATIO
    n_fx = int(round(n * ratio))
    n_zoom = int(round(n_fx * ANIM_ZOOM_RATIO))
    n_tilt = n_fx - n_zoom
    plan = [None] * n
    if n_fx <= 0:
        return plan
    # 효과 넣을 장면을 균등 간격으로 선택
    picks = sorted({int(round(i * n / n_fx)) % n for i in range(n_fx)})
    while len(picks) < n_fx:                      # 중복 제거로 모자라면 채움
        for i in range(n):
            if i not in picks:
                picks.append(i); break
        picks = sorted(set(picks))
    picks = picks[:n_fx]
    # 그 안에서 기울기를 균등 간격으로 배치, 나머지는 줌
    tilt_at = {picks[int(round(j * len(picks) / n_tilt))] for j in range(n_tilt)} if n_tilt else set()
    for i in picks:
        plan[i] = ANIM_TILT if i in tilt_at else ANIM_ZOOM
    return plan

def _uid():
    return str(uuid.uuid4()).upper()

def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")

def _unique_dir(base, name):
    """base/name 이 있으면 name_2, name_3 … 으로 충돌 회피(기존 프로젝트 보호)."""
    target = os.path.join(base, name)
    if not os.path.exists(target):
        return target
    i = 2
    while os.path.exists(f"{target}_{i}"):
        i += 1
    return f"{target}_{i}"


def _index(materials):
    """머티리얼 id → (배열이름, dict)"""
    idx = {}
    for arr, items in materials.items():
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and it.get("id"):
                    idx[it["id"]] = (arr, it)
    return idx


class CapCutDraft:
    def __init__(self, template_dir=None):
        template_dir = template_dir or TEMPLATE_DIR
        self.tdir = template_dir
        with open(os.path.join(template_dir, "draft_content.json"), encoding="utf-8") as f:
            self.content = json.load(f)
        with open(os.path.join(template_dir, "draft_meta_info.json"), encoding="utf-8") as f:
            self.meta = json.load(f)
        self.tidx = _index(self.content["materials"])
        self.proto_v = self._seg_with_path("video", "videos", "path")
        self.proto_a = self._seg_with_path("audio", "audios", "path")
        self.proto_t = self._first_seg("text")
        if not (self.proto_v and self.proto_a and self.proto_t):
            raise RuntimeError("템플릿에서 video/audio/text 원형 세그먼트를 못 찾음")

    # ── 원형(prototype) 찾기 ──
    def _seg_with_path(self, ttype, matarr, key):
        for tr in self.content["tracks"]:
            if tr.get("type") != ttype:
                continue
            for sg in tr.get("segments") or []:
                ent = self.tidx.get(sg.get("material_id"))
                if ent and ent[0] == matarr and ent[1].get(key):
                    return sg
        return None

    def _first_seg(self, ttype):
        for tr in self.content["tracks"]:
            if tr.get("type") == ttype and tr.get("segments"):
                return tr["segments"][0]
        return None

    def _track_like(self, ttype, segs):
        for tr in self.content["tracks"]:
            if tr.get("type") == ttype:
                t = copy.deepcopy(tr)
                t["id"] = _uid()
                t["segments"] = segs
                t["flag"] = 0
                return t
        return {"attribute": 0, "flag": 0, "id": _uid(), "is_default_name": True,
                "name": "", "segments": segs, "type": ttype}

    def transition_us(self):
        """템플릿의 전환 길이(마이크로초). 없으면 0."""
        for m in self.content["materials"].get("transitions") or []:
            if m.get("duration"):
                return int(m["duration"])
        return 0

    # ── 세그먼트 + 참조 머티리얼 복제 ──
    def _clone(self, proto, mats, start, dur, override,
               anim="keep", anim_dur=None, with_transition=True):
        """anim: "keep"=템플릿 그대로 / None=애니메이션 제거 / dict=해당 애니메이션 적용
           anim_dur: 애니메이션 길이(전환 overlap 포함). None이면 컷 길이.
           with_transition: False면 이 컷에 전환을 붙이지 않음(마지막 컷)."""
        seg = copy.deepcopy(proto)
        seg["id"] = _uid()
        arr, mat = self.tidx[proto["material_id"]]
        nm = copy.deepcopy(mat)
        nm["id"] = _uid()
        nm.update(override)
        mats.setdefault(arr, []).append(nm)
        seg["material_id"] = nm["id"]
        refs = []
        for rid in proto.get("extra_material_refs") or []:
            ent = self.tidx.get(rid)
            if not ent:
                continue
            a2, m2 = ent
            if a2 in DROP_REF_KINDS:        # 전환 등 제외 대상
                continue
            if a2 == "transitions" and not with_transition:
                continue                    # 마지막 컷엔 전환 없음(뒤에 이어질 게 없음)
            if a2 == "material_animations" and anim != "keep":
                if anim is None:
                    continue                # 이 장면은 애니메이션 없음
                c = copy.deepcopy(m2)
                c["id"] = _uid()
                items = c.get("animations") or []
                if items:
                    a0 = items[0]
                    a0.update(anim)                    # 줌1 / 밖으로기울기로 교체
                    a0["start"] = 0
                    a0["duration"] = int(anim_dur or dur)   # ★ 전환 overlap까지 덮게
                    c["animations"] = [a0]
                mats.setdefault(a2, []).append(c)
                refs.append(c["id"])
                continue
            c = copy.deepcopy(m2)
            c["id"] = _uid()
            mats.setdefault(a2, []).append(c)
            refs.append(c["id"])
        seg["extra_material_refs"] = refs
        seg["target_timerange"] = {"start": start, "duration": dur}
        if seg.get("source_timerange") is not None:
            seg["source_timerange"] = {"start": 0, "duration": dur}
        for k in ("common_keyframes", "keyframe_refs"):
            if isinstance(seg.get(k), list):
                seg[k] = []
        seg["speed"] = 1.0
        return seg

    def _clone_text(self, mats, start, dur, text):
        seg = self._clone(self.proto_t, mats, start, dur, {})
        mat = mats["texts"][-1]
        try:
            c = json.loads(mat["content"])
        except Exception:
            c = {"styles": [{"range": [0, len(text)], "size": 6}], "text": text}
        c["text"] = text
        for st in c.get("styles", []):
            st["range"] = [0, len(text)]
        mat["content"] = json.dumps(c, ensure_ascii=False)
        for k in ("base_content", "recognize_text"):
            if k in mat:
                mat[k] = text if k == "base_content" else ""
        mat["text_size"] = len(text)
        return seg

    # ── 원형(prototype) 복제: 저장된 검증 세그먼트를 새 ID/경로/시각으로 재사용 ──
    def _clone_proto(self, proto, mats, start, dur, path_override=None, text_override=None,
                     volume=None, src_start=0):
        """proto = {'segment':..., 'materials':{arr:[...]}} (필수/brand_template.json).
           소재를 새 ID로 복제(참조 재매핑) + 경로/텍스트/시각 교체."""
        idmap = {}
        for arr, items in proto["materials"].items():
            for it in items:
                nit = copy.deepcopy(it)
                old = nit.get("id"); new = _uid()
                nit["id"] = new
                if old:
                    idmap[old] = new
                if path_override and nit.get("path"):
                    if nit["path"].lower().endswith((".png", ".jpg", ".jpeg", ".mp4", ".mov",
                                                     ".webp", ".wav", ".mp3", ".m4a", ".aac", ".flac")):
                        nit["path"] = path_override.replace("/", "\\")
                        if "material_name" in nit:
                            nit["material_name"] = os.path.basename(path_override)
                        if "name" in nit:
                            nit["name"] = os.path.basename(path_override)
                        if arr in ("videos", "audios") and nit.get("duration"):
                            nit["duration"] = int(dur)
                if text_override is not None and arr == "texts":
                    try:
                        cc = json.loads(nit.get("content") or "{}")
                    except Exception:
                        cc = {"styles": [{"range": [0, len(text_override)]}], "text": text_override}
                    cc["text"] = text_override
                    for stl in cc.get("styles", []):
                        stl["range"] = [0, len(text_override)]
                    nit["content"] = json.dumps(cc, ensure_ascii=False)
                    if "base_content" in nit:
                        nit["base_content"] = text_override
                    nit["text_size"] = len(text_override)
                mats.setdefault(arr, []).append(nit)
        seg = copy.deepcopy(proto["segment"])
        seg["id"] = _uid()
        seg["material_id"] = idmap.get(seg.get("material_id"), seg.get("material_id"))
        seg["extra_material_refs"] = [idmap.get(r, r) for r in (seg.get("extra_material_refs") or [])]
        seg["target_timerange"] = {"start": int(start), "duration": int(dur)}
        if seg.get("source_timerange") is not None:
            seg["source_timerange"] = {"start": int(src_start), "duration": int(dur)}
        if volume is not None:                     # BGM 볼륨(-25dB 등)
            seg["volume"] = float(volume)
            if "last_nonzero_volume" in seg:
                seg["last_nonzero_volume"] = float(volume)
        for k in ("common_keyframes", "keyframe_refs"):
            if isinstance(seg.get(k), list):
                seg[k] = []
        return seg

    def _track_from_meta(self, meta, ttype, segs):
        """저장된 트랙 메타(flag/attribute 등)로 트랙 생성 (로고 오버레이 flag=2 등)."""
        t = {"attribute": 0, "flag": 0, "is_default_name": True, "name": "",
             "type": ttype, "segments": segs, "id": _uid()}
        t.update({k: v for k, v in (meta or {}).items() if k in ("flag", "attribute", "is_default_name", "name")})
        t["id"] = _uid()
        t["type"] = ttype
        t["segments"] = segs
        return t

    # ── 빌드 ──
    def build(self, out_dir, name, scenes, fps=30.0, width=1920, height=1080, brand=None,
              anim_ratio=None):
        """scenes: [{'video':절대경로, 'audio':절대경로, 'dur_us':int,
                     'cues':[(상대시작초, 상대끝초, 자막), ...]  또는  'text':자막}, ...]
           brand(선택): {'proto':brand_template, 'intro':{...}|None, 'cta':{...}, 'outro':{...}|None,
                         'cta_after':장면0based, 'logo':{'path':png}, 'disclaimer':{'text':...}|None, 'bgm':...}
             → 시작·CTA·마지막 영상(있는 것만) + 로고(우상)·창작고지(좌상 있으면) 오버레이 자동.
           anim_ratio: 애니 적용 비율(None=무협 0.70 · 경제학 이미지=1.0)."""
        os.makedirs(out_dir, exist_ok=True)
        mats = {k: [] for k in self.content["materials"].keys()}
        v_segs, a_segs, t_segs = [], [], []
        n = len(scenes)
        anims = plan_animations(n, anim_ratio)     # 무협 70% · 경제학 이미지 100% 등
        for i, sc in enumerate(scenes):            # 장면에 no_anim=True면 애니 제외(경제학 후킹영상)
            if sc.get("no_anim"):
                anims[i] = None
        half_tr = self.transition_us() // 2        # 전환(overlap)이 앞뒤 컷에서 먹는 시간
        P = brand["proto"] if brand else None
        cta_after = brand.get("cta_after") if brand else None
        reversal_after = brand.get("반전_after") if brand else None
        reversal_us = None
        spans = []                                 # 본편(오버레이) 구간 [(start,end), ...]

        def place_brand(clip, at):
            """시작/CTA/마지막 영상: 장면과 같은 방식(무음 영상 + wav 오디오)으로 배치.
               전환·애니메이션 없음. 반환: 다음 start."""
            bd = int(clip["dur_us"])
            v_segs.append(self._clone(self.proto_v, mats, at, bd, {
                "path": clip["video"].replace("/", "\\"),
                "material_name": os.path.basename(clip["video"]),
                "duration": bd, "width": width, "height": height, "has_audio": False,
                "local_material_id": "", "source": 0,
            }, anim=None, with_transition=False))
            a_segs.append(self._clone(self.proto_a, mats, at, bd, {
                "path": clip["audio"].replace("/", "\\"),
                "name": os.path.basename(clip["audio"]),
                "duration": bd, "local_material_id": "",
            }))
            return at + bd

        t0 = 0
        if brand and brand.get("intro"):           # ① 시작 영상(있으면)
            t0 = place_brand(brand["intro"], t0)
        span_open = t0
        for i, sc in enumerate(scenes):
            d = int(sc["dur_us"])
            # 마지막 컷 + CTA 직전 컷엔 전환 없음(브랜드 영상으로 하드컷)
            with_tr = (i < n - 1) and not (cta_after is not None and i == cta_after)
            anim_dur = d + (half_tr if i > 0 else 0) + (half_tr if with_tr else 0)
            v_segs.append(self._clone(self.proto_v, mats, t0, d, {
                "path": sc["video"].replace("/", "\\"),
                "material_name": os.path.basename(sc["video"]),
                "duration": d, "width": width, "height": height, "has_audio": False,
                "local_material_id": "", "source": 0,
            }, anim=anims[i], anim_dur=anim_dur, with_transition=with_tr))
            a_segs.append(self._clone(self.proto_a, mats, t0, d, {
                "path": sc["audio"].replace("/", "\\"),
                "name": os.path.basename(sc["audio"]),
                "duration": d, "local_material_id": "",
            }))
            cues = sc.get("cues")
            if not cues and sc.get("text"):
                cues = [(0.0, d / US, sc["text"])]
            for (cs, cee, txt) in (cues or []):
                if not txt:
                    continue
                st = t0 + int(round(cs * US))
                du = max(1, int(round((cee - cs) * US)))
                if st + du > t0 + d:                  # 장면 밖으로 새지 않게
                    du = max(1, t0 + d - st)
                t_segs.append(self._clone_text(mats, st, du, txt))
            t0 += d
            if brand and reversal_after is not None and i == reversal_after:
                reversal_us = t0                   # [반전] 마커 다음 장면부터 = 반전 BGM 시작
            if brand and cta_after is not None and i == cta_after:   # ② CTA 삽입(있으면)
                spans.append((span_open, t0))
                if brand.get("cta"):
                    t0 = place_brand(brand["cta"], t0)
                span_open = t0
        if brand:                                  # ③ 마지막 영상(있으면) + 마지막 본편 구간 닫기
            spans.append((span_open, t0))
            if brand.get("outro"):
                t0 = place_brand(brand["outro"], t0)

        tracks = [self._track_like("video", v_segs),
                  self._track_like("audio", a_segs),
                  self._track_like("text", t_segs)]
        if brand:                                  # ④ 오버레이: 창작고지(있으면·좌상) + 로고(우상), 본편 구간에만
            disc_segs, logo_segs = [], []
            has_disc = bool(brand.get("disclaimer"))
            has_logo = bool(brand.get("logo"))
            for (s, e) in spans:
                dur = max(1, e - s)
                if has_disc:
                    disc_segs.append(self._clone_proto(P["disclaimer"], mats, s, dur,
                                                       text_override=brand["disclaimer"]["text"]))
                if has_logo:
                    logo_segs.append(self._clone_proto(P["logo"], mats, s, dur,
                                                       path_override=brand["logo"]["path"]))
            if disc_segs:
                tracks.append(self._track_from_meta(P.get("disc_track_meta"), "text", disc_segs))
            if logo_segs:
                tracks.append(self._track_from_meta(P.get("logo_track_meta"), "video", logo_segs))

        # ⑤ 배경음악(BGM): 후킹→본편(반복)→반전 구간에 -25dB로 자동. (필수/ BGM 있을 때만)
        if brand and brand.get("bgm") and spans:
            B = brand["bgm"]; durs = B["durs"]
            regions = []                           # (start, end, [파일들])
            if len(spans) >= 2 and B.get("후킹"):
                regions.append((spans[0][0], spans[0][1], [B["후킹"]]))
                bs, be = spans[1]
            else:
                bs, be = spans[0]                  # CTA 없으면 전체가 본편
            body_files = B.get("본편") or ([B["후킹"]] if B.get("후킹") else [])
            if B.get("반전") and reversal_us and bs < reversal_us < be:
                regions.append((bs, reversal_us, body_files))
                regions.append((reversal_us, be, [B["반전"]]))
            elif be > bs and body_files:
                regions.append((bs, be, body_files))
            bgm_segs = []
            for (rs, re_, files) in regions:
                pos, k = rs, 0
                while pos < re_ - 1000 and files:  # 파일을 반복/교대로 구간에 채움
                    f = files[k % len(files)]
                    seg_dur = min(int(durs.get(f, 0)), re_ - pos)
                    if seg_dur <= 0:
                        break
                    bgm_segs.append(self._clone_proto(P["bgm"], mats, pos, seg_dur,
                                                      path_override=f, volume=BGM_VOLUME))
                    pos += seg_dur; k += 1
            if bgm_segs:
                tracks.append(self._track_from_meta(P.get("bgm_track_meta"), "audio", bgm_segs))

        c = copy.deepcopy(self.content)
        c["id"] = _uid()
        c["name"] = ""
        c["duration"] = t0
        c["fps"] = fps
        c["materials"] = mats
        c["tracks"] = tracks
        # 옛 ID를 참조하던 것들 초기화
        if isinstance(c.get("relationships"), list):
            c["relationships"] = []
        if isinstance(c.get("keyframe_graph_list"), list):
            c["keyframe_graph_list"] = []
        if isinstance(c.get("keyframes"), dict):
            c["keyframes"] = {k: ([] if isinstance(v, list) else v) for k, v in c["keyframes"].items()}
        for k in ("cover", "retouch_cover"):
            if k in c:
                c[k] = None
        c["group_container"] = None

        # ── draft_meta_info ──
        m = copy.deepcopy(self.meta)
        now_us = int(time.time() * US)
        m["draft_id"] = _uid()
        m["draft_name"] = name
        m["draft_fold_path"] = out_dir.replace("\\", "/")
        m["draft_root_path"] = os.path.dirname(out_dir).replace("\\", "/")
        m["draft_cover"] = "draft_cover.jpg"
        m["tm_duration"] = t0
        m["tm_draft_create"] = now_us
        m["tm_draft_modified"] = now_us
        m["draft_timeline_materials_size_"] = sum(
            os.path.getsize(p) for sc in scenes for p in (sc["video"], sc["audio"]) if os.path.exists(p))
        proto_media = None
        for blk in m.get("draft_materials") or []:
            if blk.get("type") == 0 and blk.get("value"):
                proto_media = blk["value"][0]
                break
        media = []
        for sc in scenes:
            for path, mtype in ((sc["video"], "video"), (sc["audio"], "music")):
                e = copy.deepcopy(proto_media) if proto_media else {}
                e.update({
                    "id": _uid(),
                    "file_Path": path.replace("\\", "/"),
                    "metetype": mtype,
                    "duration": int(sc["dur_us"]),
                    "extra_info": os.path.basename(path),
                    "width": width if mtype == "video" else 0,
                    "height": height if mtype == "video" else 0,
                    "create_time": -1,
                    "import_time": int(time.time()),
                    "import_time_ms": now_us,
                    "md5": "",
                })
                media.append(e)
        for blk in m.get("draft_materials") or []:
            blk["value"] = media if blk.get("type") == 0 else []

        with open(os.path.join(out_dir, "draft_content.json"), "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
        with open(os.path.join(out_dir, "draft_meta_info.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
        return {"duration_us": t0, "scenes": len(scenes),
                "video_segs": len(v_segs), "audio_segs": len(a_segs), "text_segs": len(t_segs)}


# ── 소재 준비 (무음 영상 + wav 오디오) ──────────────
def prepare_assets(assets_dir, seq_list, matched_dir, audio_dir=None):
    """매칭/NNN_matched.mp4 하나에서 영상·오디오를 각각 뽑는다.
       → assets/scene_N_video.mp4 (무음) · scene_N_audio.wav
       ★ 같은 파일에서 뽑으므로 두 길이가 정확히 일치(싱크 보장)."""
    os.makedirs(assets_dir, exist_ok=True)
    out = []
    for i, seq in enumerate(seq_list, 1):
        src = os.path.join(matched_dir, f"{seq}_matched.mp4")
        dst_v = os.path.abspath(os.path.join(assets_dir, f"scene_{i}_video.mp4"))
        dst_a = os.path.abspath(os.path.join(assets_dir, f"scene_{i}_audio.wav"))
        _run(["ffmpeg", "-y", "-i", src, "-an", "-c:v", "copy", dst_v])        # 무음 영상
        _run(["ffmpeg", "-y", "-i", src, "-vn", "-c:a", "pcm_s16le", dst_a])   # wav 오디오(같은 소스)
        out.append((seq, dst_v, dst_a))
    return out


def prepare_brand(assets_dir, clips):
    """브랜드 영상(시작/CTA/마지막) mp4 → 무음영상 + wav 오디오 추출(장면과 동일 방식).
       clips = {'intro':mp4, 'cta':mp4, 'outro':mp4}
       반환 = {'intro':{'video':..,'audio':..,'dur_us':int}, ...}"""
    os.makedirs(assets_dir, exist_ok=True)
    out = {}
    for key, src in clips.items():
        dv = os.path.abspath(os.path.join(assets_dir, f"brand_{key}_video.mp4"))
        da = os.path.abspath(os.path.join(assets_dir, f"brand_{key}_audio.wav"))
        _run(["ffmpeg", "-y", "-i", src, "-an", "-c:v", "copy", dv])
        _run(["ffmpeg", "-y", "-i", src, "-vn", "-c:a", "pcm_s16le", da])
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", dv], capture_output=True, text=True)
        out[key] = {"video": dv, "audio": da, "dur_us": int(round(float(r.stdout.strip()) * US))}
    return out


def make_cover(out_dir, first_video):
    cover = os.path.join(out_dir, "draft_cover.jpg")
    try:
        _run(["ffmpeg", "-y", "-i", first_video, "-vf", "thumbnail,scale=480:-1",
              "-frames:v", "1", cover])
    except Exception:
        pass
    return cover
