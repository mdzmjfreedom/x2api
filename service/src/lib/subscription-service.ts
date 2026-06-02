import { getSql } from "@/lib/db";
import { asRows } from "@/lib/sql-result";
import { formatTarget, parseTargets, type ParsedTarget } from "@/lib/targets";
import { listVideoCategories } from "@/lib/video-feed-service";

type DbSubscriptionRow = {
  subscriptionId: string;
  targetId: string;
  source: "twitter" | "youtube";
  kind: "user" | "keyword" | "channel";
  value: string;
  category: string | null;
  tags: string[] | null;
  createdAt: string;
};

function normalizeKey(value: string) {
  return value.trim().toLowerCase();
}

async function normalizeTargetCategory(category: string | null | undefined) {
  if (!category) {
    throw new Error("Target category is required.");
  }

  const key = normalizeKey(category);
  const categories = await listVideoCategories();
  const match = categories.find((item) => normalizeKey(item.slug) === key || normalizeKey(item.name) === key);
  if (!match) {
    throw new Error("Invalid target category.");
  }

  return match.slug;
}

async function ensureTargets(targets: ParsedTarget[]) {
  if (targets.length === 0) {
    return [];
  }

  const sql = getSql();

  for (const target of targets) {
    await sql`
      INSERT INTO targets (source, kind, value, normalized_value)
      VALUES (${target.source}, ${target.kind}, ${target.value}, ${target.normalizedValue})
      ON CONFLICT (source, kind, normalized_value)
      DO UPDATE SET value = EXCLUDED.value
    `;
  }

  const ensuredTargets: { id: string; source: "twitter" | "youtube"; kind: "user" | "keyword" | "channel"; value: string; normalizedValue: string }[] = [];
  for (const target of targets) {
    const rows = asRows<{ id: string; source: "twitter" | "youtube"; kind: "user" | "keyword" | "channel"; value: string; normalizedValue: string }>(await sql`
      SELECT
        id,
        source,
        kind,
        value,
        normalized_value AS "normalizedValue"
      FROM targets
      WHERE source = ${target.source}
        AND kind = ${target.kind}
        AND normalized_value = ${target.normalizedValue}
      LIMIT 1
    `);
    if (rows[0]) {
      ensuredTargets.push(rows[0]);
    }
  }

  return ensuredTargets;
}

async function upsertTargetProfiles(targets: ParsedTarget[]) {
  const configurableTargets = targets.filter((target) => target.category);
  if (configurableTargets.length === 0) {
    return;
  }

  const sql = getSql();
  for (const target of configurableTargets) {
    const category = await normalizeTargetCategory(target.category);
    await sql`
      INSERT INTO target_profiles (target_id, scope, tags, category, weight, is_public_pool)
      SELECT id, 'user', ${JSON.stringify(target.tags)}::jsonb, ${category}, 0, FALSE
      FROM targets
      WHERE source = ${target.source}
        AND kind = ${target.kind}
        AND normalized_value = ${target.normalizedValue}
      ON CONFLICT (target_id) DO UPDATE SET
        scope = CASE WHEN target_profiles.scope = 'system' THEN target_profiles.scope ELSE 'user' END,
        tags = EXCLUDED.tags,
        category = EXCLUDED.category,
        updated_at = NOW()
    `;
  }
}

export async function listSubscriptions(clientId: string) {
  const sql = getSql();
  const rows = asRows<DbSubscriptionRow>(await sql`
    SELECT
      s.id AS "subscriptionId",
      t.id AS "targetId",
      t.source,
      t.kind,
      t.value,
      tp.category,
      COALESCE(tp.tags, '[]'::jsonb) AS tags,
      s.created_at AS "createdAt"
    FROM subscriptions s
    INNER JOIN targets t ON t.id = s.target_id
    LEFT JOIN target_profiles tp ON tp.target_id = t.id
    WHERE s.client_id = ${clientId}
    ORDER BY t.source, t.kind, LOWER(t.value)
  `);

  return rows.map((row) => ({
    id: row.subscriptionId,
    targetId: row.targetId,
    target: formatTarget({ source: row.source, kind: row.kind, value: row.value }),
    source: row.source,
    kind: row.kind,
    value: row.value,
    category: row.category,
    tags: row.tags ?? [],
    createdAt: row.createdAt,
  }));
}

export async function replaceSubscriptions(clientId: string, rawTargets: unknown) {
  const sql = getSql();
  const targets = parseTargets(rawTargets);
  const ensuredTargets = await ensureTargets(targets);
  await upsertTargetProfiles(targets);

  await sql`
    DELETE FROM subscriptions
    WHERE client_id = ${clientId}
  `;

  for (const target of ensuredTargets) {
    await sql`
      INSERT INTO subscriptions (client_id, target_id)
      VALUES (${clientId}, ${target.id})
      ON CONFLICT (client_id, target_id) DO NOTHING
    `;
  }

  return listSubscriptions(clientId);
}

export async function addSubscriptions(clientId: string, rawTargets: unknown) {
  const targets = parseTargets(rawTargets);
  const ensuredTargets = await ensureTargets(targets);
  await upsertTargetProfiles(targets);
  const sql = getSql();

  for (const target of ensuredTargets) {
    await sql`
      INSERT INTO subscriptions (client_id, target_id)
      VALUES (${clientId}, ${target.id})
      ON CONFLICT (client_id, target_id) DO NOTHING
    `;
  }

  return listSubscriptions(clientId);
}

export async function removeSubscriptions(clientId: string, rawTargets: unknown) {
  const targets = parseTargets(rawTargets);
  if (targets.length === 0) {
    return listSubscriptions(clientId);
  }

  const sql = getSql();
  for (const target of targets) {
    await sql`
      DELETE FROM subscriptions
      WHERE client_id = ${clientId}
        AND target_id IN (
          SELECT id
          FROM targets
          WHERE source = ${target.source}
            AND kind = ${target.kind}
            AND normalized_value = ${target.normalizedValue}
        )
    `;
  }

  return listSubscriptions(clientId);
}
