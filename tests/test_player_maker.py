"""선수 편집기: 능력치 계산, 가격 숨김, 원본 자산과 정적 export 연결."""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gksave import export, render
from gksave.config import NEW_TRAIT_CODES


def test_maker_tab_is_separate_and_lazy_loaded():
    html = render.build_html({})
    assert html.index('data-tab="defense"') < html.index('data-tab="maker"') < html.index('data-tab="sp"')
    assert 'id="panel-maker"' in html
    assert "import('./player-maker/editor.js')" in html
    panel = html.split('id="panel-maker"', 1)[1].split('</form>', 1)[0]
    assert 'id="makerPhoto"' in panel
    assert 'id="makerSaveCard"' in panel and 'id="makerSaveAll"' in panel
    assert 'id="makerShowPrice"' in panel
    assert 'name="salary"' in panel
    assert 'name="club"' in panel
    for kind in ('Season', 'Nation', 'Club'):
        assert f'id="maker{kind}Search"' in panel
        assert f'aria-controls="maker{kind}Choices"' in panel
        assert f'id="maker{kind}Choices"' in panel
        assert f'id="maker{kind}Current"' in panel
        assert f'id="maker{kind}Results"' in panel
    assert 'id="makerTraitPicker"' in panel and 'id="makerTraitName"' in panel
    assert 'id="makerAddTrait"' in panel and 'id="makerTraitsPreview"' in panel
    assert '선수 검색' not in panel
    assert '사진 배경 제거도 브라우저 안에서 실행됩니다.' in html


@pytest.mark.skipif(shutil.which('node') is None, reason='node 없음 — JS 검증 생략')
def test_six_direct_abilities_custom_names_and_input_helpers():
    source = (render.MAKER_DIR / 'editor.js').as_uri()
    script = f"""
import assert from 'node:assert/strict';
import {{ABILITIES, bounded, radarValues, priceText, portraitBounds, portraitRegion, groupLabel, traitLabel, fullImageHeight, choiceMatches}} from {json.dumps(source)};
const abilities=Object.fromEntries(ABILITIES.map(name=>[name,120]));
assert.equal(Object.keys(abilities).length,6);
assert.deepEqual(radarValues(abilities).map(([,v])=>v),[120,120,120,120,120,120]);
abilities['스피드']=200;
assert.deepEqual(radarValues(abilities)[0],['스피드',200]);
abilities['슛']=0;
assert.equal(radarValues(abilities)[1][1],0);
const labels={{'스피드':'애교','슛':'건강'}};
assert.deepEqual(radarValues(abilities,labels).slice(0,2),[['애교',200],['건강',0]]);
assert.deepEqual(radarValues(abilities,labels).map(([,v])=>v),radarValues(abilities).map(([,v])=>v));
assert.equal(groupLabel('스피드',{{'스피드':'   '}}),'스피드');
assert.equal(groupLabel('스피드',{{'스피드':'   애교   '}}),'애교');
assert.equal(groupLabel('스피드',{{'스피드':'가'.repeat(20)}}).length,12);
abilities['스피드']=201;
assert.deepEqual(radarValues(abilities)[0],['스피드',200]);
assert.equal(bounded(-1),0); assert.equal(bounded(Infinity),0); assert.equal(bounded('no'),0);
assert.equal(bounded(999),200); assert.equal(bounded(0,1),1);
assert.equal(priceText(' 1조 BP ',true),'1조 BP');
assert.equal(priceText('1조 BP',false),'');
for (const [width,height] of [[400,1000],[1600,900],[180,180]]) {{
  const rect=portraitBounds(width,height);
  assert.ok(rect.width<=180 && rect.height<=180,'Default portrait must fit the official 180px image box');
  assert.equal(rect.y+rect.height,272,'Portrait must stop above the name row');
  assert.equal(rect.x+rect.width/2,185,'Portrait must center inside the official image region');
  assert.equal(rect.width/rect.height,width/height);
}}
assert.ok(portraitBounds(400,1000,150).height>portraitBounds(400,1000).height);
const face={{originX:1000,originY:80,width:300,height:320}};
const region=portraitRegion(1600,739,face);
assert.equal(region.width,region.height,'Landscape background must not define portrait scale');
assert.ok(region.x>700,'Frame must follow the person on the right');
assert.ok(region.x<=face.originX && region.x+region.width>=face.originX+face.width);
assert.ok(region.y<=face.originY && region.y+region.height>=face.originY+face.height);
assert.ok(region.x>=0&&region.y>=0&&region.x+region.width<=1600&&region.y+region.height<=739);
assert.ok(portraitBounds(region.width,region.height).height>=150);
assert.deepEqual(portraitRegion(400,700,null),{{x:0,y:0,width:400,height:700}});
assert.equal(traitLabel('  간식 탐지기  ','기본 특성'),'간식 탐지기');
assert.equal(traitLabel('  ','기본 특성'),'기본 특성');
assert.equal(traitLabel('가'.repeat(40),'기본 특성').length,24);
assert.equal(fullImageHeight(0),1140);
assert.ok(fullImageHeight(1)>1140);
assert.equal(fullImageHeight(1),fullImageHeight(2));
assert.ok(fullImageHeight(3)>fullImageHeight(2));
assert.equal(choiceMatches('25 UCL (25 UEFA Champions League)',' 25ucl '),true);
assert.equal(choiceMatches('PTG (Path to Glory)','ｐｔｇ'),true);
assert.equal(choiceMatches('레알 마드리드','레알마드리드'),true);
assert.equal(choiceMatches('대한민국','대한 민국'),true);
assert.equal(choiceMatches('스페인','대한민국'),false);
assert.equal(choiceMatches('PTG','  '),true);
"""
    subprocess.run([shutil.which('node'), '--input-type=module', '-e', script], check=True, capture_output=True, text=True)


def test_collected_designs_and_nations_are_complete_and_match_their_hashes():
    catalog = json.loads((render.MAKER_DIR / 'seasons.json').read_text())
    designs = catalog['seasons']
    assert {856, 863, 100, 101} <= {s['id'] for s in designs}
    assert len({s['id'] for s in designs}) == len(designs)
    for design in designs:
        assert re.fullmatch(r'#[0-9a-fA-F]{3,8}', design['color'])
        for kind in ('frame', 'emblem'):
            asset = design[kind]
            assert asset and asset['source'].startswith('https://ssl.nexon.com/')
            content = (render.MAKER_DIR / asset['file']).read_bytes()
            assert content.startswith(b'\x89PNG\r\n\x1a\n')
            assert hashlib.sha256(content).hexdigest() == asset['sha256']
    nations = json.loads((render.MAKER_DIR / 'nations.json').read_text())
    assert any(n['id'] == 167 and n['name'] == '대한민국' for n in nations)
    for nation in nations:
        content = (render.MAKER_DIR / nation['file']).read_bytes()
        assert hashlib.sha256(content).hexdigest() == nation['sha256']
    details = json.loads((render.MAKER_DIR / 'card-details.json').read_text())
    assert details['league']['source'].endswith('/league/light/ca/l2028.png')
    assert any(c['id'] == 130172 for c in details['clubs'])
    for asset in [details['league'], details['platinum'], *details['fonts'], *details['clubs']]:
        content = (render.MAKER_DIR / asset['file']).read_bytes()
        assert hashlib.sha256(content).hexdigest() == asset['sha256']
    vendor = json.loads((render.MAKER_DIR / 'vendor/sources.json').read_text())
    for name, digest in vendor['files'].items():
        assert hashlib.sha256((render.MAKER_DIR / 'vendor' / name).read_bytes()).hexdigest() == digest
    assert hashlib.sha256((render.MAKER_DIR / 'vendor/selfie_segmenter.tflite').read_bytes()).hexdigest() == vendor['model_sha256']
    assert hashlib.sha256((render.MAKER_DIR / 'vendor/blaze_face_short_range.tflite').read_bytes()).hexdigest() == vendor['face_detector_sha256']
    trait_catalog = json.loads((render.MAKER_DIR / 'traits.json').read_text())
    traits = trait_catalog['traits']
    assert trait_catalog['names_page'] == 'https://fconline.nexon.com/datacenter/coachhelper'
    assert len(traits) == 48
    assert all(t['name'] and not t['name'].startswith('특성 #') for t in traits)
    names = {t['code']: t['name'] for t in traits}
    assert names[0] == '선호 포지션 고집'
    assert names[13] == '예리한 감아차기'
    assert names[50] == '아크로바틱 피니셔'
    assert names[63] == '스피드스터'
    assert len(traits) == len({t['code'] for t in traits})
    assert {True, False} == {t['is_new'] for t in traits}
    assert {t['code'] for t in traits if t['is_new']} == NEW_TRAIT_CODES
    for trait in traits:
        content = (render.MAKER_DIR / trait['file']).read_bytes()
        assert content.startswith(b'\x89PNG\r\n\x1a\n')
        assert hashlib.sha256(content).hexdigest() == trait['sha256']


def test_export_includes_editor_and_local_assets(monkeypatch, tmp_path):
    payload = {'generated_at': '2026-11-04', 'leaderboard': [], 'same_player': [],
               'defense': {}, 'leaderboard_count': 0}
    monkeypatch.setattr(export, 'build_payload', lambda *args, **kwargs: payload)
    export.export(None, tmp_path)
    assert 'id="panel-maker"' in (tmp_path / 'index.html').read_text()
    for path in render.MAKER_DIR.rglob('*'):
        if path.is_file():
            assert (tmp_path / 'player-maker' / path.relative_to(render.MAKER_DIR)).read_bytes() == path.read_bytes()


def test_hosting_policies_allow_local_wasm_without_javascript_eval():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'vercel.json').read_text())
    csp = next(h['value'] for h in config['headers'][0]['headers'] if h['key'] == 'Content-Security-Policy')
    assert f'Content-Security-Policy: {csp}' in (root / 'out/_headers').read_text()
    assert "'wasm-unsafe-eval'" in csp and "'unsafe-eval'" not in csp
    assert "font-src 'self'" in csp


def test_hosting_policies_allow_adsense_without_allowing_arbitrary_origins():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'vercel.json').read_text())
    csp = next(h['value'] for h in config['headers'][0]['headers'] if h['key'] == 'Content-Security-Policy')
    directives = dict(part.strip().split(None, 1) for part in csp.split(';') if ' ' in part.strip())
    for directive in ('script-src', 'connect-src', 'frame-src', 'img-src'):
        sources = directives[directive].split()
        assert 'https://*.googlesyndication.com' in sources
        assert 'https://*.doubleclick.net' in sources
        assert 'https://www.google.com' in sources
        assert 'https://*.googleadservices.com' in sources
        assert 'https://*.adtrafficquality.google' in sources
        assert '*' not in sources and 'https:' not in sources and 'http:' not in sources
    for directive in ('default-src', 'object-src', 'base-uri', 'frame-ancestors', 'form-action'):
        assert directives[directive] == "'none'"
