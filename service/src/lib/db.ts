import { Pool, type QueryResult } from "pg";

import { getRequiredEnv } from "@/lib/env";

type QueryPrimitive = string | number | boolean | Date | null | undefined;
type QueryValue = QueryPrimitive | QueryChunk;

export type QueryChunk = {
  text: string;
  values: QueryPrimitive[];
};

export type SqlQuery = QueryChunk & PromiseLike<QueryResult>;

export type SqlFunction = {
  (strings: TemplateStringsArray, ...values: QueryValue[]): SqlQuery;
  unsafe: (text: string, values?: QueryPrimitive[]) => Promise<QueryResult>;
};

let pool: Pool | null = null;
let sqlClient: SqlFunction | null = null;

function getPool() {
  if (!pool) {
    pool = new Pool({
      connectionString: getRequiredEnv("DATABASE_URL"),
      ssl: { rejectUnauthorized: false },
      max: 10,
    });
  }

  return pool;
}

function isQueryChunk(value: QueryValue): value is QueryChunk {
  return value !== null && typeof value === "object" && "text" in value && "values" in value;
}

function buildQuery(strings: TemplateStringsArray, values: QueryValue[]): QueryChunk {
  let text = "";
  const params: QueryPrimitive[] = [];

  for (let index = 0; index < strings.length; index += 1) {
    text += strings[index];

    if (index >= values.length) {
      continue;
    }

    const value = values[index];
    if (isQueryChunk(value)) {
      const nestedText = value.text.replace(/\$(\d+)/g, (_match, rawIndex) => {
        return `$${params.length + Number(rawIndex)}`;
      });
      text += nestedText;
      params.push(...value.values);
      continue;
    }

    params.push(value);
    text += `$${params.length}`;
  }

  return { text, values: params };
}

function createSqlQuery(query: QueryChunk): SqlQuery {
  return {
    ...query,
    then(onFulfilled, onRejected) {
      return getPool().query(query.text, query.values).then(onFulfilled, onRejected);
    },
  };
}

function createSqlFunction(): SqlFunction {
  const sql = ((strings: TemplateStringsArray, ...values: QueryValue[]) => {
    return createSqlQuery(buildQuery(strings, values));
  }) as SqlFunction;

  sql.unsafe = async (text, values = []) => getPool().query(text, values);

  return sql;
}

export function getSql(): SqlFunction {
  if (!sqlClient) {
    sqlClient = createSqlFunction();
  }

  return sqlClient;
}
