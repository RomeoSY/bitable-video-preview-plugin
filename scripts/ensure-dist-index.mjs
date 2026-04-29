import fs from "node:fs";
import path from "node:path";

const distIndex = path.resolve(process.cwd(), "dist", "index.html");

if (!fs.existsSync(distIndex)) {
  console.error(`[feishu-check] missing required file: ${distIndex}`);
  process.exit(1);
}

const content = fs.readFileSync(distIndex, "utf-8");
if (!content.includes("<!doctype html>") && !content.includes("<!DOCTYPE html>")) {
  console.error("[feishu-check] dist/index.html is not a valid html entry.");
  process.exit(1);
}

console.log(`[feishu-check] ok: ${distIndex}`);
