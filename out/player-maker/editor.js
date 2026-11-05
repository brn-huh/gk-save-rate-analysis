const ROOT = new URL('./', import.meta.url);
export const ABILITIES = ['스피드', '슛', '패스', '드리블', '수비', '피지컬'];
export function bounded(value, min = 0, max = 200) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.min(max, Math.max(min, Math.round(n))) : min;
}
export const groupLabel = (name, labels = {}) => String(labels[name] || '').trim().slice(0, 12) || name;
export const traitLabel = (name, fallback) => String(name || '').trim().slice(0, 24) || fallback;
export const fullImageHeight = count => 1140 + (count > 0 ? 150 + Math.ceil(count / 2) * 140 : 0);
export function choiceMatches(name, query) {
  const normalize = value => value.normalize('NFKC').toLowerCase().replace(/\s+/g, '');
  return normalize(name).includes(normalize(query));
}
export function radarValues(abilities, labels = {}) {
  return ABILITIES.map(name => [groupLabel(name, labels), bounded(abilities[name])]);
}
export const priceText = (value, visible) => visible ? String(value || '').trim().slice(0, 32) : '';
export function portraitBounds(width, height, zoom = 100, offsetX = 0, offsetY = 0) {
  const scale = Math.min(180 / width, 180 / height) * zoom / 100;
  return {x: 185 - width * scale / 2 + offsetX, y: 272 - height * scale + offsetY,
    width: width * scale, height: height * scale};
}
export function portraitRegion(width, height, face) {
  if (!face) return {x: 0, y: 0, width, height};
  const size = Math.max(1, Math.floor(Math.min(Math.max(face.width, face.height) * 2.6, width, height)));
  return {
    x: Math.floor(Math.max(0, Math.min(width - size, face.originX + face.width / 2 - size / 2))),
    y: Math.floor(Math.max(0, Math.min(height - size, face.originY - face.height * .6))),
    width: size, height: size,
  };
}

function cardText(ctx, value, centerX, top, lineHeight, size, color, weight = 400, maxWidth, family = 'PlayerCard') {
  ctx.font = `${weight} ${size}px ${family}, sans-serif`;
  ctx.letterSpacing = '-1px';
  ctx.textAlign = 'center'; ctx.fillStyle = color;
  const metrics = ctx.measureText(String(value));
  const ascent = metrics.fontBoundingBoxAscent ?? metrics.actualBoundingBoxAscent;
  const descent = metrics.fontBoundingBoxDescent ?? metrics.actualBoundingBoxDescent;
  ctx.fillText(String(value), centerX, top + (lineHeight + ascent - descent) / 2, ...(maxWidth ? [maxWidth] : []));
}

function canvas(width, height) {
  const el = document.createElement('canvas');
  el.width = width;
  el.height = height;
  return el;
}
function text(ctx, value, x, y, size = 16, color = '#eaeefb', align = 'left', maxWidth) {
  ctx.fillStyle = color;
  ctx.font = `700 ${size}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
  ctx.textAlign = align;
  ctx.fillText(String(value), x, y, ...(maxWidth ? [maxWidth] : []));
}
function image(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('이미지를 불러오지 못했습니다.'));
    img.src = new URL(url, ROOT).href;
  });
}
async function json(name) {
  const response = await fetch(new URL(name, ROOT));
  if (!response.ok) throw new Error('편집기 자료를 불러오지 못했습니다.');
  return response.json();
}

let initPromise;
export function init() {
  if (!initPromise) initPromise = start().catch(error => { initPromise = null; throw error; });
  return initPromise;
}

async function start() {
  const form = document.getElementById('makerForm');
  const control = name => form.elements.namedItem(name);
  const status = document.getElementById('makerStatus');
  const say = (message, error = false) => { status.textContent = message; status.dataset.error = String(error); };
  const [{seasons}, nations, details, {traits: traitIcons}] = await Promise.all([
    json('seasons.json'), json('nations.json'), json('card-details.json'), json('traits.json'),
  ]);
  const [league, platinum] = await Promise.all([
    image(details.league.file), image(details.platinum.file),
    ...details.fonts.map(async asset => {
      const face = new FontFace(asset.family, `url("${new URL(asset.file, ROOT).href}")`, {weight: asset.weight});
      document.fonts.add(await face.load());
    }),
  ]);
  const preferred = [856, 863, 100, 101];
  const designs = seasons.filter(s => s.frame && s.emblem).sort((a, b) => {
    const rank = s => preferred.includes(s.id) ? preferred.indexOf(s.id) : 4;
    return rank(a) - rank(b) || a.name.localeCompare(b.name, 'ko', {numeric: true});
  });
  control('season').replaceChildren(...designs.map(s => new Option(s.name, s.id)));
  control('season').disabled = false;
  control('nation').replaceChildren(...nations.map(n => new Option(n.name, n.id)));
  control('nation').value = '167';
  control('nation').disabled = false;
  control('club').replaceChildren(...details.clubs.map(c => new Option(c.name, c.id)));
  control('club').value = '130172';
  control('club').disabled = false;
  const abilities = {};
  const groupNames = {};
  const abilityRoot = document.getElementById('makerAbilities');
  abilityRoot.replaceChildren();
  ABILITIES.forEach((group, groupIndex) => {
    const row = document.createElement('div');
    row.className = 'maker-ability-group maker-fields';
    const nameLabel = document.createElement('label');
    nameLabel.className = 'maker-field';
    nameLabel.textContent = `${groupIndex + 1}번 항목 이름`;
    const nameInput = document.createElement('input');
    nameInput.type = 'text'; nameInput.maxLength = 12; nameInput.value = group; nameInput.placeholder = group;
    nameInput.id = `makerGroupName-${groupIndex}`; nameLabel.htmlFor = nameInput.id;
    nameInput.setAttribute('aria-label', `${group} 항목 이름`);
    nameInput.oninput = () => { groupNames[group] = nameInput.value; };
    nameLabel.append(nameInput);
    const valueLabel = document.createElement('label');
    valueLabel.className = 'maker-field'; valueLabel.textContent = '수치';
    const input = document.createElement('input');
    input.type = 'number'; input.min = '0'; input.max = '200'; input.value = '120';
    input.inputMode = 'numeric'; input.id = `makerAbility-${groupIndex}`;
    input.setAttribute('aria-label', `${group} 수치`); valueLabel.htmlFor = input.id;
    input.oninput = () => { abilities[group] = bounded(input.value); };
    input.onchange = () => { input.value = bounded(input.value); };
    abilities[group] = 120;
    valueLabel.append(input); row.append(nameLabel, valueLabel); abilityRoot.append(row);
  });

  const card = document.getElementById('makerCard');
  const radar = document.getElementById('makerRadar');
  const photoInput = document.getElementById('makerPhoto');
  const saveCard = document.getElementById('makerSaveCard');
  const saveAll = document.getElementById('makerSaveAll');
  const traitFilter = document.getElementById('makerTraitFilter');
  const traitPicker = document.getElementById('makerTraitPicker');
  const traitNameInput = document.getElementById('makerTraitName');
  const addTraitButton = document.getElementById('makerAddTrait');
  const selectedTraits = [];
  let chosenTrait, loadingTrait = false;
  let design, frame, emblem, nation, flag, club, crest, photo, processing = false, loadingDesign = false, loadingNation = false, loadingClub = false;
  let selection = 0, nationSelection = 0, clubSelection = 0, photoSelection = 0;
  let portraitToolsPromise;

  function state() {
    return {
      name: control('playerName').value.trim() || '나만의 선수',
      position: control('position').value, ovr: bounded(control('ovr').value, 1),
      salary: bounded(control('salary').value, 1, 99),
      grade: bounded(control('grade').value, 1, 13),
      height: bounded(control('height').value, 100, 250), weight: bounded(control('weight').value, 30, 200),
      body: control('body').value, left: control('leftFoot').value, right: control('rightFoot').value,
      price: priceText(control('price').value, control('showPrice').checked),
    };
  }
  function buttons() {
    saveCard.disabled = saveAll.disabled = !frame || !emblem || !flag || !crest || processing || loadingDesign || loadingNation || loadingClub || loadingTrait;
    addTraitButton.disabled = !chosenTrait || loadingTrait;
  }
  function drawCard(s) {
    const ctx = card.getContext('2d');
    ctx.clearRect(0, 0, card.width, card.height);
    if (!frame) return;
    ctx.save(); ctx.scale(2, 2);
    ctx.drawImage(frame, 0, 0, 340, 400);
    const color = design.color;
    ctx.save();
    ctx.beginPath(); ctx.rect(95, 32, 180, 240); ctx.clip();
    if (photo) {
      const bounds = portraitBounds(photo.width, photo.height, Number(control('zoom').value), Number(control('photoX').value), Number(control('photoY').value));
      const layer = canvas(340, 400), layerCtx = layer.getContext('2d');
      layerCtx.drawImage(photo, bounds.x, bounds.y, bounds.width, bounds.height);
      const fade = layerCtx.createLinearGradient(0, 245, 0, 272);
      fade.addColorStop(0, '#000'); fade.addColorStop(1, 'transparent');
      layerCtx.globalCompositeOperation = 'destination-in'; layerCtx.fillStyle = fade; layerCtx.fillRect(0, 0, 340, 400);
      ctx.drawImage(layer, 0, 0);
    } else {
      ctx.fillStyle = 'rgba(10,15,35,.28)';
      ctx.beginPath(); ctx.arc(185, 145, 30, 0, Math.PI * 2); ctx.fill();
      ctx.beginPath(); ctx.ellipse(185, 260, 60, 70, 0, Math.PI, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
    cardText(ctx, s.ovr, 101, 90, 29, 40, color, 500);
    cardText(ctx, s.position, 101, 130, 19, 25, color);
    ctx.beginPath();
    [[101, 159], [117, 166], [117, 184], [101, 191], [85, 184], [85, 166]].forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.closePath(); ctx.fillStyle = design.salaryFill; ctx.fill();
    ctx.strokeStyle = design.salaryBorder; ctx.lineWidth = 2; ctx.stroke();
    cardText(ctx, s.salary, 101, 158, 36, 20, color, 400, undefined, 'PlayerSalary');
    const gradeColors = s.grade >= 11 ? ['#bdc5e5', '#607dc4', '#2d2b43']
      : s.grade >= 8 ? ['#f9dd62', '#dca908', '#695100']
        : s.grade >= 5 ? ['#d8d9dc', '#b8bdca', '#4e545e'] : ['#51545a', '#42464d', '#c5c8c9'];
    const gradient = ctx.createLinearGradient(72, 236, 130, 272);
    gradient.addColorStop(0, gradeColors[0]); gradient.addColorStop(1, gradeColors[1]);
    ctx.fillStyle = gradient; ctx.fillRect(72, 236, 58, 36);
    if (s.grade >= 11) ctx.drawImage(platinum, 72, 236, 58, 36);
    ctx.strokeStyle = gradeColors[0]; ctx.lineWidth = 1.5; ctx.strokeRect(72.75, 236.75, 56.5, 34.5);
    cardText(ctx, s.grade, 101, 236, 36, 30, gradeColors[2], 600);
    ctx.font = '400 24px PlayerCard, sans-serif';
    const nameWidth = Math.min(174, ctx.measureText(s.name).width);
    const nameLeft = 170 - (30 + 6 + nameWidth) / 2;
    ctx.drawImage(emblem, nameLeft, 282, 30, 24);
    cardText(ctx, s.name, nameLeft + 36 + nameWidth / 2, 282, 24, 24, design.nameColor, 400, nameWidth);
    if (flag) ctx.drawImage(flag, 125, 327 - 12 * flag.height / flag.width, 24, 24 * flag.height / flag.width);
    ctx.drawImage(league, 154, 311, 32, 32);
    if (crest) {
      const scale = Math.min(24 / crest.width, 24 / crest.height);
      ctx.drawImage(crest, 203 - crest.width * scale / 2, 327 - crest.height * scale / 2, crest.width * scale, crest.height * scale);
    }
    ctx.restore();
  }
  function render() {
    const s = state();
    drawCard(s);
    const values = radarValues(abilities, groupNames);
    drawRadar(radar, values);
    radar.setAttribute('aria-label', values.map(([name, value]) => `${name} ${value}`).join(', '));
    card.setAttribute('aria-label', `${design?.name || ''} ${s.name}, ${s.position}, OVR ${s.ovr}, 급여 ${s.salary}, ${s.grade}강, ${nation?.name || ''}, FC 아이콘 리그, ${club?.name || ''}`);
    const chips = [nation?.name, club?.name, `급여 ${s.salary}`, `${s.grade}강`, `${s.height}cm`, `${s.weight}kg`, s.body, `L${s.left} – R${s.right}`, s.price].filter(Boolean);
    document.getElementById('makerSummary').replaceChildren(...chips.map(value => {
      const span = document.createElement('span'); span.textContent = value; return span;
    }));
    control('price').disabled = !control('showPrice').checked;
    control('price').closest('label').hidden = !control('showPrice').checked;
    document.getElementById('makerCaption').textContent = `${design?.name || ''} · ${s.name}`;
    for (const [kind, label, value] of [['Season', '시즌', design], ['Nation', '국가', nation], ['Club', '클럽', club]]) {
      document.getElementById(`maker${kind}Current`).textContent = `현재 ${label}: ${value?.name || '불러오는 중'}`;
    }
    const preview = document.getElementById('makerTraitsPreview');
    document.getElementById('makerTraitsSection').hidden = !selectedTraits.length;
    preview.replaceChildren(...selectedTraits.map(trait => {
      const chip = document.createElement('span'); chip.className = `trait${trait.is_new ? ' new' : ''}`;
      const icon = trait.icon.cloneNode(); icon.className = 'trait-ico'; icon.alt = '';
      const name = document.createElement('span'); name.className = 'trait-nm'; name.textContent = traitLabel(trait.name, trait.originalName);
      chip.append(icon, name);
      if (trait.is_new) {
        const badge = document.createElement('span'); badge.className = 'trait-new'; badge.textContent = '신규'; chip.append(badge);
      }
      return chip;
    }));
  }
  function chooseTrait(trait) {
    chosenTrait = trait;
    for (const button of traitPicker.children) button.setAttribute('aria-pressed', String(Number(button.dataset.code) === trait.code));
    document.getElementById('makerTraitChoice').textContent = `${trait.is_new ? '신규' : '일반'} 특성 · ${trait.name}`;
    traitNameInput.value = trait.name;
    buttons();
  }
  function showTraitIcons() {
    const icons = traitIcons.filter(t => traitFilter.value === 'all' || t.is_new === (traitFilter.value === 'new'));
    traitPicker.replaceChildren(...icons.map(trait => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'maker-trait-option';
      button.dataset.code = trait.code;
      button.title = `${trait.is_new ? '신규' : '일반'} 특성 · ${trait.name}`;
      button.setAttribute('aria-label', button.title); button.setAttribute('aria-pressed', 'false');
      const icon = document.createElement('img'); icon.src = new URL(trait.file, ROOT).href; icon.alt = ''; icon.loading = 'lazy';
      button.append(icon); button.onclick = () => chooseTrait(trait); return button;
    }));
    if (icons.length) chooseTrait(icons.find(t => t.code === chosenTrait?.code) || icons[0]);
  }
  function renderTraitEditor() {
    document.getElementById('makerTraitEditor').replaceChildren(...selectedTraits.map((trait, index) => {
      const row = document.createElement('div'); row.className = 'maker-trait-row';
      const icon = trait.icon.cloneNode(); icon.alt = '';
      const label = document.createElement('label'); label.className = 'maker-field';
      const kind = document.createElement('span'); kind.textContent = trait.is_new ? '신규 특성' : '일반 특성';
      const input = document.createElement('input'); input.type = 'text'; input.maxLength = 24; input.value = trait.name;
      input.id = `makerSelectedTrait-${index}`; label.htmlFor = input.id; input.setAttribute('aria-label', `${index + 1}번째 특성 이름`);
      input.oninput = () => { trait.name = input.value; };
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'maker-secondary'; remove.textContent = '삭제';
      remove.setAttribute('aria-label', `${index + 1}번째 특성 삭제`);
      remove.onclick = () => { selectedTraits.splice(index, 1); renderTraitEditor(); render(); addTraitButton.focus(); };
      label.append(kind, input); row.append(icon, label, remove); return row;
    }));
  }
  traitFilter.onchange = showTraitIcons;
  addTraitButton.onclick = async () => {
    if (addTraitButton.disabled) return;
    const trait = chosenTrait, name = traitLabel(traitNameInput.value, trait.name);
    loadingTrait = true; buttons();
    try {
      const icon = await image(trait.file);
      selectedTraits.push({code: trait.code, name, originalName: trait.name, is_new: trait.is_new, icon});
      renderTraitEditor(); render(); say(`${name} 특성을 추가했습니다.`);
    } catch { say('특성 아이콘을 불러오지 못했습니다. 다시 시도해주세요.', true); }
    finally { loadingTrait = false; buttons(); }
  };
  async function selectDesign() {
    const token = ++selection;
    const next = designs.find(s => String(s.id) === control('season').value);
    loadingDesign = true; buttons(); say('시즌 디자인을 적용하고 있습니다.');
    try {
      const images = await Promise.all([image(next.frame.file), image(next.emblem.file)]);
      if (token !== selection) return;
      [frame, emblem] = images; design = next; render(); say('내 사진을 선택하거나 정보를 바꿔보세요.');
    } catch {
      if (token !== selection) return;
      control('season').value = design ? String(design.id) : '';
      say('시즌 이미지를 불러오지 못했습니다. 다른 시즌을 선택하거나 다시 시도해주세요.', true);
    } finally { if (token === selection) { loadingDesign = false; buttons(); } }
  }
  async function selectNation() {
    const token = ++nationSelection;
    const next = nations.find(n => String(n.id) === control('nation').value);
    loadingNation = true; buttons();
    try {
      const nextFlag = await image(next.file);
      if (token !== nationSelection) return;
      nation = next; flag = nextFlag; render();
    } catch {
      if (token !== nationSelection) return;
      control('nation').value = nation ? String(nation.id) : '';
      say('국기를 불러오지 못했습니다. 다른 국가를 선택해주세요.', true);
    } finally { if (token === nationSelection) { loadingNation = false; buttons(); } }
  }
  async function selectClub() {
    const token = ++clubSelection;
    const next = details.clubs.find(c => String(c.id) === control('club').value);
    loadingClub = true; buttons();
    try {
      const nextCrest = await image(next.file);
      if (token !== clubSelection) return;
      club = next; crest = nextCrest; render();
    } catch {
      if (token !== clubSelection) return;
      control('club').value = club ? String(club.id) : '';
      say('클럽 이미지를 불러오지 못했습니다. 다른 클럽을 선택해주세요.', true);
    } finally { if (token === clubSelection) { loadingClub = false; buttons(); } }
  }

  async function removeBackground(source) {
    if (!portraitToolsPromise) portraitToolsPromise = (async () => {
      const {FilesetResolver, ImageSegmenter, FaceDetector} = await import('./vendor/vision_bundle.mjs');
      const wasm = await FilesetResolver.forVisionTasks(new URL('vendor/wasm', ROOT).href);
      const detector = await FaceDetector.createFromOptions(wasm, {
        baseOptions: {modelAssetPath: new URL('vendor/blaze_face_short_range.tflite', ROOT).href},
        runningMode: 'IMAGE',
      });
      try {
        const segmenter = await ImageSegmenter.createFromOptions(wasm, {
          baseOptions: {modelAssetPath: new URL('vendor/selfie_segmenter.tflite', ROOT).href},
          runningMode: 'IMAGE', outputCategoryMask: false, outputConfidenceMasks: true,
        });
        return {detector, segmenter};
      } catch (error) { detector.close(); throw error; }
    })().catch(error => { portraitToolsPromise = null; throw error; });
    const {detector, segmenter} = await portraitToolsPromise;
    const faces = detector.detect(source).detections;
    const face = faces.reduce((largest, item) => {
      const box = item.boundingBox;
      return !largest || box.width * box.height > largest.width * largest.height ? box : largest;
    }, null);
    // Frame the main person before segmentation so retained scenery cannot shrink the face.
    const region = portraitRegion(source.width, source.height, face);
    const portrait = canvas(region.width, region.height);
    portrait.getContext('2d').drawImage(source, region.x, region.y, region.width, region.height, 0, 0, region.width, region.height);
    const result = segmenter.segment(portrait);
    try {
      // The selfie model has background/person channels; some versions expose only foreground.
      const mask = result.confidenceMasks[result.confidenceMasks.length - 1];
      const confidence = mask.getAsFloat32Array();
      const maskCanvas = canvas(mask.width, mask.height);
      const maskCtx = maskCanvas.getContext('2d');
      const pixels = maskCtx.createImageData(mask.width, mask.height);
      let foreground = 0;
      for (let i = 0; i < confidence.length; i++) {
        const alpha = Math.max(0, Math.min(1, (confidence[i] - .25) / .5));
        pixels.data[i * 4 + 3] = Math.round(alpha * 255);
        foreground += alpha;
      }
      if (foreground < confidence.length * .005) throw new Error('사진에서 사람을 찾지 못했습니다. 인물이 크게 나온 사진을 선택해주세요.');
      maskCtx.putImageData(pixels, 0, 0);
      const cutout = canvas(portrait.width, portrait.height), ctx = cutout.getContext('2d');
      ctx.drawImage(portrait, 0, 0);
      ctx.globalCompositeOperation = 'destination-in';
      ctx.drawImage(maskCanvas, 0, 0, portrait.width, portrait.height);
      return cropPortrait(cutout);
    } finally { result.close(); }
  }

  photoInput.onchange = async () => {
    const file = photoInput.files[0];
    if (!file) return;
    const token = ++photoSelection;
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 15 * 1024 * 1024) {
      say('15 MB 이하의 JPG·PNG·WebP 사진을 선택해주세요.', true); photoInput.value = ''; return;
    }
    processing = true; photoInput.disabled = true; buttons();
    say('사진 배경을 제거하고 있습니다. 처음에는 처리 도구를 준비하는 데 잠시 걸립니다.');
    let bitmap;
    try {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      bitmap = await createImageBitmap(file);
      if (bitmap.width * bitmap.height > 50_000_000) throw new Error('사진이 너무 큽니다. 5천만 화소 이하로 줄여주세요.');
      const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
      const source = canvas(Math.max(1, Math.round(bitmap.width * scale)), Math.max(1, Math.round(bitmap.height * scale)));
      source.getContext('2d').drawImage(bitmap, 0, 0, source.width, source.height);
      const cutout = await removeBackground(source);
      if (token !== photoSelection) return;
      photo = cutout;
      control('zoom').value = '100'; control('photoX').value = '0'; control('photoY').value = '0';
      document.getElementById('makerPhotoControls').hidden = false;
      render(); say('배경 제거가 완료됐습니다. 사진 크기와 위치를 조절해보세요.');
    } catch (error) {
      if (token === photoSelection) say(error.message?.includes('사진') ? error.message : '배경을 제거하지 못했습니다. 브라우저를 업데이트하거나 다른 사진으로 다시 시도해주세요.', true);
    } finally {
      bitmap?.close();
      if (token === photoSelection) { processing = false; photoInput.disabled = false; photoInput.value = ''; buttons(); }
    }
  };
  document.getElementById('makerRemovePhoto').onclick = () => {
    photoSelection++; photo = null; processing = false; photoInput.disabled = false; photoInput.value = '';
    document.getElementById('makerPhotoControls').hidden = true; render(); buttons(); say('사진을 지웠습니다.');
  };
  control('season').onchange = selectDesign;
  control('nation').onchange = selectNation;
  control('club').onchange = selectClub;
  form.oninput = event => { if (event.target.type !== 'search' && !['season', 'nation', 'club'].includes(event.target.name)) render(); };
  form.addEventListener('change', event => {
    if (event.target.type === 'number') {
      event.target.value = bounded(event.target.value, Number(event.target.min), Number(event.target.max));
      render();
    }
  });
  async function save(full) {
    if (saveAll.disabled) return;
    saveCard.disabled = saveAll.disabled = true;
    try {
      render();
      const snapshot = state();
      const output = full ? fullImage(card, snapshot, design, nation, flag, abilities, club, groupNames, selectedTraits) : card;
      const blob = await new Promise(resolve => output.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('PNG 생성 실패');
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = `${snapshot.name.replace(/[\\/:*?"<>|\x00-\x1f]/g, '_')}-${full ? '전체' : '카드'}.png`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      say(full ? '카드·기본 정보·능력치·그래프 이미지를 저장했습니다.' : '카드 이미지를 저장했습니다.');
    } catch { say('이미지를 저장하지 못했습니다. 다시 시도해주세요.', true); }
    finally { buttons(); }
  }
  saveCard.onclick = () => save(false);
  saveAll.onclick = () => save(true);
  for (const kind of ['Season', 'Nation', 'Club']) {
    const search = document.getElementById(`maker${kind}Search`);
    const select = control(kind.toLowerCase());
    const result = document.getElementById(`maker${kind}Results`);
    const choices = document.getElementById(`maker${kind}Choices`);
    const options = [...select.options];
    const close = () => {
      choices.hidden = true;
      result.textContent = '눌러서 전체 목록을 보거나 이름으로 검색하세요.';
    };
    const filter = () => {
      const matches = options.filter(option => choiceMatches(option.text, search.value));
      choices.replaceChildren(...matches.map(option => {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'maker-choice-option';
        button.textContent = option.text; button.setAttribute('aria-pressed', String(option.selected));
        button.onclick = async () => {
          select.value = option.value; search.value = option.text;
          close();
          await select.onchange();
          if (search.value === option.text) search.value = select.selectedOptions[0].text;
        };
        return button;
      }));
      choices.hidden = !matches.length;
      result.textContent = search.value.trim()
        ? matches.length ? `검색 결과 ${matches.length}개 · 선택하면 바로 적용됩니다.` : '검색 결과가 없습니다. 현재 선택은 유지됩니다.'
        : `전체 ${matches.length}개 · 스크롤해서 선택하거나 이름으로 검색하세요.`;
    };
    const cancel = () => { search.value = ''; search.focus(); close(); };
    search.onfocus = () => {
      if (search.value === select.selectedOptions[0].text) search.value = '';
      filter();
    };
    search.onclick = () => { if (choices.hidden) filter(); };
    search.onblur = choices.onfocusout = event => {
      if (event.relatedTarget !== search && !choices.contains(event.relatedTarget)) close();
    };
    search.onkeydown = event => {
      if (event.isComposing) return;
      if (event.key === 'Enter') { event.preventDefault(); if (!choices.hidden) choices.querySelector('button')?.click(); }
      if (event.key === 'ArrowDown') { event.preventDefault(); if (choices.hidden) filter(); choices.querySelector('button')?.focus(); }
      if (event.key === 'Escape') { event.preventDefault(); cancel(); }
    };
    choices.onkeydown = event => {
      if (event.key === 'Escape') { event.preventDefault(); cancel(); }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const buttons = [...choices.children], index = buttons.indexOf(document.activeElement);
        buttons[Math.max(0, Math.min(buttons.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus();
      }
    };
    search.oninput = filter; search.disabled = false; close();
  }
  showTraitIcons();
  await Promise.all([selectDesign(), selectNation(), selectClub()]);
}

function cropPortrait(source) {
  const data = source.getContext('2d').getImageData(0, 0, source.width, source.height).data;
  let left = source.width, right = 0, top = source.height, bottom = 0;
  for (let y = 0; y < source.height; y++) for (let x = 0; x < source.width; x++) {
    if (data[(y * source.width + x) * 4 + 3] > 24) {
      left = Math.min(left, x); right = Math.max(right, x); top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
  }
  if (left > right) throw new Error('사진에서 사람을 찾지 못했습니다.');
  const result = canvas(right - left + 1, bottom - top + 1);
  result.getContext('2d').drawImage(source, left, top, result.width, result.height, 0, 0, result.width, result.height);
  return result;
}

function drawRadar(target, values) {
  const ctx = target.getContext('2d'), cx = 320, cy = 270, radius = 175;
  ctx.clearRect(0, 0, target.width, target.height);
  const point = (i, scale) => [cx + Math.sin(i * Math.PI / 3) * radius * scale, cy - Math.cos(i * Math.PI / 3) * radius * scale];
  function polygon(scale) {
    ctx.beginPath();
    values.forEach(([, value], i) => {
      const p = point(i, scale === null ? value / 200 : scale);
      if (i) ctx.lineTo(...p); else ctx.moveTo(...p);
    });
    ctx.closePath();
  }
  ctx.strokeStyle = '#354261'; ctx.lineWidth = 1.5;
  for (let i = 1; i <= 4; i++) { polygon(i / 4); ctx.stroke(); }
  for (let i = 0; i < 6; i++) { ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(...point(i, 1)); ctx.stroke(); }
  polygon(null); ctx.fillStyle = 'rgba(240,209,122,.24)'; ctx.fill(); ctx.strokeStyle = '#f0d17a'; ctx.lineWidth = 3; ctx.stroke();
  values.forEach(([name, value], i) => {
    const [x, y] = point(i, 1.3);
    text(ctx, name, x, y, 22, '#bac4db', 'center', 180);
    text(ctx, value, x, y + 30, 26, '#f0d17a', 'center');
  });
}

function fullImage(card, s, design, nation, flag, abilities, club, groupNames, traits) {
  const output = canvas(1400, fullImageHeight(traits.length)), ctx = output.getContext('2d');
  ctx.fillStyle = '#04060f'; ctx.fillRect(0, 0, output.width, output.height);
  text(ctx, 'PLAYER STUDIO', 70, 68, 23, '#f0d17a');
  text(ctx, s.name, 70, 130, 44, '#eaeefb', 'left', 1220);
  text(ctx, `${design.name} · ${s.position} · OVR ${s.ovr} · 급여 ${s.salary} · ${s.grade}강`, 70, 178, 24, '#bac4db', 'left', 1260);
  ctx.drawImage(card, 35, 205, 680, 800);
  const chart = canvas(640, 560); drawRadar(chart, radarValues(abilities, groupNames));
  ctx.drawImage(chart, 720, 220);
  ctx.drawImage(flag, 780, 806, 40, 25);
  text(ctx, nation.name, 835, 828, 24);
  text(ctx, `${club.name} · FC 아이콘 리그`, 780, 872, 24, '#bac4db', 'left', 550);
  text(ctx, `${s.height}cm · ${s.weight}kg · ${s.body}`, 780, 914, 24);
  text(ctx, `L${s.left} – R${s.right}`, 780, 956, 25, '#f0d17a');
  if (s.price) text(ctx, s.price, 780, 1000, 25, '#f0d17a', 'left', 550);
  if (traits.length) {
    text(ctx, '특성', 70, 1090, 38);
    traits.forEach((trait, index) => {
      const x = 70 + index % 2 * 660, y = 1140 + Math.floor(index / 2) * 140;
      ctx.drawImage(trait.icon, x, y, 96, 96);
      text(ctx, traitLabel(trait.name, trait.originalName), x + 116, y + 38, 36, '#eaeefb', 'left', 500);
      text(ctx, trait.is_new ? '신규 특성' : '일반 특성', x + 116, y + 81, 22, trait.is_new ? '#f0d17a' : '#8792ac');
    });
  }
  text(ctx, '가상선수 카드', output.width / 2, output.height - 18, 14, '#535c72', 'center');
  return output;
}
