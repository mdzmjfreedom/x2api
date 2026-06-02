import assert from "node:assert/strict";
import test from "node:test";

import { formatTarget, parseTarget, parseTargets } from "@/lib/targets";

test("parseTarget understands user targets", () => {
  assert.deepEqual(parseTarget("OpenAI"), {
    source: "twitter",
    kind: "user",
    value: "OpenAI",
    normalizedValue: "openai",
    tags: [],
  });
});

test("parseTarget understands keyword targets", () => {
  assert.deepEqual(parseTarget("search:AI Safety"), {
    source: "twitter",
    kind: "keyword",
    value: "AI Safety",
    normalizedValue: "ai safety",
    tags: [],
  });
});

test("parseTarget understands YouTube channel targets", () => {
  assert.deepEqual(parseTarget("youtube:UCE_M8A5yxnLfW0KghEeajjw"), {
    source: "youtube",
    kind: "channel",
    value: "UCE_M8A5yxnLfW0KghEeajjw",
    normalizedValue: "uce_m8a5yxnlfw0kgheeajjw",
    tags: [],
  });
});

test("parseTargets deduplicates normalized values", () => {
  const targets = parseTargets(["OpenAI", "openai", "search:AI", "search:ai", "youtube:UCE_M8A5yxnLfW0KghEeajjw"]);
  assert.equal(targets.length, 3);
  assert.equal(formatTarget(targets[0]), "OpenAI");
  assert.equal(formatTarget(targets[1]), "search:AI");
  assert.equal(formatTarget(targets[2]), "youtube:UCE_M8A5yxnLfW0KghEeajjw");
});

test("parseTargets accepts object targets with category and free tags", () => {
  const targets = parseTargets([
    {
      target: "search:AI coding",
      category: "tech",
      tags: ["AI", " 编程 ", "ai", "", "Claude Code"],
    },
  ]);

  assert.deepEqual(targets, [
    {
      kind: "keyword",
      source: "twitter",
      value: "AI coding",
      normalizedValue: "ai coding",
      category: "tech",
      tags: ["AI", "编程", "Claude Code"],
    },
  ]);
});

test("parseTargets accepts explicit YouTube object targets", () => {
  const targets = parseTargets([
    {
      source: "youtube",
      kind: "channel",
      target: "https://www.youtube.com/channel/UCE_M8A5yxnLfW0KghEeajjw",
      category: "tech",
      tags: ["YouTube"],
    },
  ]);

  assert.deepEqual(targets, [
    {
      source: "youtube",
      kind: "channel",
      value: "UCE_M8A5yxnLfW0KghEeajjw",
      normalizedValue: "uce_m8a5yxnlfw0kgheeajjw",
      category: "tech",
      tags: ["YouTube"],
    },
  ]);
});

test("parseTargets rejects invalid target metadata", () => {
  assert.throws(
    () =>
      parseTargets([
        {
          target: "search:AI",
          tags: ["AI"],
        },
      ]),
    /Target category is required/,
  );

  assert.throws(
    () =>
      parseTargets([
        {
          target: "search:AI",
          category: 1,
        },
      ]),
    /Target category must be a string/,
  );

  assert.throws(
    () =>
      parseTargets([
        {
          target: "search:AI",
          category: "tech",
          tags: "AI",
        },
      ]),
    /Target tags must be an array/,
  );
});
