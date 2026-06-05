/**
 * claude-client.ts
 *
 * Thin wrapper around the Anthropic SDK. Model IDs are read EXCLUSIVELY from
 * src/config/models.json — never hardcode a model ID anywhere else in the
 * codebase. Update models.json in one place when IDs change.
 *
 * Two tiers:
 *   - classify(): Haiku-tier, fast/cheap urgency classification.
 *   - draft():    Sonnet-tier, drafting / analysis / interpretation.
 */

import Anthropic from "@anthropic-ai/sdk";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

interface ModelTier {
  model: string;
  max_tokens: number;
}
interface ModelsConfig {
  drafting: ModelTier;
  classification: ModelTier;
}

const models: ModelsConfig = JSON.parse(
  readFileSync(resolve(__dirname, "../config/models.json"), "utf-8"),
);

let _client: Anthropic | null = null;
function client(): Anthropic {
  if (_client) return _client;
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey || apiKey.startsWith("[")) {
    throw new Error(
      "ANTHROPIC_API_KEY is not set (or still a placeholder). Set it in .env.",
    );
  }
  _client = new Anthropic({ apiKey });
  return _client;
}

function firstText(message: Anthropic.Message): string {
  const block = message.content.find((b) => b.type === "text");
  return block && block.type === "text" ? block.text.trim() : "";
}

/**
 * Haiku-tier classification. Returns the raw text response (caller parses).
 * `system` is optional; `user` is the content to classify.
 */
export async function classify(user: string, system?: string): Promise<string> {
  const resp = await client().messages.create({
    model: models.classification.model,
    max_tokens: models.classification.max_tokens,
    system,
    messages: [{ role: "user", content: user }],
  });
  return firstText(resp);
}

/**
 * Sonnet-tier drafting / analysis. Returns the raw text response.
 */
export async function draft(user: string, system?: string): Promise<string> {
  const resp = await client().messages.create({
    model: models.drafting.model,
    max_tokens: models.drafting.max_tokens,
    system,
    messages: [{ role: "user", content: user }],
  });
  return firstText(resp);
}

/**
 * Sonnet-tier drafting that constrains output to a JSON schema via
 * structured outputs, then parses it. Throws if Claude refuses or returns
 * invalid JSON.
 */
export async function draftJson<T = unknown>(
  user: string,
  schema: Record<string, unknown>,
  system?: string,
): Promise<T> {
  const resp = await client().messages.create({
    model: models.drafting.model,
    max_tokens: models.drafting.max_tokens,
    system,
    messages: [{ role: "user", content: user }],
    // Structured outputs: constrains the response to valid JSON for the schema.
    output_config: { format: { type: "json_schema", schema } },
  } as Anthropic.MessageCreateParamsNonStreaming);

  if (resp.stop_reason === "refusal") {
    throw new Error("Claude refused the request (stop_reason=refusal).");
  }
  const text = firstText(resp);
  return JSON.parse(text) as T;
}

export const modelIds = {
  drafting: models.drafting.model,
  classification: models.classification.model,
};
