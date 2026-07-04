"""정적 HTML 표출 (인터랙티브).

export 페이로드를 자기완결형 HTML 한 장으로 렌더. 외부 의존 없음(inline CSS/JS).
데이터를 JSON 으로 embed 하고 vanilla JS 로 렌더 → 검색·정렬·행 클릭 시 거리존별·
타입별 드릴다운. 그대로 열거나 GitHub Pages 등에 올려도 된다.

교란 주의 라벨을 페이지 최상단 배너로 노출한다(설계 D1 결정).
"""

from __future__ import annotations

import json
from typing import Any


def build_html(payload: dict[str, Any]) -> str:
    page = {
        "generated_at": str(payload.get("generated_at", "")),
        "gate": payload.get("gate_min_matches"),
        "since": payload.get("since"),
        "date_range": payload.get("date_range", {}),
        "warning": payload.get("warning", ""),
        "grade_effect": payload.get("grade_effect", {}),
        "leaderboard": payload.get("leaderboard", []),
        "same_player": payload.get("same_player", []),
    }
    # <script> 탈출 방지: '<' → <
    data_json = json.dumps(page, ensure_ascii=False).replace("<", "\\u003c")
    return _TEMPLATE.replace("__DATA__", data_json)


_TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FC온라인 GK 선방률 리더보드</title>
<style>
  :root{ --bd:#eee; --mut:#888; --acc:#2b6cb0; }
  *{box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       max-width:960px;margin:0 auto;padding:20px 14px;color:#1a1a1a}
  h1{font-size:1.5rem;margin:0 0 2px}
  h2{font-size:1.15rem;margin:28px 0 8px}
  h4{margin:0 0 6px;font-size:.9rem;color:#444}
  .meta{color:var(--mut);font-size:.85rem}
  .warn{background:#fff4e5;border:1px solid #ffb84d;border-radius:8px;
        padding:11px 13px;font-size:.88rem;line-height:1.5;margin:14px 0}
  .controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}
  .controls input{padding:7px 10px;border:1px solid #ccc;border-radius:7px;font-size:.9rem;flex:1;min-width:160px}
  .controls .lab{color:var(--mut);font-size:.82rem}
  button.sort{padding:6px 11px;border:1px solid #ccc;background:#fff;border-radius:7px;
              cursor:pointer;font-size:.85rem}
  button.sort.active{background:var(--acc);color:#fff;border-color:var(--acc)}
  table{width:100%;border-collapse:collapse;font-size:.92rem}
  th,td{padding:8px 10px;border-bottom:1px solid var(--bd);text-align:left}
  thead th{position:sticky;top:0;background:#fafafa;color:#555;font-size:.8rem;font-weight:600}
  td.rank{color:#aaa;width:2.3rem}
  td.pct{font-variant-numeric:tabular-nums;font-weight:600}
  td.num,td.season{color:#555;font-variant-numeric:tabular-nums}
  tr.row{cursor:pointer}
  tr.row:hover{background:#f6f9ff}
  tr.detail>td{background:#fafcff;padding:12px 16px}
  .detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
  @media(max-width:640px){.detail-grid{grid-template-columns:1fr}}
  .zbar{display:grid;grid-template-columns:120px 52px 1fr 62px;align-items:center;gap:8px;margin:3px 0;font-size:.82rem}
  .zbar .zt{font-weight:600;font-variant-numeric:tabular-nums}
  .zbar .zbg{background:#e8e8e8;border-radius:4px;height:9px;overflow:hidden}
  .zbar .zf{display:block;height:100%;background:var(--acc)}
  .zbar .zn{color:var(--mut);text-align:right;font-variant-numeric:tabular-nums}
  table.mini{font-size:.84rem}
  table.mini td{padding:4px 8px}
  .stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px 16px;font-size:.84rem}
  .stat{display:flex;justify-content:space-between;border-bottom:1px dotted #e3e3e3;padding:3px 0}
  .stat span{color:var(--mut)}
  .stat b{font-variant-numeric:tabular-nums}
  .muted{color:var(--mut);font-size:.85rem}
  details{margin:5px 0}
  summary{cursor:pointer;font-weight:600}
  .empty{text-align:center;color:#999;padding:22px}
  footer{margin-top:36px;padding-top:14px;border-top:1px solid var(--bd);
         color:var(--mut);font-size:.8rem;line-height:1.6}
  footer a{color:var(--acc);text-decoration:none}
</style></head><body>
<h1>FC온라인 골키퍼 선방률 리더보드</h1>
<p class="meta" id="meta"></p>
<div class="warn" id="warn"></div>

<div class="controls">
  <input id="search" placeholder="선수 이름 검색…">
  <span class="lab">정렬</span>
  <button class="sort active" data-sort="save_pct">선방률</button>
  <button class="sort" data-sort="gsax_per_shot">GSAx</button>
  <button class="sort" data-sort="gsax_ex_short_per_shot">GSAx(초근제외)</button>
  <button class="sort" data-sort="matches">표본</button>
</div>
<p class="muted">행을 클릭하면 그 카드의 <b>거리 구간별·슛 타입별</b> 선방률이 펼쳐집니다.</p>
<table id="lb">
  <thead><tr><th>#</th><th>선수</th><th>시즌</th><th>강화</th><th>선방률</th><th id="gsaxHdr">GSAx/100</th><th>표본</th></tr></thead>
  <tbody></tbody>
</table>

<h2>강화 효과 (유저 내 비교)</h2>
<p class="muted" id="ge"></p>

<h2>동일 선수 · 시즌 비교</h2>
<p class="muted">같은 선수의 시즌·강화별 선방률(raw)과 GSAx. (여전히 raw 는 유저 교란 포함)</p>
<div id="sp"></div>

<footer>
  데이터 출처: 본 분석의 모든 경기 데이터는 <b>NEXON Open API</b>
  (<a href="https://open.api.nexon.com" target="_blank" rel="noopener">open.api.nexon.com</a>)
  의 FC 온라인 매치 데이터를 수집·가공해 제공합니다.<br>
  넥슨 및 FC 온라인과 무관한 비공식 팬 분석이며, 지표는 공개 API 데이터에 기반한 추정치입니다.
</footer>

<script id="gk-data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('gk-data').textContent);
let sortKey='save_pct', q='';
const pct=v=>v==null?'N/A':(v*100).toFixed(1)+'%';
const gps=v=>v==null?'':(v*100>=0?'+':'')+(v*100).toFixed(1);
const esc=s=>{const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;};

const dr=D.date_range||{};
const period=(dr.min&&dr.max)?`데이터 기간 ${dr.min} ~ ${dr.max} · `:'';
document.getElementById('meta').textContent =
  `${period}게이트 ${D.gate}경기↑ · ${D.leaderboard.length}장`
  + (D.since?` · ${D.since} 이후`:'');
document.getElementById('warn').innerHTML = '<b>⚠️ 읽는 법:</b> ' + esc(D.warning);

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
    stat('1대1(독대) 선방률', pct(e.unassisted_save))+
    stat('연계·컷백 선방률', pct(e.assisted_save))+
    stat('경기당 평균 평점', e.gk_rating==null?'-':e.gk_rating)+
    stat('패스 성공률', pct(e.pass_pct));
    // 공중볼(aerial)은 게임상 GK에 거의 안 잡혀(중앙값 1) 노이즈 → 화면 미표시. 데이터는 DB 유지.
  return `<div class="detail-grid"><div><h4>거리 구간별 (근사 미터)</h4>${zones}</div>`+
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
  let rows=D.leaderboard.filter(c=>!q||(c.player_name||'').toLowerCase().includes(q));
  rows=rows.slice().sort((a,b)=>{
    const av=a[sortKey], bv=b[sortKey];
    if(av==null&&bv==null)return 0; if(av==null)return 1; if(bv==null)return -1; return bv-av;
  });
  if(!rows.length){tb.innerHTML='<tr><td colspan="7" class="empty">해당하는 카드가 없습니다.</td></tr>';return;}
  const gf = sortKey.indexOf('gsax')===0 ? sortKey : 'gsax_per_shot';  // GSAx 컬럼은 활성 모드 값
  document.getElementById('gsaxHdr').textContent =
    gf==='gsax_ex_short_per_shot' ? 'GSAx/100(초근×)' : 'GSAx/100';
  rows.forEach((c,i)=>{
    const tr=document.createElement('tr'); tr.className='row';
    tr.innerHTML=`<td class="rank">${i+1}</td><td>${esc(c.player_name||('spId '+c.gk_sp_id))}</td>`+
      `<td class="season">${esc(c.season_name||'')}</td><td class="num">${c.grade}강</td>`+
      `<td class="pct">${pct(c.save_pct)}</td><td class="num">${gps(c[gf])}</td>`+
      `<td class="num">${c.matches}</td>`;
    tr.onclick=()=>toggle(tr,c); tb.appendChild(tr);
  });
}
document.getElementById('search').oninput=e=>{q=e.target.value.trim().toLowerCase();render();};
document.querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('[data-sort]').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); sortKey=b.dataset.sort; render();
});

const ge=D.grade_effect||{}, de=ge.mean_save_pct_delta_per_grade;
document.getElementById('ge').innerHTML =
  '같은 유저·같은 카드에서 강화단계 1 상승당 평균 선방률 변화(유저 실력 교란 제거): '+
  `<b>${de==null?'표본 부족':(de*100>=0?'+':'')+(de*100).toFixed(2)+'%p'}</b> `+
  `(페어유저 ${ge.paired_users||0}, 페어 ${ge.pairs||0})`;

document.getElementById('sp').innerHTML=(D.same_player||[]).map(g=>{
  const rows=g.cards.map(c=>
    `<tr><td>${esc(c.season_name||'')}</td><td class="num">${c.grade}강</td>`+
    `<td class="pct">${pct(c.save_pct)}</td><td class="num">${gps(c.gsax_per_shot)}</td>`+
    `<td class="num">${c.matches}</td></tr>`).join('');
  return `<details><summary>${esc(g.player_name)}</summary><table class="mini">`+
    `<thead><tr><th>시즌</th><th>강화</th><th>선방률</th><th>GSAx/100</th><th>표본</th></tr></thead>`+
    `<tbody>${rows}</tbody></table></details>`;
}).join('') || '<span class="muted">비교할 동일선수 데이터가 아직 없습니다.</span>';

render();
</script>
</body></html>
"""
