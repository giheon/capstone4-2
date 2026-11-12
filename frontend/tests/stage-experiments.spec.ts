// Section experiments and video datasets on the M2 mock (playwright.config.ts, project "mock"). A section
// experiment is a set of runs over one range that keeps growing (docs/design/16 §16.4). The mock starts with four
// sets (mock/stage.ts): two over 영상 정보 추출, one over 법률 분석 → 계획 that asks the user (with a run that
// waits for an answer), and one over 계획 whose runs continue an answered run. A launched run takes a few seconds.
import { type Locator, type Page, expect, test } from "@playwright/test";
import { compared, idle, part, plan, runCount, runRows } from "./helpers";

test.beforeEach(async ({ request, page }) => {
  await request.post("/api/lab/settings", { data: { speed: 8 } });
  // every confirmation is asked inside the page
  page.on("dialog", (dialog) => {
    throw new Error(`unexpected browser dialog: ${dialog.message()}`);
  });
});

const rail = (page: Page) => page.getByRole("group", { name: "실험할 구간" });
const section = (page: Page, name: string) => rail(page).locator(".rail-sec", { hasText: name });

/** the cell of the comparison table under the metric with this id */
async function metricCell(page: Page, row: Locator, metric: string): Promise<Locator> {
  const heads = page.locator("#sx-compare-title ~ .table-wrap").first().locator("thead th");
  const index = await heads.evaluateAll((ths, id) => ths.findIndex((th) => (th as HTMLElement).dataset.metric === id), metric);
  expect(index, `column of ${metric}`).toBeGreaterThan(0);
  return row.locator("th, td").nth(index);
}

test("a set is made, grows launch by launch, and compares combinations over two datasets", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/lab/experiments");
  await expect(page.getByRole("heading", { name: "실험", exact: true })).toBeVisible();
  // one row per set: range, whether it asks the user, combinations, videos, runs, waiting answers, cost, state
  const asking = page.locator('.sx-list tbody tr[data-experiment="sx_seed_ask_e452"]');
  await expect(asking).toContainText("법률 분석 → 계획");
  await expect(asking.locator(".sx-asks")).toHaveText("직접 응답");
  await expect(asking).toContainText("baseline-v2");
  await expect(asking).toContainText(/응답 대기 \d+건/);
  await expect(asking.locator(".sx-status.is-waiting")).toBeVisible();
  const seeded = page.locator('.sx-list tbody tr[data-experiment="sx_seed_det_b71c"]');
  await expect(seeded).toContainText("영상 정보 추출");
  await expect(seeded).toContainText("16 / 16건");
  await expect(seeded.locator(".sx-asks")).toHaveCount(0);
  await expect(seeded.locator(".sx-status")).toHaveText("모두 끝남");
  // the step store names its steps itself
  await expect(page.locator(".store-list")).toContainText("검출 · 추적");

  // a set over this range and profile exists: the way there comes first, and the form only on request
  await page.goto("/lab/experiments/new?profile=local-only");
  await expect(section(page, "영상 정보 추출")).toHaveAttribute("aria-pressed", "true");
  const same = page.getByTestId("sx-same-range");
  await expect(same).toContainText("검출기 × OCR · 두 데이터셋");
  await expect(same.getByRole("link", { name: "기존 실험에 추가" })).toHaveAttribute("href", "/lab/experiments/sx_seed_det_b71c?add=1");
  await expect(page.getByRole("heading", { name: "첫 실행" })).toHaveCount(0);
  await same.getByRole("button", { name: "그래도 별도 실험으로 만들기" }).click();
  await expect(page.getByRole("heading", { name: "첫 실행" })).toBeVisible();

  // nothing chosen: the server says what is missing, and nothing can be started
  await expect(runCount(page)).toContainText("실행 0건");
  await expect(plan(page).locator(".sx-problems")).toContainText("실험할 영상을 하나 이상 고르세요.");
  await expect(page.getByRole("button", { name: "실험 시작" })).toBeDisabled();

  // one video, nothing changed: the base profile itself, from the beginning, asking nothing
  await page.getByRole("checkbox", { name: "예시 영상 (합성)" }).check();
  await expect(runCount(page)).toContainText("실행 1건");
  const one = plan(page).locator(".sx-plan-one");
  await expect(one).toHaveAttribute("data-start", "scratch");
  await expect(one).toContainText("처음부터");
  await expect(one).toContainText("없음 — 끝까지 혼자 실행됩니다");
  await expect(plan(page).locator(".sx-problems")).toHaveCount(0);

  // a part: the base profile's own candidate is chosen and marked; adding another gives two combinations
  const ocr = part(page, /^글자 인식/);
  await expect(ocr.locator(".pick-state")).toContainText("기준 그대로 · RapidOCR tiny");
  await ocr.getByRole("button", { name: /^글자 인식/ }).click();
  const own = ocr.locator(".pick-option", { hasText: "기준 프로필 그대로" }).getByRole("checkbox");
  await expect(own).toBeChecked();
  await expect(own).toBeDisabled(); // the only one chosen stays
  await ocr.locator(".pick-option", { hasText: "det small" }).getByRole("checkbox").check();
  await expect(own).toBeEnabled();
  await expect(ocr.locator(".pick-chip")).toHaveText(["기준 RapidOCR tiny", "RapidOCR small"]);
  await expect(runCount(page)).toContainText("실행 2건");
  await expect(plan(page).locator(".sx-plan-sub")).toHaveText("조합 2개 × 영상 1개");
  await expect(plan(page).locator('.sx-plan-table tbody tr[data-start="scratch"]')).toHaveCount(2);
  await expect(plan(page).locator(".sx-plan-table")).toContainText("글자 인식 (OCR) RapidOCR small");
  // what the combination this launch adds computes again, as the server says it (the base profile itself: nothing to say)
  await expect(plan(page).locator(".sx-plan-new li")).toHaveText(["글자 인식 (OCR) RapidOCR small — 기준과 달라 다시 계산: 텍스트 인식 · 영상 이해 조립"]);
  // a candidate this profile cannot run is not offered, in the words of the profile check
  const scene = part(page, /^장면 설명/);
  await scene.getByRole("button", { name: /^장면 설명/ }).click();
  const cloud = scene.locator(".pick-option", { hasText: "Gemini 3.8 Flash" });
  await expect(cloud.locator(".pick-reason")).toContainText("V-3(로컬만)인데");
  await expect(cloud.getByRole("checkbox")).toBeDisabled();

  await page.getByLabel("제목 (선택)").fill("E2E OCR 비교");
  await page.getByRole("button", { name: "실험 시작" }).click();
  await expect(page).toHaveURL(/\/lab\/experiments\/sx_[^/?]+$/);
  const id = new URL(page.url()).pathname.split("/").pop() ?? "";
  await expect(page.getByRole("heading", { name: "E2E OCR 비교" })).toBeVisible();
  await expect(idle(page)).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".sx-facts")).toContainText("2 / 2건 끝남");

  // comparison: the base combination first and marked, then the other, each over the videos it completed
  await expect(compared(page)).toHaveCount(2);
  await expect(compared(page).nth(0)).toContainText("기준");
  await expect(compared(page).nth(0)).toContainText("local-only");
  await expect(compared(page).nth(1)).toContainText("글자 인식 (OCR) RapidOCR small");
  await expect(compared(page).nth(1)).toContainText("‘영상 정보 추출’부터 기준과 다름");
  for (const row of [compared(page).nth(0), compared(page).nth(1)]) await expect(row).toContainText("1 / 1개");
  // time and cost: what the range takes with nothing reused, and beside it what the runs actually spent
  const compareHead = page.locator("#sx-compare-title ~ .table-wrap").first().locator("thead");
  await expect(compareHead.locator('[data-column="full-sec"]')).toHaveText("재사용 없이 걸렸을 시간");
  await expect(compareHead.locator('[data-column="spent-sec"]')).toHaveText("실제로 쓴 시간");
  await expect(compareHead.locator('[data-column="full-cost"]')).toHaveText("재사용 없이 들었을 비용");
  for (const row of [compared(page).nth(0), compared(page).nth(1)]) {
    await expect(row.locator('[data-column="full-sec"]')).toContainText(/\d초/);
    await expect(row.locator('[data-column="spent-sec"]')).toContainText(/\d초/);
  }
  const first = (await (await page.request.get(`/api/lab/stage-experiments/${id}`)).json()) as { summary: { fullSecMean: number; sectionSecMean: number }[]; runs: { start: string; startSection: string }[] };
  // the second combination took detection and tracking from the store: it spent less than the range takes
  expect(first.summary[1].sectionSecMean).toBeLessThan(first.summary[1].fullSecMean);
  expect(first.runs.map((r) => [r.start, r.startSection])).toEqual([["scratch", "perception"], ["scratch", "perception"]]);
  // the score columns are whatever metrics the runs carry, with how to read them
  const texts = page.locator('#sx-compare-title ~ .table-wrap thead th[data-metric="expected.texts"]').first();
  await expect(texts).toContainText("글자 인식");
  await expect(texts).toContainText("높을수록 좋음");
  await expect(await metricCell(page, compared(page).nth(0), "expected.texts")).toContainText("66.7%");
  await expect(await metricCell(page, compared(page).nth(1), "expected.texts")).toContainText("100%");
  // with one dataset there is nothing to break down
  await expect(page.locator(".sx-by-dataset")).toHaveCount(0);

  // a second launch into the same set: the same two combinations on the videos of another dataset
  await page.getByRole("button", { name: "실행 추가" }).click();
  const add = page.locator(".sx-add");
  await expect(add.getByRole("heading", { name: "실행 추가" })).toBeVisible();
  await add.getByRole("button", { name: /^글자 인식/ }).click();
  await add.locator(".pick-option", { hasText: "det small" }).getByRole("checkbox").check();
  await add.getByRole("checkbox", { name: "팀 촬영본 · 매장" }).check();
  await expect(runCount(page)).toContainText("실행 4건");
  await expect(add.locator(".sx-plan-sub")).toHaveText("조합 2개 × 영상 2개");
  await expect(add.locator(".sx-plan-new")).toHaveCount(0); // both combinations are in the set already
  await add.getByRole("button", { name: "실행 4건 추가" }).click();
  await expect(add).toHaveCount(0);
  await expect(page.locator(".sx-facts")).toContainText("6 / 6건 끝남", { timeout: 60_000 });
  await expect(page).toHaveURL(new RegExp(`/lab/experiments/${id}$`)); // still the same set
  await expect(compared(page)).toHaveCount(2); // no new combination: the runs joined the two that were there
  await expect(compared(page).nth(0)).toContainText("3 / 3개");
  // one of the three videos has no labels: the mean says how many videos it rests on
  await expect(await metricCell(page, compared(page).nth(0), "expected.texts")).toContainText("영상 2개에서");

  // per dataset: the same columns, one group per dataset
  const byDataset = page.locator(".sx-by-dataset");
  await expect(byDataset.locator("tbody")).toHaveCount(2);
  const team = byDataset.locator('tbody[data-dataset="team-footage-7c2e"]');
  await expect(team.locator(".sx-group-row")).toContainText("팀 촬영본 · 매장");
  await expect(team.locator('tr[data-variant="base"]')).toContainText("2 / 2개");
  await expect(byDataset.locator('tbody[data-dataset="sample"] tr[data-variant="base"]')).toContainText("1 / 1개");
  await expect(byDataset.locator('thead th[data-metric="expected.people"]')).toContainText("인물 수 일치");

  // the grid: every pair has its run; a combination is repeated on a video with the shortcut of its row
  const grid = page.locator(".results");
  const cell = grid.locator('tbody tr[data-variant="v1"] td[data-item="sample/sample"]');
  await expect(cell).toContainText("완료");
  await expect(cell.locator(".sx-cell-runs a")).toHaveCount(1);
  await grid.locator('tbody tr[data-variant="v1"]').getByRole("button", { name: "이 조합으로 다른 영상" }).click();
  await expect(add.getByRole("heading", { name: "실행 추가" })).toBeVisible();
  await expect(part(page, /^글자 인식/).locator(".pick-chip")).toHaveText(["RapidOCR small"]); // that combination alone
  await add.getByRole("checkbox", { name: "예시 영상 (합성)" }).check();
  await expect(runCount(page)).toContainText("실행 1건");
  await add.getByRole("button", { name: "실행 1건 추가" }).click();
  await expect(page.locator(".sx-facts")).toContainText("7 / 7건 끝남", { timeout: 60_000 });
  await expect(cell).toContainText("실행 2건");
  await expect(cell.locator(".sx-cell-runs a")).toHaveCount(2);
  // the mean of that combination still counts the video once
  await expect(compared(page).nth(1)).toContainText("3 / 3개");
  await expect(compared(page).nth(1)).toContainText("실행 4 / 4건 완료");

  // the run list: newest first, where each started, what it found, its scores; a run opens to its steps
  await expect(runRows(page)).toHaveCount(7);
  const latest = runRows(page).first();
  await expect(latest).toHaveAttribute("data-run", "r7");
  await expect(latest).toContainText("처음부터");
  await expect(latest).toContainText(/글자 \d+ · 인물 \d+ · 번호판 \d+ · 장면 \d+/);
  await expect(latest.locator(".sx-score").first()).toContainText("글자 인식");
  await latest.getByRole("button", { name: "r7 자세히" }).click();
  const detail = page.locator(".run-detail");
  await expect(detail.locator('.sx-steps tr[data-step="perceive/detect_track"]')).toHaveAttribute("data-fate", "store");
  await expect(detail.locator('.sx-steps tr[data-step="perceive/assemble"]')).toHaveAttribute("data-fate", "run");
  await expect(detail.getByRole("heading", { name: /기준과 다른 점/ })).toBeVisible();
  await expect(detail.locator('.sx-score-rows tr[data-metric="expected.texts"]')).toContainText("expected");
  await expect(detail.getByRole("heading", { name: "영상 정보 추출 결과" })).toBeVisible();

  // scores computed elsewhere arrive through the API and get a column of their own, read from what they say of themselves
  const put = await page.request.put(`/api/lab/stage-experiments/${id}/runs/r1/scores`, {
    data: { evaluator: "e2e-tool", scores: [{ metric: "e2e.latency", label: "E2E 예시 지표", value: 1.5, unit: "sec", direction: "lower", detail: "테스트가 넣은 값" }] },
  });
  expect(put.ok()).toBe(true);
  await page.reload();
  const outside = page.locator('#sx-compare-title ~ .table-wrap thead th[data-metric="e2e.latency"]').first();
  await expect(outside).toContainText("E2E 예시 지표");
  await expect(outside).toContainText("낮을수록 좋음");
  await expect(await metricCell(page, compared(page).nth(0), "e2e.latency")).toContainText("1.50초");
  await expect(await metricCell(page, compared(page).nth(0), "e2e.latency")).toContainText("영상 1개에서");
  // a score without a metric or a number is refused
  expect((await page.request.put(`/api/lab/stage-experiments/${id}/runs/r1/scores`, { data: { evaluator: "e2e-tool", scores: [{ label: "이름 없음", value: 1 }] } })).status()).toBe(422);

  // a run is taken out of the set after a confirmation in the page; its case stays under "실행"
  const caseId = ((await (await page.request.get(`/api/lab/stage-experiments/${id}`)).json()) as { runs: { id: string; caseId: string }[] }).runs.find((r) => r.id === "r7")?.caseId ?? "";
  await runRows(page).first().getByRole("button", { name: "r7 실험에서 빼기" }).click();
  const confirm = page.getByRole("group", { name: "r7 지우기 확인" });
  await confirm.getByRole("button", { name: "취소" }).click();
  await expect(runRows(page)).toHaveCount(7);
  await runRows(page).first().getByRole("button", { name: "r7 실험에서 빼기" }).click();
  await confirm.getByRole("button", { name: "빼기" }).click();
  await expect(runRows(page)).toHaveCount(6);
  await expect(cell.locator(".sx-cell-runs a")).toHaveCount(1);
  const runs = (await (await page.request.get("/api/lab/runs")).json()) as { id: string; experimentId?: string }[];
  expect(runs.find((r) => r.id === caseId)?.experimentId).toBe(id);

  // each run is a run of its own: the grid leads to its topology
  await cell.locator(".sx-cell-runs a").click();
  await expect(page).toHaveURL(/\/lab\/runs\/exp_/);
  await expect(page.locator('.react-flow__node[data-id="perceive"] .tn-subgraph.st-done')).toBeVisible();
  await expect(page.locator('.react-flow__node[data-id="report"] .st-done')).toHaveCount(0); // it stopped after the section
  await page.getByRole("link", { name: "구간 실험 보기" }).click();
  await expect(page).toHaveURL(new RegExp(`/lab/experiments/${id}$`));
  await expect(page.getByRole("heading", { name: "E2E OCR 비교", exact: true })).toBeVisible();
});

test("a range that asks the user takes one run at a time, never answers for the person, and goes on when they answer in the service screen", async ({ page, request }) => {
  test.setTimeout(180_000);
  // the example set: a run that waits leads the page; a run made the old way says so
  await page.goto("/lab/experiments/sx_seed_ask_e452");
  await expect(page.locator(".sx-facts .sx-asks")).toHaveText("직접 응답");
  await expect(page.getByTestId("sx-interactive-note")).toContainText("자동으로");
  const waitingSeed = page.locator(".sx-waiting-table tbody tr", { hasText: "Planner (계획 + 검증) GPT-5.4 mini" });
  await expect(waitingSeed).toContainText("대상 확인 필요");
  await expect(waitingSeed.getByRole("link", { name: /^서비스 화면에서/ })).toHaveAttribute("href", /\/cases\/exp_/);
  await expect(waitingSeed.getByRole("link", { name: "토폴로지" })).toHaveAttribute("href", /\/lab\/runs\/exp_/);
  await expect(page.locator('.sx-runs tr[data-run="r1"]')).toContainText("자동 응답 (이전 방식)");
  await expect(page.locator('.sx-runs tr[data-run="r2"]')).not.toContainText("자동 응답");
  // the person's answers differ between the two answered runs, and so does what was decided
  await expect(page.locator('.sx-runs tr[data-run="r3"]')).toContainText("기준과");

  // a new set over 법률 분석 alone: the rail marks what each section asks, and the screen says what that means
  await page.goto("/lab/experiments/new");
  await expect(section(page, "법률 분석").locator(".rail-asks")).toContainText("사용자에게 물음: 같은 사람 확인 · 질문");
  await expect(section(page, "검토 · 감사").locator(".rail-asks")).toContainText("계획 검토 · 감사 확인");
  await expect(section(page, "영상 정보 추출").locator(".rail-asks")).toHaveCount(0);
  await expect(page.getByTestId("sx-interactive-notice")).toHaveCount(0);
  await section(page, "법률 분석").click();
  const notice = page.getByTestId("sx-interactive-notice");
  await expect(notice).toContainText("법률 분석: 같은 사람 확인 · 질문");
  await expect(notice).toContainText("한 번에 1건씩");
  await expect(notice).toContainText("자동으로 답하지 않습니다");
  await expect(notice).toContainText("서비스 화면에서 직접 답해야");
  await expect(page.getByTestId("sx-same-range")).toHaveCount(0);

  // one candidate per part, one video
  const analyst = part(page, /^Legal Analyst/).first();
  await analyst.getByRole("button", { name: /^Legal Analyst/ }).click();
  await expect(analyst.getByRole("checkbox")).toHaveCount(0);
  await expect(analyst.getByRole("radio", { name: "Claude Sonnet 5.5", exact: true })).toBeChecked();
  await analyst.getByRole("radio", { name: "GPT-5.4 mini", exact: true }).check();
  await expect(analyst.getByRole("radio", { name: "Claude Sonnet 5.5", exact: true })).not.toBeChecked();
  await page.getByRole("radio", { name: "매장 앞 골목" }).check();
  await page.getByRole("radio", { name: "카페 인터뷰 (예시 영상)" }).check();
  await expect(page.getByRole("radio", { name: "매장 앞 골목" })).not.toBeChecked();
  await expect(runCount(page)).toContainText("실행 1건");
  const one = plan(page).locator(".sx-plan-one");
  await expect(one).toContainText("Legal Analyst GPT-5.4 mini");
  await expect(one).toContainText("‘법률 분석’ 앞에서 이어 감"); // the video's analysis is not computed again
  await expect(one).toContainText("같은 사람 확인 → 질문");
  await expect(one).toContainText("서비스 화면에서 직접 답해야");
  // the server refuses more than one run for such a range, whoever asks
  const two = { from: "analysis", baseProfile: "baseline-v2", matrix: { "llm.analyst": ["claude-sonnet-5.5", "gpt-5.4-mini"] }, items: ["sample/sample"] };
  const preview = (await (await request.post("/api/lab/stage-experiments/preview", { data: two })).json()) as { interactive: boolean; runs: unknown[]; problems: string[] };
  expect(preview.interactive).toBe(true);
  expect(preview.runs).toHaveLength(2);
  expect(preview.problems[0]).toContain("실행은 한 번에 1건(조합 1개 × 영상 1개)만 추가합니다");
  const refused = await request.post("/api/lab/stage-experiments", { data: two });
  expect(refused.status()).toBe(422);
  expect(((await refused.json()) as { detail: string }).detail).toContain("한 번에 1건");

  await page.getByLabel("제목 (선택)").fill("E2E 직접 응답");
  await page.getByRole("button", { name: "실험 시작" }).click();
  await expect(page).toHaveURL(/\/lab\/experiments\/sx_[^/?]+$/);
  const id = new URL(page.url()).pathname.split("/").pop() ?? "";

  // the run stops at its first question and waits: nothing answers for the person
  const waiting = page.locator(".sx-waiting-table tbody tr");
  await expect(waiting).toHaveCount(1, { timeout: 30_000 });
  await expect(waiting).toContainText("대상 확인 필요");
  await expect(page.locator(".sx-facts .sx-status.is-waiting")).toContainText("응답 대기 1");
  await page.waitForTimeout(4000); // an automatic answer would have come by now (the mock's autopilot takes 0.35 s at this speed)
  await expect(waiting).toContainText("대상 확인 필요");
  const record = (await (await request.get(`/api/lab/stage-experiments/${id}`)).json()) as { status: string; runs: { caseId: string; status: string; waitingFor: string; interactive: boolean; autoAnswered: boolean }[] };
  expect(record.status).toBe("waiting");
  expect(record.runs[0]).toMatchObject({ status: "waiting", waitingFor: "identity", interactive: true, autoAnswered: false });
  const caseId = record.runs[0].caseId;
  // in the run list it is a run the person answers; it is not among the uploader's own work on the service home
  const listed = ((await (await request.get("/api/lab/runs")).json()) as { id: string; waitingFor?: string; interactive?: boolean }[]).find((r) => r.id === caseId);
  expect(listed).toMatchObject({ waitingFor: "identity", interactive: true });
  expect(((await (await request.get("/api/cases")).json()) as { id: string }[]).some((c) => c.id === caseId)).toBe(false);

  // the person answers on the service screen of that run
  await waiting.getByRole("link", { name: "서비스 화면에서 대상 확인" }).click();
  await expect(page).toHaveURL(new RegExp(`/cases/${caseId}$`));
  await expect(page.getByRole("link", { name: "구간 실험", exact: true })).toHaveAttribute("href", `/lab/experiments/${id}`);
  await expect(page.getByRole("heading", { name: /같은 사람인가요/ })).toBeVisible();
  await page.getByLabel("같은 사람", { exact: true }).check();
  await page.getByRole("button", { name: "확인하고 계속" }).click();
  await expect(page.getByRole("heading", { name: /판단에 필요한 사실/ })).toBeVisible({ timeout: 60_000 });
  await page.locator('input[name="q-Q1"][value="public"]').check();
  await page.locator('input[name="q-Q2"][value="yes"]').check();
  await page.locator('input[name="q-Q3"][value="no"]').check();
  await page.getByRole("button", { name: /답변 보내기/ }).click();

  // it ends at the end of the range, and the way back to the set is there
  await expect(page.getByRole("heading", { name: "요청한 구간까지만 실행한 실험용 실행입니다" })).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".stage-lead")).toContainText("법적 판단까지 실행하고 마쳤습니다");
  await page.getByRole("link", { name: "구간 실험으로 돌아가기" }).click();
  await expect(page).toHaveURL(new RegExp(`/lab/experiments/${id}$`));
  await expect(idle(page)).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".sx-waiting-table")).toHaveCount(0);
  const done = runRows(page).first();
  await expect(done).toHaveAttribute("data-status", "completed");
  await expect(done).toContainText(/보호 요건 \d+ · 질문 3/);
  await expect(done).toContainText("응답 대기"); // the time spent waiting is reported apart from the run's own time
  const after = (await (await request.get(`/api/lab/stage-experiments/${id}`)).json()) as { runs: { metrics: { waitSec: number; analysis: { questions: number } } }[] };
  expect(after.runs[0].metrics.waitSec).toBeGreaterThan(3);
  expect(after.runs[0].metrics.analysis.questions).toBe(3);

  // the next run of the same range is asked again: adding one more makes it wait at the first question anew
  await page.getByRole("button", { name: "실행 추가" }).click();
  await page.locator(".sx-add").getByRole("radio", { name: "카페 인터뷰 (예시 영상)" }).check();
  await expect(plan(page).locator(".sx-plan-one")).toContainText("같은 사람 확인 → 질문");
  await page.locator(".sx-add").getByRole("button", { name: "실행 1건 추가" }).click();
  await expect(page.locator(".sx-waiting-table tbody tr")).toHaveCount(1, { timeout: 30_000 });
  await expect(page.locator(".sx-waiting-table tbody tr")).toContainText("대상 확인 필요");
  // a waiting run can be taken out of the set; nothing is left waiting then
  await runRows(page).first().getByRole("button", { name: "r2 실험에서 빼기" }).click();
  await page.getByRole("group", { name: "r2 지우기 확인" }).getByRole("button", { name: "빼기" }).click();
  await expect(page.locator(".sx-waiting-table")).toHaveCount(0);
  await expect(idle(page)).toBeVisible();
});

test("a range behind the questions continues an answered run instead of asking again, and says why a video without one cannot start", async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.goto("/lab/experiments/sx_seed_plan_7d10");
  await expect(page.getByRole("heading", { name: "계획 모델 비교" })).toBeVisible();
  await expect(page.locator(".sx-facts .sx-asks")).toHaveCount(0);
  // its runs started in front of the plan, from the run of the other set that a person answered
  const answered = ((await (await request.get("/api/lab/stage-experiments/sx_seed_ask_e452")).json()) as { runs: { id: string; caseId: string }[] }).runs.find((r) => r.id === "r2")?.caseId ?? "";
  const first = page.locator('.sx-runs tr[data-run="r1"]');
  await expect(first.locator(".sx-run-start")).toContainText("‘계획’ 앞에서 이어 감");
  await expect(first.locator(".sx-run-start").getByRole("link")).toHaveAttribute("href", `/lab/runs/${answered}`);
  // a run that failed says why, in the list and in the grid
  const failed = page.locator('.sx-runs tr[data-status="failed"]');
  await expect(failed).toContainText("계획이 검증을 3회 안에 통과하지 못했습니다");
  await expect(page.locator(".sx-compare tbody tr", { hasText: "계획 생성 상한 1" }).first()).toContainText("실패 1");

  await page.getByRole("button", { name: "실행 추가" }).click();
  const add = page.locator(".sx-add");
  // a video nobody has answered the analysis of: the server's reason, and no start
  await add.getByRole("button", { name: "영상 고르기" }).click();
  await add.getByRole("checkbox", { name: "주문대 앞 (오후)" }).check();
  await expect(runCount(page)).toContainText("실행 1건");
  await expect(plan(page).locator(".sx-plan-problem")).toContainText("'주문대 앞 (오후)'에는 '법률 분석'까지 마친 실행이 없습니다");
  await expect(plan(page).locator(".sx-problems")).toContainText("'법률 분석'을(를) 포함한 구간 실험을 먼저 실행하거나 서비스에서 이 영상을 그 단계까지 진행하세요");
  await expect(add.getByRole("button", { name: "실행 1건 추가" })).toBeDisabled();
  // the request is refused in the same words when it is made anyway
  const direct = await request.post("/api/lab/stage-experiments/sx_seed_plan_7d10/runs", { data: { items: ["team-footage-7c2e/v1-41d0"] } });
  expect(direct.status()).toBe(422);
  expect(((await direct.json()) as { detail: string }).detail).toContain("'법률 분석'까지 마친 실행이 없습니다");
  await add.getByRole("checkbox", { name: "주문대 앞 (오후)" }).uncheck();

  // the example video has an answered run: the plan continues it, asks nothing, and offers the runs to continue
  await add.getByRole("checkbox", { name: "예시 영상 (합성)" }).check();
  const one = plan(page).locator(".sx-plan-one");
  await expect(one).toHaveAttribute("data-start", "fork");
  await expect(one).toContainText("‘계획’ 앞에서 이어 감");
  await expect(one).toContainText("그때 사용자가 한 답을 그대로 씁니다. 다시 묻지 않습니다.");
  await expect(one).toContainText("없음 — 끝까지 혼자 실행됩니다");
  // the runs it could continue are offered, the server's choice first: a run that worked the analysis out itself
  // (the answered run of the other set, or a case someone took that far in the service), not one of this set
  const source = one.getByLabel("이어 갈 실행");
  const options = await source.locator("option").evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value));
  expect(options.length).toBeGreaterThan(1);
  expect(options).toContain(answered);
  const own = ((await (await request.get("/api/lab/stage-experiments/sx_seed_plan_7d10")).json()) as { runs: { caseId: string }[] }).runs.map((r) => r.caseId);
  expect(own).not.toContain(options[0]);
  await expect(source).toHaveValue(options[0]);
  await expect(one.locator(".sx-plan-source a")).toHaveAttribute("href", `/lab/runs/${options[0]}`);
  // neither the demo run nor the run made the old way is offered: nobody gave their answers
  const old = ((await (await request.get("/api/lab/stage-experiments/sx_seed_ask_e452")).json()) as { runs: { id: string; caseId: string }[] }).runs.find((r) => r.id === "r1")?.caseId ?? "";
  expect(options).not.toContain(old);
  expect(options.some((o) => o.startsWith("demo_"))).toBe(false);
  const chosen = await request.post("/api/lab/stage-experiments/preview", { data: { experiment: "sx_seed_plan_7d10", items: ["sample/sample"], source: old } });
  expect(((await chosen.json()) as { runs: { problem: string }[] }).runs[0].problem).toContain("질문에 자동으로 답한 실행이라 그 답을 이어 쓰지 않습니다.");
  // another run to continue is the person's choice
  await source.selectOption(options[1]);
  await expect(one.locator(".sx-plan-source a")).toHaveAttribute("href", `/lab/runs/${options[1]}`);
  await expect(source).toHaveValue(options[1]);
  // the server still lists every run that can be continued, with the chosen one as the source
  await expect(source.locator("option")).toHaveCount(options.length);
  const named = (await (await request.post("/api/lab/stage-experiments/preview", { data: { experiment: "sx_seed_plan_7d10", items: ["sample/sample"], source: options[1] } })).json()) as {
    runs: { source: { caseId: string }; candidates: { caseId: string }[] }[];
  };
  expect(named.runs[0].source.caseId).toBe(options[1]);
  expect(named.runs[0].candidates.map((c) => c.caseId)).toEqual(options);

  const before = await runRows(page).count();
  await add.getByRole("button", { name: "실행 1건 추가" }).click();
  await expect(runRows(page)).toHaveCount(before + 1, { timeout: 30_000 });
  const added = runRows(page).first();
  await expect(added).toHaveAttribute("data-status", "completed", { timeout: 30_000 });
  await expect(added.locator(".sx-run-start").getByRole("link")).toHaveAttribute("href", `/lab/runs/${options[1]}`);
  await expect(added).toContainText(/결정 \d+ · 가림 \d+ · 유지 \d+/);
  // it never waited for anyone
  const run = ((await (await request.get("/api/lab/stage-experiments/sx_seed_plan_7d10")).json()) as { runs: { interactive: boolean; start: string; sourceCaseId: string; startSection: string; forkedAt: string; metrics: { waitSec: number } }[] }).runs.at(-1);
  expect(run).toMatchObject({ interactive: false, start: "fork", sourceCaseId: options[1], startSection: "plan", forkedAt: "plan" });
  expect(run?.metrics.waitSec).toBe(0);
  // the base combination now has two runs on that video
  await expect(page.locator('.results tbody tr[data-variant="base"] td[data-item="sample/sample"]')).toContainText(/실행 \d+건/);
});

test("the example sets show an uneven comparison, a skipped launch, and what a VLM said differently", async ({ page }) => {
  // a launch that was stopped left one combination with fewer videos: the means are not marked as comparable
  await page.goto("/lab/experiments/sx_seed_det_b71c");
  await expect(page.locator(".sx-basis")).toContainText("조합마다 완료한 영상이 다릅니다");
  await expect(page.locator(".sx-basis")).toContainText("‘사람 · 물체 검출 D-FINE-S’ 1개 · 나머지 3개");
  await expect(page.locator(".sx-compare .sx-best")).toHaveCount(0);
  const stopped = page.locator('.results tbody tr[data-variant="v4"]');
  await expect(stopped.locator('td[data-item="sample/sample"]')).toContainText("건너뜀");
  await expect(stopped.locator('td[data-item="sample/sample"]')).toContainText("실험을 멈춤");
  // a pair that was run twice holds both runs
  await expect(page.locator('.results tbody tr[data-variant="v2"] td[data-item="sample/sample"]')).toContainText("실행 2건");
  // two datasets: the breakdown is there, and a combination that never ran on one says so
  await expect(page.locator('.sx-by-dataset tbody[data-dataset="sample"] tr[data-variant="v4"]')).toContainText("0 / 1개");

  // an empty cell offers the run that would fill it, with both chosen
  await page.goto("/lab/experiments/sx_seed_plan_7d10");
  await page.locator(".results thead").getByRole("button", { name: "이 영상으로 다른 조합" }).click();
  await expect(page.locator(".sx-add").getByRole("checkbox", { name: "예시 영상 (합성)" })).toBeChecked();
  await expect(part(page, /^Planner/).locator(".pick-state")).toContainText("기준 그대로");

  // Every combination completed the same video: they are compared on what the range takes and costs with nothing
  // reused, although the later ones took most steps from the store and spent far less
  await page.goto("/lab/experiments/sx_seed_vlm_3a9f");
  await expect(page.locator(".sx-basis")).toHaveCount(0);
  const cloud = page.locator(".sx-compare tbody tr", { hasText: "장면 설명 Gemini 3.8 Flash" });
  await expect(cloud.locator('[data-column="full-sec"] .sx-best')).toHaveText("최단");
  await expect(cloud.locator('[data-column="full-sec"] .delta')).toHaveClass(/is-good/);
  await expect(cloud.locator('[data-column="spent-sec"] .sx-best')).toHaveCount(0);
  const onDevice = page.locator(".sx-compare tbody tr", { hasText: "장면 설명 Qwen3-VL-8B" });
  await expect(onDevice.locator('[data-column="full-cost"] .sx-best')).toHaveText("최저");
  await expect(onDevice.locator('[data-column="full-sec"] .delta')).toHaveClass(/is-bad/);
  const local = page.locator(".sx-runs tbody tr", { hasText: "Qwen3-VL-8B" });
  await expect(local).toContainText("기준과 95%");
  await local.getByRole("button", { name: /자세히$/ }).click();
  await expect(page.locator(".run-detail .diff-list")).toContainText("장면 4 장소 (street → other)");
  await expect(page.locator(".run-detail .diff-list")).toContainText("장면 1 사람 수 (2 → 1)");
});

test("a set is renamed and its cost cap changed in place, a launch is stopped while it runs, and the set is deleted after a confirmation", async ({ page, request }) => {
  test.setTimeout(120_000);
  // a launch large enough to be still running when it is stopped
  const created = await request.post("/api/lab/stage-experiments", {
    data: {
      title: "E2E 멈출 실험",
      from: "perception",
      baseProfile: "budget-saver",
      matrix: { "perception.object_detector": ["rfdetr-nano", "rfdetr-small", "dfine-s", "rfdetr-nano-onnx"], "perception.ocr": ["rapidocr-tiny", "rapidocr-small", "gemini-3.8-flash"] },
      items: ["sample/sample"],
    },
  });
  expect(created.status()).toBe(201);
  const body = (await created.json()) as { id: string; status: string; runs: unknown[]; variants: { id: string; base: boolean }[]; title: string };
  expect(body.runs).toHaveLength(12);
  expect(body.status).toBe("running");
  expect(body.variants[0]).toMatchObject({ id: "base", base: true });
  await page.goto(`/lab/experiments/${body.id}`);
  await expect(page.locator(".sx-live")).toBeVisible();
  await page.getByRole("button", { name: "실험 멈추기" }).click();
  await expect(idle(page)).toBeVisible({ timeout: 30_000 });
  // nothing failed: the runs that never started are skipped, saying why
  await expect(page.locator('.sx-runs tr[data-status="skipped"]').first()).toContainText("실험을 멈춤");
  await expect(page.locator('.sx-runs tr[data-status="failed"]')).toHaveCount(0);
  // with nothing queued or running there is nothing to stop
  expect((await request.post(`/api/lab/stage-experiments/${body.id}/stop`)).status()).toBe(409);

  // the name and the cost cap are corrected where they are shown
  await page.getByRole("button", { name: "실험 이름 바꾸기" }).click();
  const rename = page.getByRole("form", { name: "실험 이름 바꾸기" });
  await rename.getByLabel("실험 이름").fill("E2E 고친 이름");
  await rename.getByRole("button", { name: "저장" }).click();
  await expect(page.getByRole("heading", { name: "E2E 고친 이름" })).toBeVisible();
  await page.getByRole("button", { name: "상한 바꾸기" }).click();
  const cap = page.getByRole("form", { name: "비용 상한 바꾸기" });
  await cap.getByLabel("비용 상한 (USD)").fill("0");
  await expect(cap.getByRole("button", { name: "저장" })).toBeDisabled();
  await cap.getByLabel("비용 상한 (USD)").fill("7.5");
  await cap.getByRole("button", { name: "저장" }).click();
  await expect(page.locator(".sx-cap")).toContainText("상한 $7.50");
  const stored = (await (await request.get(`/api/lab/stage-experiments/${body.id}`)).json()) as { title: string; costCapUsd: number };
  expect(stored).toMatchObject({ title: "E2E 고친 이름", costCapUsd: 7.5 });

  // deleting asks in the page, and "취소" keeps the set
  await page.getByRole("button", { name: "지우기", exact: true }).click();
  const confirm = page.getByRole("group", { name: "실험 지우기 확인" });
  await confirm.getByRole("button", { name: "취소" }).click();
  await expect(confirm).toHaveCount(0);
  await page.getByRole("button", { name: "지우기", exact: true }).click();
  await confirm.getByRole("button", { name: "지우기" }).click();
  await expect(page).toHaveURL(/\/lab\/experiments$/);
  await expect(page.locator('.sx-list tbody tr[data-experiment="sx_seed_vlm_3a9f"]')).toBeVisible();
  await expect(page.locator(`.sx-list tbody tr[data-experiment="${body.id}"]`)).toHaveCount(0);
});

test("the evaluators that score runs today are listed apart from the planned ones", async ({ page, request }) => {
  const registered = (await (await request.get("/api/lab/evaluators")).json()) as { id: string; sections: string[]; metrics: { id: string; unit: string; direction: string | null }[] }[];
  expect(registered.map((e) => e.id)).toEqual(["expected"]);
  expect(registered[0].sections).toEqual(["perception"]);
  expect(registered[0].metrics.map((m) => m.id)).toEqual(["expected.texts", "expected.people", "expected.plates", "expected.scenes"]);

  await page.goto("/lab/datasets");
  await expect(page.getByRole("heading", { name: "등록된 평가기 1" })).toBeVisible();
  const table = page.getByTestId("registered-evaluators");
  const row = table.locator('tbody tr[data-evaluator="expected"]');
  await expect(row).toContainText("데이터셋 정답과 비교");
  await expect(row).toContainText("영상 정보 추출");
  await expect(row.locator(".ev-metrics li")).toHaveCount(4);
  await expect(row.locator(".ev-metrics li").first()).toContainText("글자 인식");
  await expect(row.locator(".ev-metrics li").first()).toContainText("expected.texts");
  await expect(row.locator(".ev-metrics li").first()).toContainText("높을수록 좋음");
  // what the team is still designing is labelled as planned, not as something that runs
  const planned = page.getByRole("heading", { name: /^계획된 평가기 \d+/ });
  await expect(planned).toContainText("아직 등록 안 됨");
  await expect(page.getByRole("heading", { name: /^평가 데이터셋 \d+/ })).toContainText("계획");
  // no screen types scores in
  await expect(page.locator("main input, main textarea")).toHaveCount(0);
});

test("video datasets are created, filled from a file and from a run, corrected, and removed", async ({ page, request }) => {
  const name = `E2E 묶음 ${Date.now()}`;
  await page.goto("/lab/datasets");
  await expect(page.getByRole("heading", { name: /^영상 데이터셋 \d+$/ })).toBeVisible();
  await expect(page.locator('[data-set="sample"]')).toContainText("기본 제공");
  // a video carries a memo and what is known to be in it; nothing answers questions for the person any more
  await expect(page.locator('[data-set="sample"] thead')).not.toContainText("자동 응답");

  await page.getByRole("button", { name: "새 데이터셋" }).click();
  const form = page.getByRole("form", { name: "새 영상 데이터셋" });
  await form.getByLabel("이름").fill(name);
  await form.getByLabel("설명 (선택)").fill("테스트가 만든 묶음");
  await expect(form.getByRole("radio", { name: /^금지/ })).toBeChecked(); // closed unless someone says otherwise
  await form.getByRole("button", { name: "데이터셋 만들기" }).click();
  const card = page.locator(".vset", { has: page.getByRole("heading", { name }) });
  await expect(card).toContainText("외부 전송 금지");
  await expect(card).toContainText("아직 영상이 없습니다");

  // from a file, with what is in it
  await card.getByRole("button", { name: "영상 추가" }).click();
  await card.locator('input[type="file"]').setInputFiles({ name: "e2e-clip.mp4", mimeType: "video/mp4", buffer: Buffer.alloc(2_400_000, 1) });
  await card.getByLabel("제목 (선택)").fill("올린 영상");
  await card.getByText("정답 (선택)").click();
  await expect(card.getByLabel(/질문에 자동으로 답할 값/)).toHaveCount(0);
  await card.getByLabel(/영상에 보이는 글자/).fill("출입구 안내\n행사 현수막");
  await card.getByLabel("인물 수").fill("2");
  await card.getByRole("button", { name: "영상 추가" }).last().click();
  const uploaded = card.locator("tbody tr", { hasText: "e2e-clip.mp4" });
  await expect(uploaded).toContainText("올린 영상");
  await expect(uploaded).toContainText("글자 2 · 인물 2");
  const stored = ((await (await request.get("/api/lab/video-sets")).json()) as { name: string; items: Record<string, unknown>[] }[]).find((s) => s.name === name);
  expect(stored?.items[0]).not.toHaveProperty("answers");

  // from an existing run
  const runs = (await (await request.get("/api/lab/runs")).json()) as { id: string }[];
  const demo = runs.find((r) => r.id.startsWith("demo_"));
  expect(demo).toBeTruthy();
  await card.getByRole("button", { name: "영상 추가" }).click();
  await card.getByText("기존 실행에서").click();
  await card.locator(".vs-add select").selectOption(demo!.id);
  await card.getByRole("button", { name: "영상 추가" }).last().click();
  await expect(card.locator("tbody tr[data-item]")).toHaveCount(2);
  await expect(card.getByRole("link", { name: demo!.id })).toHaveAttribute("href", `/lab/runs/${demo!.id}`);

  // a video is corrected where it is listed: its title and what is known to be in it
  await uploaded.getByRole("button", { name: "올린 영상 고치기" }).click();
  const edit = card.getByRole("form", { name: "올린 영상 고치기" });
  await expect(edit.getByLabel(/영상에 보이는 글자/)).toHaveValue("출입구 안내\n행사 현수막");
  await edit.getByLabel("제목").fill("고친 영상");
  await edit.getByLabel("장면 수").fill("3");
  await edit.getByRole("button", { name: "저장" }).click();
  await expect(uploaded).toContainText("고친 영상");
  await expect(uploaded).toContainText("글자 2 · 인물 2 · 장면 3");
  // emptying the labels clears them
  await uploaded.getByRole("button", { name: "고친 영상 고치기" }).click();
  const again = card.getByRole("form", { name: "고친 영상 고치기" });
  await again.getByLabel(/영상에 보이는 글자/).fill("");
  await again.getByLabel("인물 수").fill("");
  await again.getByLabel("장면 수").fill("");
  await again.getByRole("button", { name: "저장" }).click();
  await expect(uploaded.locator("td").nth(2)).toHaveText("없음");

  // the set itself: its name and whether its videos may be sent to cloud models
  const renamed = `${name} (고침)`;
  await card.getByRole("button", { name: "데이터셋 고치기" }).click();
  const setForm = card.getByRole("form", { name: `${name} 고치기` });
  await setForm.getByLabel("이름").fill(renamed);
  await setForm.getByRole("radio", { name: /^허용/ }).check();
  await expect(setForm.locator(".vs-caution")).toContainText("외부 API 모델로 전송될 수 있습니다");
  await setForm.getByRole("button", { name: "저장" }).click();
  const edited = page.locator(".vset", { has: page.getByRole("heading", { name: renamed }) });
  await expect(edited).toContainText("외부 전송 허용");

  // the corrected set is what a launch offers (a profile no other test makes a set with: an existing set would hide the form)
  await page.goto("/lab/experiments/new?profile=research-max");
  await expect(page.locator(".pick", { hasText: renamed })).toContainText("영상 2개");
  await expect(page.locator(".pick", { hasText: renamed })).toContainText("외부 전송 허용");

  // removing asks inside the page
  await page.goto("/lab/datasets");
  const row = edited.locator("tbody tr", { hasText: "e2e-clip.mp4" });
  await row.getByRole("button", { name: "고친 영상 빼기" }).click();
  await row.getByRole("group", { name: "영상 빼기 확인" }).getByRole("button", { name: "취소" }).click();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "고친 영상 빼기" }).click();
  await row.getByRole("group", { name: "영상 빼기 확인" }).getByRole("button", { name: "빼기" }).click();
  await expect(edited.locator("tbody tr[data-item]")).toHaveCount(1);
  await edited.getByRole("button", { name: "데이터셋 지우기" }).click();
  await edited.getByRole("group", { name: "데이터셋 지우기 확인" }).getByRole("button", { name: "지우기" }).click();
  await expect(page.getByRole("heading", { name: renamed })).toHaveCount(0);
  // the built-in set offers none of this
  await expect(page.locator('[data-set="sample"]').getByRole("button")).toHaveCount(0);
});

test("whole-pipeline evaluation experiments stay reachable on the mock, marked as the later milestone", async ({ page }) => {
  await page.goto("/lab/experiments");
  const later = page.locator(".sx-later");
  await expect(later).toContainText("다음 마일스톤");
  await later.getByRole("link", { name: "평가 실험 열기" }).click();
  await expect(page).toHaveURL(/\/lab\/experiments\/pipeline$/);
  await expect(page.getByRole("heading", { name: "전체 파이프라인 평가 실험" })).toBeVisible();
  await expect(page.locator(".example-banner")).toContainText("모의 백엔드의 예시");
  // one of them opens by its own address
  await page.goto("/lab/experiments/pipeline/exp-1105-baseline");
  await expect(page.getByRole("heading", { name: "베이스라인 비교" })).toBeVisible();
});
