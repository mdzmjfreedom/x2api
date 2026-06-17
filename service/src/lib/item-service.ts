import {
  listItemsByFeedTokenFromOpenSearch,
  listItemsFromOpenSearch,
  type OpenSearchItemRecord as ItemRecord,
  type OpenSearchListItemsResult as ListItemsResult,
} from "@/lib/opensearch-item-service";

type ItemQuery = {
  clientId: string;
  limit?: number;
  cursor?: string | null;
  keyword?: string | null;
  target?: string | null;
  tags?: string[] | null;
  categories?: string[] | null;
  since?: string | null;
};

export type { ItemRecord, ListItemsResult };

export async function listItems(query: ItemQuery): Promise<ListItemsResult> {
  return await listItemsFromOpenSearch(query);
}

export async function listItemsByFeedToken(feedToken: string, limit = 50) {
  return await listItemsByFeedTokenFromOpenSearch({ feedToken, limit });
}
