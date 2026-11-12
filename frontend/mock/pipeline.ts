// Pipeline catalog and profiles, read from configs/pipeline: catalog.yaml (every swappable part and its
// candidates), profiles/*.yaml (one candidate per part) and service.yaml (which profile the service uses).
// Files are re-read when their mtime changes, so hand edits show up without restarting the dev server.
// Writes from the Lab (new candidates, profiles, promotions) go to the same files.
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import YAML, { type Document, isMap, isSeq, type YAMLMap } from "yaml";
import type {
  AddCandidateRequest,
  AdapterKind,
  Capability,
  Catalog,
  CatalogAdapter,
  CatalogCandidate,
  CatalogParam,
  CatalogSlot,
  CatalogStage,
  ModelTier,
  ProfileIssue,
  ProfileSpec,
  RunComponent,
  ServiceTarget,
} from "../src/api/contract";
import { HttpError } from "./http";

/** one chosen candidate with everything the mock engine needs to act on it */
export interface ResolvedPart extends RunComponent {
  adapter: string;
  ref?: string;
  license?: string;
  price?: { in: number; out: number };
  caps: Capability[];
  lawirko?: number;
  planned: boolean;
  /** model vendor for API models (anthropic, openai, google …) */
  vendor?: string;
}

export interface ResolvedProfile {
  id: string;
  name: string;
  configHash: string;
  components: RunComponent[];
  part: Record<string, ResolvedPart>;
  params: Record<string, number>;
}

interface StoredProfile {
  spec: ProfileSpec;
  path: string;
  source: "file" | "lab";
  updatedAt: string;
}

/** parts that receive video frames or crops */
export const FRAME_SLOTS = ["vlm.scene", "vlm.person", "vlm.inspect", "perception.ocr"];
const ID_PATTERN = /^[a-z0-9][a-z0-9._-]{1,47}$/;
/** matches the hand-written files, so a Lab edit only adds lines instead of reformatting the file */
const YAML_OUT = { lineWidth: 0, flowCollectionPadding: false } as const;
const CAPS: Capability[] = ["tools", "json", "image", "video"];
const CAP_LABEL: Record<Capability, string> = { tools: "도구 호출", json: "엄격한 JSON 출력", image: "이미지 입력", video: "영상 입력" };

type Raw = Record<string, unknown>;
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : typeof v === "number" ? String(v) : fallback);
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map((x) => str(x)).filter(Boolean) : []);

export class PipelineStore {
  readonly catalogFile: string;
  readonly profilesDir: string;
  readonly serviceFile: string;
  private readonly cache = new Map<string, { mtimeMs: number; value: unknown }>();

  constructor(
    readonly dir: string,
    /** shown in the UI instead of absolute paths */
    private readonly displayDir: string,
  ) {
    this.catalogFile = path.join(dir, "catalog.yaml");
    this.profilesDir = path.join(dir, "profiles");
    this.serviceFile = path.join(dir, "service.yaml");
    fs.mkdirSync(this.profilesDir, { recursive: true });
  }

  display(file: string): string {
    return path.join(this.displayDir, path.relative(this.dir, file)).split(path.sep).join("/");
  }

  private cached<T>(file: string, parse: (text: string, stat: fs.Stats) => T): T {
    const stat = fs.statSync(file);
    const hit = this.cache.get(file);
    if (hit && hit.mtimeMs === stat.mtimeMs) return hit.value as T;
    const value = parse(fs.readFileSync(file, "utf8"), stat);
    this.cache.set(file, { mtimeMs: stat.mtimeMs, value });
    return value;
  }

  private write(file: string, text: string): void {
    const tmp = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, text, "utf8");
    fs.renameSync(tmp, file);
    this.cache.delete(file);
  }

  /* ---------- catalog ---------- */

  catalog(): Catalog {
    return this.cached(this.catalogFile, (text, stat) => {
      let raw: Raw;
      try {
        raw = YAML.parse(text) as Raw;
      } catch (error) {
        throw new HttpError(500, `카탈로그 파일을 읽을 수 없습니다 (${this.display(this.catalogFile)}): ${(error as Error).message}`);
      }
      const adapters: CatalogAdapter[] = Object.entries((raw.adapters ?? {}) as Record<string, Raw>).map(([id, a]) => ({
        id,
        label: str(a.label, id),
        kind: (str(a.kind, "local") as AdapterKind) ?? "local",
        ref: str(a.ref),
        example: a.example === undefined ? undefined : str(a.example),
        lookup: a.lookup === "openrouter" ? "openrouter" : undefined,
        addable: a.addable !== false,
      }));
      const stages: CatalogStage[] = ((raw.stages ?? []) as Raw[]).map((st) => ({
        id: str(st.id),
        label: str(st.label, str(st.id)),
        description: str(st.description),
        slots: ((st.slots ?? []) as Raw[]).map((sl) => normalizeSlot(sl, str(st.id))),
      }));
      const params: CatalogParam[] = ((raw.params ?? []) as Raw[]).map((p) => ({
        id: str(p.id),
        stage: str(p.stage),
        label: str(p.label, str(p.id)),
        unit: str(p.unit),
        default: num(p.default) ?? 0,
        min: num(p.min) ?? 0,
        max: num(p.max) ?? 100,
        step: num(p.step) ?? 1,
      }));
      return { path: this.display(this.catalogFile), modifiedAt: stat.mtime.toISOString(), adapters, stages, params };
    });
  }

  slots(): CatalogSlot[] {
    return this.catalog().stages.flatMap((s) => s.slots);
  }

  slot(id: string): CatalogSlot {
    const slot = this.slots().find((s) => s.id === id);
    if (!slot) throw new HttpError(404, `카탈로그에 '${id}' 부품이 없습니다.`);
    return slot;
  }

  addCandidate(req: AddCandidateRequest): CatalogCandidate {
    const catalog = this.catalog();
    const slot = this.slot(req.slot);
    const adapter = catalog.adapters.find((a) => a.id === req.adapter);
    if (!adapter || !slot.adapters.includes(adapter.id)) throw new HttpError(400, `'${slot.label}'에는 '${req.adapter}' 계열을 쓸 수 없습니다.`);
    if (!adapter.addable) throw new HttpError(400, `'${adapter.label}' 후보는 코드에서만 추가할 수 있습니다.`);
    const ref = str(req.ref).trim();
    const label = str(req.label).trim() || ref;
    if (!ref) throw new HttpError(400, "모델 ID(ref)를 입력하세요.");
    if (slot.candidates.some((c) => c.ref === ref)) throw new HttpError(409, `'${ref}'는 이미 '${slot.label}' 후보에 있습니다.`);
    const id = slugify(ref);
    if (!ID_PATTERN.test(id)) throw new HttpError(400, "모델 ID로 후보 키를 만들 수 없습니다. 영문·숫자로 된 ID를 쓰세요.");
    if (slot.candidates.some((c) => c.id === id)) throw new HttpError(409, `'${id}' 키를 가진 후보가 이미 있습니다.`);
    const caps = (req.caps ?? []).filter((c): c is Capability => CAPS.includes(c));
    const missing = slot.requires.filter((c) => !caps.includes(c));
    if (missing.length) throw new HttpError(400, `'${slot.label}'에는 ${missing.map((c) => CAP_LABEL[c]).join(", ")} 기능이 필요합니다.`);
    const price = req.price && Number.isFinite(req.price.in) && Number.isFinite(req.price.out) ? { in: Math.max(0, req.price.in), out: Math.max(0, req.price.out) } : undefined;
    if (adapter.kind === "api" && !price) throw new HttpError(400, "외부 API 모델은 단가가 필요합니다 (USD / 1M 토큰).");

    const entry: Raw = { id, label, adapter: adapter.id, ref };
    if (price) entry.price = price;
    if (caps.length) entry.caps = caps;
    if (req.tier === "frontier" || req.tier === "efficient") entry.tier = req.tier;
    if (req.license?.trim()) entry.license = req.license.trim();
    if (req.note?.trim()) entry.note = req.note.trim();
    entry.source = "lab";
    entry.added = new Date().toISOString().slice(0, 10);

    const doc = YAML.parseDocument(fs.readFileSync(this.catalogFile, "utf8"));
    const at = locateSlot(doc, slot.id);
    const node = doc.createNode(entry) as YAMLMap;
    for (const key of ["price", "caps"]) {
      const child = node.get(key, true) as { flow?: boolean } | undefined;
      if (child) child.flow = true;
    }
    doc.addIn([...at, "candidates"], node);
    this.write(this.catalogFile, doc.toString(YAML_OUT));
    return this.slot(slot.id).candidates.find((c) => c.id === id) as CatalogCandidate;
  }

  removeCandidate(slotId: string, candidateId: string): void {
    const slot = this.slot(slotId);
    const candidate = slot.candidates.find((c) => c.id === candidateId);
    if (!candidate) throw new HttpError(404, "후보를 찾을 수 없습니다.");
    if (candidate.source !== "lab") throw new HttpError(409, "파일에 직접 적은 후보는 catalog.yaml에서 지우세요.");
    if (slot.default === candidateId) throw new HttpError(409, "부품의 기본 후보는 지울 수 없습니다.");
    const users = this.listProfiles().filter((p) => p.spec.selections[slotId] === candidateId);
    if (users.length) throw new HttpError(409, `프로필 ${users.map((p) => p.spec.id).join(", ")}이(가) 이 후보를 쓰고 있습니다.`);
    const doc = YAML.parseDocument(fs.readFileSync(this.catalogFile, "utf8"));
    const at = locateSlot(doc, slot.id);
    const seq = doc.getIn([...at, "candidates"]);
    if (!isSeq(seq)) throw new HttpError(500, "카탈로그 구조를 읽을 수 없습니다.");
    const index = seq.items.findIndex((item) => isMap(item) && item.get("id") === candidateId);
    doc.deleteIn([...at, "candidates", index]);
    this.write(this.catalogFile, doc.toString(YAML_OUT));
  }

  /* ---------- profiles ---------- */

  listProfiles(): StoredProfile[] {
    const files = fs.readdirSync(this.profilesDir).filter((f) => f.endsWith(".yaml")).sort();
    const out: StoredProfile[] = [];
    for (const file of files) {
      const full = path.join(this.profilesDir, file);
      try {
        out.push(this.cached(full, (text, stat) => parseProfile(text, file.replace(/\.yaml$/, ""), this.display(full), stat)));
      } catch {
        // an unreadable profile file is skipped rather than breaking the whole Lab
      }
    }
    return out;
  }

  getProfile(id: string): StoredProfile {
    const found = this.listProfiles().find((p) => p.spec.id === id);
    if (!found) throw new HttpError(404, `프로필 '${id}'을(를) 찾을 수 없습니다.`);
    return found;
  }

  saveProfile(input: ProfileSpec, mode: "create" | "update"): StoredProfile {
    const id = str(input.id).trim();
    if (!ID_PATTERN.test(id)) throw new HttpError(400, "프로필 ID는 영문 소문자·숫자·'-'로 2~48자여야 합니다.");
    const file = path.join(this.profilesDir, `${id}.yaml`);
    const exists = fs.existsSync(file);
    if (mode === "create" && exists) throw new HttpError(409, `'${id}' 프로필이 이미 있습니다.`);
    if (mode === "update" && !exists) throw new HttpError(404, `프로필 '${id}'을(를) 찾을 수 없습니다.`);
    const previous = exists ? this.getProfile(id) : undefined;
    const spec = this.normalizeSpec({ ...input, id });
    const errors = this.issues(spec).filter((i) => i.level === "error");
    if (errors.length) throw new HttpError(400, errors.map((e) => e.text).join(" / "));
    this.write(file, this.renderProfile(spec, previous?.source ?? "lab"));
    return this.getProfile(id);
  }

  deleteProfile(id: string): void {
    const profile = this.getProfile(id);
    const service = this.service();
    const used = (Object.keys(service) as ServiceTarget[]).filter((k) => service[k] === id);
    if (used.length) throw new HttpError(409, "서비스가 쓰고 있는 프로필은 지울 수 없습니다. 다른 프로필을 먼저 승격하세요.");
    fs.unlinkSync(path.join(this.profilesDir, `${profile.spec.id}.yaml`));
  }

  private normalizeSpec(input: ProfileSpec): ProfileSpec {
    const catalog = this.catalog();
    const selections: Record<string, string> = {};
    for (const slot of catalog.stages.flatMap((s) => s.slots)) selections[slot.id] = str(input.selections?.[slot.id], slot.default);
    const params: Record<string, number> = {};
    for (const p of catalog.params) {
      const v = Number(input.params?.[p.id]);
      params[p.id] = Number.isFinite(v) ? v : p.default;
    }
    return {
      id: input.id,
      name: str(input.name).trim() || input.id,
      description: str(input.description).trim(),
      base: input.base ? str(input.base) : undefined,
      selections,
      params,
    };
  }

  private renderProfile(spec: ProfileSpec, source: "file" | "lab"): string {
    const catalog = this.catalog();
    const scalar = (v: unknown) => YAML.stringify(v).trim();
    const lines = [
      "# 프로필: catalog.yaml의 부품마다 후보 하나를 고른 조합. 실험실 > 프로필에서 만들고 고칠 수 있다.",
      `id: ${spec.id}`,
      `name: ${scalar(spec.name)}`,
      `description: ${scalar(spec.description || "")}`,
      ...(spec.base ? [`base: ${scalar(spec.base)}`] : []),
      `source: ${source}`,
      "selections:",
    ];
    for (const stage of catalog.stages) {
      lines.push(`  # ${stage.label}`);
      for (const slot of stage.slots) lines.push(`  ${slot.id}: ${scalar(spec.selections[slot.id])}`);
    }
    lines.push("params:");
    for (const p of catalog.params) lines.push(`  ${p.id}: ${scalar(spec.params[p.id])}`);
    return `${lines.join("\n")}\n`;
  }

  /* ---------- service pointer ---------- */

  service(): Record<ServiceTarget, string> {
    return this.cached(this.serviceFile, (text) => {
      const raw = (YAML.parse(text) ?? {}) as Raw;
      return { default: str(raw.default, "baseline-v2"), local_only: str(raw.local_only, str(raw.default, "baseline-v2")) };
    });
  }

  promote(id: string, target: ServiceTarget): void {
    this.getProfile(id);
    const doc = YAML.parseDocument(fs.readFileSync(this.serviceFile, "utf8")) as Document;
    doc.set(target, id);
    this.write(this.serviceFile, doc.toString(YAML_OUT));
  }

  /* ---------- resolution and checks ---------- */

  resolve(spec: ProfileSpec, overrides: Record<string, string> = {}): ResolvedProfile {
    const catalog = this.catalog();
    const part: Record<string, ResolvedPart> = {};
    const components: RunComponent[] = [];
    const adapters = new Map(catalog.adapters.map((a) => [a.id, a]));
    for (const stage of catalog.stages) {
      for (const slot of stage.slots) {
        const wanted = overrides[slot.id] ?? spec.selections[slot.id] ?? slot.default;
        const c = slot.candidates.find((x) => x.id === wanted) ?? slot.candidates.find((x) => x.id === slot.default) ?? slot.candidates[0];
        const kind: AdapterKind = adapters.get(c.adapter)?.kind ?? "local";
        const component: RunComponent = {
          slot: slot.id,
          stage: stage.id,
          slotLabel: slot.label,
          candidate: c.id,
          label: c.label,
          short: c.short ?? c.label,
          kind,
          tier: c.tier,
          usedBy: slot.usedBy,
        };
        components.push(component);
        part[slot.id] = {
          ...component,
          adapter: c.adapter,
          ref: c.ref,
          license: c.license,
          price: c.price,
          caps: c.caps ?? [],
          lawirko: c.lawirko,
          planned: c.status === "planned",
          vendor: vendorOf(c),
        };
      }
    }
    const params: Record<string, number> = {};
    for (const p of catalog.params) {
      const v = Number(spec.params?.[p.id]);
      params[p.id] = Number.isFinite(v) ? v : p.default;
    }
    const hashInput = JSON.stringify({
      s: Object.fromEntries(Object.entries(part).map(([k, v]) => [k, v.candidate]).sort()),
      p: Object.fromEntries(Object.entries(params).sort()),
    });
    return {
      id: spec.id,
      name: spec.name,
      configHash: createHash("sha256").update(hashInput).digest("hex").slice(0, 8),
      components,
      part,
      params,
    };
  }

  issues(spec: ProfileSpec): ProfileIssue[] {
    const catalog = this.catalog();
    const issues: ProfileIssue[] = [];
    const resolved = this.resolve(spec);
    for (const slot of catalog.stages.flatMap((s) => s.slots)) {
      const chosen = spec.selections[slot.id];
      if (chosen === undefined) {
        issues.push({ level: "info", slot: slot.id, text: `카탈로그에 새로 생긴 부품 '${slot.label}' — 기본 후보를 씁니다.` });
        continue;
      }
      const c = slot.candidates.find((x) => x.id === chosen);
      if (!c) {
        issues.push({ level: "error", slot: slot.id, text: `'${slot.label}'의 후보 '${chosen}'이(가) 카탈로그에 없습니다.` });
        continue;
      }
      if (c.status === "planned") issues.push({ level: "error", slot: slot.id, text: `'${slot.label}'의 '${c.label}'은(는) 아직 구현되지 않았습니다.` });
      const missing = slot.requires.filter((cap) => !(c.caps ?? []).includes(cap));
      if (missing.length && c.adapter !== "none") {
        issues.push({ level: "error", slot: slot.id, text: `'${slot.label}'에는 ${missing.map((m) => CAP_LABEL[m]).join(", ")} 기능이 필요한데 '${c.label}'은(는) 지원하지 않습니다.` });
      }
      if (c.license && /AGPL|비상용|non-commercial/i.test(c.license)) {
        issues.push({ level: "warn", slot: slot.id, text: `'${c.label}' 라이선스(${c.license})는 서비스 전환 전에 검토해야 합니다.` });
      }
    }
    const p = resolved.part;
    const mode = p["vlm.input_mode"]?.candidate;
    const cloudFrames = FRAME_SLOTS.filter((s) => p[s]?.kind === "api");
    if (mode === "v3" && cloudFrames.length) {
      issues.push({ level: "error", slot: "vlm.input_mode", text: `V-3(로컬만)인데 ${cloudFrames.map((s) => p[s].slotLabel).join(", ")}이(가) 외부 API입니다.` });
    }
    if (mode === "v1" && cloudFrames.length) {
      issues.push({ level: "warn", slot: "vlm.input_mode", text: "V-1은 원본 프레임을 외부로 보냅니다. 동의받은 영상이나 합성 영상에만 쓰세요." });
    }
    const auditor = p["llm.auditor"];
    if (auditor?.adapter === "none") {
      issues.push({ level: "warn", slot: "llm.auditor", text: "감사를 생략하면 문신 같은 잔여 식별자를 놓칠 수 있습니다." });
    } else if (auditor?.vendor) {
      const same = (["llm.analyst", "llm.planner"] as const).filter((s) => p[s]?.vendor === auditor.vendor);
      if (same.length) issues.push({ level: "warn", slot: "llm.auditor", text: `감사자가 ${same.map((s) => p[s].slotLabel).join(" · ")}와 같은 회사 모델이라 독립성이 약해집니다.` });
    }
    if (p["agent.questions"]?.candidate === "disabled") issues.push({ level: "warn", slot: "agent.questions", text: "동적 질문을 끄면 모르는 사실을 보수적으로 가정해 과잉 처리가 늘 수 있습니다." });
    if (p["rag.retrieval"]?.candidate === "dense") issues.push({ level: "info", slot: "rag.bm25_tokenizer", text: "dense만 쓰므로 BM25 토크나이저는 쓰이지 않습니다." });
    if (p["rag.seed"]?.candidate === "disabled") issues.push({ level: "info", slot: "rag.seed", text: "시드 묶음 없이 핵심 조문부터 검색하므로 검색 호출이 늘어납니다." });
    for (const param of catalog.params) {
      const v = spec.params?.[param.id];
      if (v === undefined) continue;
      if (!Number.isFinite(Number(v)) || Number(v) < param.min || Number(v) > param.max) {
        issues.push({ level: "error", text: `${param.label}은(는) ${param.min}~${param.max} 사이여야 합니다.` });
      }
    }
    return issues;
  }
}

function normalizeSlot(sl: Raw, stage: string): CatalogSlot {
  return {
    id: str(sl.id),
    stage,
    label: str(sl.label, str(sl.id)),
    usedBy: list(sl.used_by),
    adapters: list(sl.adapters),
    requires: list(sl.requires).filter((c): c is Capability => CAPS.includes(c as Capability)),
    default: str(sl.default),
    candidates: ((sl.candidates ?? []) as Raw[]).map((c) => {
      const price = c.price as Raw | undefined;
      return {
        id: str(c.id),
        label: str(c.label, str(c.id)),
        short: c.short === undefined ? undefined : str(c.short),
        adapter: str(c.adapter),
        ref: c.ref === undefined ? undefined : str(c.ref),
        license: c.license === undefined ? undefined : str(c.license),
        price: price && num(price.in) !== undefined ? { in: num(price.in) ?? 0, out: num(price.out) ?? 0 } : undefined,
        caps: list(c.caps).filter((x): x is Capability => CAPS.includes(x as Capability)),
        tier: c.tier === "frontier" || c.tier === "efficient" ? (c.tier as ModelTier) : undefined,
        lawirko: num(c.lawirko),
        status: c.status === "planned" ? "planned" : undefined,
        note: c.note === undefined ? undefined : str(c.note),
        source: c.source === "lab" ? "lab" : "file",
        addedAt: c.added === undefined ? undefined : str(c.added),
      } satisfies CatalogCandidate;
    }),
  };
}

function parseProfile(text: string, fallbackId: string, display: string, stat: fs.Stats): StoredProfile {
  const raw = (YAML.parse(text) ?? {}) as Raw;
  const selections = Object.fromEntries(Object.entries((raw.selections ?? {}) as Raw).map(([k, v]) => [k, str(v)]));
  const params = Object.fromEntries(
    Object.entries((raw.params ?? {}) as Raw)
      .map(([k, v]) => [k, Number(v)] as const)
      .filter(([, v]) => Number.isFinite(v)),
  );
  return {
    spec: {
      id: str(raw.id, fallbackId),
      name: str(raw.name, str(raw.id, fallbackId)),
      description: str(raw.description),
      base: raw.base ? str(raw.base) : undefined,
      selections,
      params,
    },
    path: display,
    source: raw.source === "lab" ? "lab" : "file",
    updatedAt: stat.mtime.toISOString(),
  };
}

function locateSlot(doc: Document, slotId: string): (string | number)[] {
  const stages = doc.get("stages");
  if (isSeq(stages)) {
    for (let i = 0; i < stages.items.length; i += 1) {
      const slots = doc.getIn(["stages", i, "slots"]);
      if (!isSeq(slots)) continue;
      const j = slots.items.findIndex((s) => isMap(s) && s.get("id") === slotId);
      if (j >= 0) return ["stages", i, "slots", j];
    }
  }
  throw new HttpError(404, `카탈로그에 '${slotId}' 부품이 없습니다.`);
}

function slugify(ref: string): string {
  const last = ref.trim().split("/").filter(Boolean).pop() ?? ref;
  return last
    .toLowerCase()
    .replace(/\.(onnx|pt|pth|safetensors)$/, "")
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

function vendorOf(c: CatalogCandidate): string | undefined {
  if (c.adapter === "openrouter" && c.ref?.includes("/")) return c.ref.split("/")[0];
  if (c.adapter === "anthropic") return "anthropic";
  if (c.adapter === "gemini-api") return "google";
  if (c.adapter === "openai-embeddings") return "openai";
  return undefined;
}
