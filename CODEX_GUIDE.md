# TTS+영상 변환기 — 통합 가이드

CapCut 유사 편집기와 통합하려는 개발자(코덱스)를 위한 문서.
**이 파이프라인이 무엇을 하고, 어디를 건드리면 안 되고, 어디가 연결 지점인지**를 설명한다.

---

## 1. 한 줄 요약

> 무협 소설 대본을 넣으면 → 장면별로 **나레이션/대사를 TTS로 생성**하고
> → **영상 클립 길이를 오디오에 맞춰 정렬**하고 → **자막(.srt)을 단어 단위로 싱크**해서
> → **CapCut 초안 프로젝트(draft)를 통째로 만들어낸다.**

최종 산출물은 CapCut에서 바로 열리는 프로젝트 폴더다. 사람이 CapCut에서 마무리 편집만 한다.

---

## 2. 전체 흐름

```
대본.txt
   │  ① 분석 (load_segments)
   ▼
[장면 001..115]  각 장면 = 나레이션 또는 대사(또는 둘 다)
   │  ② TTS 제작 (run_tts)   ← 크레딧 소모 지점
   ▼
오디오/001.mp3 …                       ← 장면별 음성
   │  ③ 영상 매칭 (fit_video_to_audio)
   ▼
매칭/001_matched.mp4 …                 ← 영상 길이 = 오디오 길이
자막/001.srt …                         ← ASR 강제정렬로 단어 타이밍
   │  ④ CapCut 내보내기 (capcut_export)
   ▼
CapCut draft 프로젝트 폴더              ← 사람이 열어서 마무리
```

UI는 Gradio 웹앱(`app.py`, `127.0.0.1:7860`)이고
**4단계 위저드**로 되어 있다: `1 대본 입력 → 2 영상 매칭 → 3 TTS 제작 → 4 영상 제작`.

---

## 3. 입력 계약 — 대본 형식 (가장 중요)

파서는 두 형식을 자동 감지한다(`detect_format`). 실제로 쓰는 건 **시각화 양식**이다.

```
[장면 1]
[한국어 번역] 낙양의 아침은 북과 징소리로 시작되었다.
[영어 이미지 프롬프트] A wuxia manhwa illustration of dawn over Luoyang. No text.

[장면 2]
[한국어 번역] [혈마존|분노]"네 이놈! 감히 내 앞에서 검을 뽑겠다는 것이냐!"
[영어 이미지 프롬프트] A villain in black robes drawing a saber. No text.

[CTA]

[장면 3]
[한국어 번역] 검이 울었다. [서무겸|단호]"물러서시오." 바람이 멎었다.
[영어 이미지 프롬프트] ...
```

규칙:

| 요소 | 의미 |
|---|---|
| `[장면 N]` | 장면 번호. 출력 파일명이 `%03d` (`001.mp3`)로 붙는다 |
| `[한국어 번역]` | TTS가 읽을 본문. **`[한국어 원문]`도 같은 뜻**(시리즈마다 다름) |
| `[영어 이미지 프롬프트]` | 이미지 생성용. TTS는 무시 |
| `[화자\|감정]"대사"` | 대사. 따옴표 안이 읽히고, 화자/감정은 음성 선택에 쓰인다 |
| 따옴표 밖 텍스트 | 나레이션 |
| `[CTA]` / `[반전]` | 마커. 장면이 아니라 **직전 장면 뒤 위치 표시** |

**한 장면에 나레이션과 대사가 섞이거나 대사가 2개 이상이면** `_split_parts`가 파트로 쪼개
각각 생성한 뒤 `_concat_audio`로 이어붙인다(화자 전환 사이 0.3초 정적).

---

## 4. 모듈 지도

| 파일 | 역할 |
|---|---|
| `tts_pipeline.py` | **엔진 본체.** 파싱·역할배정·감정매핑·TTS생성·영상정렬·자막 |
| `app.py` | Gradio UI. 위저드 4단계, 미리보기, 매핑 확정 |
| `capcut_export.py` | CapCut draft 프로젝트 생성 (**통합 핵심 지점**) |
| `gemini_engine.py` | 대사 엔진 — Gemini TTS (**현재 기본**) |
| `typecast_engine.py` | 대사 엔진 — 타입캐스트 |
| `flow_pipeline.py` | Google Flow(이미지/영상 생성) 프롬프트 내보내기·가져오기 |
| `yadam_pipeline.py` / `keizai_pipeline.py` | 다른 카테고리(야담/경제학). 무협과 **격리** |
| `발음교정.py` / `.json` | TTS 보내기 직전 텍스트 교정(조사 씹힘 대응) |
| `발음검사.py` · `발음AB.py` · `제미나이보이스.py` | 품질 측정 도구(크레딧 0 또는 극소) |
| `test_무협파이프라인.py` | **회귀 테스트 100개.** 크레딧 0, 몇 초 |

### 출력 폴더 (코드 상수)

```python
OUT_AUDIO = "오디오"      # 001.mp3 …  TTS 결과
OUT_MATCH = "매칭"        # 001_matched.mp4 …  영상+오디오 정렬본
OUT_SRT   = "자막"        # 전체.srt / 001.srt …
VIDEO_DIR = "영상클립"     # 001.mp4 …  입력 영상(또는 이미지)
```

---

## 5. 음성 배정 구조

```
화자 이름("혈마존")  →  역할("악역")  →  엔진별 voice_id
      role_of()              voice_map.json
```

- `character_cast.json` — 작품마다 교체. 캐릭터 ↔ 역할
- `voice_map.json` — **영구 고정.** 역할 ↔ 성우. 엔진별 필드를 나란히 가진다:

```json
"악역": {
  "voice_id":    "...",        // Supertone (종료됨)
  "el_voice_id": "...",        // ElevenLabs
  "tc_voice_id": "tc_...",     // Typecast
  "gm_voice_id": "Enceladus"   // Gemini
}
```

미등록 화자는 `guess_role_by_name` → `auto_role`로 자동 배정한다
(이름 호칭 기반: 진인/장로/방장 → 노인, 소저/낭자 → 여주, 마존/혈마 → 악역 …).
우선순위: **등록 캐스트 > 이름 호칭 > 대사량 1위=남주 > 기본값**.

---

## 6. 엔진 추상화 (대사 엔진 4종)

```python
ENGINE_ST = "supertone"   # 2026-08-31 종료. 코드 경로만 남음
ENGINE_EL = "elevenlabs"  # 감정 = v3 오디오 태그 [angry] 등
ENGINE_TC = "typecast"    # 감정 = 문맥 기반 smart 자동
ENGINE_GM = "gemini"      # ★ 현재 기본. 감정 = 한국어 연기 지시문

DIALOGUE_ENGINE  = ENGINE_GM   # 대사 (2026-10 Gemini 로 전환)
NARRATION_ENGINE = ENGINE_EL   # 나레이션(주인님 목소리 클론) — 고정
```

전환은 `set_engines(dialogue=...)` 또는 UI 라디오.
분기점은 **`_synth_one()` 하나뿐**이다. 새 엔진을 붙이려면:

1. `xxx_engine.py` 새로 만들고 `synth(out_path, text, voice_id, …)` 제공
2. `ENGINE_XX` 상수 추가
3. `_synth_one()`에 분기 추가
4. `_part_sig()`에 서명 형식 추가 ← **빠뜨리면 재생성이 안 걸린다**
5. `voice_map.json`에 `xx_voice_id` 추가
6. `app.py` 라디오에 선택지 추가 + **표시 경로 3곳**(매핑표/미리보기 행/요약)

### 감정 처리가 엔진마다 다르다

| 엔진 | 방식 |
|---|---|
| Typecast | `emotion_type: smart` — 앞뒤 장면 문맥을 넘기면 AI가 판단 |
| ElevenLabs | `[angry]` 같은 v3 오디오 태그를 대사 앞에 붙임 |
| **Gemini** | **한국어 연기 지시문**을 `Part.speech_metadata`로 전달 |

**Gemini 주의:** 지시를 프롬프트 접두어(`"분노한 목소리로: 대사"`)로 넣으면
모델이 그 지시문을 **그대로 소리내어 읽는다**. 반드시 `Part.speech_metadata`를 써야 하고,
`google-genai>=2.29.0`이어야 그 필드가 존재한다.

Gemini는 성우별 지원 style 제약이 없으므로, 수퍼톤 시절 폴백(`resolve_style`)을 거치지 않고
`KO_EMO_STYLE`이 **대본의 한국어 감정어를 그대로** 지시문으로 바꾼다(41종 + 변형어).

---

## 7. ★ 재생성 방지 구조 (통합 시 반드시 이해할 것)

TTS 생성은 **돈이 든다.** 그래서 장면마다 "서명"을 저장해두고, 서명이 같으면 건너뛴다.

```python
# 나레이션
"N|{text}|{voice_id}|{speed}"
# 대사(Gemini)
"D|{text}|GM|{role}|{voice}|{emotion}"
# 다중 화자
"M|" + "||".join(파트별 서명)
```

- 저장 위치: `오디오/.audio_texts.json`
- `_needs_gen()`이 **mp3 존재 + 비어있지 않음 + 서명 일치**를 모두 만족할 때만 건너뛴다
- `run_tts(resume=True)`가 기본 → 중단 후 재실행하면 남은 것만 생성

**통합할 때 지켜야 할 것:** 서명에 들어가는 값(텍스트/voice_id/speed/감정)을
의미 없이 바꾸면 **전체가 재생성되며 크레딧이 소모된다.**
반대로 새 엔진을 추가하면서 서명에 엔진 식별자를 안 넣으면,
엔진을 바꿔도 **옛 음성이 그대로 재사용된다.**

부수 효과로, 이 매니페스트에는 **장면별 원문 텍스트가 전부 들어 있다**(복구에 쓸 수 있음).

---

## 8. 영상 매칭과 자막

- `find_scene_media(seq)` — `영상클립/`에서 `mp4·mov·webm·mkv·m4v·avi` 먼저, 없으면 `jpg·png·webp·bmp`
- 이미지 장면은 `image_to_clip()`이 **ffmpeg zoompan으로 켄번즈 모션을 구워서** 영상화한다
  - 무브 6종(push/pull/left/right/up/down)을 장면번호로 **결정적** 순환(랜덤 아님)
  - 강도는 길이 반비례(짧은 장면일수록 강하게)
- `fit_video_to_audio()` — 영상을 오디오 길이에 **프레임 격자 정렬**해서 맞춤
- 자막: `asr_words()`(faster-whisper small)로 **단어 타이밍만** 얻고 텍스트는 대본을 쓴다
  → 인식 오탈자에 강하다. 실패 시 무음 기반 정렬로 폴백
  → 자막 한 줄 **28자 이내**

---

## 9. CapCut 내보내기 — **여기가 통합 지점**

`capcut_export.py`가 **진짜 CapCut draft 프로젝트 폴더**를 만든다
(`draft_content.json` + `draft_meta_info.json`). 템플릿은 `capcut_template/`에 있다.

자동으로 들어가는 것:

- 브랜드 세그먼트: `시작 → 후킹 → CTA → 본편 → 마지막` 배치 (`필수/` 폴더 자산, 저장소 제외)
- 로고 오버레이(우상단) + 창작고지 텍스트(좌상단) — 본편 구간에만
- CTA 위치는 대본의 `[CTA]` 마커가 결정
- 장면 전환(비네트), 프리셋 애니메이션(`plan_animations(n, ratio)`)
- `no_anim` 플래그 — 켄번즈를 이미 구운 이미지 장면은 **CapCut 프리셋 애니를 제외**(이중 애니 방지)

편집기를 새로 만든다면 이 모듈이 **포맷 레퍼런스**가 된다.
`draft_content.json`의 materials/tracks/segments 구조를 그대로 읽으면 된다.

---

## 10. 실행 방법

```powershell
# 1) 의존성 (Python 3.12)
pip install -r requirements.txt

# 2) 키 설정 — .env.example 복사해서 .env 만들고 값 채우기
#    ELEVENLABS_API_KEY / TYPECAST_API_KEY / GEMINI_API_KEY

# 3) 앱 실행
.\무협스튜디오_실행.bat          # 또는
.\.venv\Scripts\python.exe app.py   # http://127.0.0.1:7860

# 4) 회귀 테스트 (크레딧 0, 몇 초)
.\.venv\Scripts\python.exe test_무협파이프라인.py
```

UI를 거치지 않고 엔진만 쓰려면:

```python
import tts_pipeline as e
segs = e.load_segments("대본.txt")      # 장면 파싱
print(e.tts_plan("대본.txt"))           # 생성 없이 계획만(크레딧 0)
e.set_engines(dialogue=e.ENGINE_GM)     # 대사 엔진 선택
e.run_tts("대본.txt", resume=True)      # ★ 실제 생성 — 크레딧 소모
```

---

## 11. 건드리면 안 되는 것 (불변 규칙)

1. **나레이션은 주인님 목소리 클론으로 고정이다.** `NARRATION_ENGINE = ENGINE_EL`,
   `voice_map.json`의 `나레이션` 항목에는 다른 엔진 voice_id 를 넣지 않는다.
   대사 엔진을 바꿔도 나레이션은 영향받지 않아야 한다(서명이 분리돼 있다).
2. **`voice_map.json`의 기존 `voice_id` 값은 수정 금지.** 역할↔성우는 영구 고정이다.
   필드 **추가**는 괜찮다.
3. **`오디오/` `자막/` `매칭/` `완성본/`은 실제 산출물이다. 삭제 금지.**
4. **코드를 바꾸면 `test_무협파이프라인.py`를 돌려서 100개 전부 통과하는지 확인할 것.**
   통과(종료코드 0) 전에는 완료로 보지 않는다.
5. **TTS 실제 생성은 크레딧을 쓴다.** 승인 없이 `run_tts()`를 호출하지 말 것.
   계획만 보려면 `tts_plan()` / `dry_run()`을 쓴다.
6. **`app.build_dryrun()`은 읽기 전용이 아니다** — 입력을 `_작업대본.txt`에 덮어쓴다.
   테스트용으로 호출하면 작업 대본이 날아간다.
7. 야담/경제학 파이프라인은 무협과 **격리**되어 있다. 새 카테고리는 기존 무협 코드를
   수정하지 말고 **별도 모듈로 추가**한다.

## 12. 알려진 제약

- **Gemini 무료 티어**는 하루 10여 건에서 `429 RESOURCE_EXHAUSTED`. 결제 연동 필요.
  (과금은 매우 저렴: 오디오 출력 25토큰/초 × $9/1M → 대사 한 화 약 $0.04)
- **Gemini 출력은 24kHz** → 실효 대역 약 12kHz. ElevenLabs(15.4kHz)보다 어둡다.
  나레이션과 섞을 때 음질 차이가 들릴 수 있다.
- **ElevenLabs 한국어**는 `○○에게`의 `에`를 삼킬 때가 있다.
  해결책은 **`에게` 앞 띄어쓰기**뿐(IPA/phoneme 규칙은 영어 전용 모델에서만 동작).
  `발음교정.json`이 이걸 처리한다. 단 `에게서`는 분리하면 오히려 망가진다.
- 감정 측정값(피치 이동폭)은 **런마다 흔들린다**(같은 보이스가 9.2~17.9st).
  미세 순위로 읽지 말고 등급(상/중/하)으로만 볼 것.
