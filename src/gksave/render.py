"""정적 HTML 표출 (인터랙티브).

export 페이로드를 자기완결형 HTML 한 장으로 렌더. 외부 의존 없음(inline CSS/JS).
데이터를 JSON 으로 embed 하고 vanilla JS 로 렌더 → 검색·정렬·행 클릭 시 거리존별·
타입별 드릴다운. 그대로 열거나 GitHub Pages 등에 올려도 된다.

교란 주의 라벨을 페이지 최상단 배너로 노출한다(설계 D1 결정).
"""

from __future__ import annotations

import json
from typing import Any

IMAGE_CDN = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common"

# 페이지에 그대로 실려 나가는 이미지 JS. tests/test_render.py 가 node 로 이 문자열을
# 직접 실행해 pid 파생과 폴백 체인을 검증하므로, DOM 에 의존하는 코드를 넣지 말 것.
IMAGE_JS = r"""
const CDN='https://fco.dn.nexoncdn.co.kr/live/externalAssets/common';
// pid 는 spid 뒤 6자리이고 선행 0 을 지워야 한다. p000488.png 는 403, p488.png 는 200.
const pidOf=spid=>String(spid).slice(-6).replace(/^0+/,'');
const portraitUrl=spid=>CDN+'/players/p'+pidOf(spid)+'.png';
const actionUrl=spid=>CDN+'/playersAction/p'+spid+'.png';
// 목록 썸네일은 커버리지 100% 인 얼굴만 쓴다. 액션샷은 38% 가 없어 403 이 쏟아진다.
const thumbUrl=portraitUrl;
const PLACEHOLDER='data:image/svg+xml;utf8,'+encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'+
  '<circle cx="64" cy="46" r="26" fill="#26305a"/>'+
  '<path d="M12 128c0-31 23-52 52-52s52 21 52 52z" fill="#26305a"/></svg>');
// data-fb 가 있으면 그쪽으로 한 번 더 시도하고, 없으면 플레이스홀더에서 멈춘다.
function imgFallback(el){
  el.onerror=null;
  const fb=el.dataset.fb;
  if(fb){el.removeAttribute('data-fb');el.onerror=()=>imgFallback(el);el.src=fb;}
  else{el.src=PLACEHOLDER;}
}
"""

# 리더보드·비교탭이 공유하는 이름 검색 규칙. tests/test_render.py 가 node 로 직접 실행한다.
FILTER_JS = r"""
// 빈 질의는 전부 통과, 대소문자 무시 부분일치.
const matchName=(name,q)=>!q||String(name==null?'':name).toLowerCase().includes(q);
"""


def build_html(payload: dict[str, Any]) -> str:
    page = {
        "generated_at": str(payload.get("generated_at", "")),
        "gate": payload.get("gate_min_matches"),
        "since": payload.get("since"),
        "date_range": payload.get("date_range", {}),
        "total_collected_matches": payload.get("total_collected_matches", 0),
        "warning": payload.get("warning", ""),
        "grade_effect": payload.get("grade_effect", {}),
        "leaderboard": payload.get("leaderboard", []),
        "same_player": payload.get("same_player", []),
    }
    # <script> 탈출 방지: '<' → <
    data_json = json.dumps(page, ensure_ascii=False).replace("<", "\\u003c")
    return (
        _TEMPLATE.replace("__IMAGE_JS__", IMAGE_JS)
        .replace("__FILTER_JS__", FILTER_JS)
        .replace("__DATA__", data_json)
    )


_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FC온라인 GK 선방률 리더보드</title>
<style>
  :root{ --bg:#04060f; --panel:#0b1024; --panel2:#0e1533; --line:#1c2444;
         --gold:#f0d17a; --gold2:#caa966; --text:#eaeefb; --mut:#8792ac; }
  *{box-sizing:border-box}
  html{background:var(--bg)}
  body{font-family:'Pretendard',-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
       max-width:1080px;margin:0 auto;padding:26px 16px 60px;color:var(--text);position:relative;
       background:
         radial-gradient(1200px 520px at 50% -10%,rgba(240,209,122,.14),transparent 60%),
         radial-gradient(900px 480px at 12% 4%,rgba(70,110,230,.10),transparent 58%),
         radial-gradient(900px 480px at 88% 4%,rgba(240,209,122,.06),transparent 58%),
         var(--bg)}
  /* 별(starfield) — 화면 고정, 콘텐츠 뒤 */
  body::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background-repeat:no-repeat;
    background:
      radial-gradient(1.6px 1.6px at 18% 24%,rgba(240,209,122,.55),transparent),
      radial-gradient(1.4px 1.4px at 68% 16%,rgba(234,238,251,.5),transparent),
      radial-gradient(1.2px 1.2px at 42% 62%,rgba(240,209,122,.4),transparent),
      radial-gradient(1.6px 1.6px at 84% 54%,rgba(234,238,251,.42),transparent),
      radial-gradient(1.1px 1.1px at 9% 72%,rgba(240,209,122,.36),transparent),
      radial-gradient(1.3px 1.3px at 55% 40%,rgba(234,238,251,.34),transparent),
      radial-gradient(1.5px 1.5px at 92% 20%,rgba(240,209,122,.46),transparent),
      radial-gradient(1.1px 1.1px at 30% 88%,rgba(234,238,251,.3),transparent),
      radial-gradient(1.3px 1.3px at 76% 82%,rgba(240,209,122,.32),transparent),
      radial-gradient(1.2px 1.2px at 6% 40%,rgba(234,238,251,.3),transparent),
      radial-gradient(1.4px 1.4px at 48% 10%,rgba(240,209,122,.4),transparent),
      radial-gradient(1.1px 1.1px at 62% 92%,rgba(234,238,251,.28),transparent)}
  h1{font-size:1.7rem;font-weight:800;margin:0 0 4px;letter-spacing:-.01em}
  h1 .star{color:var(--gold);margin-right:8px}
  h1 .t{background:linear-gradient(90deg,#fff,var(--gold));-webkit-background-clip:text;background-clip:text;color:transparent}
  h2{font-size:1.15rem;font-weight:700;margin:30px 0 8px;padding-left:10px;border-left:3px solid var(--gold)}
  h4{margin:0 0 8px;font-size:.9rem;color:var(--gold2);font-weight:700}
  .meta{color:var(--mut);font-size:.85rem}
  .warn{background:rgba(240,209,122,.07);border:1px solid rgba(240,209,122,.28);border-radius:10px;
        padding:12px 14px;font-size:.88rem;line-height:1.55;margin:16px 0;color:#e7d5a8}
  .warn b{color:var(--gold)}
  .controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:14px 0 6px}
  .controls input{padding:9px 12px;border:1px solid var(--line);border-radius:9px;font-size:.9rem;
        flex:1;min-width:170px;background:var(--panel);color:var(--text);font-family:inherit}
  .controls input::placeholder{color:#5a6483}
  .controls .lab{color:var(--mut);font-size:.82rem}
  button.sort{padding:7px 13px;border:1px solid var(--line);background:var(--panel);color:var(--mut);
        border-radius:9px;cursor:pointer;font-size:.85rem;font-family:inherit;transition:.15s}
  button.sort:hover{border-color:var(--gold2);color:var(--text)}
  button.sort.active{background:linear-gradient(180deg,var(--gold),var(--gold2));color:#1a1405;
        border-color:var(--gold);font-weight:700}
  table{width:100%;border-collapse:collapse;font-size:.92rem}
  th,td{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left}
  thead th{position:sticky;top:0;background:#070b1c;color:var(--gold2);font-size:.78rem;
        font-weight:700;letter-spacing:.02em;z-index:2}
  td.rank{color:var(--gold2);width:2.4rem;font-weight:700;font-variant-numeric:tabular-nums}
  td.pct{font-variant-numeric:tabular-nums;font-weight:700;color:var(--gold)}
  td.num,td.season{color:var(--mut);font-variant-numeric:tabular-nums}
  tr.row{cursor:pointer;transition:background .12s}
  tr.row:hover{background:var(--panel2)}
  tr.detail>td{background:#070c1e;padding:14px 18px}
  .detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
  @media(max-width:640px){.detail-grid{grid-template-columns:1fr}}
  .zbar{display:grid;grid-template-columns:130px 54px 1fr 64px;align-items:center;gap:9px;margin:4px 0;font-size:.82rem}
  .zbar .zt{font-weight:700;font-variant-numeric:tabular-nums;color:var(--gold)}
  .zbar .zbg{background:rgba(255,255,255,.08);border-radius:5px;height:9px;overflow:hidden}
  .zbar .zf{display:block;height:100%;background:linear-gradient(90deg,var(--gold2),var(--gold))}
  .zbar .zn{color:var(--mut);text-align:right;font-variant-numeric:tabular-nums}
  table.mini{font-size:.84rem}
  table.mini td,table.mini th{padding:5px 9px;border-bottom:1px solid var(--line)}
  table.mini th{color:var(--gold2);font-weight:700}
  .stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:5px 18px;font-size:.84rem}
  .stat{display:flex;justify-content:space-between;border-bottom:1px dotted var(--line);padding:4px 0}
  .stat span{color:var(--mut)}
  .stat b{font-variant-numeric:tabular-nums;color:var(--text)}
  .muted{color:var(--mut);font-size:.85rem}
  details{margin:5px 0;border-bottom:1px solid var(--line)}
  summary{cursor:pointer;font-weight:700;padding:7px 0;color:var(--text)}
  summary:hover{color:var(--gold)}
  .empty{text-align:center;color:var(--mut);padding:24px}
  footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);
         color:var(--mut);font-size:.8rem;line-height:1.7}
  footer a{color:var(--gold2);text-decoration:none}
  /* 상단 상시 배너(주의 라벨 + 강화효과) */
  .banner{background:rgba(240,209,122,.07);border:1px solid rgba(240,209,122,.28);border-radius:10px;
        padding:11px 14px;font-size:.86rem;line-height:1.5;margin:14px 0 4px;color:#e7d5a8}
  .banner b{color:var(--gold)}
  /* 탭 */
  .tabs{display:flex;gap:6px;margin:16px 0 4px;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .tab{padding:9px 15px;border:1px solid transparent;border-bottom:none;border-radius:9px 9px 0 0;
        background:transparent;color:var(--mut);cursor:pointer;font-size:.9rem;font-family:inherit;
        font-weight:700;transition:.15s;margin-bottom:-1px}
  .tab:hover{color:var(--text)}
  .tab.active{color:#1a1405;background:linear-gradient(180deg,var(--gold),var(--gold2));
        border-color:var(--gold)}
  .panel{display:none}
  .panel.active{display:block}
  /* 더보기 */
  .more{display:flex;gap:8px;justify-content:center;margin:16px 0 4px}
  .more button{padding:8px 18px;border:1px solid var(--line);background:var(--panel);color:var(--gold2);
        border-radius:9px;cursor:pointer;font-size:.86rem;font-family:inherit;font-weight:700;transition:.15s}
  .more button:hover{border-color:var(--gold2);color:var(--text)}
  /* 지표 설명 */
  .help p{font-size:.9rem;line-height:1.7;color:var(--text)}
  .help .lead{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
  .help .lead dt{color:var(--gold);font-weight:700;margin-top:10px}
  .help .lead dt:first-child{margin-top:0}
  .help .lead dd{margin:2px 0 0;color:var(--text);font-size:.88rem;line-height:1.6}
  .help ol.usage{margin:8px 0 0;padding-left:1.2em;font-size:.9rem;line-height:1.7}
  .help details{margin:6px 0}
  .help details p{font-size:.86rem;color:var(--mut);margin:4px 0 8px}
  .help dl.terms dt{color:var(--gold2);font-weight:700;margin-top:9px;font-size:.86rem}
  .help dl.terms dd{margin:1px 0 0;color:var(--mut);font-size:.85rem;line-height:1.55}
  /* 선수 이미지 — 투명 PNG 라 어두운 원판을 깔고 골드 링을 두른다.
     얼굴 중심이 캔버스 (62,68) 이라 42% 로 위로 당겨야 원 안에 들어온다. */
  .thumb{width:36px;height:36px;border-radius:50%;object-fit:cover;object-position:50% 42%;
        background:#141a35;border:1px solid var(--gold2);flex:0 0 auto}
  .pcell{display:flex;align-items:center;gap:9px;min-width:0}
  /* min-width:0 이 없으면 flex 아이템이 콘텐츠 폭 아래로 못 줄어 말줄임이 안 걸린다.
     한글은 공백이 없어 줄바꿈을 허용하면 글자 단위로 쪼개진다 → 반드시 nowrap + 말줄임. */
  .pcell .pn{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  summary .pcell{display:inline-flex;vertical-align:middle}
  /* 상세 히어로 — 원본이 128px 이라 112px 를 넘겨 키우지 않는다 */
  .hero{display:flex;align-items:center;gap:16px;padding-bottom:14px;margin-bottom:14px;
        border-bottom:1px solid var(--line)}
  .hero-img{width:112px;height:112px;border-radius:12px;object-fit:cover;flex:0 0 auto;
        background:radial-gradient(circle at 50% 34%,#1b2242,#0b1024);border:1px solid var(--line)}
  .hero-meta h3{margin:0 0 3px;font-size:1.05rem;font-weight:800;letter-spacing:-.01em}
  .hero-meta .sub{margin:0 0 9px;color:var(--mut);font-size:.85rem}
  .hero-meta .big{font-size:1.5rem;font-weight:800;color:var(--gold);font-variant-numeric:tabular-nums}
  .hero-meta .big small{margin-left:7px;font-size:.78rem;font-weight:600;color:var(--mut)}
  /* 표는 375px 에서 606px 다. 본문이 아니라 표만 가로로 스크롤시킨다.
     overflow-x 를 상시로 걸면 overflow-y 가 auto 로 승격돼 sticky thead 가 깨진다.
     데스크톱은 표가 안 넘치므로 모바일에서만 감싼다. */
  @media(max-width:640px){
    .tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
    .hero{flex-direction:column;align-items:flex-start;gap:12px}
    .thumb{width:28px;height:28px}
    /* 선수 컬럼 폭을 묶어 긴 이름을 말줄임한다(줄바꿈 금지 — 세로로 쪼개진다) */
    .pcell{gap:7px;max-width:118px}
    th,td{padding:9px 8px}
  }
</style></head><body>
<h1><span class="star">★</span><span class="t">FC온라인 골키퍼 선방률 리더보드</span></h1>
<p class="meta" id="meta"></p>

<div class="banner">
  <div id="warn"></div>
</div>

<div class="tabs">
  <button class="tab active" data-tab="lb">리더보드</button>
  <button class="tab" data-tab="sp">동일 선수 비교</button>
  <button class="tab" data-tab="help">지표 설명 · 사용법</button>
</div>

<!-- 탭 1: 리더보드 -->
<div class="panel active" id="panel-lb">
  <div class="controls">
    <input id="search" placeholder="선수 이름 검색…">
    <span class="lab">정렬</span>
    <button class="sort active" data-sort="save_pct">선방률</button>
    <button class="sort" data-sort="gsax_per_shot">GSAx</button>
    <button class="sort" data-sort="gsax_ex_short_per_shot">GSAx(초근제외)</button>
    <button class="sort" data-sort="matches">표본</button>
  </div>
  <p class="muted">행을 클릭하면 그 카드의 <b>거리 구간별·슛 타입별</b> 선방률이 펼쳐집니다. 용어가 낯설면 <b>지표 설명</b> 탭을 보세요.</p>
  <div class="tw">
    <table id="lb">
      <thead><tr><th>#</th><th>선수</th><th>시즌</th><th>강화</th><th>선방률</th><th id="gsaxHdr">GSAx/100</th><th>표본</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <div class="more" id="more"></div>
</div>

<!-- 탭 2: 동일 선수 비교 -->
<div class="panel" id="panel-sp">
  <div class="controls">
    <input id="spSearch" placeholder="선수 이름 검색…">
    <span class="lab" id="spCount"></span>
  </div>
  <p class="muted">같은 선수의 시즌·강화별 선방률(raw)과 GSAx. (여전히 raw 는 유저 교란 포함)</p>
  <div id="sp"></div>
  <p class="empty" id="spEmpty" style="display:none">해당하는 선수가 없습니다.</p>
</div>

<!-- 탭 3: 지표 설명 · 사용법 -->
<div class="panel help" id="panel-help">
  <h2>이 페이지 사용법</h2>
  <ol class="usage">
    <li><b>리더보드 탭</b>에서 선방률·GSAx·표본으로 정렬하고, 검색창에 선수 이름을 넣어 찾습니다. 기본은 상위 100장만 보이고 <b>더 보기</b>로 펼칩니다.</li>
    <li>표의 <b>행을 클릭</b>하면 그 카드의 거리 구간별·슛 타입별 선방률과 세부 스탯이 펼쳐집니다.</li>
    <li><b>동일 선수 비교 탭</b>에서 같은 선수의 시즌·강화별 성적을 나란히 봅니다.</li>
  </ol>

  <h2>핵심 지표</h2>
  <dl class="lead">
    <dt>선방률</dt>
    <dd>상대의 유효슛 중 막아낸 비율. <b>선방 ÷ (선방 + 실점)</b>으로 계산합니다. 값이 높을수록 잘 막은 것. 단, 이 순위의 raw 선방률에는 카드 성능뿐 아니라 <b>그 카드를 쓴 유저의 실력·수비 라인·상대 슛 난이도</b>가 섞여 있어 '카드 추천'이 아닙니다.</dd>
    <dt>GSAx / 100</dt>
    <dd>Goals Saved Above Expected — <b>슛 난이도를 보정</b>한 지표입니다. 거리·각도로 계산한 '기대 실점'보다 실제로 얼마나 더(또는 덜) 막았는지를 유효슛 100개 기준으로 환산합니다. <b>+면 기대보다 선방, −면 기대보다 실점</b>. 유저 실력 교란을 줄인, 선방률보다 공정한 비교값입니다.</dd>
    <dt>GSAx(초근제외)</dt>
    <dd>초근거리(5m 미만) 슛을 뺀 GSAx. 골문 앞 난사처럼 GK가 어쩔 수 없는 상황을 제외해, 포지셔닝·반응 능력을 더 잘 드러냅니다.</dd>
    <dt>표본 · 게이트</dt>
    <dd>표본 = 그 카드가 집계된 경기 수. 표본이 적으면 우연(뽀록)일 수 있어 신뢰도가 낮습니다. 그래서 최소 <b id="gateN"></b>경기 이상(게이트)인 카드만 순위에 올립니다. 순위를 볼 때 표본 수를 꼭 함께 보세요.</dd>
  </dl>

  <h2>세부 지표 (행 클릭 시 펼쳐지는 값)</h2>
  <details><summary>거리 구간별 · 슛 타입별</summary>
    <dl class="terms">
      <dt>거리 구간별</dt><dd>실점·선방을 골문과의 근사 거리(초근/근/중/원거리)로 나눈 선방률. 어느 거리에 강하고 약한지 보여줍니다.</dd>
      <dt>슛 타입별</dt><dd>노멀·감아차기·헤더·발리 등 슛 종류별 선방률. 표본 3개 미만 타입은 노이즈라 숨깁니다.</dd>
    </dl>
  </details>
  <details><summary>상황 · 수비 맥락 · GK 스탯</summary>
    <dl class="terms">
      <dt>노출도(경기당 유효슛)</dt><dd>한 경기에 GK가 마주한 평균 유효슛 수. 높을수록 수비 부담이 큰 환경.</dd>
      <dt>마주한 / 실점 평균 거리</dt><dd>받은 슛과 먹힌 슛의 평균 거리(근사 미터). 둘의 차이가 클수록 먼 슛을 잘 막았다는 뜻.</dd>
      <dt>박스 안 / 밖 선방률</dt><dd>페널티 박스 안팎으로 나눈 선방률.</dd>
      <dt>1대1 선방률</dt><dd>도움 없이 개인 돌파로 들어온 슛(단독 찬스)에 대한 선방률.</dd>
      <dt>연계·컷백 선방률</dt><dd>도움(어시스트)을 받은 슛에 대한 선방률.</dd>
      <dt>경기당 평균 평점 · 패스 성공률</dt><dd>게임이 부여한 GK 평점과 패스 성공률.</dd>
    </dl>
  </details>
  <details><summary>강화 효과 (유저 내 비교)</summary>
    <p id="geDetail"></p>
    <p>같은 유저가 같은 카드를 강화단계만 올렸을 때 선방률이 얼마나 변하는지를 짝지어 비교한 값입니다. 유저 실력 차이를 제거했기 때문에 <b>강화 그 자체의 효과</b>에 가장 가깝습니다.</p>
  </details>

  <h2>주의</h2>
  <div class="warn" id="warnFull"></div>
</div>

<footer>
  데이터 출처: 본 분석의 모든 경기 데이터는 <b>NEXON Open API</b>
  (<a href="https://open.api.nexon.com" target="_blank" rel="noopener">open.api.nexon.com</a>)
  의 FC 온라인 매치 데이터를 수집·가공해 제공합니다.<br>
  넥슨 및 FC 온라인과 무관한 비공식 팬 분석이며, 지표는 공개 API 데이터에 기반한 추정치입니다.
</footer>

<script id="gk-data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('gk-data').textContent);
const PAGE=100;
let sortKey='save_pct', q='', limit=PAGE;
const pct=v=>v==null?'N/A':(v*100).toFixed(1)+'%';
const gps=v=>v==null?'':(v*100>=0?'+':'')+(v*100).toFixed(1);
const esc=s=>{const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;};
// esc 는 따옴표를 escape 하지 않는다 → 속성값에 쓸 땐 escAttr 로 감싼다.
const escAttr=s=>esc(s).replace(/"/g,'&quot;');
__IMAGE_JS__
__FILTER_JS__
// 썸네일은 얼굴 → 플레이스홀더 2단. 히어로는 액션샷 → 얼굴 → 플레이스홀더 3단(data-fb).
const thumbImg=(spid,name)=>spid==null?'':
  `<img class="thumb" src="${thumbUrl(spid)}" alt="${escAttr(name||'')}" width="36" height="36" `+
  `loading="lazy" decoding="async" onerror="imgFallback(this)">`;
const heroImg=(spid,name)=>spid==null?'':
  `<img class="hero-img" src="${actionUrl(spid)}" data-fb="${portraitUrl(spid)}" `+
  `alt="${escAttr(name||'')}" width="112" height="112" loading="lazy" decoding="async" `+
  `onerror="imgFallback(this)">`;

const dr=D.date_range||{};
// date_range 는 since 를 반영한 실제 집계 창이다. 원시 ISO since 를 덧붙이면 중복이자 노이즈.
const period=(dr.min&&dr.max)?`데이터 기간 ${dr.min} ~ ${dr.max} · `:'';
const totalMatches = Number(D.total_collected_matches || 0).toLocaleString('ko-KR');
document.getElementById('meta').textContent =
  `${period}총 수집 경기 ${totalMatches}건 · 게이트 ${D.gate}경기↑ · ${D.leaderboard.length}장`;
document.getElementById('warn').innerHTML =
  '<b>⚠️ 읽는 법:</b> 이 순위는 raw 선방률이라 카드 성능에 <b>유저 실력</b>이 섞여 있어 \'카드 추천\'이 아닙니다. 용어·자세한 설명은 <b>지표 설명</b> 탭.';
document.getElementById('warnFull').innerHTML = esc(D.warning);
var gEl=document.getElementById('gateN'); if(gEl) gEl.textContent=D.gate;

function detailHtml(c){
  const zones=(c.zones||[]).map(z=>
    `<div class="zbar"><span>${esc(z.zone)}</span>`+
    `<span class="zt">${pct(z.save_pct)}</span>`+
    `<span class="zbg"><span class="zf" style="width:${z.save_pct==null?0:(z.save_pct*100).toFixed(0)}%"></span></span>`+
    `<span class="zn">${z.saves}/${z.shots}</span></div>`).join('') || '<span class="muted">좌표 없음</span>';
  const types=(c.types||[]).filter(t=>t.shots>=3).map(t=>
    `<tr><td>${esc(t.name)}</td><td class="pct">${pct(t.save_pct)}</td><td class="num">${t.saves}/${t.shots}</td></tr>`).join('');
  const e=c.extras||{};
  const stat=(label,val)=>`<div class="stat"><span>${label}</span><b>${val}</b></div>`;
  const m=v=>v==null?'-':v+'m';
  const statsHtml=
    stat('노출도(경기당 유효슛)', e.exposure==null?'-':e.exposure.toFixed(1))+
    stat('마주한 평균 거리', m(e.faced_dist_m))+
    stat('실점 평균 거리', m(e.conceded_dist_m))+
    stat('박스 안 선방률', pct(e.in_pen_save))+
    stat('박스 밖 선방률', pct(e.out_pen_save))+
    stat('1대1 선방률', pct(e.unassisted_save))+
    stat('연계·컷백 선방률', pct(e.assisted_save))+
    stat('경기당 평균 평점', e.gk_rating==null?'-':e.gk_rating)+
    stat('패스 성공률', pct(e.pass_pct));
    // 공중볼(aerial)은 게임상 GK에 거의 안 잡혀(중앙값 1) 노이즈 → 화면 미표시. 데이터는 DB 유지.
  const hero=
    `<div class="hero">${heroImg(c.gk_sp_id,c.player_name)}<div class="hero-meta">`+
    `<h3>${esc(c.player_name||('spId '+c.gk_sp_id))}</h3>`+
    `<p class="sub">${esc(c.season_name||'')} · ${c.grade}강</p>`+
    `<div class="big">${pct(c.save_pct)}<small>선방률 · 표본 ${c.matches}경기</small></div>`+
    `</div></div>`;
  return hero+
         `<div class="detail-grid"><div><h4>거리 구간별 (근사 미터)</h4>${zones}</div>`+
         `<div><h4>슛 타입별</h4><table class="mini"><tbody>${types}</tbody></table></div></div>`+
         `<h4 style="margin-top:14px">상황 · 수비 맥락 · GK 스탯</h4><div class="stats">${statsHtml}</div>`;
}
function toggle(tr,c){
  const nx=tr.nextElementSibling;
  if(nx&&nx.classList.contains('detail')){nx.remove();return;}
  const d=document.createElement('tr'); d.className='detail';
  d.innerHTML=`<td colspan="7">${detailHtml(c)}</td>`; tr.after(d);
}
function render(){
  const tb=document.querySelector('#lb tbody'); tb.innerHTML='';
  const more=document.getElementById('more'); more.innerHTML='';
  let rows=D.leaderboard.filter(c=>matchName(c.player_name,q));
  rows=rows.slice().sort((a,b)=>{
    const av=a[sortKey], bv=b[sortKey];
    if(av==null&&bv==null)return 0; if(av==null)return 1; if(bv==null)return -1; return bv-av;
  });
  if(!rows.length){tb.innerHTML='<tr><td colspan="7" class="empty">해당하는 카드가 없습니다.</td></tr>';return;}
  const gf = sortKey.indexOf('gsax')===0 ? sortKey : 'gsax_per_shot';  // GSAx 컬럼은 활성 모드 값
  document.getElementById('gsaxHdr').textContent =
    gf==='gsax_ex_short_per_shot' ? 'GSAx/100(초근×)' : 'GSAx/100';
  // 검색 중이면 전체에서 찾도록 캡 무시, 아니면 상위 limit 장만 그린다(초기 로드 경량화)
  const vis = q ? rows : rows.slice(0, limit);
  vis.forEach((c,i)=>{
    const tr=document.createElement('tr'); tr.className='row';
    tr.innerHTML=`<td class="rank">${i+1}</td>`+
      `<td><div class="pcell">${thumbImg(c.gk_sp_id,c.player_name)}`+
      `<span class="pn">${esc(c.player_name||('spId '+c.gk_sp_id))}</span></div></td>`+
      `<td class="season">${esc(c.season_name||'')}</td><td class="num">${c.grade}강</td>`+
      `<td class="pct">${pct(c.save_pct)}</td><td class="num">${gps(c[gf])}</td>`+
      `<td class="num">${c.matches}</td>`;
    tr.onclick=()=>toggle(tr,c); tb.appendChild(tr);
  });
  if(q) return;  // 검색 중엔 더보기/접기 없음
  if(rows.length>limit){
    const b1=document.createElement('button');
    b1.textContent=`더 보기 (${vis.length}/${rows.length})`;
    b1.onclick=()=>{limit+=PAGE; render();};
    const b2=document.createElement('button');
    b2.textContent='전체 보기';
    b2.onclick=()=>{limit=rows.length; render();};
    more.appendChild(b1); more.appendChild(b2);
  }else if(limit>PAGE){
    const b0=document.createElement('button');
    b0.textContent='접기 (상위 100)';
    b0.onclick=()=>{limit=PAGE; render(); document.getElementById('panel-lb').scrollIntoView({behavior:'smooth',block:'start'});};
    more.appendChild(b0);
  }
}
document.getElementById('search').oninput=e=>{q=e.target.value.trim().toLowerCase();limit=PAGE;render();};
document.querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-sort]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); sortKey=b.dataset.sort; limit=PAGE; render();
});
// 탭 전환
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('panel-'+t.dataset.tab).classList.add('active');
});

// 강화 효과는 귀무 결과다(신뢰구간이 0 포함). 상단 배너에 상시 고정하면 '카드 추천 아님'
// 경고의 주목도를 갉아먹어, 지표 설명 탭에서만 전문을 보여준다.
const ge=D.grade_effect||{}, de=ge.mean_save_pct_delta_per_grade, se=ge.se_per_grade;
const pp=v=>(v*100>=0?'+':'')+(v*100).toFixed(2)+'%p';
let geLong;
if(de==null){
  geLong='표본이 부족해 강화 효과를 추정할 수 없습니다.';
}else{
  const lo=se==null?null:de-1.96*se, hi=se==null?null:de+1.96*se;
  const ciTxt=(lo!=null)?`[${pp(lo)}, ${pp(hi)}]`:'';
  const ci=ciTxt?` (95% 신뢰구간 ${ciTxt})`:'';
  const split=`올라간 페어 ${ge.up_pairs||0} : 내려간 페어 ${ge.down_pairs||0}`;
  const nulEffect = (lo!=null && lo<=0 && hi>=0);
  if(nulEffect){
    geLong=`강화단계 1 상승당 평균 선방률 변화는 <b>${pp(de)}</b>지만, 95% 신뢰구간이 ${ciTxt} 로 <b>0을 포함</b>합니다. 즉 이 표본에서 강화단계는 선방률에 <b>유의미한 차이를 만들지 못했습니다</b>(${split} 로 거의 반반, 페어 ${ge.pairs||0}). 표본이 쌓이면 값이 달라질 수 있습니다.`;
  }else{
    geLong=`강화단계 1 상승당 평균 선방률 변화: <b>${pp(de)}</b>${ci} (${split}, 페어 ${ge.pairs||0}).`;
  }
}
document.getElementById('geDetail').innerHTML = geLong;

document.getElementById('sp').innerHTML=(D.same_player||[]).map(g=>{
  const rows=g.cards.map(c=>
    `<tr><td>${esc(c.season_name||'')}</td><td class="num">${c.grade}강</td>`+
    `<td class="pct">${pct(c.save_pct)}</td><td class="num">${gps(c.gsax_per_shot)}</td>`+
    `<td class="num">${c.matches}</td></tr>`).join('');
  // 그룹 내 카드는 시즌만 다르고 pid 는 같다(182그룹 전수 확인) → 첫 카드로 썸네일을 만든다.
  const sp=(g.cards[0]||{}).gk_sp_id;
  return `<details data-name="${escAttr(g.player_name)}"><summary><span class="pcell">${thumbImg(sp,g.player_name)}`+
    `<span class="pn">${esc(g.player_name)}</span></span></summary><table class="mini">`+
    `<thead><tr><th>시즌</th><th>강화</th><th>선방률</th><th>GSAx/100</th><th>표본</th></tr></thead>`+
    `<tbody>${rows}</tbody></table></details>`;
}).join('') || '<span class="muted">비교할 동일선수 데이터가 아직 없습니다.</span>';

// 재렌더 대신 display 토글로 거른다 → 펼쳐둔 그룹이 닫히지 않고, 182개를 다시 그리지도 않는다.
const spEls=[...document.querySelectorAll('#sp details')];
const spCount=document.getElementById('spCount'), spEmpty=document.getElementById('spEmpty');
function spFilter(){
  const q=(document.getElementById('spSearch').value||'').trim().toLowerCase();
  let n=0;
  spEls.forEach(d=>{const ok=matchName(d.dataset.name,q); d.style.display=ok?'':'none'; if(ok)n++;});
  spCount.textContent = spEls.length ? (q?`${n} / ${spEls.length}명`:`${spEls.length}명`) : '';
  spEmpty.style.display = (spEls.length && !n) ? '' : 'none';
}
document.getElementById('spSearch').oninput=spFilter;
spFilter();

render();
</script>
</body></html>
"""
