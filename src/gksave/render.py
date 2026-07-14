"""정적 HTML 표출 (인터랙티브).

export 페이로드를 자기완결형 HTML 한 장으로 렌더. CSS/JS 는 전부 inline.
외부 의존은 둘뿐이다 — 넥슨 애널리틱스 스크립트(약관), 선수 이미지 CDN(폴백 있음).
데이터를 JSON 으로 embed 하고 vanilla JS 로 렌더 → 검색·정렬·행 클릭 시 거리존별·
타입별 드릴다운. 그대로 열거나 GitHub Pages 등에 올려도 된다.

교란 주의 라벨을 페이지 최상단 배너로 노출한다(설계 D1 결정).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

IMAGE_CDN = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common"

# 브라우저 탭 아이콘(파비콘). 자기완결형 유지를 위해 64px PNG 를 data URI 로 임베드한다.
# 원본은 assets/gk-icon.png. 흰 캔버스는 네 모서리 flood-fill 로 투명화(공·장갑 흰색은 검은
# 라운드 사각형에 둘러싸여 안전) 후 사각형으로 크롭·64px 축소 → base64 를 favicon.txt 로 커밋.
FAVICON = (Path(__file__).parent / "favicon.txt").read_text().strip()

# 페이지에 그대로 실려 나가는 이미지 JS. tests/test_render.py 가 node 로 이 문자열을
# 직접 실행해 pid 파생과 폴백 체인을 검증하므로, DOM 에 의존하는 코드를 넣지 말 것.
IMAGE_JS = r"""
const CDN='https://fco.dn.nexoncdn.co.kr/live/externalAssets/common';
// pid 는 spid 뒤 6자리이고 선행 0 을 지워야 한다. p000488.png 는 403, p488.png 는 200.
const pidOf=spid=>String(spid).slice(-6).replace(/^0+/,'');
const portraitUrl=spid=>CDN+'/players/p'+pidOf(spid)+'.png';
const actionUrl=spid=>CDN+'/playersAction/p'+spid+'.png';
// 국기: 넥슨 CDN 국가 코드별 작은 국기.
const flagUrl=code=>CDN+'/countries/smallflags/'+code+'.png';
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
# 강화단계 필터는 별도 드랍박스(gradeFilter)로 분리되어 있어 이 함수는 이름만 본다.
FILTER_JS = r"""
// 빈 질의는 전부 통과, 대소문자 무시 부분일치. (동일 선수 비교 탭은 한 명 검색이라 이걸 그대로 씀)
const matchName=(name,q)=>!q||String(name==null?'':name).toLowerCase().includes(q);
// 리더보드 전용: 쉼표로 구분한 여러 이름 중 하나라도 일치(OR). "노이어, 칸" → 두 선수 카드 모두.
// 한글 이름엔 공백이 있어(예: "마누엘 노이어") 공백이 아니라 쉼표로만 나눈다.
const matchNames=(name,q)=>{
  if(!q) return true;
  const terms=q.split(',').map(s=>s.trim()).filter(Boolean);
  return !terms.length || terms.some(t=>matchName(name,t));
};
// 리더보드 전용: 카드의 국가명 또는 클럽명에 부분일치(OR). 이름 검색과는 별개 입력이며,
// 국가·클럽을 서로 AND 로 조합하지 않는다(한 입력이 국가든 클럽이든 걸리면 통과).
const matchNatClub=(bio,q)=>{
  if(!q) return true;
  if(!bio) return false;
  if(matchName(bio.nation_name,q)) return true;
  return (bio.clubs||[]).some(c=>matchName(c,q));
};
// 리더보드 전용: 급여 범위 필터. lo/hi 는 숫자 또는 null(미지정). 급여 미상(null)은
// 범위가 하나라도 지정되면 제외한다(검증 불가한 값을 통과시키지 않음).
const matchSalary=(salary,lo,hi)=>{
  if(lo==null && hi==null) return true;
  if(salary==null) return false;
  if(lo!=null && salary<lo) return false;
  if(hi!=null && salary>hi) return false;
  return true;
};
"""

# 선방률 신뢰구간. 비율이라 Wilson 95% 구간(작은 표본·극단 비율에서 Wald 보다 정확).
# 시행 수 = 유효슛(선방+실점). tests/test_render.py 가 node 로 직접 검증한다.
# 주의(문구로 노출): 슛이 완전 독립은 아니라(같은 경기·유저 군집) 실제 구간은 이보다 약간 넓다.
STATS_JS = r"""
const Z=1.96;
// 선방 s, 실점 g → [lo, hi] 95% Wilson 구간. 유효슛 0이면 null.
function wilson(s,g){
  const n=s+g; if(!(n>0)) return null;   // NaN(undefined 합)·0·음수 모두 차단
  const p=s/n, z2=Z*Z, d=1+z2/n;
  const c=(p+z2/(2*n))/d;
  const h=(Z*Math.sqrt(p*(1-p)/n+z2/(4*n*n)))/d;
  return [Math.max(0,c-h), Math.min(1,c+h)];
}
// 반폭(±%p) 문자열. 순위 신뢰도 표기용.
function ciText(s,g){
  const w=wilson(s,g); if(!w) return '';
  return '±'+(((w[1]-w[0])/2)*100).toFixed(1)+'%p';
}
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
        _TEMPLATE.replace("__FAVICON__", FAVICON)
        .replace("__IMAGE_JS__", IMAGE_JS)
        .replace("__FILTER_JS__", FILTER_JS)
        .replace("__STATS_JS__", STATS_JS)
        .replace("__DATA__", data_json)
    )


_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FC온라인 GK 선방률 리더보드</title>
<link rel="icon" type="image/png" href="__FAVICON__">
<link rel="apple-touch-icon" href="__FAVICON__">
<!-- 넥슨 Open API 애널리틱스(페이지뷰 집계). app_id 는 스크립트가 자기 src 에서 읽으므로
     공개가 정상 설계다. async 없으면 외부 요청이 첫 렌더를 막는다. -->
<script type="text/javascript" src="https://openapi.nexon.com/js/analytics.js?app_id=307467" async></script>
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
  /* 급여 숫자 입력은 검색창처럼 늘어나지 않게 고정 폭 */
  .controls input.numf{flex:0 0 auto;width:74px;min-width:0;padding:9px 10px;text-align:center}
  .controls .lab{color:var(--mut);font-size:.82rem}
  /* 입력 옆 물음표 도움말 — hover 시 설명 말풍선 */
  .controls .tip{flex:0 0 auto;width:18px;height:18px;border-radius:50%;border:1px solid var(--line);
        color:var(--mut);font-size:.72rem;font-weight:700;display:inline-flex;align-items:center;
        justify-content:center;cursor:help;position:relative;user-select:none}
  .controls .tip:hover{color:#1a1405;background:var(--gold);border-color:var(--gold)}
  .controls .tip:hover::after{content:attr(data-tip);position:absolute;top:calc(100% + 7px);left:50%;
        transform:translateX(-50%);width:max-content;max-width:230px;white-space:normal;text-align:left;
        background:var(--panel2);border:1px solid var(--line);color:var(--text);font-size:.78rem;
        font-weight:400;line-height:1.4;padding:7px 10px;border-radius:8px;z-index:30;
        box-shadow:0 8px 24px rgba(0,0,0,.5)}
  .controls select{padding:8px 10px;border:1px solid var(--line);border-radius:9px;font-size:.85rem;
        background:var(--panel);color:var(--text);font-family:inherit;cursor:pointer}
  button.sort{padding:7px 13px;border:1px solid var(--line);background:var(--panel);color:var(--mut);
        border-radius:9px;cursor:pointer;font-size:.85rem;font-family:inherit;transition:.15s}
  button.sort:hover{border-color:var(--gold2);color:var(--text)}
  button.sort.active{background:linear-gradient(180deg,var(--gold),var(--gold2));color:#1a1405;
        border-color:var(--gold);font-weight:700}
  table{width:100%;border-collapse:collapse;font-size:.92rem}
  th,td{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left}
  thead th{position:sticky;top:0;background:#070b1c;color:var(--gold2);font-size:.78rem;
        font-weight:700;letter-spacing:.02em;z-index:2}
  thead th.sortable{cursor:pointer;white-space:nowrap;user-select:none}
  thead th.sortable:hover{color:var(--gold)}
  thead th.sortable.active{color:var(--gold)}
  thead th .arr{font-size:.72rem;opacity:.4;margin-left:1px}
  thead th.sortable.active .arr{opacity:1}
  td.rank{color:var(--gold2);width:2.4rem;font-weight:700;font-variant-numeric:tabular-nums}
  td.pct{font-variant-numeric:tabular-nums;font-weight:700;color:var(--gold)}
  td.pct .ci{display:block;font-size:.7rem;font-weight:500;color:var(--mut)}
  button.gate{padding:6px 11px;border:1px solid var(--line);background:var(--panel);color:var(--mut);
        border-radius:9px;cursor:pointer;font-size:.82rem;font-family:inherit;transition:.15s}
  button.gate:hover{border-color:var(--gold2);color:var(--text)}
  button.gate.active{background:linear-gradient(180deg,var(--gold),var(--gold2));color:#1a1405;
        border-color:var(--gold);font-weight:700}
  td.num,td.season{color:var(--mut);font-variant-numeric:tabular-nums}
  .scell{display:inline-flex;align-items:center;gap:6px}
  .season-ico{flex:0 0 auto;object-fit:contain;vertical-align:middle;height:22px;width:auto}
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
  summary .pcell{display:inline-flex;vertical-align:middle;align-items:center}
  .sp-total{margin-left:9px;font-size:.78rem;font-weight:600;color:var(--gold2);
        background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:2px 8px;
        font-variant-numeric:tabular-nums;white-space:nowrap}
  /* 상세 히어로 — 원본이 128px 이라 112px 를 넘겨 키우지 않는다 */
  .hero{display:flex;align-items:center;gap:16px;padding-bottom:14px;margin-bottom:14px;
        border-bottom:1px solid var(--line)}
  .hero-img{width:112px;height:112px;border-radius:12px;object-fit:cover;flex:0 0 auto;
        background:radial-gradient(circle at 50% 34%,#1b2242,#0b1024);border:1px solid var(--line)}
  .hero-meta h3{margin:0 0 3px;font-size:1.05rem;font-weight:800;letter-spacing:-.01em}
  .hero-meta .sub{margin:0 0 9px;color:var(--mut);font-size:.85rem}
  .hero-meta .big{font-size:1.5rem;font-weight:800;color:var(--gold);font-variant-numeric:tabular-nums}
  .hero-meta .big small{margin-left:7px;font-size:.78rem;font-weight:600;color:var(--mut)}
  .hero-meta .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
  .chips .chip{font-size:.78rem;color:var(--text);background:var(--panel2);border:1px solid var(--line);
        border-radius:7px;padding:3px 8px;font-variant-numeric:tabular-nums}
  .chips .chip b{color:var(--gold2);font-weight:700}
  .chips .chip.nat{display:inline-flex;align-items:center;gap:5px}
  .chips .chip.nat .flag{border-radius:2px;object-fit:cover;vertical-align:middle}
  .hero-meta .clubs{margin-top:8px;font-size:.8rem;color:var(--mut);line-height:1.5}
  .hero-meta .clubs b{color:var(--gold2);font-weight:700;margin-right:4px}
  /* 표는 375px 에서 606px 다. 본문이 아니라 표만 가로로 스크롤시킨다.
     overflow-x 를 상시로 걸면 overflow-y 가 auto 로 승격돼 sticky thead 가 깨진다.
     데스크톱은 표가 안 넘치므로 모바일에서만 감싼다. */
  @media(max-width:640px){
    .tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
    .hero{flex-direction:column;align-items:flex-start;gap:12px}
    .thumb{width:28px;height:28px}
    .pcell{gap:7px}
    /* 리더보드 표의 선수 컬럼만 폭을 묶어 긴 이름을 말줄임(줄바꿈 금지 — 세로로 쪼개짐).
       동일선수 summary 의 .pcell 은 총경기 배지까지 들어가 118px 로 묶으면 이름이 0px 로
       잘린다 → 표 셀(td) 안의 .pcell 로만 한정한다. */
    td .pcell{max-width:118px}
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
    <input id="search" placeholder="이름 검색">
    <span class="tip" data-tip="여러 명은 쉼표로 구분해서 검색해요. 예: 노이어, 칸">?</span>
    <input id="natClubSearch" placeholder="국가·클럽 검색">
    <span class="tip" data-tip="국가명 또는 클럽명으로 검색해요. 예: 이탈리아 / 유벤투스">?</span>
    <select id="gradeFilter"><option value="">강화 전체</option></select>
    <span class="lab">급여</span>
    <input id="salMin" class="numf" type="number" inputmode="numeric" min="0" placeholder="이상">
    <span class="lab">~</span>
    <input id="salMax" class="numf" type="number" inputmode="numeric" min="0" placeholder="이하">
    <button id="exShort" class="sort" title="GSAx 열에서 초근거리(&lt;5m) 뽀록성 슛 제외">GSAx 초근제외</button>
    <span class="lab">경기수↑</span>
    <button class="gate active" data-gate="200">200</button>
    <button class="gate" data-gate="300">300</button>
    <button class="gate" data-gate="500">500</button>
  </div>
  <p class="muted"><b>컬럼 제목</b>을 클릭하면 그 항목으로 정렬됩니다(다시 누르면 오름/내림 전환). 행을 클릭하면 그 카드의 <b>거리 구간별·슛 타입별</b> 선방률이 펼쳐집니다. 선방률 옆 <b>±%p</b>는 표본에서 온 95% 신뢰구간. 용어가 낯설면 <b>지표 설명</b> 탭을 보세요.</p>
  <div class="tw">
    <table id="lb">
      <thead><tr><th>#</th><th>선수</th><th>시즌</th><th class="sortable" data-col="grade">강화 <span class="arr"></span></th><th class="sortable" data-col="salary">급여 <span class="arr"></span></th><th class="sortable" data-col="ovr">OVR <span class="arr"></span></th><th class="sortable" data-col="save_pct">선방률 <span class="arr"></span></th><th class="sortable" data-col="gsax" id="gsaxHdr">GSAx/100 <span class="arr"></span></th><th class="sortable" data-col="matches">경기수 <span class="arr"></span></th></tr></thead>
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
  <p class="muted">같은 선수의 시즌·강화별 선방률(보정 전)과 GSAx. (여전히 유저 실력 교란 포함)</p>
  <div id="sp"></div>
  <p class="empty" id="spEmpty" style="display:none">해당하는 선수가 없습니다.</p>
</div>

<!-- 탭 3: 지표 설명 · 사용법 -->
<div class="panel help" id="panel-help">
  <h2>이 페이지 사용법</h2>
  <ol class="usage">
    <li><b>리더보드 탭</b>에서 선방률·GSAx로 정렬하고, 검색창에 선수 이름을 넣어 찾습니다. <b>여러 명을 한꺼번에</b> 보려면 쉼표로 구분해 넣으세요(예: <b>노이어, 칸</b>). <b>국가·클럽 검색</b>으로 특정 국가(예: <b>이탈리아</b>)나 클럽(예: <b>유벤투스</b>) 출신만 볼 수도 있습니다. 옆의 <b>강화 드랍박스</b>로 특정 강화단계만 골라볼 수도 있습니다. 기본은 상위 100장만 보이고 <b>더 보기</b>로 펼칩니다.</li>
    <li>표의 <b>행을 클릭</b>하면 그 카드의 거리 구간별·슛 타입별 선방률과 세부 스탯이 펼쳐집니다.</li>
    <li><b>동일 선수 비교 탭</b>에서 같은 선수의 시즌·강화별 성적을 나란히 봅니다.</li>
  </ol>

  <h2>핵심 지표</h2>
  <dl class="lead">
    <dt>선방률</dt>
    <dd>상대의 유효슛 중 막아낸 비율. <b>선방 ÷ (선방 + 실점)</b>으로 계산합니다. 값이 높을수록 잘 막은 것. 단, 이 순위의 선방률은 <b>보정하지 않은 값</b>이라 카드 성능뿐 아니라 <b>그 카드를 쓴 유저의 실력·수비 라인·상대 슛 난이도</b>가 섞여 있으니 카드 순위가 아니라 <b>참고용</b>으로 봐주세요.</dd>
    <dt>GSAx / 100</dt>
    <dd>Goals Saved Above Expected — <b>슛 난이도를 보정</b>한 지표입니다. 거리·각도로 계산한 '기대 실점'보다 실제로 얼마나 더(또는 덜) 막았는지를 유효슛 100개 기준으로 환산합니다. <b>+면 기대보다 선방, −면 기대보다 실점</b>. 유저 실력 교란을 줄인, 선방률보다 공정한 비교값입니다.</dd>
    <dt>GSAx(초근제외)</dt>
    <dd>초근거리(5m 미만) 슛을 뺀 GSAx. 골문 앞 난사처럼 GK가 어쩔 수 없는 상황을 제외해, 포지셔닝·반응 능력을 더 잘 드러냅니다.</dd>
    <dt>경기수 · 게이트</dt>
    <dd>경기수 = 그 카드로 수집·집계된 경기 수(= 통계 표본). 경기수가 적으면 우연(뽀록)일 수 있어 신뢰도가 낮습니다. 그래서 최소 <b id="gateN"></b>경기 이상(게이트)인 카드만 순위에 올립니다. 리더보드 위 <b>경기수↑ 200/300/500</b> 버튼으로 기준을 올려 <b>뽀록 상위권을 걸러</b> 볼 수 있습니다.</dd>
    <dt>신뢰구간 (±%p)</dt>
    <dd>선방률 옆 <b>±N%p</b>는 그 표본에서 나온 <b>95% 신뢰구간</b>(Wilson)입니다. 실제 선방률이 이 범위 안에 있을 가능성이 높다는 뜻으로, 유효슛(≈경기수×5)이 많을수록 좁아집니다. 예: 50경기 ±6%p, 200경기 ±3%p. 두 카드의 구간이 크게 겹치면 순위 차이를 단정할 수 없습니다. 단, 이는 <b>정밀도</b>지 정확도가 아니며(유저 실력 등 교란은 별개 — GSAx 참고), 슛이 완전 독립은 아니라 실제 구간은 이보다 약간 넓습니다.</dd>
  </dl>

  <h2>세부 지표 (행 클릭 시 펼쳐지는 값)</h2>
  <details><summary>거리 구간별 · 슛 타입별</summary>
    <dl class="terms">
      <dt>거리 구간별</dt><dd>실점·선방을 골문과의 근사 거리(초근/근/중/원거리)로 나눈 선방률. 어느 거리에 강하고 약한지 보여줍니다.</dd>
      <dt>슛 타입별</dt><dd>노멀·감아차기·헤더·발리 등 슛 종류별 선방률. 표본 3개 미만 타입은 노이즈라 숨깁니다. <b>기타(#13)</b> 처럼 표기된 항목은 넥슨 공개 명세가 1~12번만 정의하는데 게임에 그 뒤 추가된 슛 종류입니다. 이름을 임의로 붙이지 않고 번호를 그대로 둡니다.</dd>
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
let sortCol='save_pct', sortDir='desc', gsaxMode='gsax_per_shot';
let q='', limit=PAGE, minGate=200, gradeFilter='', natClubQ='', salMin=null, salMax=null;
// 컬럼 id → 정렬/표시 값. 급여·OVR 은 c.info 중첩, GSAx 는 초근제외 토글에 따라 값이 바뀐다.
function sortVal(c,col){
  if(col==='salary') return c.info&&c.info.salary;
  if(col==='ovr') return c.info&&c.info.ovr;
  if(col==='gsax') return c[gsaxMode];
  return c[col];   // grade, save_pct, matches
}
const pct=v=>v==null?'N/A':(v*100).toFixed(1)+'%';
const gps=v=>v==null?'':(v*100>=0?'+':'')+(v*100).toFixed(1);
const esc=s=>{const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;};
// esc 는 따옴표를 escape 하지 않는다 → 속성값에 쓸 땐 escAttr 로 감싼다.
const escAttr=s=>esc(s).replace(/"/g,'&quot;');
__IMAGE_JS__
__FILTER_JS__
__STATS_JS__
// 썸네일은 얼굴 → 플레이스홀더 2단. 히어로는 액션샷 → 얼굴 → 플레이스홀더 3단(data-fb).
const thumbImg=(spid,name)=>spid==null?'':
  `<img class="thumb" src="${thumbUrl(spid)}" alt="${escAttr(name||'')}" width="36" height="36" `+
  `loading="lazy" decoding="async" onerror="imgFallback(this)">`;
const heroImg=(spid,name)=>spid==null?'':
  `<img class="hero-img" src="${actionUrl(spid)}" data-fb="${portraitUrl(spid)}" `+
  `alt="${escAttr(name||'')}" width="112" height="112" loading="lazy" decoding="async" `+
  `onerror="imgFallback(this)">`;
// 시즌명 앞 엠블럼 아이콘. 이미지는 넥슨 CDN 직접. 시즌명은 alt/title(hover)로.
const seasonIcon=(img,name)=>img?
  `<img class="season-ico" src="${img}" alt="${escAttr(name||'')}" title="${escAttr(name||'')}" `+
  `width="18" height="18" loading="lazy" decoding="async" onerror="this.style.display='none'">`:'';
// 목록용 시즌 셀: 아이콘만 보여준다(텍스트 제거). 아이콘 없으면 시즌명으로 폴백.
const seasonCell=(img,name)=>img?seasonIcon(img,name):esc(name||'');

const dr=D.date_range||{};
// date_range 는 since 를 반영한 실제 집계 창이다. 원시 ISO since 를 덧붙이면 중복이자 노이즈.
const period=(dr.min&&dr.max)?`데이터 기간 ${dr.min} ~ ${dr.max} · `:'';
const totalMatches = Number(D.total_collected_matches || 0).toLocaleString('ko-KR');
document.getElementById('meta').textContent =
  `${period}총 수집 경기 ${totalMatches}건 · ${D.leaderboard.length}장`;
document.getElementById('warn').innerHTML =
  '<b>⚠️ 읽는 법:</b> 이 순위는 보정하지 않은 선방률이라 카드 성능뿐 아니라 <b>유저 실력</b>도 섞여 있어요. 카드 순위가 아니라 <b>참고용</b>으로만 봐주세요. 용어·자세한 설명은 <b>지표 설명</b> 탭.';
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
  const info=c.info||{}, bio=c.bio||{};
  const chip=(label,val)=>val==null?'':`<span class="chip"><b>${label}</b>${esc(val)}</span>`;
  // 국가 칩: 넥슨 CDN 국기 + 국가명. code 만 있으면 국기라도 표시(국가명 파싱 실패 대비).
  const natChip = bio.nation_code==null ? '' :
    `<span class="chip nat"><img class="flag" src="${flagUrl(bio.nation_code)}" alt="" `+
    `width="20" height="14" loading="lazy" onerror="this.style.display='none'">${esc(bio.nation_name||'')}</span>`;
  const infoRow=[
    natChip,
    chip('급여 ', info.salary),
    chip('기본 OVR ', info.ovr),
    chip('키 ', info.height==null?null:info.height+'cm'),
    chip('몸무게 ', info.weight==null?null:info.weight+'kg'),
    chip('체형 ', info.body_type),
  ].join('');
  // 클럽 이력: 표기순 나열. 없으면 생략.
  const clubsRow = (bio.clubs&&bio.clubs.length)
    ? `<div class="clubs"><b>클럽</b> ${bio.clubs.map(esc).join(' · ')}</div>` : '';
  const hero=
    `<div class="hero">${heroImg(c.gk_sp_id,c.player_name)}<div class="hero-meta">`+
    `<h3>${esc(c.player_name||('spId '+c.gk_sp_id))}</h3>`+
    `<p class="sub"><span class="scell">${seasonIcon(c.season_img,c.season_name)}${esc(c.season_name||'')}</span> · ${c.grade}강</p>`+
    `<div class="big">${pct(c.save_pct)}<small>선방률 ${ciText(c.saves,c.goals)} · 경기수 ${c.matches}</small></div>`+
    (infoRow?`<div class="chips">${infoRow}</div>`:'')+
    clubsRow+
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
  d.innerHTML=`<td colspan="9">${detailHtml(c)}</td>`; tr.after(d);
}
function render(){
  const tb=document.querySelector('#lb tbody'); tb.innerHTML='';
  const more=document.getElementById('more'); more.innerHTML='';
  let rows=D.leaderboard.filter(c=>matchNames(c.player_name,q) && c.matches>=minGate &&
    (!gradeFilter || c.grade===+gradeFilter) && matchNatClub(c.bio,natClubQ) &&
    matchSalary(c.info&&c.info.salary,salMin,salMax));
  // 정렬은 필터 뒤에 적용된다 → 검색·필터 결과 안에서만 순서가 매겨진다. null 은 항상 뒤로.
  rows=rows.slice().sort((a,b)=>{
    const av=sortVal(a,sortCol), bv=sortVal(b,sortCol);
    if(av==null&&bv==null)return 0; if(av==null)return 1; if(bv==null)return -1;
    return sortDir==='asc' ? av-bv : bv-av;
  });
  if(!rows.length){tb.innerHTML='<tr><td colspan="9" class="empty">해당하는 카드가 없습니다.</td></tr>';updateHeaders();return;}
  const gf = gsaxMode;   // GSAx 열 표시값(초근제외 토글에 따라)
  document.getElementById('gsaxHdr').firstChild.textContent =
    (gsaxMode==='gsax_ex_short_per_shot' ? 'GSAx/100(초근×) ' : 'GSAx/100 ');
  updateHeaders();
  // 검색 중(이름 또는 국가/클럽)이면 전체에서 찾도록 캡 무시, 아니면 상위 limit 장만(경량화)
  const searching = q || natClubQ;
  const vis = searching ? rows : rows.slice(0, limit);
  vis.forEach((c,i)=>{
    const tr=document.createElement('tr'); tr.className='row';
    tr.innerHTML=`<td class="rank">${i+1}</td>`+
      `<td><div class="pcell">${thumbImg(c.gk_sp_id,c.player_name)}`+
      `<span class="pn">${esc(c.player_name||('spId '+c.gk_sp_id))}</span></div></td>`+
      `<td class="season"><span class="scell">${seasonCell(c.season_img,c.season_name)}</span></td><td class="num">${c.grade}강</td>`+
      `<td class="num">${(c.info&&c.info.salary!=null)?c.info.salary:''}</td>`+
      `<td class="num">${(c.info&&c.info.ovr!=null)?c.info.ovr:''}</td>`+
      `<td class="pct">${pct(c.save_pct)}<span class="ci">${ciText(c.saves,c.goals)}</span></td><td class="num">${gps(c[gf])}</td>`+
      `<td class="num">${c.matches}</td>`;
    tr.onclick=()=>toggle(tr,c); tb.appendChild(tr);
  });
  if(searching) return;  // 검색 중엔 더보기/접기 없음
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
document.getElementById('natClubSearch').oninput=e=>{natClubQ=e.target.value.trim().toLowerCase();limit=PAGE;render();};
// 급여 범위 — 빈칸이면 null(미지정). 숫자 아니면 무시.
const parseSal=v=>{const n=parseInt(v,10);return Number.isFinite(n)?n:null;};
document.getElementById('salMin').oninput=e=>{salMin=parseSal(e.target.value);limit=PAGE;render();};
document.getElementById('salMax').oninput=e=>{salMax=parseSal(e.target.value);limit=PAGE;render();};
// 강화 드랍박스 — 실제 데이터에 있는 강화단계로 옵션을 만들어, 새 강화가 추가돼도 코드 수정 없이 반영된다.
const gSel=document.getElementById('gradeFilter');
[...new Set(D.leaderboard.map(c=>c.grade))].sort((a,b)=>a-b).forEach(g=>{
  const o=document.createElement('option'); o.value=g; o.textContent=g+'강'; gSel.appendChild(o);
});
gSel.onchange=e=>{gradeFilter=e.target.value;limit=PAGE;render();};
// 컬럼 헤더 클릭 정렬 — 활성 컬럼엔 ▼/▲, 정렬 가능하나 비활성인 컬럼엔 옅은 ⇅.
function updateHeaders(){
  document.querySelectorAll('#lb thead th.sortable').forEach(th=>{
    const on = th.dataset.col===sortCol;
    th.classList.toggle('active', on);
    th.querySelector('.arr').textContent = on ? (sortDir==='asc'?'▲':'▼') : '⇅';
  });
}
document.querySelectorAll('#lb thead th.sortable').forEach(th=>th.onclick=()=>{
  const col=th.dataset.col;
  if(sortCol===col) sortDir = sortDir==='asc' ? 'desc' : 'asc';  // 같은 컬럼 재클릭 → 방향 토글
  else { sortCol=col; sortDir='desc'; }                          // 새 컬럼 → 내림차순부터
  limit=PAGE; render();
});
// GSAx 초근제외 토글 — GSAx 열의 값·정렬 대상을 초근거리 제외 버전으로 전환.
document.getElementById('exShort').onclick=()=>{
  gsaxMode = gsaxMode==='gsax_per_shot' ? 'gsax_ex_short_per_shot' : 'gsax_per_shot';
  document.getElementById('exShort').classList.toggle('active', gsaxMode==='gsax_ex_short_per_shot');
  limit=PAGE; render();
};
// 경기수 게이트 필터 (200/300/500) — 데이터 재요청 없이 화면에서만 거른다
document.querySelectorAll('[data-gate]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-gate]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); minGate=+b.dataset.gate; limit=PAGE; render();
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
    `<tr><td><span class="scell">${seasonCell(c.season_img,c.season_name)}</span></td><td class="num">${c.grade}강</td>`+
    `<td class="num">${(c.info&&c.info.salary!=null)?c.info.salary:''}</td>`+
    `<td class="pct">${pct(c.save_pct)}<span class="ci">${ciText(c.saves,c.goals)}</span></td><td class="num">${gps(c.gsax_per_shot)}</td>`+
    `<td class="num">${c.matches}</td></tr>`).join('');
  // 그룹 내 카드는 시즌만 다르고 pid 는 같다(182그룹 전수 확인) → 첫 카드로 썸네일을 만든다.
  const sp=(g.cards[0]||{}).gk_sp_id;
  // 시즌·강화 상관없이 이 선수로 수집·비교된 총 경기수
  const totalGames=g.cards.reduce((s,c)=>s+(c.matches||0),0);
  return `<details data-name="${escAttr(g.player_name)}"><summary><span class="pcell">${thumbImg(sp,g.player_name)}`+
    `<span class="pn">${esc(g.player_name)}</span>`+
    `<span class="sp-total">총 ${totalGames.toLocaleString('ko-KR')}경기 · ${g.cards.length}장</span></span></summary><table class="mini">`+
    `<thead><tr><th>시즌</th><th>강화</th><th>급여</th><th>선방률</th><th>GSAx/100</th><th>경기수</th></tr></thead>`+
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
