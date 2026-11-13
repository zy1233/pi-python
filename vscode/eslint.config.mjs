import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/**", "bin/**", "node_modules/**", ".vscode-test/**", ".vsix-cache/**"] },
  ...tseslint.configs.recommended,
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "@typescript-eslint/no-floating-promises": "off",
      eqeqeq: ["error", "always"],
    },
  },
  {
    // The core runs in unit tests without VS Code (docs/specs/2026-11-04-vscode-extension-design.md, section 2).
    files: ["src/core/**/*.ts", "test/**/*.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        { paths: [{ name: "vscode", message: "src/core and the tests must not import vscode." }] },
      ],
    },
  },
);
