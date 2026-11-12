// Fills a new candidate from OpenRouter's public model list (no API key needed): name, price, inputs, tool support.
import type { Capability, CandidateLookup } from "../src/api/contract";
import { HttpError } from "./http";

const MODELS_URL = "https://openrouter.ai/api/v1/models";
const TTL_MS = 10 * 60 * 1000;

interface OpenRouterModel {
  id: string;
  canonical_slug?: string;
  name?: string;
  context_length?: number;
  pricing?: { prompt?: string; completion?: string };
  architecture?: { input_modalities?: string[] };
  supported_parameters?: string[];
}

let cache: { at: number; models: OpenRouterModel[] } | undefined;

async function models(): Promise<OpenRouterModel[]> {
  if (cache && Date.now() - cache.at < TTL_MS) return cache.models;
  let response: Response;
  try {
    response = await fetch(MODELS_URL, { signal: AbortSignal.timeout(15_000), headers: { Accept: "application/json" } });
  } catch {
    throw new HttpError(502, "OpenRouter에 연결할 수 없습니다. 네트워크를 확인하거나 값을 직접 입력하세요.");
  }
  if (!response.ok) throw new HttpError(502, `OpenRouter 모델 목록을 가져오지 못했습니다 (${response.status}).`);
  const body = (await response.json()) as { data?: OpenRouterModel[] };
  cache = { at: Date.now(), models: body.data ?? [] };
  return cache.models;
}

const perMillion = (v: string | undefined) => Math.round(Number(v ?? 0) * 1_000_000 * 1000) / 1000;

export async function lookupOpenRouter(ref: string): Promise<CandidateLookup> {
  const slug = ref.trim();
  if (!slug.includes("/")) throw new HttpError(400, "OpenRouter slug는 '회사/모델' 형식입니다 (예: anthropic/claude-sonnet-5.5).");
  const list = await models();
  const m = list.find((x) => x.id === slug) ?? list.find((x) => x.canonical_slug === slug);
  if (!m) throw new HttpError(404, `OpenRouter 공개 목록에 '${slug}'이(가) 없습니다.`);
  const params = m.supported_parameters ?? [];
  const inputs = m.architecture?.input_modalities ?? [];
  const caps: Capability[] = [];
  if (params.includes("tools")) caps.push("tools");
  if (params.includes("structured_outputs")) caps.push("json");
  if (inputs.includes("image")) caps.push("image");
  if (inputs.includes("video")) caps.push("video");
  const price = { in: perMillion(m.pricing?.prompt), out: perMillion(m.pricing?.completion) };
  return {
    ref: m.id,
    label: (m.name ?? m.id).replace(/^[^:]+:\s*/, ""),
    price,
    caps,
    // a starting guess from price; the person adding the model can change it
    tier: price.in >= 1.5 ? "frontier" : "efficient",
    contextLength: m.context_length,
  };
}
