// L53 (Neon → SQLite): the data pipeline's tipping_point.sqlite carries a
// `docs` table — every app-facing JSON/GeoJSON by its data-root-relative path
// (lead-ml c7ab438: path PRIMARY KEY, bytes, sha256, encoding 'gzip', body).
// The server reads it when GitHub raw misses, before the bundled copy.
//
// Where the file is: DATA_SQLITE, else app/.data/tipping_point.sqlite (the
// image's writable cache dir), else the monorepo's data/tipping_point.sqlite.
// A background refresh downloads it from the GitHub fallback branch into
// app/.data when the copy is missing or older than 3 h, so it is there for
// the day GitHub raw is down. Tests use DATA_SQLITE only (no refresh).
import { mkdir, rename, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { gunzipSync } from "node:zlib";

export const SQLITE_NAME = "tipping_point.sqlite";
const REFRESH_MS = 3 * 3_600_000;
const isTest = () => process.env.NODE_ENV === "test";

export const sqliteCandidates = (): string[] => {
  if (process.env.DATA_SQLITE) return [process.env.DATA_SQLITE];
  if (isTest()) return [];
  return [path.join(process.cwd(), ".data", SQLITE_NAME), path.join(process.cwd(), "..", "data", SQLITE_NAME)];
};

interface Db { prepare(sql: string): { get(...a: unknown[]): unknown } ; close(): void }
let open: { file: string; mtimeMs: number; db: Db } | undefined;

async function db(): Promise<Db | undefined> {
  for (const file of sqliteCandidates()) {
    let mtimeMs: number;
    try { mtimeMs = (await stat(file)).mtimeMs; } catch { continue; }
    if (open && open.file === file && open.mtimeMs === mtimeMs) return open.db;
    try {
      // @ts-expect-error -- node:sqlite exists in Node ≥ 22.5 and Bun; @types/node 20 lacks its types
      const { DatabaseSync } = (await import("node:sqlite")) as { DatabaseSync: new (f: string, o: { readOnly: boolean }) => unknown };
      const next = new DatabaseSync(file, { readOnly: true }) as unknown as Db;
      try { open?.db.close(); } catch { /* already closed */ }
      open = { file, mtimeMs, db: next };
      return next;
    } catch {
      continue;
    }
  }
  return undefined;
}

// One document by its data-root-relative path; undefined when absent.
export async function readSqliteDoc<T>(key: string): Promise<T | undefined> {
  try {
    const d = await db();
    if (!d) return undefined;
    const row = d.prepare("SELECT encoding, body FROM docs WHERE path = ?").get(key.replace(/^\/+/, "")) as
      { encoding?: string; body?: Uint8Array } | undefined;
    if (!row?.body) return undefined;
    const buf = Buffer.from(row.body);
    const text = (row.encoding === "gzip" ? gunzipSync(buf) : buf).toString("utf-8");
    return JSON.parse(text) as T;
  } catch {
    return undefined;
  }
}

// Fire-and-forget: keep app/.data/tipping_point.sqlite at most 3 h old.
let lastTry = 0;
export function refreshSqlite(remoteBase: string, nowMs: number): void {
  if (isTest() || process.env.DATA_SQLITE || process.env.DATA_SQLITE_REFRESH === "0") return;
  if (nowMs - lastTry < REFRESH_MS) return;
  lastTry = nowMs;
  const file = path.join(process.cwd(), ".data", SQLITE_NAME);
  void (async () => {
    try {
      const age = await stat(file).then((s) => nowMs - s.mtimeMs, () => Infinity);
      if (age < REFRESH_MS) return;
      const res = await fetch(`${remoteBase.replace(/\/+$/, "")}/${SQLITE_NAME}`, { signal: AbortSignal.timeout(60_000) });
      if (!res.ok) return;
      const bytes = new Uint8Array(await res.arrayBuffer());
      if (bytes.length < 16 || new TextDecoder().decode(bytes.slice(0, 15)) !== "SQLite format 3") return;
      await mkdir(path.dirname(file), { recursive: true });
      await writeFile(`${file}.tmp`, bytes);
      await rename(`${file}.tmp`, file);
    } catch {
      // a failed refresh keeps the previous copy
    }
  })();
}
