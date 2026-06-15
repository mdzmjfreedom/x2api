import { requireClient } from "@/lib/auth";
import { jsonError, jsonOk } from "@/lib/http";
import { PaginationInputError } from "@/lib/pagination";
import { parseStringListParam } from "@/lib/query-params";
import { listVideoFeed, parseVideoFeedSource } from "@/lib/video-feed-service";

function publicApiOrigin(requestUrl: string) {
  const configuredBaseUrl = process.env.X2API_PUBLIC_BASE_URL?.trim();
  if (configuredBaseUrl) {
    const configured = new URL(configuredBaseUrl);
    if (configured.protocol === "http:" || configured.protocol === "https:") {
      return configured.origin;
    }
  }
  return new URL(requestUrl).origin;
}

function absolutizeRelativeVideoUrls<T extends { items?: Array<{ videoUrl?: string }> }>(result: T, requestUrl: string): T {
  const origin = publicApiOrigin(requestUrl);
  for (const item of result.items ?? []) {
    if (item.videoUrl?.startsWith("/")) {
      item.videoUrl = new URL(item.videoUrl, origin).toString();
    }
  }
  return result;
}

function parsePositiveInt(raw: string | null, field: string) {
  if (raw === null) {
    return undefined;
  }

  if (!/^\d+$/.test(raw)) {
    throw new Error(`Invalid ${field}. Expected a positive integer.`);
  }

  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error(`Invalid ${field}. Expected a positive integer.`);
  }

  return value;
}

export async function GET(request: Request) {
  try {
    const client = await requireClient();
    const { searchParams } = new URL(request.url);
    const query = {
      clientId: client.id,
      limit: parsePositiveInt(searchParams.get("limit"), "limit"),
      cursor: searchParams.get("cursor"),
      keyword: searchParams.get("keyword"),
      tags: parseStringListParam(searchParams, "tag"),
      categories: parseStringListParam(searchParams, "category"),
      source: parseVideoFeedSource(searchParams.get("source")),
    };
    const result = await listVideoFeed(query);
    return jsonOk(absolutizeRelativeVideoUrls(result, request.url));
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to query video feed.";
    if (message === "Missing API key." || message === "Invalid API key.") {
      return jsonError(message, 401);
    }
    if (error instanceof PaginationInputError || message.startsWith("Invalid ")) {
      return jsonError(message, 400);
    }
    return jsonError(message, 500);
  }
}
