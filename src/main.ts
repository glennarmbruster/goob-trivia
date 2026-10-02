import "./styles.css";

type Status = {
  installed: boolean;
  facts: number;
  disneyFacts: number;
  disneySongs: number;
  categories: number;
  version: string;
  bytes: number;
  integrity: "unknown" | "passed" | "failed";
};

type Result = {
  id: string;
  answer: string;
  question: string;
  category: string;
  subcategory: string | null;
  difficulty: string;
  details: string | null;
  score: number;
  explanation: string;
  kind: "fact" | "song";
  work: string | null;
  characterPerformers: string | null;
  recordingArtists: string | null;
  year: number | null;
};

const worker = new Worker(new URL("./search.worker.ts", import.meta.url), { type: "module" });
const pending = new Map<number, { resolve: (value: any) => void; reject: (reason: Error) => void }>();
let requestId = 0;
let status: Status = { installed: false, facts: 0, disneyFacts: 0, disneySongs: 0, categories: 0, version: "", bytes: 0, integrity: "unknown" };
let results: Result[] = [];
let message = "";
let busy = false;
let installProgress = 0;
let persisted = false;
let storageUsage = 0;
let storageQuota = 0;

const callWorker = <T>(action: string, payload: Record<string, unknown> = {}) =>
  new Promise<T>((resolve, reject) => {
    requestId += 1;
    pending.set(requestId, { resolve, reject });
    worker.postMessage({ id: requestId, action, payload });
  });

worker.onmessage = (event) => {
  if (event.data.type === "progress") {
    installProgress = event.data.progress;
    render();
    return;
  }
  const request = pending.get(event.data.id);
  if (!request) return;
  pending.delete(event.data.id);
  if (event.data.type === "error") request.reject(new Error(event.data.error));
  else request.resolve(event.data.result);
};

const escapeHtml = (value: string) =>
  value.replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]!);

const formatBytes = (bytes: number) => {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  const level = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** level).toFixed(level > 1 ? 1 : 0)} ${units[level]}`;
};

const updateStorage = async () => {
  persisted = (await navigator.storage?.persisted?.()) ?? false;
  const estimate = await navigator.storage?.estimate?.();
  storageUsage = estimate?.usage ?? 0;
  storageQuota = estimate?.quota ?? 0;
};

const requestPersistence = async () => {
  if (navigator.storage?.persist) await navigator.storage.persist();
  await updateStorage();
};

const resultMarkup = () => {
  if (!status.installed) {
    return `<section class="empty-state"><span class="empty-icon">?</span><h2>Install a trivia database to begin</h2><p>The starter pack proves the offline search and includes a small Deep Disney collection. Larger private packs use the same import flow.</p></section>`;
  }
  if (!results.length && !message) {
    return `<section class="empty-state compact"><span class="empty-icon">⌕</span><h2>Ready for a clue</h2><p>Enter the words you remember from the question. Imperfect wording and misspellings are okay.</p></section>`;
  }
  if (!results.length) {
    return `<section class="empty-state compact"><span class="empty-icon">…</span><h2>No confident answer yet</h2><p>Try a distinctive name, place, movie, object, or detail from the question.</p></section>`;
  }
  return `<section class="results" aria-label="Trivia matches">
    ${results.map((result, index) => `<article class="result-card ${index === 0 ? "top-result" : ""}">
      <div class="rank">${index + 1}</div>
      <div class="result-copy">
        <div class="result-tags"><span>${result.kind === "song" ? "Disney Music" : escapeHtml(result.category)}</span>${result.subcategory ? `<span>${escapeHtml(result.subcategory)}</span>` : ""}<span>${escapeHtml(result.difficulty)}</span></div>
        <h2>${escapeHtml(result.answer)}</h2>
        ${result.kind === "song" ? `<p class="question">${result.work ? `From <strong>${escapeHtml(result.work)}</strong>` : "Disney music"}${result.year ? ` · ${result.year}` : ""}</p>${result.characterPerformers ? `<p class="song-credit"><span>Characters</span>${escapeHtml(result.characterPerformers)}</p>` : ""}${result.recordingArtists ? `<p class="song-credit"><span>Performed by</span>${escapeHtml(result.recordingArtists)}</p>` : ""}` : `<p class="question">${escapeHtml(result.question)}</p>`}
        ${result.details ? `<p class="details">${escapeHtml(result.details)}</p>` : ""}
        <p class="explanation">${escapeHtml(result.explanation)}</p>
      </div>
      <div class="match-score" aria-label="Match score ${result.score} out of 100"><strong>${result.score}</strong><span>match</span></div>
    </article>`).join("")}
  </section>`;
};

const render = () => {
  const app = document.querySelector<HTMLElement>("#app")!;
  const ready = status.installed;
  app.innerHTML = `<div class="page-shell">
    <header class="app-header">
      <div class="brand-mark" aria-hidden="true">?</div>
      <div class="brand-copy"><span>GOOBS</span><h1>Trivia Finder</h1></div>
      <button class="status-pill ${ready ? "ready" : ""}" id="database-button" type="button" aria-controls="database-panel"><i></i>${ready ? "Offline ready" : "Database needed"}</button>
    </header>

    <section class="search-surface" aria-labelledby="search-heading">
      <div class="era-badge">Cruise · Pub · Deep Disney</div>
      <h2 id="search-heading">What do you remember?</h2>
      <form id="search-form" class="search-form">
        <label class="sr-only" for="trivia-query">Trivia clue</label>
        <input id="trivia-query" name="query" type="search" inputmode="search" autocomplete="off" placeholder="little mermaid crab or song lyrics" ${ready ? "" : "disabled"} />
        <button type="submit" ${ready && !busy ? "" : "disabled"}>${busy ? '<span class="spinner"></span>' : "Find answer"}</button>
      </form>
      <p class="search-hint">Partial question, visual detail, answer, or remembered Disney lyric</p>
    </section>

    <div class="notice ${message ? "visible" : ""}" role="status">${escapeHtml(message)}</div>
    ${resultMarkup()}

    <section class="database-panel" id="database-panel" aria-labelledby="database-heading">
      <div class="panel-heading"><div><span class="eyebrow">LOCAL COLLECTION</span><h2 id="database-heading">Trivia Database</h2></div><span class="database-state ${ready ? "ready" : ""}">${ready ? "READY ✓" : "NOT INSTALLED"}</span></div>
      <div class="database-stats">
        <div><span>Records</span><strong>${ready ? status.facts.toLocaleString() : "—"}</strong></div>
        <div><span>Deep Disney</span><strong>${ready ? status.disneyFacts.toLocaleString() : "—"}</strong></div>
        <div><span>Disney Songs</span><strong>${ready ? status.disneySongs.toLocaleString() : "—"}</strong></div>
        <div><span>Categories</span><strong>${ready ? status.categories.toLocaleString() : "—"}</strong></div>
        <div><span>Storage</span><strong>${formatBytes(storageUsage)}</strong></div>
      </div>
      ${ready ? `<p class="version-line">Database version: <strong>${escapeHtml(status.version)}</strong></p>` : ""}
      ${busy && installProgress > 0 ? `<div class="progress"><span style="width:${installProgress * 100}%"></span></div>` : ""}
      <div class="database-actions">
        <button class="primary-action" id="sample-button" type="button" ${busy ? "disabled" : ""}>${ready ? "Reinstall starter pack" : "Install starter database"}</button>
        <label class="secondary-action ${busy ? "disabled" : ""}">Import my database<input id="database-file" type="file" ${busy ? "disabled" : ""} /></label>
        <button class="text-action" id="offline-test" type="button" ${ready && !busy ? "" : "disabled"}>Run offline test</button>
      </div>
      <p class="storage-note">${persisted ? "Persistent storage granted. The browser should retain this database until you remove it." : "Storage is currently best-effort. Installing the PWA improves retention."}${storageQuota ? ` ${formatBytes(Math.max(0, storageQuota - storageUsage))} available.` : ""}</p>
    </section>
    <footer><span>Private by design</span><span>No account · No analytics · No search connection</span></footer>
  </div>`;
  bindEvents();
};

const withBusy = async (operation: () => Promise<void>) => {
  busy = true;
  message = "";
  render();
  try { await operation(); }
  catch (error) { message = error instanceof Error ? error.message : String(error); }
  finally { busy = false; installProgress = 0; await updateStorage(); render(); }
};

const installBlob = async (file: Blob) => {
  await requestPersistence();
  status = await callWorker<Status>("install", { file });
  results = [];
  message = `${status.facts.toLocaleString()} trivia facts installed and verified.`;
};

const bindEvents = () => {
  document.querySelector("#database-button")?.addEventListener("click", () => document.querySelector("#database-panel")?.scrollIntoView({ behavior: "smooth" }));
  document.querySelector<HTMLFormElement>("#search-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = new FormData(event.currentTarget as HTMLFormElement).get("query")?.toString().trim() ?? "";
    if (!query) return;
    void withBusy(async () => {
      results = await callWorker<Result[]>("search", { query });
      message = results.length ? `${results.length} possible ${results.length === 1 ? "answer" : "answers"}` : "No confident matches";
    });
  });
  document.querySelector("#sample-button")?.addEventListener("click", () => {
    void withBusy(async () => {
      const response = await fetch(`${import.meta.env.BASE_URL}sample-trivia-database.sqlite`);
      if (!response.ok) throw new Error("The starter database could not be loaded.");
      await installBlob(await response.blob());
    });
  });
  document.querySelector<HTMLInputElement>("#database-file")?.addEventListener("change", (event) => {
    const file = (event.currentTarget as HTMLInputElement).files?.[0];
    if (file) void withBusy(() => installBlob(file));
  });
  document.querySelector("#offline-test")?.addEventListener("click", () => {
    void withBusy(async () => {
      status = await callWorker<Status>("verify");
      const cachesPresent = "caches" in window ? (await caches.keys()).length > 0 : false;
      const controlled = Boolean(navigator.serviceWorker?.controller) || import.meta.env.DEV;
      if (!cachesPresent && !import.meta.env.DEV) throw new Error("The application shell is not cached yet. Reopen once while online.");
      if (!controlled) throw new Error("The offline application shell is not active yet. Close and reopen the app.");
      message = `Offline test passed: app shell, ${status.facts.toLocaleString()} facts, search index, and database integrity are ready.`;
    });
  });
};

const start = async () => {
  render();
  try {
    const [databaseStatus] = await Promise.all([callWorker<Status>("status"), updateStorage()]);
    status = databaseStatus;
  } catch (error) { message = error instanceof Error ? error.message : String(error); }
  render();
};

void start();
