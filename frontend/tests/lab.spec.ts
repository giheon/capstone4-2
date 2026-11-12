import { type APIRequestContext, type Locator, type Page, expect, test } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("/api/lab/settings", { data: { speed: 8 } });
});

/** a demo run that is over: its record no longer moves, whatever the other tests start meanwhile */
async function finishedDemo(request: APIRequestContext): Promise<string> {
  const runs = (await (await request.get("/api/lab/runs")).json()) as { id: string; status: string; live: boolean }[];
  const demo = runs.find((r) => r.id.startsWith("demo_") && r.status === "COMPLETED" && !r.live);
  expect(demo).toBeTruthy();
  return demo!.id;
}

async function openFinishedDemo(page: Page, request: APIRequestContext): Promise<void> {
  await page.goto(`/lab/runs/${await finishedDemo(request)}`);
  await expect(page.locator('.react-flow__node[data-id="report"] .st-done')).toBeVisible();
}

const seqs = (rows: Locator) => rows.evaluateAll((els) => els.map((el) => Number((el as HTMLElement).dataset.seq)));

/** where a record sits in its list: "in" the visible part (below whatever sticks to the top), or "out" of it */
async function inList(page: Page, which: "first-hit" | "last-row", scroller = ".inspector-body"): Promise<"in" | "out" | "none"> {
  return page.evaluate(
    ([which, scroller]) => {
      const box = document.querySelector(scroller);
      if (!box) return "none";
      const rows = box.querySelectorAll(which === "first-hit" ? "[data-hit]" : "[data-seq]");
      const row = which === "first-hit" ? rows[0] : rows[rows.length - 1];
      if (!row) return "none";
      const head = box.querySelector("[data-rec-head]");
      const top = head ? head.getBoundingClientRect().bottom : box.getBoundingClientRect().top;
      const r = row.getBoundingClientRect();
      return r.top >= top - 1 && r.bottom <= box.getBoundingClientRect().bottom + 1 ? "in" : "out";
    },
    [which, scroller] as const,
  );
}

test("the recorded demo shows the topology, agent memory and replay", async ({ page, request }) => {
  const runs = (await (await request.get("/api/lab/runs")).json()) as { id: string }[];
  const demo = runs.find((r) => r.id.startsWith("demo_"));
  expect(demo).toBeTruthy();
  await page.goto(`/lab/runs/${demo!.id}`);
  const model = page.locator('.react-flow__node[data-id="legal_analyst/model"]');
  await expect(model).toBeVisible();
  await expect(page.locator('.react-flow__node[data-id="report"] .st-done')).toBeVisible();

  await model.click();
  await expect(page.getByRole("tab", { name: "에이전트 메모리" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".mem-item").first()).toBeVisible();

  await page.getByText("재생", { exact: true }).click();
  const slider = page.getByLabel("이벤트 위치");
  await expect(slider).toBeVisible();
  await slider.fill("40");
  await expect(page.locator('.react-flow__node[data-id="report"] .st-done')).toHaveCount(0);
});

test("a live demo run lights nodes up as it progresses", async ({ page }) => {
  await page.goto("/lab/runs");
  await page.getByRole("button", { name: "데모 실행 시작" }).click();
  await expect(page).toHaveURL(/\/lab\/runs\/demo_/);
  await expect(page.locator('.react-flow__node[data-id="ingest"] .st-done')).toBeVisible({ timeout: 30_000 });
  await expect(
    page.locator('.react-flow__node[data-id="legal_analyst/model"] .st-running, .react-flow__node[data-id="legal_analyst/model"] .st-done'),
  ).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".tl-bar").first()).toBeVisible();
});

const labNav = (page: Page) => page.getByRole("navigation", { name: "실험실 메뉴" });

test("the Lab opens on the run list, under one menu entry", async ({ page }) => {
  await page.goto("/lab");
  await expect(page).toHaveURL(/\/lab\/runs$/);
  await expect(page.getByRole("heading", { name: "실행", level: 1 })).toBeVisible();
  // runs and their topology are one entry: there is no separate topology or run-history menu
  await expect(labNav(page).getByRole("link")).toHaveText(["실행", "실험", "데이터셋 · 평가기", "프로필", "모델 후보"]);
  await expect(labNav(page).getByRole("link", { name: "실행", exact: true })).toHaveAttribute("aria-current", "page");
  // the lead sends composing and comparing runs to the experiments menu
  await expect(page.locator(".lab-lead").getByRole("link", { name: "실험" })).toHaveAttribute("href", "/lab/experiments");
  // the one place a demo run starts
  await expect(page.getByRole("button", { name: /데모 실행/ })).toHaveCount(1);
  // policy F1 exists only on the mock's example summaries: the table of runs has no column for it
  const tables = page.locator(".runs-table");
  await expect(tables.first().locator("tbody tr").first()).toBeVisible();
  await expect(tables.first().locator("thead")).not.toContainText("정책 F1");
  await expect(tables.last().locator("thead")).toContainText("정책 F1");
  await expect(tables.last().locator("tr.run-row")).toHaveCount(0);

  // the brand and the service's way into the Lab land on the same list
  await page.goto("/lab/profiles");
  await page.getByRole("link", { name: "PrivFrame 실험실" }).click();
  await expect(page).toHaveURL(/\/lab\/runs$/);
  await page.goto("/");
  await page.getByRole("navigation", { name: "영역" }).getByRole("link", { name: "실험실" }).click();
  await expect(page).toHaveURL(/\/lab\/runs$/);
});

test("a run opens from the list into its topology, and the bar leads back to the list", async ({ page, request }) => {
  const id = await finishedDemo(request);
  await page.goto("/lab/runs");
  // the run's own row: rows of runs that continued from it name it too (section experiments)
  const own = () => page.locator("tr.run-row", { has: page.locator(`a.run-link[href="/lab/runs/${id}"]`) });
  const row = own();
  // the title is the row's one link to the run, and a click anywhere else in the row follows it
  await expect(row.locator(`a[href="/lab/runs/${id}"]`)).toHaveCount(1);
  await row.locator("td.num").first().click();
  await expect(page).toHaveURL(new RegExp(`/lab/runs/${id}$`));
  await expect(page.locator('.react-flow__node[data-id="report"] .st-done')).toBeVisible();
  await expect(labNav(page).getByRole("link", { name: "실행", exact: true })).toHaveAttribute("aria-current", "page");

  // the bar names the run it shows; choosing another run and starting one happen in the list
  const bar = page.locator(".runbar");
  await expect(bar.getByRole("heading", { level: 1 })).toContainText("카페 인터뷰");
  await expect(bar.locator(".runbar-id")).toHaveText(id);
  await expect(bar.locator("select")).toHaveCount(0);
  await expect(bar.getByRole("button", { name: /데모 실행/ })).toHaveCount(0);
  await expect(bar.locator(".run-status")).toContainText("완료");
  // a run that answered itself has no service screen
  await expect(bar.getByRole("link", { name: /서비스 화면/ })).toHaveCount(0);

  await bar.getByRole("link", { name: "실행 목록" }).click();
  await expect(page).toHaveURL(/\/lab\/runs$/);
  await expect(own()).toBeVisible();
  // the row's own links go where they say, not to the topology
  await own().locator("td.runs-profile a").click();
  await expect(page).toHaveURL(/\/lab\/profiles\//);
});

test("a run id the server does not have says so and leads back to the list", async ({ page }) => {
  await page.goto("/lab/runs/demo_missing0");
  await expect(page.getByText("이 실행을 찾을 수 없습니다")).toBeVisible();
  await expect(page.locator(".empty-state")).toContainText("demo_missing0");
  await expect(page.locator(".runbar")).toHaveCount(0);
  await page.getByRole("link", { name: "실행 목록으로" }).click();
  await expect(page).toHaveURL(/\/lab\/runs$/);
  await expect(page.getByRole("heading", { name: "실행", level: 1 })).toBeVisible();
});

test("a run that waits for a person is marked in the list and answered through the service screen", async ({ page, request }) => {
  // a service case answers nothing by itself: it stops at the first thing it needs a person for
  const created = await request.post("/api/cases", {
    data: { filename: "cafe.mp4", sizeBytes: 1000, memo: "카페 인터뷰 영상입니다.", mode: "anonymized_upload", sample: true },
  });
  const id = ((await created.json()) as { id: string }).id;
  await expect
    .poll(async () => ((await (await request.get("/api/lab/runs")).json()) as { id: string; waitingFor?: string }[]).find((r) => r.id === id)?.waitingFor, { timeout: 30_000 })
    .toBe("identity");

  await page.goto("/lab/runs");
  const row = page.locator("tr.run-row", { hasText: id });
  await expect(row).toHaveClass(/is-waiting/);
  await expect(row.locator(".status-text.is-waiting")).toHaveText("대상 확인 필요");
  // runs that answer themselves are never counted as waiting, whatever status they pass through
  await page.getByLabel("응답 대기만").check();
  await expect(row).toBeVisible();
  await expect(page.locator("tr.run-row:not(.is-waiting)")).toHaveCount(0);

  await row.getByRole("link").first().click();
  await expect(page).toHaveURL(new RegExp(`/lab/runs/${id}$`));
  const answer = page.locator(".runbar-actions").getByRole("link");
  await expect(answer).toHaveText("서비스 화면에서 대상 확인");
  await expect(answer).toHaveClass(/btn-primary/);
  await answer.click();
  await expect(page).toHaveURL(new RegExp(`/cases/${id}$`));
  await expect(page.getByRole("heading", { name: /같은 사람인가요/ })).toBeVisible();
});

test("checkpoints are read-only: continuing with other parts belongs to section experiments", async ({ page, request }) => {
  await openFinishedDemo(page, request);
  await page.getByRole("tab", { name: "체크포인트" }).click();
  const panel = page.locator(".checkpoints");
  await expect(panel.locator(".ckpt").first()).toBeVisible();
  await expect(panel.locator(".ckpt").first().getByRole("button")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: /분기|이어서 실행/ })).toHaveCount(0);
  await expect(panel.locator("form, select")).toHaveCount(0);
  await expect(panel.getByRole("link", { name: "구간 실험" })).toHaveAttribute("href", "/lab/experiments");
});

test("LLMOps pages render their data", async ({ page }) => {
  // section experiments are the experiments page (tests/stage-experiments.spec.ts); the whole-pipeline
  // evaluation experiments of the later milestone are one link away on the mock
  await page.goto("/lab/experiments");
  await expect(page.getByRole("heading", { name: "실험", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "평가 실험 열기" }).click();
  await page.getByRole("link", { name: "베이스라인 비교" }).click();
  await expect(page).toHaveURL(/\/lab\/experiments\/pipeline\/exp-/);
  await expect(page.getByRole("heading", { name: "베이스라인 비교" })).toBeVisible();
  await expect(page.locator(".heat-cell")).toHaveCount(65);
  await page.goto("/lab/datasets");
  await expect(page.getByRole("heading", { name: /^등록된 평가기 \d+$/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^계획된 평가기 \d+/ })).toBeVisible();
  await page.goto("/lab/profiles");
  await expect(page.getByText(/다른 항목 \d+개/)).toBeVisible();
  await expect(page.getByRole("link", { name: /baseline-v2/ }).first()).toBeVisible();
});

test("a profile assembled from catalog parts runs with those parts", async ({ page }) => {
  await page.goto("/lab/profiles/new?base=baseline-v2");
  const id = page.getByRole("textbox", { name: "ID", exact: true });
  await id.fill("mini-analyst");
  await page.locator("#slot-llm-analyst").selectOption("gpt-5.4-mini");
  await expect(page.locator(".slot-field.is-changed")).toHaveCount(1);
  await expect(page.locator(".summary-cost")).toContainText("$");
  await page.getByRole("button", { name: "저장하고 실행" }).click();
  await expect(page).toHaveURL(/\/lab\/runs\/demo_/);
  await expect(page.locator(".run-profile")).toHaveText("mini-analyst");
  await expect(page.locator('.react-flow__node[data-id="legal_analyst/model"]')).toContainText("GPT-5.4 mini", { timeout: 30_000 });

  await page.goto("/lab/profiles/mini-analyst");
  await expect(page.getByRole("heading", { name: "mini-analyst" })).toBeVisible();
  await expect(page.locator(".parts-table tr.is-diff")).toHaveCount(1);
});

test("a candidate added in the Lab is offered in the profile editor", async ({ page }) => {
  await page.goto("/lab/catalog");
  const card = page.locator(".slot-card", { has: page.getByRole("heading", { name: "임베딩", exact: true }) });
  await card.getByRole("button", { name: "후보 추가" }).click();
  await card.getByLabel("Hugging Face 저장소").fill("kakaocorp/kanana-2-3b-embedding");
  await card.getByLabel("화면 이름").fill("Kanana-2 3B 임베딩");
  await card.getByLabel("라이선스").fill("Kanana Open License");
  await card.getByRole("button", { name: "카탈로그에 추가" }).click();
  await expect(card.getByRole("status")).toContainText("추가했습니다");
  await expect(card.locator("tbody")).toContainText("실험실 추가");

  await page.goto("/lab/profiles/new");
  await expect(page.locator("#slot-rag-embedding option", { hasText: "Kanana-2 3B 임베딩" })).toHaveCount(1);
});

test("an experiment scores profiles against the gold plan", async ({ page, request }) => {
  const denied = await request.post("/api/lab/profiles/budget-saver/promote", { data: { target: "default" } });
  expect(denied.status()).toBe(409);

  // the link of a profile page opens the form with the profile chosen
  await page.goto("/lab/experiments/pipeline?profiles=budget-saver");
  await page.getByRole("button", { name: /실험 시작 · 실행/ }).click();
  await expect(page).toHaveURL(/\/lab\/experiments\/pipeline\/exp-/);
  const gate = page.locator(".gate-matrix");
  await expect(gate).toContainText("baseline-v2");
  await expect(gate.locator(".gate-total")).toContainText("미달");
  await expect(page.getByRole("table").filter({ hasText: "원인" })).toContainText("명찰");
  await page.locator("main").getByRole("link", { name: "토폴로지" }).first().click();
  await expect(page).toHaveURL(/\/lab\/runs\/exp_/);
  await expect(page.locator('.react-flow__node[data-id="report"] .st-done')).toBeVisible();
});

test("inspector records read oldest first, top to bottom", async ({ page, request }) => {
  await openFinishedDemo(page, request);
  const ascending = (list: number[]) => expect(list).toEqual([...list].sort((a, b) => a - b));

  // state channels (the tab the inspector opens on): in the order of their latest write
  const channels = page.locator(".channel-list > li");
  await expect(channels.first()).toContainText("video");
  await expect(channels.last()).toContainText("report");
  const channelSeqs = await seqs(channels);
  expect(channelSeqs.length).toBeGreaterThan(5);
  ascending(channelSeqs);

  // event log: a finished run is listed whole, from its first event at the top to its end at the bottom
  await page.getByRole("tab", { name: "이벤트" }).click();
  const rows = page.locator(".event-row");
  await expect(rows.first()).toContainText("run.start");
  await expect(rows.last()).toContainText("run.end");
  const eventSeqs = await seqs(rows);
  expect(eventSeqs[0]).toBe(1);
  expect(eventSeqs.length).toBeGreaterThan(300);
  ascending(eventSeqs);
  // a record that no longer moves opens at its beginning
  expect(await page.locator(".inspector-body").evaluate((el) => el.scrollTop)).toBe(0);

  await page.getByRole("tab", { name: "체크포인트" }).click();
  const checkpoints = page.locator(".ckpt");
  await expect(checkpoints.first()).toContainText("step 1 ");
  ascending(await seqs(checkpoints));

  // agent memory: the system prompt first, the agent's last message at the bottom
  await page.locator('.react-flow__node[data-id="legal_analyst/model"]').click();
  await page.getByRole("tab", { name: "에이전트 메모리" }).click();
  const messages = page.locator(".mem-item");
  await expect(messages.first()).toHaveClass(/role-system/);
  ascending(await seqs(messages));
});

test("picking a node takes the record list to that node's records and marks them", async ({ page, request }) => {
  await openFinishedDemo(page, request);
  await page.getByRole("tab", { name: "이벤트" }).click();
  const rows = page.locator(".event-row");
  await expect(rows.first()).toContainText("run.start");
  await expect(page.locator("[data-hit]")).toHaveCount(0);

  await page.locator('.react-flow__node[data-id="plan"]').click();
  await expect(page.locator(".inspector-title h2")).toHaveText("계획 수립");
  await expect(page.locator(".rec-note")).toHaveText(/계획 수립 노드 이벤트 \d+건/);
  // the node ran long after the top of the list: its first record is scrolled in, clear of the sticky filters
  await expect.poll(() => inList(page, "first-hit")).toBe("in");
  expect(await page.locator(".inspector-body").evaluate((el) => el.scrollTop)).toBeGreaterThan(1000);

  // marked: every record of plan, and nothing else
  const marks = await rows.evaluateAll((els) =>
    els.map((el) => ({
      kind: el.querySelector(".ev-kind")?.textContent ?? "",
      text: el.querySelector(".ev-text")?.textContent ?? "",
      hit: el.classList.contains("is-hit"),
    })),
  );
  const starts = marks.filter((m) => m.kind === "node.start");
  expect(starts.length).toBeGreaterThan(20);
  for (const m of starts) expect(m.hit, `node.start ${m.text}`).toBe(m.text === "plan");
  for (const m of marks.filter((m) => m.kind === "state")) expect(m.hit, `state ${m.text}`).toBe(/← plan:/.test(m.text));
  for (const m of marks.filter((m) => ["run.start", "run.status", "activity", "run.end"].includes(m.kind))) expect(m.hit, m.kind).toBe(false);
  for (const m of marks.filter((m) => m.hit)) expect(m.text).toContain("plan");
  // not by colour alone: a bar in the margin and heavier text
  const hit = page.locator(".event-row.is-hit").first();
  expect(await hit.evaluate((el) => getComputedStyle(el, "::before").borderLeftWidth)).toBe("2px");
  expect(Number(await hit.locator(".ev-kind").evaluate((el) => getComputedStyle(el).fontWeight))).toBeGreaterThanOrEqual(700);

  // picking the same node again comes back to its records
  await page.locator(".inspector-body").hover();
  await page.mouse.wheel(0, 3000);
  await expect.poll(() => inList(page, "first-hit")).toBe("out");
  await page.locator('.react-flow__node[data-id="plan"]').click();
  await expect.poll(() => inList(page, "first-hit")).toBe("in");

  // the other lists follow the same selection
  await page.getByRole("tab", { name: "상태 메모리" }).click();
  await expect(page.locator('.channel-list > li[data-channel="plan"]')).toHaveClass(/is-hit/);
  await expect(page.locator('.channel-list > li[data-channel="video"]')).not.toHaveClass(/is-hit/);
  await expect(page.locator(".channel-list > li.is-hit")).toHaveCount(1);
  await expect.poll(() => inList(page, "first-hit")).toBe("in");
  await page.getByRole("tab", { name: "체크포인트" }).click();
  await expect(page.locator(".ckpt.is-hit")).toHaveCount(1);
  await expect(page.locator(".ckpt.is-hit .ckpt-next")).toHaveText("→ plan");

  // a node holds what is inside it: the agent's records include its model turns and tool calls
  await page.getByRole("tab", { name: "이벤트" }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.locator('.react-flow__node[data-id="legal_analyst"]').click({ position: { x: 40, y: 8 } });
  await expect(page.locator(".inspector-title h2")).toHaveText("Legal Analyst");
  await expect.poll(() => inList(page, "first-hit")).toBe("in");
  const agentStarts = await page
    .locator(".event-row.ev-node-start")
    .evaluateAll((els) => els.map((el) => ({ text: el.querySelector(".ev-text")?.textContent ?? "", hit: el.classList.contains("is-hit") })));
  expect(agentStarts.filter((m) => m.hit).length).toBeGreaterThan(10);
  for (const m of agentStarts) expect(m.hit, `node.start ${m.text}`).toBe(m.text === "legal_analyst" || m.text.startsWith("legal_analyst/"));

  // a list with nothing from the node says so
  await page.locator('.react-flow__node[data-id="legal_analyst/checklist"]').click();
  await page.getByRole("tab", { name: "체크포인트" }).click();
  await expect(page.locator(".rec-note")).toHaveText("checklist 노드 직전에 저장된 체크포인트는 없습니다.");
  await expect(page.locator("[data-hit]")).toHaveCount(0);

  // clearing the selection clears the marks
  await page.getByRole("tab", { name: "이벤트" }).click();
  await expect(page.locator(".event-row.is-hit").first()).toBeVisible();
  await page.getByRole("button", { name: "선택 해제" }).click();
  await expect(page.locator("[data-hit]")).toHaveCount(0);
  await expect(page.locator(".rec-note")).toHaveCount(0);
});

test("the event log stays with the newest record until the reader leaves it", async ({ page, request }) => {
  await openFinishedDemo(page, request);
  await page.getByRole("tab", { name: "이벤트" }).click();
  const rows = page.locator(".event-row");
  const jump = page.locator(".tail-jump button");
  await expect(rows.first()).toContainText("run.start");

  // in a replay the list ends at the cursor and follows it
  await page.getByText("재생", { exact: true }).click();
  const slider = page.getByLabel("이벤트 위치");
  for (const cursor of ["120", "200"]) {
    await slider.fill(cursor);
    await expect(rows.last()).toHaveAttribute("data-seq", cursor);
    await expect.poll(() => inList(page, "last-row")).toBe("in");
    await expect(jump).toHaveCount(0);
  }

  // scrolling up stops following; what arrives meanwhile is counted, not shown
  await page.locator(".inspector-body").hover();
  await page.mouse.wheel(0, -600);
  await expect(jump).toHaveText("최신 기록으로");
  await slider.fill("260");
  await expect(rows.last()).toHaveAttribute("data-seq", "260");
  await expect(jump).toHaveText("새 기록 60건");
  expect(await inList(page, "last-row")).toBe("out");

  await jump.click();
  await expect.poll(() => inList(page, "last-row")).toBe("in");
  await expect(jump).toHaveCount(0);

  // while it follows, the log keeps its newest 300 rows and offers the rest
  await slider.fill("379");
  await expect(rows.last()).toHaveAttribute("data-seq", "379");
  await expect(rows.first()).toHaveAttribute("data-seq", "80");
  await expect(page.locator(".event-more")).toHaveText("앞선 이벤트 79건 보기");
  await expect.poll(() => inList(page, "last-row")).toBe("in");

  // a pick wins over the tail, and reaches records the cap had dropped: ingest ran at the very start
  await page.locator('.react-flow__node[data-id="ingest"]').click();
  await expect(rows.first()).toHaveAttribute("data-seq", "1");
  await expect(page.locator(".event-row.is-hit").first()).toHaveAttribute("data-seq", /^[1-9]$/);
  await expect.poll(() => inList(page, "first-hit")).toBe("in");
  // the list stays with the node while the record moves on
  await slider.fill("300");
  await expect(rows.last()).toHaveAttribute("data-seq", "300");
  await slider.fill("379");
  await expect(rows.last()).toHaveAttribute("data-seq", "379");
  await expect(jump).toHaveText("새 기록 79건");
  expect(await inList(page, "first-hit")).toBe("in");
  expect(await inList(page, "last-row")).toBe("out");
});

