#!/usr/bin/env python3
"""트레잇 아이콘을 배경색으로 분류해 신규특성(금색) 코드 집합을 재생성한다.

fc-info/넥슨 CDN 의 트레잇 아이콘(traits/trait_icon_{code}.png)은 신규특성이면 배경이
금색, 일반특성이면 회색이다. HTML 마크업엔 구분 표식이 없어(클래스 동일) 아이콘 픽셀
색으로만 판별한다. 이 스크립트가 뽑은 코드 집합을 config.NEW_TRAIT_CODES 에 반영한다.

새 게임 업데이트로 금색 코드가 늘면 이 스크립트를 다시 돌려 상수를 갱신하면 된다.

의존성: `pip install -e '.[tools]'` (Pillow) — 런타임 아닌 1회성 도구.
사용:  python scripts/classify_new_traits.py
"""

from __future__ import annotations

from io import BytesIO

import httpx
from PIL import Image

CDN = "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/traits"
MAX_CODE = 99   # 존재하는 코드까지만 살핀다(없으면 404)


def is_gold(im: Image.Image) -> bool:
    """육각형 배경 가장자리 픽셀을 샘플해 금색(따뜻+밝음) 여부를 판정."""
    w, h = im.size
    px = im.load()
    pts = [(0.5, 0.1), (0.1, 0.5), (0.9, 0.5), (0.5, 0.9), (0.15, 0.2), (0.85, 0.2)]
    samples = []
    for fx, fy in pts:
        r, g, b, a = px[int(w * fx), int(h * fy)]
        if a > 200:
            samples.append((r, g, b))
    if not samples:
        return False
    r = sum(s[0] for s in samples) / len(samples)
    b = sum(s[2] for s in samples) / len(samples)
    return (r - b) > 35 and r > 140   # 금색: R 이 B 보다 확실히 높고 밝음


def main() -> None:
    gold: list[int] = []
    with httpx.Client(timeout=20, headers={"User-Agent": "gk-save-rate-analysis"}) as cli:
        for code in range(1, MAX_CODE + 1):
            resp = cli.get(f"{CDN}/trait_icon_{code:02d}.png")
            if resp.status_code != 200:
                continue
            im = Image.open(BytesIO(resp.content)).convert("RGBA")
            if is_gold(im):
                gold.append(code)
    print("NEW_TRAIT_CODES = frozenset({" + ", ".join(map(str, gold)) + "})")


if __name__ == "__main__":
    main()
