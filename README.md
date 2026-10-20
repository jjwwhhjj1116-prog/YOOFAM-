# 무협 오디오북 TTS + 영상 매칭 파이프라인

대본을 **이중 TTS**(나레이션=ElevenLabs / 대사=Supertone)로 음성 변환하고,
장면별로 **영상 길이를 오디오에 맞추고 자막(.srt)까지** 자동 생성하는 도구.

---

## 1. 폴더 구조

```
TTS+영상 변환기/
├─ tts_pipeline.py       ← 메인 스크립트
├─ voice_map.json        ← 역할 ↔ 성우 (영구 고정, 작품 바뀌어도 유지)
├─ character_cast.json   ← 캐릭터 ↔ 역할 (작품마다 교체)
├─ .env                  ← API 키 2개 (ELEVENLABS / SUPERTONE)
├─ 대본.txt              ← ★ 여기에 대본을 넣는다 (파일명 자유)
│
├─ 영상클립/             ← ★ Flow 영상을 001.mp4, 002.mp4 … 로 넣는다
│
├─ 오디오/               ← [출력] TTS 조각  001.mp3, 002.mp3 …
├─ 매칭/                 ← [출력] 영상+오디오 매칭본  001_matched.mp4 …
├─ 자막/                 ← [출력] 전체.srt + 001.srt, 002.srt …
└─ 장면오디오/           ← (legacy 전용, 평소 비어 있음)
```

`영상클립·오디오·매칭·자막·장면오디오` 폴더는 스크립트가 자동으로 만든다.

---

## 2. 최초 1회 준비

```powershell
# 가상환경(Python 3.12) 사용
.\.venv\Scripts\Activate.ps1        # 활성화 (또는 아래처럼 python 경로 직접 지정)

# 패키지 설치 (검증된 고정 버전)
pip install -r requirements.txt

# .env 에 API 키 채우기
#   ELEVENLABS_API_KEY=...
#   SUPERTONE_API_KEY=...
```

> ffmpeg / ffprobe 가 설치되어 있어야 한다(정규화·영상매칭·자막 타이밍에 필요).
> 재부팅 후에는 `ffmpeg` 명령이 자동 인식된다.

---

## 2-1. 웹 대시보드 실행 (권장 · PowerShell 불필요)

1. **실행**: `무협스튜디오_실행.bat` **더블클릭** → 서버 창은 **최소화 상태로 작업표시줄에 남고**, 3초 뒤 브라우저(http://127.0.0.1:7860)가 자동으로 **한 번만** 열린다.
2. **종료**: **작업표시줄의 그 검은 창을 열어 닫거나** Ctrl+C. (에러가 나면 그 창에서 메시지를 볼 수 있다.)
3. **수정 확인**: `개발모드_실행.bat` 로 실행하면 `app.py` 저장 시 서버가 자동 재시작 → **브라우저 새로고침(F5)** 만 하면 최신 반영. (일반 사용은 `무협스튜디오_실행.bat`)

> 배너를 바꾸려면 `assets/banner.png` 를 교체 후 .bat를 다시 실행.

---

## 3. 대본 작성 규칙

```
검은 그림자가 사천당가의 담을 넘었다.        ← 나레이션 (따옴표 없는 줄)  → ElevenLabs
[진소운|냉소]"당가의 독은 이미 내 것이다."    ← 대사 [화자|감정]"..."       → Supertone
[독수마군|분노]"네놈이 감히!"
```

- **나레이션**: 따옴표 없는 일반 문장.
- **대사**: `[화자|감정]"대사내용"` 형식. 둥근따옴표(" ")·곧은따옴표(") 모두 허용.
- 대사 화자 이름은 `character_cast.json` 에 **역할**로 등록되어 있어야 한다.
  (미등록 화자는 기본 역할 `남자1`로 처리되고 경고가 뜬다.)
- 한 줄 = 한 장면. 위→아래 순서대로 001, 002, 003 … 번호가 매겨진다.

`character_cast.json` 예시 (작품마다 이 파일만 새로 씀):
```json
{ "진소운": "남주", "설희": "여주", "독수마군": "악역", "청허진인": "노인" }
```

---

## 4. 명령어

가상환경 python으로 실행한다. (활성화했다면 그냥 `python`)

### ① 드라이런 — 비용 0, 리포트만 (먼저 이걸로 점검 권장)
```powershell
python tts_pipeline.py dryrun 대본.txt
```
전체 줄 수 / style 폴백 목록 / 폴백 감정 카운트 / cast 미등록 화자 를 출력.
**API 호출·파일 생성 없음.**

### ② TTS 음성 생성
```powershell
python tts_pipeline.py tts 대본.txt
```
→ `오디오/001.mp3, 002.mp3 …` 생성 (각 줄마다 역할·style·엔진 로그 출력).

### ③ 성우 목록 조회 (세팅용)
```powershell
python tts_pipeline.py list
```
→ Supertone 성우별 voice_id / 지원 style 확인. `voice_map.json` 채울 때 사용.

### ④ 영상 매칭 + 자막 생성
```powershell
python tts_pipeline.py match 대본.txt
```
전제: `영상클립/001.mp4 …` 와 `오디오/001.mp3 …` 가 같은 번호로 준비돼 있을 것.
→ `매칭/001_matched.mp4 …` + `자막/전체.srt`, `자막/001.srt …` 생성.

---

## 5. 실전 작업 순서

1. `대본.txt` 작성 → `character_cast.json` 의 역할에 맞게 화자 이름 등록.
2. `python tts_pipeline.py dryrun 대본.txt` 로 폴백·미등록 화자 점검.
3. `python tts_pipeline.py tts 대본.txt` → `오디오/` 에 mp3 생성.
4. Flow에서 만든 영상을 `영상클립/` 에 **오디오와 같은 번호**(001.mp4 …)로 저장.
5. `python tts_pipeline.py match 대본.txt` → `매칭/` 영상 + `자막/` srt 생성.
6. CapCut에서 `매칭/`의 mp4들과 `자막/전체.srt` 를 불러 최종 편집.

---

## 6. 결과물 위치 요약

| 명령 | 입력 | 출력 위치 |
|---|---|---|
| `tts`   | 대본.txt              | `오디오/NNN.mp3` |
| `match` | 대본.txt + `영상클립/NNN.mp4` + `오디오/NNN.mp3` | `매칭/NNN_matched.mp4`, `자막/전체.srt`, `자막/NNN.srt` |

---

## 7. 조정 포인트 (tts_pipeline.py 상단 상수)

| 상수 | 기본값 | 설명 |
|---|---|---|
| `MATCH_STRATEGY` | `"hybrid"` | 길이맞춤 전략. `hybrid`/`speed`/`pad_trim` |
| `SPEED_TOLERANCE` | `0.10` | 편차 10% 이내는 배속조절, 초과면 프리즈/트림 |
| `SRT_SPLIT` | `True` | 긴 장면 자막을 여러 cue로 분할 |
| `SRT_MAX_CHARS` | `23` | 한글 자막 한 줄 최대 글자수(20~25 권장) |

나레이션 음색은 `voice_map.json` 의 `"나레이션".settings` 값(stability, speed 등)만
바꾸면 된다(코드 수정 불필요).

> `match_legacy` 명령은 예전 `scene_map.json`(여러 오디오를 한 장면으로 묶기) 방식용으로
> 남겨둔 것이며, 평소에는 위 `match` 를 쓴다.
