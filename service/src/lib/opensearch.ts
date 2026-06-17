import { Client } from "@opensearch-project/opensearch";

const DEFAULT_ITEMS_INDEX = "x2_items";

let client: Client | null = null;

export function getOpenSearchItemsIndex() {
  return process.env.OPENSEARCH_ITEMS_INDEX?.trim() || DEFAULT_ITEMS_INDEX;
}

export function isOpenSearchFeedEnabled() {
  return Boolean(process.env.OPENSEARCH_URL?.trim());
}

export function assertOpenSearchFeedEnabled(context: string) {
  if (!isOpenSearchFeedEnabled()) {
    throw new Error(`[${context}] OpenSearch feed is required but OPENSEARCH_URL is not configured.`);
  }
}

export function getOpenSearchClient() {
  const node = process.env.OPENSEARCH_URL?.trim();
  if (!node) {
    return null;
  }

  if (!client) {
    client = new Client({
      node,
      ssl: {
        rejectUnauthorized: false,
      },
      requestTimeout: 2500,
    });
  }

  return client;
}
