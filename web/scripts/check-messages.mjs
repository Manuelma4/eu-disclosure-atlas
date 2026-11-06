import { readFileSync } from "node:fs";

const messages = JSON.parse(readFileSync(new URL("../messages/en.json", import.meta.url), "utf8"));

function countMessages(value, path) {
  if (typeof value === "string" && value.trim()) return 1;
  if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length) {
    return Object.entries(value).reduce((total, [key, child]) => total + countMessages(child, `${path}.${key}`), 0);
  }
  throw new Error(`${path}: expected a non-empty message or group`);
}

console.log(`messages: ${countMessages(messages, "en")} English keys`);
