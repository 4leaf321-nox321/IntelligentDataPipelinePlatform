"""첨부가 **진짜 이미지인가** — 서버가 열어 보고 정한다. 그리고 작은 미리보기를 만든다.

## 브라우저가 말한 종류를 믿지 않는다

올릴 때 오는 `Content-Type` 은 클라이언트가 붙인 말이다 — `image/png` 라고 적고 HTML 을
보낼 수 있다. 그 말을 믿고 화면이 「이미지」 로 띄우면 스크립트가 앱의 주소에서 돈다.
그래서 **Pillow 로 실제로 열어 보고**, 아래 넷 중 하나로 읽힐 때만 이미지라고 한다.

## SVG 는 이미지가 아니다(여기서는)

SVG 는 글로 된 문서라 스크립트를 품을 수 있다. 래스터 넷만 미리보기 대상이고, SVG 는
다른 파일처럼 내려받기만 된다.

## 미리보기

상세 화면이 사진 수십 장을 원본으로 받으면 화면 하나가 수백 MB 다. 긴 변 `THUMB_EDGE`
의 WebP 를 만들어 filestore 에 같이 둔다(내용 주소라 같은 사진은 미리보기도 하나다).
휴대폰 사진은 픽셀은 눕혀 두고 EXIF 로 「세워서 보라」 고 적는다 — 미리보기는 그것을
반영해 세운다. 원본은 브라우저가 EXIF 를 읽어 스스로 세운다.
"""

from __future__ import annotations

import io
import logging
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

#: 미리보기로 띄울 수 있는 것 — Pillow 가 읽은 형식 → 내보낼 종류.
RASTER = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}

#: 미리보기의 긴 변(px). 상세 화면의 격자 한 칸이 160px 이라 고해상도 화면에서 두 배.
THUMB_EDGE = 320

#: 이보다 큰 그림은 이미지로 보지 않는다(파일로는 붙는다). 압축 폭탄 — 몇 KB 파일이
#: 풀면 수십 GB 가 되는 것 — 을 미리보기를 만들다 서버가 풀어 버리지 않게 한다.
#: 1억 화소면 휴대폰 · 카메라 사진(1200만 ~ 5000만)은 넉넉히 들어간다.
MAX_PIXELS = 100_000_000


@dataclass(frozen=True)
class Inspected:
    """서버가 확인한 이미지. 크기는 **세운 뒤**(EXIF 회전 반영)의 가로 · 세로다."""

    content_type: str
    width: int
    height: int


#: 열어 볼 것 — 저장소의 파일, 또는 아직 저장하지 않은 바이트(사진 일괄 업로드의 계획은
#: zip 안의 항목을 저장하지 않고 본다).
Source = Path | bytes


def _open(source: Source) -> Image.Image | None:
    """래스터 넷 중 하나로 열리면 그 그림, 아니면 None. 화소 수 상한을 넘어도 None.

    바이트면 열 때마다 새 스트림으로 — `verify()` 가 받은 스트림을 닫는다(PNG, 실측). 하나를
    되감아 다시 쓰면 `inspect` 의 두 번째 열기가 「닫힌 파일」 로 터졌다."""
    try:
        with warnings.catch_warnings():
            # Pillow 는 큰 그림에 경고만 하고 연다 — 그것을 오류로 바꿔 거른다.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(source if isinstance(source, Path) else io.BytesIO(source))
            if image.format not in RASTER:
                image.close()
                return None
            if image.width * image.height > MAX_PIXELS:
                image.close()
                return None
            return image
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        ValueError,
    ):
        return None


def inspect(source: Source) -> Inspected | None:
    """이미지면 (종류, 가로, 세로). 아니면 None.

    머리만 읽고 끝내지 않는다 — `verify()` 로 몸통까지 훑는다. 머리만 PNG 이고 나머지가
    깨진 파일은 미리보기를 만들 때에야 터지고, 그때는 이미 「이미지」 로 저장된 뒤다.
    """
    image = _open(source)
    if image is None:
        return None
    try:
        kind = RASTER[str(image.format)]
        image.verify()
    except Exception:  # Pillow 는 깨진 파일에 형식마다 다른 예외를 던진다
        return None
    finally:
        image.close()
    # verify() 뒤에는 다시 열어야 한다(Pillow 의 규칙). 회전을 반영한 크기를 잰다.
    again = _open(source)
    if again is None:
        return None
    try:
        width, height = again.size
        orientation = again.getexif().get(0x0112)  # EXIF Orientation
        if orientation in (5, 6, 7, 8):  # 90° · 270° 계열 — 가로 · 세로가 바뀐다
            width, height = height, width
        return Inspected(content_type=kind, width=width, height=height)
    except Exception:  # 회전 정보가 깨진 파일 — 이미지로 보지 않는다
        return None
    finally:
        again.close()


def thumbnail(path: Path) -> bytes | None:
    """긴 변 `THUMB_EDGE` 의 WebP. 만들 수 없으면 None(그때 화면은 파일 아이콘을 띄운다).

    움직이는 GIF 는 첫 장만 — 격자에서 수십 장이 함께 움직이면 화면을 읽을 수 없다.
    """
    image = _open(path)
    if image is None:
        return None
    try:
        # JPEG 는 줄여서 풀 수 있다 — 5000만 화소를 다 풀지 않고 필요한 만큼만.
        image.draft("RGB", (THUMB_EDGE * 2, THUMB_EDGE * 2))
        upright = ImageOps.exif_transpose(image)
        if upright.mode not in ("RGB", "RGBA"):
            upright = upright.convert("RGBA" if "A" in upright.getbands() else "RGB")
        upright.thumbnail((THUMB_EDGE, THUMB_EDGE))
        out = io.BytesIO()
        upright.save(out, format="WEBP", quality=80, method=4)
        return out.getvalue()
    except Exception:  # 미리보기는 없어도 된다. 업로드를 막지 않는다
        logger.warning("미리보기를 만들지 못함: %s", path.name, exc_info=True)
        return None
    finally:
        image.close()
