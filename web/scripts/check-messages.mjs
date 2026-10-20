// Every language must have exactly the keys of messages/en.json, with the same ICU placeholders and tags.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "messages");
const load = (locale) => JSON.parse(readFileSync(join(dir, `${locale}.json`), "utf8"));
const flatten = (obj, prefix = "") =>
  Object.entries(obj).flatMap(([k, v]) =>
    typeof v === "object" ? flatten(v, `${prefix}${k}.`) : [[`${prefix}${k}`, String(v)]],
  );
// Argument names ({count}, {count, plural, ...}) and rich-text tags (<link>), not the translated words.
const tokens = (text) =>
  [...new Set([...text.matchAll(/\{\s*(\w+)\s*[,}]/g)].map((m) => `{${m[1]}}`).concat(text.match(/<\/?\w+>/g) ?? []))].sort();

const reference = new Map(flatten(load("en")));
let problems = 0;
for (const locale of ["fr", "es", "de"]) {
  const messages = new Map(flatten(load(locale)));
  for (const [key, text] of reference) {
    if (!messages.has(key)) {
      console.error(`${locale}: missing ${key}`);
      problems++;
    } else if (tokens(messages.get(key)).join() !== tokens(text).join()) {
      console.error(`${locale}: ${key} has ${tokens(messages.get(key))} instead of ${tokens(text)}`);
      problems++;
    }
  }
  for (const key of messages.keys()) {
    if (!reference.has(key)) {
      console.error(`${locale}: unknown key ${key}`);
      problems++;
    }
  }
}
if (problems) process.exit(1);
console.log(`messages: ${reference.size} keys, identical in en, fr, es and de`);
