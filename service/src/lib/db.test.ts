import assert from "node:assert/strict";
import test from "node:test";

import { normalizeDatabaseUrl } from "@/lib/db";

test("normalizeDatabaseUrl converts sslmode=require to no-verify", () => {
  const normalized = normalizeDatabaseUrl("postgres://user:pass@example.com/db?sslmode=require");

  assert.match(normalized, /sslmode=no-verify/);
});

test("normalizeDatabaseUrl leaves custom ssl material untouched", () => {
  const input = "postgres://user:pass@example.com/db?sslmode=require&sslrootcert=/tmp/root.crt";

  assert.equal(normalizeDatabaseUrl(input), input);
});

test("normalizeDatabaseUrl leaves plain urls untouched", () => {
  const input = "postgres://user:pass@example.com/db";

  assert.equal(normalizeDatabaseUrl(input), input);
});
