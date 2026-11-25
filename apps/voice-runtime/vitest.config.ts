import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    testTimeout: 5_000,
    environment: "node",
    env: { TZ: "Asia/Shanghai" },
    include: ["src/**/*.test.ts"],
  },
});
