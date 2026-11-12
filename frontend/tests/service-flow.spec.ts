import { expect, test, type Page } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("/api/lab/settings", { data: { speed: 8 } });
});

async function startSample(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /예시 영상/ }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "분석 시작" }).click();
  await expect(page).toHaveURL(/\/cases\//);
  await expect(page.getByRole("heading", { name: /같은 사람인가요/ })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel("같은 사람", { exact: true }).check();
  await page.getByRole("button", { name: "확인하고 계속" }).click();
  await expect(page.getByRole("heading", { name: /판단에 필요한 사실/ })).toBeVisible({ timeout: 60_000 });
}

test("uploader goes from the sample video to a verified output", async ({ page }) => {
  await startSample(page);
  await page.locator('input[name="q-Q1"][value="public"]').check();
  await page.locator('input[name="q-Q2"][value="yes"]').check();
  await page.locator('input[name="q-Q3"][value="no"]').check();
  await page.getByRole("button", { name: /답변 보내기/ }).click();

  await expect(page.getByRole("button", { name: "감사 요청" })).toBeVisible({ timeout: 60_000 });
  // redaction strength is not a user control
  await expect(page.getByLabel(/강도/)).toHaveCount(0);

  // something on screen is kept or covered with a mosaic: there is no other treatment to choose, anywhere on the screen
  const methods = (id: string) => page.locator(`input[name="method-${id}"]`).evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value));
  for (const id of ["D-1", "D-2", "D-3", "D-4", "D-5"]) expect(await methods(id)).toEqual(["keep", "mosaic"]);
  await expect(page.getByRole("radiogroup", { name: "D-1 처리 방식" }).locator("label")).toHaveText(["유지", "모자이크"]);
  await expect(page.locator(".review")).not.toContainText(/흐림|가림|대안/);

  // the file's location tag is a decision too: kept or removed, with no shape, no range and no place on the timeline
  const location = page.locator(".dcard", { hasText: "촬영 위치 정보" });
  expect(await methods("D-6")).toEqual(["keep", "remove"]);
  await expect(location.getByRole("radiogroup").locator("label")).toHaveText(["유지", "삭제"]);
  await expect(page.locator('input[name="method-D-6"][value="remove"]')).toBeChecked();
  await expect(location.locator(".tag-file")).toHaveText("파일 정보");
  await expect(location.locator(".ranges, select")).toHaveCount(0);
  await expect(location).toContainText("REQ-5");
  await expect(page.locator(".dtl .dtl-row", { hasText: "D-6" })).toHaveCount(0);
  await expect(page.locator(".dtl-file-row")).toContainText(["D-6 촬영 위치 정보"]);
  await page.getByRole("button", { name: "감사 요청" }).click();

  await expect(page.getByRole("heading", { name: /감사에서 확인할 사항/ })).toBeVisible({ timeout: 60_000 });
  const cards = await page.locator(".fcard").all();
  expect(cards.length).toBeGreaterThan(0);
  for (const card of cards) await card.getByText("권고 적용").click();
  await page.getByRole("button", { name: /출력 영상 만들기/ }).click();

  await expect(page.getByRole("heading", { name: /원본과 같은 영상/ })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("table").first()).toContainText("프레임 수");
  // the checks are listed as the report has them (one more because the location tag was removed), all passed
  const verification = page.locator("section", { has: page.getByRole("heading", { name: "출력 검증" }) });
  await expect(verification.locator("tbody tr", { hasText: "위치 메타데이터 삭제" }).locator(".verdict-pass")).toHaveText("통과");
  await expect(verification.locator(".verdict-fail")).toHaveCount(0);
  // the script lists the file-wide decision without a time range
  const fileRow = page.locator("table.script tbody tr").last();
  await expect(fileRow.locator("td").nth(0)).toHaveText("파일 전체");
  await expect(fileRow.locator("td").nth(1)).toHaveText("촬영 위치 정보");
  await expect(fileRow.locator("td").nth(2)).toHaveText("삭제");
  await expect(fileRow.locator("button")).toHaveCount(0);
  await expect(page.locator(".result")).not.toContainText(/흐림|가림/);
  await expect(page.getByRole("link", { name: /보고서 JSON/ })).toBeVisible();
});

test("keeping the location tag against its requirement is caught by the audit", async ({ page }) => {
  await startSample(page);
  await page.locator('input[name="q-Q1"][value="public"]').check();
  await page.locator('input[name="q-Q2"][value="yes"]').check();
  await page.locator('input[name="q-Q3"][value="no"]').check();
  await page.getByRole("button", { name: /답변 보내기/ }).click();
  await expect(page.getByRole("button", { name: "감사 요청" })).toBeVisible({ timeout: 60_000 });

  const location = page.locator(".dcard", { hasText: "촬영 위치 정보" });
  await page.locator('input[name="method-D-6"][value="keep"]').check();
  await expect(location.locator(".dcard-warning")).toBeVisible();
  await expect(page.locator(".dtl-file-row")).toContainText("유지");
  await page.getByRole("button", { name: "감사 요청" }).click();

  // the finding is about the file, so it shows no scene; applying it removes the tag again
  await expect(page.getByRole("heading", { name: /감사에서 확인할 사항/ })).toBeVisible({ timeout: 60_000 });
  const finding = page.locator(".fcard", { hasText: "REQ-5" });
  await expect(finding.locator(".tag-file")).toBeVisible();
  await expect(finding.locator(".fcard-frame")).toHaveCount(0);
  for (const card of await page.locator(".fcard").all()) await card.getByText("권고 적용").click();
  await page.getByRole("button", { name: /출력 영상 만들기/ }).click();
  await expect(page.getByRole("heading", { name: /원본과 같은 영상/ })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator("section", { has: page.getByRole("heading", { name: "출력 검증" }) })).toContainText("위치 메타데이터 삭제");
});

test("a late answer in review re-evaluates only the dependent decision", async ({ page }) => {
  await startSample(page);
  await page.locator('input[name="q-Q1"][value="public"]').check();
  await page.locator('input[name="q-Q2"][value="later"]').check();
  await page.locator('input[name="q-Q3"][value="no"]').check();
  await page.getByRole("button", { name: /답변 보내기/ }).click();
  await expect(page.getByRole("button", { name: "감사 요청" })).toBeVisible({ timeout: 60_000 });

  // without the consent answer the interviewee's face is covered conservatively
  await expect(page.locator('input[name="method-D-4"][value="mosaic"]')).toBeChecked();
  await page.getByRole("tab", { name: /미답 질문/ }).click();
  await page.locator('input[name="deferred-Q2"][value="yes"]').check();
  await page.getByRole("button", { name: "답하고 다시 평가" }).click();
  await expect(page.getByRole("status")).toContainText("D-4", { timeout: 30_000 });
  await page.getByRole("tab", { name: /결정/ }).click();
  await expect(page.locator('input[name="method-D-4"][value="keep"]')).toBeChecked();
});

/** the case page with the server's answer changed on the way: for states the mock never reaches on its own */
async function openCaseAs(page: Page, change: (detail: Record<string, any>) => void): Promise<string> {
  const id = new URL(page.url()).pathname.split("/").pop() ?? "";
  const detail = await (await page.request.get(`/api/cases/${id}`)).json();
  change(detail);
  await page.route(`**/api/cases/${id}`, (route) => route.fulfill({ json: detail }));
  await page.reload();
  return id;
}

test("a run that covered only part of the pipeline says so and points to the Lab", async ({ page }) => {
  await startSample(page);
  const id = await openCaseAs(page, (detail) => Object.assign(detail, { status: "COMPLETED", stoppedAfter: "legal_analyst", report: null }));
  await expect(page.getByRole("heading", { name: "요청한 구간까지만 실행한 실험용 실행입니다" })).toBeVisible();
  await expect(page.locator(".stage-lead")).toContainText("법적 판단까지 실행하고 마쳤습니다");
  await expect(page.getByRole("link", { name: "실험실에서 이 실행 보기" })).toHaveAttribute("href", `/lab/runs/${id}`);
  // the steps it ran are done, the rest were never reached, and nothing claims a result
  await expect(page.locator(".step.is-done")).toHaveText([/업로드/, /영상 분석/, /대상 확인/, /법적 판단/]);
  await expect(page.locator(".step.is-current")).toHaveCount(0);
  await expect(page.locator(".case-head .eyebrow")).toHaveText("일부 구간만 실행");
  await expect(page.locator(".result")).toHaveCount(0);
});

test("a plan stored with a method that no longer exists is shown as stored, without that method as a choice", async ({ page }) => {
  await startSample(page);
  await page.locator('input[name="q-Q1"][value="public"]').check();
  await page.locator('input[name="q-Q2"][value="later"]').check();
  await page.locator('input[name="q-Q3"][value="no"]').check();
  await page.getByRole("button", { name: /답변 보내기/ }).click();
  await expect(page.getByRole("button", { name: "감사 요청" })).toBeVisible({ timeout: 60_000 });

  await openCaseAs(page, (detail) => {
    detail.plan.decisions.find((d: { id: string }) => d.id === "D-1").method = "blur";
  });
  await expect(page.getByRole("button", { name: "감사 요청" })).toBeVisible();
  const card = page.locator(".dcard", { has: page.locator('input[name="method-D-1"]') });
  await expect(card.locator('input[name="method-D-1"]')).toHaveCount(2);
  await expect(card.locator('input[name="method-D-1"]:checked')).toHaveCount(0);
  await expect(card).toContainText("저장된 처리 방식 ‘blur’");
  // the stored decision is still drawn as covered: the preview never shows it as the bare picture
  await expect(page.locator(".player-frame .scene .ov-mosaic")).not.toHaveCount(0);
});
