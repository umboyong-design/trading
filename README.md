# TRADING PULSE Web — 국내·미국 주식 통합 트레이딩 보조 웹앱

> **이번 버전은 Windows 설치형이 아닙니다.** GitHub에 소스 코드를 올려 클라우드(Render 등)에 배포한 뒤, PC/휴대폰에서 HTTPS 주소로 접속하는 웹앱입니다. 아직 실제 배포된 공개 URL은 없습니다.

**검증 상태**: 내부 API/기술분석 단위 테스트 통과. **TradingView 실제 계정 OAuth 및 한국/미국 시세 데이터 응답은 계정이 없어 아직 검증되지 않았습니다.** 검증되지 않은 가격이나 TOP 10은 생성하지 않습니다.

## 주요 기능

- 국내(KR) 및 미국(US) 시장 탭, 거래소 달력/서머타임 반영, 한국시간(KST) 표시
- `TOP 10 지금 분석`을 눌러 TradingView 공식 MCP 베타의 스크리너와 OHLCV로 후보를 랭킹
- 1분, 5분, 10분, 30분봉 기술분석. `10분봉`은 공식 MCP 미지원으로 **확정된 1분봉 10개를 합산**
- EMA(9/21), RSI(14), ATR(14), VWAP, 상대거래량, 직전 고점으로 조건부 매수 감시/매도 검토, 진입 트리거/손절/목표를 표시
- TradingView **공식 차트 위젯** 삽입, 관심종목 수동 추가, 선택한 시장 75초 간격 다시 분석, 브라우저 알림(알림 권한 필요)
- 매수가·매도가는 체결 보장가가 아닌 **조건부 기술적 시나리오**이며, 지연 또는 휴장 시 실시간 신호로 표시하지 않음
- 공용으로 노출되지 않도록 비밀번호 로그인 + 서버 보관 TradingView OAuth 토큰

## 1. 설치 없이 브라우저에서 사용할 수 있도록 배포하기

**준비물**: (1) 무료 GitHub 계정 (2) Render 계정 (3) TradingView 공식 MCP 사용이 가능한 Essential 이상 계정(체험판 제외). 플랫폼 정책과 데이터 요금에 따라 달라질 수 있습니다.

1. GitHub에서 *새 비공개 저장소* `trading-pulse-web`를 만듭니다.
2. 이 압축파일을 풀고 폴더 **안의 모든 코드 파일**(`main.py`, `render.yaml`, `static/` 등)을 GitHub 저장소 최상위에 업로드합니다. `.state` 폴더(토큰 포함)는 업로드하지 않습니다.
3. [Render](https://dashboard.render.com/)에서 **New → Blueprint**를 선택하고 방금 만든 GitHub 저장소를 연결합니다. `render.yaml`이 웹 서비스를 설정합니다.
4. `APP_PASSWORD`에 **안전한 관리자 비밀번호**(16자 이상 권장)를 지정합니다. 이것은 TradingView 비밀번호가 아닌 **웹앱 자체 로그인 비밀번호**입니다.
5. Render에 배포되어 `https://...onrender.com` 같은 고유 주소가 생기면, Render의 **Environment**에서 `APP_BASE_URL` 값을 그 **정확한 HTTPS 주소**로 지정하고 재배포/재시작합니다. 끝에 `/`를 붙이지 마세요.
6. 주소로 접속하여 `APP_PASSWORD`로 로그인한 뒤 **연동 안내 → TradingView 계정 연결**을 클릭합니다. 링크가 보이면 TradingView 사이트에서 로그인하고 앱으로 돌아옵니다.
7. 연결 후 **TOP 10 지금 분석**을 누릅니다. 사용자는 이후부터 Windows 프로그램 실행 없이 브라우저 주소로 접속합니다.

**중요**: Render Free는 앱이 휴면 상태로 전환될 수 있고 저장공간이 **영구 보존되지 않습니다**. 서버 재시작 시 TradingView 재로그인이 필요할 수 있습니다. 안정 운영에는 영구 디스크/DB가 필요합니다. 추가 사용자 권한·암호화된 영구 DB·서버 재시작에 강한 OAuth 동기화 등은 현재 미구현이며, **단일 사용자 전용**입니다.

### Render 환경변수

|이름|필수|예시/설명|
|---|---|---|
|`APP_PASSWORD`|예|웹앱 관리자 비밀번호. 저장소에 넣지 마세요.|
|`APP_SESSION_SECRET`|예|Render Blueprint가 자동 생성하며 세션 쿠키 서명에 사용|
|`APP_BASE_URL`|예|배포된 정확한 HTTPS 주소. 예: `https://trading-pulse-abc.onrender.com`|
|`STATE_DIR`|선택|영구 디스크를 붙인 경우 `/var/data/trading-pulse` 등으로 설정|

`APP_PASSWORD`, `APP_SESSION_SECRET`, TradingView OAuth 토큰을 GitHub/프런트엔드/공유 파일에 저장하지 마세요.

## 2. 핵심 제약 및 주의

**공식 데이터 연결 방식**: TradingView는 웹 위젯에 포함된 시세를 외부 앱에 재사용하도록 제공하는 일반 REST API는 없으며, 별도로 [공식 MCP 베타](https://www.tradingview.com/mcp/docs)를 제공합니다. 이 앱은 MCP 인증·`run_screener`·`get_ohlcv`를 사용합니다. 공식 위젯으로 화면 차트를 표시하되, 차트 DOM/네트워크를 크롤링하지 않습니다. 사용자 계정에 따라 데이터 권한, 실시간/지연 여부, 호출 수 제한이 있을 수 있습니다.

**랭킹 범위**: 미국과 한국 **각 시장 전체 종목을 전수 분석한 TOP 10은 아닙니다.** MCP 스크리너/관심종목에서 추린 후보 중 **최대 14개를 분봉 정밀 분석**하여 10개 순위를 표시합니다. 현재 버전은 백테스트 적중률, 승률, 실시간 틱·호가, 자동 매수/매도 주문을 제공하지 않습니다.

**신호 새로고침**: 데이터는 확정된 **1분 봉** 및 30분 봉에 기반합니다. 75초 간격 재분석은 해당 웹 페이지를 열고 자동 재분석을 켠 동안만 실행됩니다. MCP 시세가 150초 이상 지연되거나 정규장 외 시간에는 실시간 신호로 표시하지 않습니다. 계정의 거래소 시세 자체가 지연됐을 가능성은 별도로 확인해야 합니다.

**한계**: 무료 호스팅 슬립/스토리지 초기화, OAuth 세션 만료, MCP 요금제/호출 제한, 미국 프리/애프터마켓과 한국의 거래소 권한, 최소 틱, 슬리피지, 호가 스프레드, 실제 체결 여부는 별도 관리해야 합니다. 분석은 투자 자문·주문 실행을 대체하지 않습니다.

## 3. 개발자용 테스트 (배포 시 일반 사용자는 필요 없음)

```bash
python -m pip install -r requirements.txt
python -m pip install pytest httpx
python -m pytest -q tests
```

클라우드 Start 명령: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`. **반드시 workers=1**: 브라우저 OAuth의 인증 중 임시 상태는 프로세스 메모리에 있기 때문입니다. 서비스 여러 인스턴스/서버를 쓰려면 Redis나 DB 기반 OAuth 상태 저장으로 확장해야 합니다.

브라우저에서 UI를 미리 확인하려면 **`webapp-preview.html`**을 열 수 있습니다. 이 파일은 UI 미리보기이며 **실제 실시간 시세나 종목 추천을 발생시키지 않습니다**.

## 출처

- TradingView MCP: https://www.tradingview.com/mcp/docs
- TradingView 위젯: https://www.tradingview.com/widget-docs/getting-started/
- MCP OAuth SDK: https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/oauth-clients.md
- Render Blueprint: https://render.com/docs/infrastructure-as-code
