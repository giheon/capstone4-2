// Section experiments against the real backend in fixture mode (playwright.config.ts, project "real"): scripted
// models and detectors, a fresh data directory with no example video. A generated clip becomes a case, the case's
// video a dataset, and then three sets of runs are built on it (docs/design/16 §16.3–16.4):
//   1. 영상 정보 추출 — a matrix of two candidates for the scene description; swapping only that part must reuse
//      detection · tracking · OCR · grouping. A second launch adds a third combination to the same set.
//   2. 법률 분석 — a range that asks the user: one run, which waits until the question is answered on its service
//      screen, and then ends at the end of the range.
//   3. 계획 — a range behind the questions: its runs continue the answered run of (2) and ask nothing. A second
//      launch adds a run to the same set.
// On the way: what the server refuses to start is refused in the launch panel too, in the server's words.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { type APIRequestContext, type Page, expect, test } from "@playwright/test";
import type { RunMetrics, StageExperiment, StageRun } from "../src/api/stage";
import { compared, idle, part, plan, runCount, runRows } from "./helpers";

let clip = "";

test.beforeAll(() => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "privframe-e2e-stage-"));
  clip = path.join(dir, "stage-clip.mp4");
  // its own picture (not the clip of real-backend.spec.ts), so nothing of this video is in the step store yet
  execFileSync(
    "ffmpeg",
    ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=duration=3:size=640x360:rate=24", "-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-shortest", "-pix_fmt", "yuv420p", clip],
    { stdio: "pipe" },
  );
});

test.afterAll(() => {
  if (clip) fs.rmSync(path.dirname(clip), { recursive: true, force: true });
});

// the records as this spec reads them: every run it looks into has started and has its measurements
type Run = Omit<StageRun, "caseId" | "metrics"> & { caseId: string; metrics: RunMetrics };
type Experiment = Omit<StageExperiment, "runs"> & { runs: Run[] };

const record = async (request: APIRequestContext, id: string) => (await (await request.get(`/api/lab/stage-experiments/${id}`)).json()) as Experiment;
const idOf = (page: Page) => new URL(page.url()).pathname.split("/").pop() ?? "";

test("sets of runs over a range: a matrix with reuse, a range the user answers, and a range that continues the answered run", async ({ page, request }) => {
  test.setTimeout(600_000);
  page.on("dialog", (dialog) => {
    throw new Error(`unexpected browser dialog: ${dialog.message()}`);
  });

  // the sections, what each asks the user, and for every step what its result depends on, come from the server
  const sections = (await (await request.get("/api/lab/sections")).json()) as { id: string; interactions: { kind: string }[]; steps: { node: string; slots: string[]; stored: boolean }[] }[];
  expect(sections.map((s) => s.id)).toEqual(["perception", "analysis", "plan", "review", "render"]);
  expect(Object.fromEntries(sections.map((s) => [s.id, s.interactions.map((i) => i.kind)]))).toEqual({ perception: [], analysis: ["identity", "questions"], plan: [], review: ["review", "audit"], render: [] });
  const describe = sections[0].steps.find((s) => s.node === "perceive/describe_segments");
  expect(describe?.slots).toContain("vlm.scene");
  expect(describe?.stored).toBe(true);
  expect(((await (await request.get("/api/lab/evaluators")).json()) as { id: string }[]).map((e) => e.id)).toEqual(["expected"]);

  // 1. a clip uploaded as a case
  const created = await request.post("/api/cases", {
    multipart: { file: { name: "stage-clip.mp4", mimeType: "video/mp4", buffer: fs.readFileSync(clip) }, memo: "구간 실험용 테스트 영상", mode: "anonymized_upload", sample: "false" },
  });
  expect(created.ok()).toBe(true);
  const caseId = ((await created.json()) as { id: string }).id;
  // analysed far enough to stop at its first question: the step store now holds its perception
  await expect
    .poll(async () => ((await (await request.get(`/api/cases/${caseId}`)).json()) as { status: string }).status, { timeout: 90_000 })
    .not.toMatch(/^(QUEUED|ANALYZING)$/);

  // 2. a video set made from that case, closed to cloud models as a new set is
  const name = `실험용 영상 ${Date.now()}`;
  await page.goto("/lab/datasets");
  await expect(page.locator(".mock-badge")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /^영상 데이터셋 \d+$/ })).toBeVisible();
  // the evaluator that scores runs is the one registered on the server
  await expect(page.getByTestId("registered-evaluators").locator('tbody tr[data-evaluator="expected"]')).toContainText("데이터셋 정답과 비교");
  // a fresh server may have no sets: the empty state offers the same action as the section's header
  await page.getByRole("button", { name: "새 데이터셋" }).first().click();
  const form = page.getByRole("form", { name: "새 영상 데이터셋" });
  await form.getByLabel("이름").fill(name);
  await expect(form.getByRole("radio", { name: /^금지/ })).toBeChecked();
  await form.getByRole("button", { name: "데이터셋 만들기" }).click();
  const card = page.locator(".vset", { has: page.getByRole("heading", { name }) });
  await expect(card).toContainText("외부 전송 금지");
  await card.getByRole("button", { name: "영상 추가" }).click();
  await card.getByText("기존 실행에서").click();
  await card.locator(".vs-add select").selectOption(caseId);
  await card.getByLabel("제목 (선택)").fill("테스트 클립");
  await card.getByRole("button", { name: "영상 추가" }).last().click();
  const video = card.locator("tbody tr", { hasText: "stage-clip.mp4" });
  await expect(video).toContainText("테스트 클립");
  await expect(video).toContainText("640×360");
  await expect(video.getByRole("link", { name: caseId })).toBeVisible();
  // what the scripted detectors find in any clip, written down as the video's labels
  await video.getByRole("button", { name: "테스트 클립 고치기" }).click();
  const labels = card.getByRole("form", { name: "테스트 클립 고치기" });
  await expect(labels.getByLabel(/질문에 자동으로 답할 값/)).toHaveCount(0); // nothing answers for the person any more
  await labels.getByLabel(/영상에 보이는 글자/).fill("김민지 바리스타");
  await labels.getByLabel("인물 수").fill("1");
  await labels.getByRole("button", { name: "저장" }).click();
  await expect(video).toContainText("글자 1 · 인물 1");
  const setId = await datasetId(request, name);
  const item = `${setId}/${await itemId(request, name)}`;

  // 3. the launch panel refuses what the server would refuse
  await page.goto("/lab/experiments/new");
  const rail = page.getByRole("group", { name: "실험할 구간" });
  await expect(rail.locator(".rail-sec", { hasText: "영상 정보 추출" })).toHaveAttribute("aria-pressed", "true");
  await expect(rail.locator(".rail-sec", { hasText: "법률 분석" }).locator(".rail-asks")).toContainText("같은 사람 확인 · 질문");
  await expect(page.locator(".sx-base select")).toHaveValue("baseline-v2");
  const set = page.locator(".pick", { hasText: name });
  await set.getByRole("checkbox").check(); // the only video there is may be chosen already
  await expect(runCount(page)).toContainText("실행 1건");
  // (a) the base profile sends frames to a cloud model and the set may not leave this machine
  const start = page.getByRole("button", { name: "실험 시작" });
  await expect(plan(page).locator(".sx-problems")).toContainText(`'${name}'은(는) 외부 전송 금지 데이터셋인데, 이 조합은 프레임을 외부 모델로 보냅니다.`);
  await expect(start).toBeDisabled();
  const closed = await request.post("/api/lab/stage-experiments", { data: { from: "perception", baseProfile: "baseline-v2", datasets: [setId] } });
  expect(closed.status()).toBe(422);
  expect(((await closed.json()) as { detail: string }).detail).toContain("외부 전송 금지 데이터셋인데");
  // (b) a candidate this backend has no adapter for is not offered, with the server's reason
  const detector = part(page, /^사람 · 물체 검출/);
  await detector.getByRole("button", { name: /^사람 · 물체 검출/ }).click();
  const yolo = detector.locator(".pick-option", { hasText: "YOLO26n" });
  await expect(yolo.locator(".pick-reason")).toContainText("실행하는 어댑터가 아직 없습니다");
  await expect(yolo.getByRole("checkbox")).toBeDisabled();
  // (c) a profile that keeps frames on this machine cannot take a cloud VLM
  await page.locator(".sx-base select").selectOption("local-only");
  const localScene = part(page, /^장면 설명/);
  await localScene.getByRole("button", { name: /^장면 설명/ }).click();
  const cloud = localScene.locator(".pick-option", { hasText: "Gemini 3.8 Flash" });
  await expect(cloud.locator(".pick-reason")).toContainText("V-3(로컬만)인데");
  await expect(cloud.getByRole("checkbox")).toBeDisabled();
  // (d) a range behind the questions cannot start on a video nobody has answered the analysis of
  await page.goto("/lab/experiments/new?from=plan");
  await expect(page.getByTestId("sx-reuse-note")).toContainText("사람이 답을 마친 실행에서 이어 가며");
  await page.locator(".pick", { hasText: name }).getByRole("checkbox").check();
  await expect(plan(page).locator(".sx-problems")).toContainText("'법률 분석'까지 마친 실행이 없습니다");
  await expect(page.getByRole("button", { name: "실험 시작" })).toBeDisabled();

  // 4. the set is opened to cloud models (the clip is synthetic), and the perception set starts with a matrix
  await page.goto("/lab/datasets");
  await card.getByRole("button", { name: "데이터셋 고치기" }).click();
  const setForm = card.getByRole("form", { name: `${name} 고치기` });
  await setForm.getByRole("radio", { name: /^허용/ }).check();
  await setForm.getByRole("button", { name: "저장" }).click();
  await expect(card).toContainText("외부 전송 허용");

  await page.goto("/lab/experiments/new");
  await expect(page.getByTestId("sx-interactive-notice")).toHaveCount(0);
  await page.locator(".pick", { hasText: name }).getByRole("checkbox").check();
  const scene = part(page, /^장면 설명/);
  await scene.getByRole("button", { name: /^장면 설명/ }).click();
  await expect(scene.locator(".pick-option", { hasText: "기준 프로필 그대로" }).getByRole("checkbox")).toBeChecked();
  await scene.locator(".pick-option", { hasText: "Gemini 3.8 Flash" }).getByRole("checkbox").check();
  await expect(runCount(page)).toContainText("실행 2건");
  await expect(plan(page).locator(".sx-plan-sub")).toHaveText("조합 2개 × 영상 1개");
  await expect(plan(page).locator('.sx-plan-table tbody tr[data-start="scratch"]')).toHaveCount(2); // the first section starts from the beginning
  await expect(plan(page).locator(".sx-plan-table")).toContainText("장면 설명 Gemini 3.8 Flash");
  // what the new combination computes again comes with the plan
  await expect(plan(page).locator(".sx-plan-new li")).toHaveText(["장면 설명 Gemini 3.8 Flash — 기준과 달라 다시 계산: 장면 설명 · 영상 이해 조립"]);
  await page.getByLabel("제목 (선택)").fill("장면 설명 VLM 비교 (E2E)");
  await expect(start).toBeEnabled({ timeout: 15_000 });
  await start.click();
  await expect(page).toHaveURL(/\/lab\/experiments\/sx_[^/?]+$/);
  const perceptionId = idOf(page);

  // 5. it finishes: both combinations ran, and the swapped one took what it did not change from the store
  await expect(idle(page)).toBeVisible({ timeout: 150_000 });
  await expect(page.getByRole("heading", { name: "장면 설명 VLM 비교 (E2E)" })).toBeVisible();
  await expect(page.locator(".sx-facts")).toContainText("2 / 2건 끝남");
  await expect(compared(page)).toHaveCount(2);
  await expect(compared(page).nth(0)).toContainText("기준");
  await expect(compared(page).nth(1)).toContainText("장면 설명 Gemini 3.8 Flash");
  await expect(compared(page).nth(1)).toContainText("‘영상 정보 추출’부터 기준과 다름");
  for (const row of [compared(page).nth(0), compared(page).nth(1)]) await expect(row).toContainText("1 / 1개");
  // the scripted VLMs say the same of the scene, and both find what the labels say is there: the score columns
  // are the metrics of the registered evaluator
  const heads = page.locator("#sx-compare-title ~ .table-wrap").first().locator("thead th");
  await expect(heads.filter({ hasText: "글자 인식" })).toContainText("높을수록 좋음");
  await expect(heads.filter({ hasText: "인물 수 일치" })).toHaveCount(1);
  await expect(compared(page).nth(1)).toContainText("100%");
  await expect(compared(page).nth(1).locator(".sx-differs")).toHaveCount(0);
  // compared on what the range takes with nothing reused; what the runs actually spent stands beside it
  for (const row of [compared(page).nth(0), compared(page).nth(1)]) {
    await expect(row.locator('[data-column="full-sec"]')).toContainText(/\d초/);
    await expect(row.locator('[data-column="spent-sec"]')).toContainText(/\d초/);
  }

  let perception = await record(request, perceptionId);
  expect(perception.interactive).toBe(false);
  expect(perception.variants.map((v) => [v.id, v.base])).toEqual([["base", true], ["v1", false]]);
  expect(perception.variants[1].label).toBe("장면 설명 Gemini 3.8 Flash");
  expect(perception.variants[1].recomputes).toEqual(expect.arrayContaining(["perceive/describe_segments", "perceive/assemble"]));
  expect(perception.variants[1].recomputes).not.toContain("perceive/detect_track");
  expect(perception.variants[1].configHash).not.toBe(perception.variants[0].configHash);
  const swapped = perception.runs.find((r) => r.variant === "v1") as Run;
  expect(swapped).toMatchObject({ status: "completed", interactive: false, autoAnswered: false, start: "scratch", sourceCaseId: null, startSection: "perception" });
  expect(swapped.metrics.steps.find((s) => s.node === "perceive/detect_track")?.reused).toBe(true);
  expect(swapped.metrics.steps.find((s) => s.node === "perceive/describe_segments")?.reused).toBe(false);
  expect(swapped.metrics.reuse.steps).toContain("perceive/detect_track");
  expect(swapped.scores.map((s) => s.metric)).toEqual(expect.arrayContaining(["expected.texts", "expected.people"]));
  expect(Object.keys(perception.summary[0])).toEqual(expect.arrayContaining(["items", "sectionSecMean", "costUsdMean", "fullSecMean", "fullCostUsdMean", "agreementMean", "scores", "byDataset"]));
  const means = perception.summary[1] as { sectionSecMean: number; fullSecMean: number };
  expect(means.fullSecMean).toBeGreaterThanOrEqual(means.sectionSecMean); // spent + what reuse saved

  // the run's row opens to its steps: detection · tracking, OCR and grouping were reused, the description ran
  const swappedRow = page.locator(`.sx-runs tr[data-run="${swapped.id}"]`);
  await expect(swappedRow).toContainText("처음부터");
  await expect(swappedRow.locator(".sx-score").first()).toBeVisible();
  await swappedRow.getByRole("button", { name: `${swapped.id} 자세히` }).click();
  const steps = page.locator(".run-detail .sx-steps");
  for (const node of ["perceive/make_proxy", "perceive/detect_track", "perceive/group_identities", "perceive/ocr"]) {
    await expect(steps.locator(`tr[data-step="${node}"]`), node).toHaveAttribute("data-fate", "store");
  }
  await expect(steps.locator('tr[data-step="perceive/describe_segments"]')).toHaveAttribute("data-fate", "run");
  await expect(steps.locator('tr[data-step="perceive/assemble"]')).toHaveAttribute("data-fate", "run");
  await expect(page.locator(".run-detail")).toContainText("기준과 다른 점");
  await expect(page.locator(".run-detail .sx-score-rows")).toContainText("expected");

  // each run is a run of its own under "실행": the grid leads to its topology, which says which combination it is
  await page.locator('.results tbody tr[data-variant="v1"] .sx-cell-runs a').click();
  await expect(page).toHaveURL(new RegExp(`/lab/runs/${swapped.caseId}$`));
  await expect(page.locator('.react-flow__node[data-id="perceive"] .st-done').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".run-status")).toContainText("완료");
  await expect(page.locator(".run-status")).toContainText("조합: 장면 설명 Gemini 3.8 Flash");
  await expect(page.locator('.react-flow__node[data-id="report"] .st-done')).toHaveCount(0); // it stopped after the section
  await page.getByRole("link", { name: "구간 실험 보기" }).click();
  await expect(page).toHaveURL(new RegExp(`/lab/experiments/${perceptionId}$`));

  // 6. a second launch adds a third combination to the same set; the earlier runs stay
  await page.getByRole("button", { name: "실행 추가" }).click();
  const add = page.locator(".sx-add");
  await expect(add.getByRole("heading", { name: "실행 추가" })).toBeVisible();
  const addScene = add.locator(".pick", { has: page.getByRole("button", { name: /^장면 설명/ }) });
  await addScene.getByRole("button", { name: /^장면 설명/ }).click();
  await addScene.locator(".pick-option", { hasText: "Qwen3.6-27B" }).getByRole("checkbox").check();
  await addScene.locator(".pick-option", { hasText: "기준 프로필 그대로" }).getByRole("checkbox").uncheck(); // that candidate alone
  await add.locator(".pick", { hasText: name }).getByRole("checkbox").check();
  await expect(runCount(page)).toContainText("실행 1건");
  await expect(add.locator(".sx-plan-one")).toContainText("장면 설명 Qwen3.6-27B");
  await add.getByRole("button", { name: "실행 1건 추가" }).click();
  await expect(page.locator(".sx-facts")).toContainText("3 / 3건 끝남", { timeout: 150_000 });
  await expect(compared(page)).toHaveCount(3);
  await expect(runRows(page)).toHaveCount(3);
  perception = await record(request, perceptionId);
  expect(perception.variants.map((v) => v.id)).toEqual(["base", "v1", "v2"]);
  expect(perception.runs.map((r) => r.id)).toEqual(["r1", "r2", "r3"]);

  // 7. a range that asks the user: the screen says what that means, and takes one candidate and one video
  await page.goto("/lab/experiments/new?from=analysis");
  const notice = page.getByTestId("sx-interactive-notice");
  await expect(notice).toContainText("법률 분석: 같은 사람 확인 · 질문");
  await expect(notice).toContainText("한 번에 1건씩");
  await expect(notice).toContainText("자동으로 답하지 않습니다");
  await page.getByRole("radio", { name: "테스트 클립" }).check();
  const analyst = part(page, /^Legal Analyst/).first();
  await analyst.getByRole("button", { name: /^Legal Analyst/ }).click();
  await expect(analyst.getByRole("checkbox")).toHaveCount(0);
  await expect(analyst.locator(".pick-option", { hasText: "기준 프로필 그대로" }).getByRole("radio")).toBeChecked();
  await expect(runCount(page)).toContainText("실행 1건");
  const one = plan(page).locator(".sx-plan-one");
  await expect(one).toHaveAttribute("data-start", "fork"); // the video's perception is not computed again
  await expect(one).toContainText("‘법률 분석’ 앞에서 이어 감");
  await expect(one).toContainText("같은 사람 확인 → 질문");
  // more than one run is refused for such a range, whoever asks
  const analysts = ((await (await request.get("/api/lab/catalog")).json()) as { stages: { slots: { id: string; candidates: { id: string; status?: string }[] }[] }[] }).stages
    .flatMap((s) => s.slots)
    .find((s) => s.id === "llm.analyst")
    ?.candidates.filter((c) => c.status !== "planned")
    .slice(0, 2)
    .map((c) => c.id);
  const two = await request.post("/api/lab/stage-experiments/preview", { data: { from: "analysis", baseProfile: "baseline-v2", matrix: { "llm.analyst": analysts }, items: [item], datasets: [setId] } });
  expect(((await two.json()) as { problems: string[] }).problems.join(" ")).toContain("한 번에 1건");
  await page.getByLabel("제목 (선택)").fill("법률 분석 직접 응답 (E2E)");
  await page.getByRole("button", { name: "실험 시작" }).click();
  await expect(page).toHaveURL(/\/lab\/experiments\/sx_[^/?]+$/);
  const analysisId = idOf(page);

  // 8. the run stops at the analyst's question and waits there: nothing answers for the person
  const waiting = page.locator(".sx-waiting-table tbody tr");
  await expect(waiting).toHaveCount(1, { timeout: 120_000 });
  await expect(waiting).toContainText("질문 답변 필요");
  await expect(page.locator(".sx-facts .sx-status.is-waiting")).toContainText("응답 대기 1");
  await expect(page.locator(".sx-facts .sx-asks")).toHaveText("직접 응답");
  await page.waitForTimeout(3000);
  let analysis = await record(request, analysisId);
  expect(analysis.status).toBe("waiting");
  expect(analysis.runs[0]).toMatchObject({ status: "waiting", waitingFor: "questions", interactive: true, autoAnswered: false, start: "fork", startSection: "analysis" });
  const answeredCase = analysis.runs[0].caseId;
  // it is a run the person answers, and it is not among the uploader's own work on the service home
  const listed = ((await (await request.get("/api/lab/runs")).json()) as { id: string; waitingFor?: string; interactive?: boolean }[]).find((r) => r.id === answeredCase);
  expect(listed).toMatchObject({ waitingFor: "questions", interactive: true });
  expect(((await (await request.get("/api/cases")).json()) as { id: string }[]).some((c) => c.id === answeredCase)).toBe(false);

  // the person answers on the service screen of that run, and comes back to the set
  await waiting.getByRole("link", { name: "서비스 화면에서 질문 답변" }).click();
  await expect(page).toHaveURL(new RegExp(`/cases/${answeredCase}$`));
  await expect(page.getByRole("link", { name: "구간 실험", exact: true })).toHaveAttribute("href", `/lab/experiments/${analysisId}`);
  await expect(page.getByRole("heading", { name: /판단에 필요한 사실 1가지/ })).toBeVisible({ timeout: 60_000 });
  await page.locator('input[name="q-Q1"][value="public"]').check();
  await page.getByRole("button", { name: /답변 보내기/ }).click();
  await expect(page.getByRole("heading", { name: "요청한 구간까지만 실행한 실험용 실행입니다" })).toBeVisible({ timeout: 120_000 });
  await page.getByRole("link", { name: "구간 실험으로 돌아가기" }).click();
  await expect(page).toHaveURL(new RegExp(`/lab/experiments/${analysisId}$`));
  await expect(idle(page)).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".sx-waiting-table")).toHaveCount(0);
  await expect(runRows(page).first()).toHaveAttribute("data-status", "completed");
  await expect(runRows(page).first()).toContainText(/보호 요건 \d+ · 질문 1/);
  await expect(runRows(page).first()).toContainText("응답 대기"); // the wait is reported apart from the run's own time
  analysis = await record(request, analysisId);
  expect(analysis.runs[0].metrics.analysis?.questions).toBe(1);
  expect(analysis.runs[0].metrics.waitSec).toBeGreaterThan(2);

  // 9. a range behind the questions: its runs continue the answered run and ask nothing
  await page.goto("/lab/experiments/new?from=plan");
  await expect(page.getByTestId("sx-interactive-notice")).toHaveCount(0);
  await page.locator(".pick", { hasText: name }).getByRole("checkbox").check();
  const planner = part(page, /^Planner/).first();
  await planner.getByRole("button", { name: /^Planner/ }).click();
  const other = planner.locator(".pick-option:not(.is-off)").filter({ hasNotText: "기준 프로필 그대로" }).first();
  const otherName = (await other.locator("label").innerText()).trim();
  await other.getByRole("checkbox").check();
  await expect(runCount(page)).toContainText("실행 2건");
  await expect(plan(page).locator(".sx-problems")).toHaveCount(0);
  const planned = plan(page).locator(".sx-plan-table tbody tr[data-start]");
  await expect(planned).toHaveCount(2);
  for (const row of await planned.all()) {
    await expect(row).toHaveAttribute("data-start", "fork");
    await expect(row).toContainText("‘계획’ 앞에서 이어 감");
    await expect(row.getByRole("link")).toHaveAttribute("href", `/lab/runs/${answeredCase}`);
  }
  await expect(plan(page).locator(".sx-plan-table thead")).not.toContainText("묻는 것"); // nothing is asked
  await page.getByLabel("제목 (선택)").fill("계획 모델 비교 (E2E)");
  await page.getByRole("button", { name: "실험 시작" }).click();
  await expect(page).toHaveURL(/\/lab\/experiments\/sx_[^/?]+$/);
  const planId = idOf(page);
  await expect(idle(page)).toBeVisible({ timeout: 150_000 });
  await expect(page.locator(".sx-waiting-table")).toHaveCount(0); // it never waited for anyone
  await expect(compared(page)).toHaveCount(2);
  await expect(compared(page).nth(1)).toContainText(otherName.split(" · ")[0]);
  for (const row of await runRows(page).all()) {
    await expect(row).toHaveAttribute("data-status", "completed");
    await expect(row.locator(".sx-run-start")).toContainText("‘계획’ 앞에서 이어 감");
    await expect(row.locator(".sx-run-start").getByRole("link")).toHaveAttribute("href", `/lab/runs/${answeredCase}`);
    await expect(row).toContainText("결정 2");
  }
  let planSet = await record(request, planId);
  expect(planSet.interactive).toBe(false);
  for (const run of planSet.runs) {
    expect(run).toMatchObject({ status: "completed", start: "fork", sourceCaseId: answeredCase, forkedAt: "plan", startSection: "plan", interactive: false });
    expect(run.metrics.waitSec).toBe(0);
    expect(run.metrics.plan?.decisions).toBe(2);
    // the answer came with the checkpoint: the run's own case has it, without having asked
    const detail = (await (await request.get(`/api/cases/${run.caseId}`)).json()) as { experimentId: string; stoppedAfter: string; questions: { answer?: string }[] };
    expect(detail.questions.map((q) => q.answer)).toEqual(["public"]);
    expect(detail).toMatchObject({ experimentId: planId, stoppedAfter: "review_guide" });
  }

  // 10. a second launch into the plan set: the base combination again, on the same video
  await page.getByRole("button", { name: "실행 추가" }).click();
  await page.locator(".sx-add .pick", { hasText: name }).getByRole("checkbox").check();
  await expect(runCount(page)).toContainText("실행 1건");
  const again = page.locator(".sx-add .sx-plan-one");
  await expect(again).toHaveAttribute("data-start", "fork");
  await expect(again).toContainText("그때 사용자가 한 답을 그대로 씁니다. 다시 묻지 않습니다.");
  await expect(again).toContainText("없음 — 끝까지 혼자 실행됩니다");
  const picker = again.getByLabel("이어 갈 실행");
  await expect(picker).toHaveValue(answeredCase); // the run that worked the analysis out itself comes first
  // another run of the list can be named, and the list stays whole
  const offered = await picker.locator("option").evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value));
  expect(offered.length).toBeGreaterThan(1);
  await picker.selectOption(offered[1]);
  await expect(again.locator(".sx-plan-source a")).toHaveAttribute("href", `/lab/runs/${offered[1]}`);
  await expect(picker.locator("option")).toHaveCount(offered.length);
  await picker.selectOption(answeredCase);
  await expect(again.locator(".sx-plan-source a")).toHaveAttribute("href", `/lab/runs/${answeredCase}`);
  await page.locator(".sx-add").getByRole("button", { name: "실행 1건 추가" }).click();
  await expect(page.locator(".sx-facts")).toContainText("3 / 3건 끝남", { timeout: 150_000 });
  await expect(compared(page)).toHaveCount(2); // no new combination
  await expect(page.locator('.results tbody tr[data-variant="base"] td.result-cell')).toContainText("실행 2건");
  planSet = await record(request, planId);
  expect(planSet.runs.map((r) => [r.id, r.variant])).toEqual([["r1", "base"], ["r2", "v1"], ["r3", "base"]]);

  // 11. the list: three sets, the asking one marked, each with its runs; the step store has something to offer
  await page.goto("/lab/experiments");
  const row = (id: string) => page.locator(`.sx-list tbody tr[data-experiment="${id}"]`);
  await expect(row(perceptionId)).toContainText("영상 정보 추출");
  await expect(row(perceptionId)).toContainText("3 / 3건");
  await expect(row(perceptionId).locator(".sx-asks")).toHaveCount(0);
  await expect(row(analysisId).locator(".sx-asks")).toHaveText("직접 응답");
  await expect(row(analysisId)).toContainText("1 / 1건");
  await expect(row(planId)).toContainText("3 / 3건");
  await expect(row(planId).locator(".sx-status")).toHaveText("모두 끝남");
  await expect(page.locator(".store-list")).toContainText("검출 · 추적");
  await expect(page.locator(".store-list")).toContainText("메모에서 사실 추출"); // named by the server, not by a section
  await expect(page.locator(".sx-later")).toHaveCount(0); // nothing of the later milestone is offered here

  // a new set over a range that has one leads to it first
  await page.goto("/lab/experiments/new?from=plan");
  const same = page.getByTestId("sx-same-range");
  await expect(same).toContainText("계획 모델 비교 (E2E)");
  await expect(same.getByRole("link", { name: "기존 실험에 추가" })).toHaveAttribute("href", `/lab/experiments/${planId}?add=1`);
  await expect(page.getByRole("heading", { name: "첫 실행" })).toHaveCount(0);
  await same.getByRole("link", { name: "기존 실험에 추가" }).click();
  await expect(page.locator(".sx-add").getByRole("heading", { name: "실행 추가" })).toBeVisible();
});

async function videoSet(request: APIRequestContext, name: string) {
  const sets = (await (await request.get("/api/lab/video-sets")).json()) as { id: string; name: string; items: Record<string, unknown>[] }[];
  const found = sets.find((s) => s.name === name);
  expect(found).toBeTruthy();
  return found!;
}

async function datasetId(request: APIRequestContext, name: string): Promise<string> {
  const found = await videoSet(request, name);
  // the web app is not told where the videos are on the server, and a video carries no answers
  for (const item of found.items) {
    expect(item).not.toHaveProperty("video");
    expect(item).not.toHaveProperty("sha256");
    expect(item).not.toHaveProperty("answers");
  }
  return found.id;
}

async function itemId(request: APIRequestContext, name: string): Promise<string> {
  return String((await videoSet(request, name)).items[0].id);
}
