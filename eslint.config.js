// ESLint 9 flat config.
//
// Scope: what the TypeScript compiler can't already enforce. `tsc` runs
// with strict + noUnusedLocals/Parameters (see tsconfig.json), so the
// unused-vars family is left to the compiler; eslint adds correctness
// rules and the React-hooks rules (stale-closure / deps mistakes are the
// #1 class of bug tsc cannot see).
//
// Run: `npm run lint` (CI runs it too — see .github/workflows/ci.yml).

import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist",
      "node_modules",
      "src-tauri",
      "test-results",
      "playwright-report",
      "assets",
      "public",
    ],
  },
  {
    files: ["src/**/*.{ts,tsx}", "e2e/**/*.ts", "*.config.ts"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      // browser for src/, node for e2e/ + config files; the union is
      // simpler than per-dir blocks and false negatives here are cheap.
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      // tsc's noUnusedLocals/noUnusedParameters already gate these; two
      // enforcers of the same thing disagree on edge cases (e.g. `_`
      // prefixes), so the compiler wins.
      "@typescript-eslint/no-unused-vars": "off",
      // Deliberate `any`s sit at JSON/IPC boundaries; surface them as
      // warnings rather than blocking CI.
      "@typescript-eslint/no-explicit-any": "warn",
    },
  },
);
