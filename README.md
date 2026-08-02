# 카카오톡 무료 테크 뉴스 브리핑

노트북과 휴대폰이 꺼져 있어도 GitHub Actions가 매일 **한국시간 오전 8시 7분**에 최신 IT·개발·AI·OpenAI·Claude 뉴스를 모아 카카오톡 `나와의 채팅`으로 보냅니다.

## 특징

- GitHub Actions 무료 제공량과 공개 RSS만 사용해 비용 0원
- 기사 제목·RSS 본문에서 핵심 문장을 뽑아 짧게 요약(유료 AI API 불필요)
- 카테고리별 기사를 고르게 선택하고 제목 중복 제거
- 최근 14일 전송 이력을 보관해 다음 날 같은 기사 반복 전송 방지
- 공개 저장소의 예약 실행이 60일 무활동으로 중지되지 않도록 월 1회 자동 유지
- 카카오 액세스 토큰은 실행할 때마다 자동 발급
- 약 2개월 주기의 카카오 리프레시 토큰 교체도 GitHub Secret에 자동 반영

## 1. 카카오 앱 준비

1. [Kakao Developers](https://developers.kakao.com/)에서 애플리케이션을 만듭니다.
2. **앱 > 플랫폼 키**에서 `REST API 키`를 확인합니다.
3. **카카오 로그인**을 활성화하고 Redirect URI에 `https://example.com/oauth`를 등록합니다.
4. **카카오 로그인 > 동의항목**에서 `카카오톡 메시지 전송(talk_message)`을 선택 동의로 설정합니다.
5. **제품 링크 관리 > 웹 도메인**에 `https://news.google.com`과 `https://github.blog`를 등록합니다.
6. 아래 URL의 값을 바꿔 브라우저에서 열고 동의합니다.

```text
https://kauth.kakao.com/oauth/authorize?client_id=REST_API_KEY&redirect_uri=https%3A%2F%2Fexample.com%2Foauth&response_type=code&scope=talk_message
```

7. 이동된 주소창의 `?code=...` 값을 복사한 뒤, 1회용 인가 코드를 즉시 토큰으로 교환합니다. Client Secret 기능이 켜져 있으면 마지막 줄도 포함합니다.

```bash
curl -X POST https://kauth.kakao.com/oauth/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=authorization_code' \
  -d 'client_id=REST_API_KEY' \
  -d 'redirect_uri=https://example.com/oauth' \
  -d 'code=인가_코드' \
  -d 'client_secret=CLIENT_SECRET'
```

응답의 `refresh_token`을 보관합니다. 인가 코드는 한 번만 쓸 수 있으며 짧은 시간 안에 만료됩니다.

## 2. GitHub 저장소와 Secrets 설정

이 폴더를 GitHub 저장소로 push한 뒤 **Settings > Secrets and variables > Actions**에 다음 Repository secrets를 만듭니다.

| Secret | 값 |
|---|---|
| `KAKAO_REST_API_KEY` | 카카오 REST API 키 |
| `KAKAO_CLIENT_SECRET` | 카카오 Client Secret. 기능을 껐다면 빈 값 |
| `KAKAO_REFRESH_TOKEN` | 위에서 받은 refresh_token |
| `SECRET_ROTATION_PAT` | 아래 설명의 GitHub fine-grained PAT |

`SECRET_ROTATION_PAT`은 이 저장소만 선택하고 **Repository permissions > Secrets: Read and write** 권한만 부여해 만듭니다. 카카오가 새 refresh token을 반환할 때 Secret을 안전하게 교체하는 용도입니다.

## 3. 첫 실행

GitHub 저장소의 **Actions > Kakao tech news digest > Run workflow**에서 먼저 `dry_run`을 체크해 뉴스 수집 결과를 로그로 확인합니다. 정상이면 체크를 끄고 다시 실행합니다.

스케줄은 `.github/workflows/news-digest.yml`의 cron으로 바꿀 수 있습니다. GitHub cron은 UTC이므로 한국시간에서 9시간을 빼야 합니다.

## 로컬 확인

```bash
python -m venv .venv
pip install -r requirements.txt pytest
DRY_RUN=1 python -m newsbot.main
pytest -q
```

PowerShell에서는 실행 전에 `$env:DRY_RUN='1'`을 사용합니다.

## 무료 사용 관련 주의

- 공개 저장소의 표준 GitHub Actions는 무료입니다. 비공개 저장소의 GitHub Free 계정은 월 2,000분 범위에서 무료이며 이 작업은 하루 수 분 이내입니다.
- 2026년 7월 30일 GitHub Models가 종료되어, 외부 AI API 없이 동작하는 핵심 문장 추출 방식을 사용합니다.
- 공개 저장소는 60일간 활동이 없으면 예약 workflow가 중지될 수 있어, 이 프로젝트는 월 1회 `.schedule-keepalive`를 자동 갱신합니다.
- 키와 토큰을 코드나 로그에 붙여 넣지 말고 반드시 GitHub Secrets에 저장하세요.
