"""E2E 의 화면 차례 — `run.py` 가 세운 API(빌드한 화면) · 샘플 PLM 에 진짜 브라우저로.

`run.py` 가 아니라 `.work/e2e/venv` 의 Python 으로 돈다(Playwright 가 거기 있다). 환경
변수: E2E_BASE · E2E_MOCK · E2E_ADMIN(아이디 · 비밀번호 JSON 의 자리) · E2E_SHOTS ·
E2E_HEADED · E2E_CHROME.

단계마다 화면 사진을 남기고, 화면의 JavaScript 오류(pageerror)가 하나라도 나면 실패다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

BASE = os.environ["E2E_BASE"]
MOCK = os.environ["E2E_MOCK"]
ADMIN = json.loads(Path(os.environ["E2E_ADMIN"]).read_text(encoding="utf-8"))
SHOTS = Path(os.environ["E2E_SHOTS"])
FILE_SLUG = "mock-plm-model"
NEW_SLUG = "e2e-rest-models"
expect.set_options(timeout=20_000)


def cached_chrome() -> str | None:
    """Playwright 가 기대하는 Chromium 이 없으면 캐시에 있는 가장 새 headless shell."""
    given = os.environ.get("E2E_CHROME")
    if given:
        return given
    cache = Path.home() / ".cache" / "ms-playwright"
    found = sorted(
        cache.glob(
            "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"
        ),
        key=lambda one: int(re.sub(r"\D", "", one.parts[-3]) or 0),
    )
    return str(found[-1]) if found else None


class Steps:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.n = 0

    def done(self, name: str) -> None:
        self.n += 1
        self.page.screenshot(path=str(SHOTS / f"{self.n:02d}-{name}.png"), full_page=True)
        print(f"[e2e] ✓ {self.n:02d} {name}", flush=True)


def login(page: Page, steps: Steps) -> None:
    page.goto(f"{BASE}/login")
    page.get_by_label("아이디").fill(ADMIN["email"])
    page.get_by_label("비밀번호").fill(ADMIN["password"])
    page.get_by_role("button", name="로그인").click()
    page.wait_for_url(lambda url: "/login" not in url)
    steps.done("로그인")


def relay_map(page: Page, steps: Steps) -> None:
    page.goto(f"{BASE}/relay/map")
    expect(page.get_by_text(FILE_SLUG).first).to_be_visible()
    steps.done("연결 지도")


def definition_editor(page: Page, steps: Steps) -> None:
    """정의 파일에서 출발해 칸 하나를 고쳐 초안으로 — 그 판을 시험하면 원천(샘플 PLM)에
    닿는다."""
    page.goto(f"{BASE}/relay/pipelines/{FILE_SLUG}")
    panel = page.locator("section").filter(has=page.get_by_role("heading", name="정의 버전"))
    expect(panel).to_be_visible()
    panel.get_by_role("button", name="초안 작성").click()
    editor = page.get_by_test_id("definition-editor")
    size = editor.get_by_label("page_size", exact=True)
    expect(size).to_have_value("100")
    expect(editor.get_by_label("식별자", exact=True)).to_have_value("ModelId")
    size.fill("200")
    expect(editor.get_by_test_id("definition-changes")).to_have_text(
        "원본 대비 변경 사항: adapter.page_size"
    )
    steps.done("정의 편집")
    editor.get_by_label("정의 메모").fill("E2E — 쪽 크기")
    editor.get_by_role("button", name="초안 저장").click()
    expect(page.get_by_test_id("definition-saved")).to_contain_text("버전 1 초안을 저장했습니다")
    expect(panel.get_by_text("E2E — 쪽 크기")).to_be_visible()

    panel.get_by_role("button", name="정의 버전 1 테스트").click()
    expect(panel.get_by_text("정의 버전 1 테스트")).to_be_visible(timeout=60_000)
    expect(panel.get_by_text("수신 성공").first).to_be_visible()
    steps.done("정의 시험")


def new_connection(page: Page, steps: Steps) -> None:
    """샘플 PLM 의 REST 판을 탐색해 칸을 고르고 정의 판 초안까지 — 대상은 없어도 저장된다."""
    page.goto(f"{BASE}/relay/pipelines/new")
    page.get_by_label("base_url", exact=True).fill(MOCK)
    page.get_by_label("path", exact=True).fill("/api/v1/dev-models")
    page.get_by_label("rows_path", exact=True).fill("items")
    page.get_by_label("인증 방식").select_option("bearer")
    page.get_by_label("인증 정보의 환경 변수 이름").fill("MOCK_PLM_TOKEN")
    page.get_by_role("button", name="탐색").click()
    key = page.get_by_label("model_id 식별자", exact=True)
    expect(key).to_be_visible(timeout=30_000)
    key.check()
    steps.done("새 연결 탐색")
    page.get_by_label("파이프라인 slug").fill(NEW_SLUG)
    page.get_by_label("원천 이름").fill("plm")
    page.get_by_label("대상 SP 주소").fill("http://127.0.0.1:8087")
    page.get_by_label("대상 토큰의 환경 변수").fill("RELAY_TARGET_TOKEN")
    page.get_by_role("button", name=re.compile("정의 버전 초안 저장")).click()
    expect(page.get_by_text("정의 버전 1 초안을 저장했습니다")).to_be_visible()
    steps.done("새 연결 정의 저장")

    page.goto(f"{BASE}/relay/pipelines/{NEW_SLUG}")
    panel = page.locator("section").filter(has=page.get_by_role("heading", name="정의 버전"))
    expect(panel.get_by_text("버전 1", exact=True)).to_be_visible()
    steps.done("새 파이프라인 상세")


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as play:
        try:
            browser = play.chromium.launch(headless=not os.environ.get("E2E_HEADED"))
        except PlaywrightError:
            chrome = cached_chrome()
            if not chrome:
                raise
            browser = play.chromium.launch(
                headless=not os.environ.get("E2E_HEADED"), executable_path=chrome
            )
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="ko-KR")
        page.on("pageerror", lambda failure: errors.append(str(failure)))
        steps = Steps(page)
        try:
            login(page, steps)
            relay_map(page, steps)
            definition_editor(page, steps)
            new_connection(page, steps)
        except Exception:
            page.screenshot(path=str(SHOTS / "failed.png"), full_page=True)
            raise
        finally:
            browser.close()
    if errors:
        print("[e2e] 화면의 JavaScript 오류:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
