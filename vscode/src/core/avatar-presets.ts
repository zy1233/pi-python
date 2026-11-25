export const AVATAR_PRESET_IDS = [
  "claude",
  "gemini",
  "deepseek",
  "openai",
  "grok",
  "kimi",
  "qwen",
] as const;

export type AvatarPresetId = (typeof AVATAR_PRESET_IDS)[number];

export const DEFAULT_AVATAR_PRESET: AvatarPresetId = "claude";

export interface AvatarPresetInfo {
  id: AvatarPresetId;
  label: string;
  description: string;
  file: string;
}

export const AVATAR_PRESETS: readonly AvatarPresetInfo[] = [
  { id: "claude", label: "Claude (Claude娘)", description: "Copper-orange hair, gold-rimmed glasses & orange daisy", file: "claude.png" },
  { id: "gemini", label: "Gemini (星辉双子娘)", description: "Starlight sapphire-lavender hair & four-pointed star hairpin", file: "gemini.png" },
  { id: "deepseek", label: "DeepSeek (鲸鱼娘)", description: "Blue-haired chibi maid with whale ear fins", file: "deepseek.png" },
  { id: "openai", label: "OpenAI (小龙娘)", description: "White-haired dragon girl with horns & knot ornaments", file: "openai.png" },
  { id: "grok", label: "Grok (Grok娘)", description: "Blonde twin-tails, dark gothic dress & golden X emblem", file: "grok.png" },
  { id: "kimi", label: "Kimi (月下星辰娘)", description: "Midnight-indigo hair & golden crescent-moon hairpin", file: "kimi.png" },
  { id: "qwen", label: "Qwen (通义千问娘)", description: "Periwinkle-haired beret girl in indigo-white hanfu", file: "qwen.png" },
];

const PRESET_SET = new Set<string>(AVATAR_PRESET_IDS);

export function isAvatarPresetId(value: unknown): value is AvatarPresetId {
  return typeof value === "string" && PRESET_SET.has(value);
}

export function normalizeAvatarPreset(value: unknown): AvatarPresetId {
  return isAvatarPresetId(value) ? value : DEFAULT_AVATAR_PRESET;
}

const MODEL_PRESET_PATTERNS: readonly [AvatarPresetId, RegExp][] = [
  ["deepseek", /(?:^|[^a-z0-9])deepseek(?=$|\d|[^a-z])/],
  ["gemini", /(?:^|[^a-z0-9])(?:gemini|gemma)(?=$|\d|[^a-z])/],
  ["claude", /(?:^|[^a-z0-9])claude(?=$|\d|[^a-z])|\banthropic\//],
  ["grok", /(?:^|[^a-z0-9])grok(?=$|\d|[^a-z])|\bxai\/|\bx-ai\//],
  ["kimi", /(?:^|[^a-z0-9])(?:kimi|moonshot)(?=$|\d|[^a-z])/],
  ["qwen", /(?:^|[^a-z0-9])(?:qwen|qwq)(?=$|\d|[^a-z])|\btongyi\b/],
  ["openai", /(?:^|[^a-z0-9])(?:openai|gpt)(?=$|\d|[^a-z])|\bchatgpt\b|(?:^|[^a-z0-9])o[134](?=$|[^a-z0-9])/],
];

/**
 * Matches a model id/name (e.g. `auto/deepseek-v4.1-flash`, `google/gemini-2.5-pro`, `gpt-4o`)
 * to a built-in mascot preset, or returns `undefined` if unrecognized.
 */
export function matchModelAvatarPreset(...candidates: (string | undefined)[]): AvatarPresetId | undefined {
  for (const raw of candidates) {
    if (!raw) continue;
    const text = raw.trim().toLowerCase();
    if (text === "") continue;
    for (const [preset, pattern] of MODEL_PRESET_PATTERNS) {
      if (pattern.test(text)) return preset;
    }
  }
  return undefined;
}
