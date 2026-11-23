// Talos host for the standalone OpenClaw scheduling algorithms.
import { readFileSync } from "node:fs";
import { computeNextRunAtMs } from "./schedule.ts";
const { schedule, now } = JSON.parse(readFileSync(0, "utf8"));
console.log(JSON.stringify(computeNextRunAtMs(schedule, now) ?? null));
