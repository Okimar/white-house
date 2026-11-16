// Estimated tokens of a text: the same rules as tavern/core/tokens.py (by script: a Latin word ≈ one token per
// 5 letters, Cyrillic per 3.5, other letters per 2.5, each CJK character one, digits in threes, each line break and
// run of punctuation one). Remembered per text, so a long prompt view is counted once. Keep the two in step.
const RUNS = /([぀-ヿ㐀-䶿一-鿿가-힯豈-﫿])|([Ѐ-ӿ]+)|([A-Za-zÀ-ɏ]+)|(\p{Nd}+)|(\n+)|([ \t\r\f\v]+)|((?:(?![\p{Nd}_])[\p{L}\p{M}])+)|([^\p{L}\p{M}\p{N}_\s]+)/gu;
const cache = new Map();

export function estimateTokens(text) {
  if (!text) return 0;
  const known = cache.get(text);
  if (known !== undefined) return known;
  let total = 0;
  for (const m of text.matchAll(RUNS)) {
    const size = m[0].length;
    if (m[1]) total += 1;
    else if (m[2]) total += Math.ceil(size / 3.5);
    else if (m[3]) total += Math.ceil(size / 5);
    else if (m[4]) total += Math.ceil(size / 3);
    else if (m[5]) total += 1;
    else if (m[6]) total += size > 1 ? Math.floor(size / 4) : 0;
    else if (m[7]) total += Math.ceil(size / 2.5);
    else total += Math.max(1, Math.ceil(size / 2));
  }
  const result = Math.max(1, Math.floor(total));
  if (cache.size > 4000) cache.clear();
  cache.set(text, result);
  return result;
}
