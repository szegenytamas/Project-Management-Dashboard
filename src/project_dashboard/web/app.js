// Project Dashboard - the browser UI.
//
// A plain client of the documented REST API: everything it does, Swagger or
// curl could do too. The server decides every permission; the UI only hides
// buttons that would be refused anyway.
//
// Security rules of this file:
//   * Text from the server is ALWAYS inserted with textContent (via el()),
//     never innerHTML - a project named "<img onerror=...>" stays plain text.
//   * The token lives in sessionStorage: it survives a reload but not closing
//     the tab, and is removed on sign-out or the first 401.
//   * The page's Content-Security-Policy allows scripts and styles from this
//     site only. Widths (progress and usage bars) are set through the style
//     API (element.style.width), which the CSP allows - unlike style="..."
//     attributes in markup.

const TOKEN_KEY = "pd.session";

// The server's limits (Settings in core/config.py). The server enforces them;
// the UI only uses them to warn early and to draw the usage bar.
const PROJECT_LIMIT_BYTES = 50 * 1024 * 1024;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_FILES_PER_UPLOAD = 10;
const ACCEPTED = [".pdf", ".docx"];

// ------------------------------------------------------------ small helpers --

const $ = (id) => document.getElementById(id);

/** Create an element; children may be strings (become text) or elements. */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child); // strings become text nodes
  return node;
}

/** An icon from the sprite in index.html: <svg><use href="#i-name"></svg>. */
function icon(name, className = "icon") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(ns, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** "3 minutes ago", "yesterday", or a date for older times. */
function timeAgo(iso) {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  const units = [["year", 31536000], ["month", 2592000], ["day", 86400], ["hour", 3600], ["minute", 60]];
  // "en": the UI's texts are English, so the relative times are too (not the browser's language).
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return rtf.format(-Math.round(seconds / size), unit);
  }
  return "just now";
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function showStatus(message, kind = "ok") {
  const box = $("status");
  box.textContent = message;
  box.className = `status ${kind}`;
  box.hidden = false;
  clearTimeout(showStatus.timer);
  if (kind === "ok") showStatus.timer = setTimeout(() => (box.hidden = true), 4000);
}

const clearStatus = () => ($("status").hidden = true);

/** Disable a form's buttons while a request runs, so it cannot be sent twice. */
async function busy(form, work) {
  const buttons = form.querySelectorAll("button");
  buttons.forEach((b) => (b.disabled = true));
  try {
    await work();
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

/** The "31.0 MB of 50 MB" bar; turns amber above 80 % and red above 95 %. */
function usageBar(totalBytes) {
  const share = Math.min(totalBytes / PROJECT_LIMIT_BYTES, 1);
  const fill = el("div", { class: `bar-fill${share > 0.95 ? " full" : share > 0.8 ? " high" : ""}` });
  fill.style.width = `${(share * 100).toFixed(1)}%`; // style API: allowed by the CSP
  const bar = el("div", {
    class: "bar",
    role: "meter",
    "aria-label": "Storage used",
    "aria-valuemin": "0",
    "aria-valuemax": String(PROJECT_LIMIT_BYTES),
    "aria-valuenow": String(totalBytes),
  }, fill);
  return el("div", { class: "usage" },
    el("div", { class: "row-between" },
      el("span", {}, `${formatBytes(totalBytes)} of 50 MB`),
      el("span", {}, `${Math.round(share * 100)} %`)),
    bar);
}

// ------------------------------------------------------------------ session --

function loadSession() {
  try {
    const session = JSON.parse(sessionStorage.getItem(TOKEN_KEY));
    if (session && session.expiresAt > Date.now()) return session;
  } catch {
    /* missing or broken entry: treat as signed out */
  }
  sessionStorage.removeItem(TOKEN_KEY);
  return null;
}

let session = loadSession();

function saveSession(login, tokenResponse) {
  session = {
    login,
    token: tokenResponse.access_token,
    // A minute early, so a request never starts with an almost-expired token.
    expiresAt: Date.now() + (tokenResponse.expires_in - 60) * 1000,
  };
  sessionStorage.setItem(TOKEN_KEY, JSON.stringify(session));
}

function signOut(message) {
  session = null;
  sessionStorage.removeItem(TOKEN_KEY);
  location.hash = "";
  render();
  if (message) showStatus(message, "error");
}

// -------------------------------------------------------------- API client --

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** Turn FastAPI's error bodies into one readable message. */
function errorMessage(status, body) {
  const detail = body && body.detail;
  if (Array.isArray(detail)) {
    // 422: a list of {loc, msg}; show "field: message" lines.
    return detail.map((e) => `${e.loc.slice(-1)[0]}: ${e.msg.replace(/^Value error, /, "")}`).join("\n");
  }
  if (typeof detail === "string") return detail;
  return `Request failed (HTTP ${status})`;
}

/** Shared error path for fetch and XHR: sign out on 401, then throw. */
function failure(status, body, auth) {
  if (status === 401 && auth) signOut("Your session has ended - please sign in again.");
  // 429 already says how long to wait ("try again in 42 seconds").
  return new ApiError(status, errorMessage(status, body));
}

/**
 * Call the API. Adds the token, sends JSON or a FormData body, and throws
 * ApiError with the server's message on any 4xx/5xx.
 */
async function api(method, path, { json, form, raw = false, auth = true } = {}) {
  const headers = {};
  if (auth) {
    if (!session) throw new ApiError(401, "Please sign in");
    headers.Authorization = `Bearer ${session.token}`;
  }
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) {
    body = form; // the browser sets multipart/form-data with its boundary
  }

  const response = await fetch(path, { method, headers, body });
  if (response.ok) {
    if (raw) return response;
    return response.status === 204 ? null : response.json();
  }
  throw failure(response.status, await response.json().catch(() => null), auth);
}

/**
 * Upload with progress. fetch() cannot report upload progress, XMLHttpRequest
 * can: its upload.onprogress event tells how many bytes were sent so far.
 */
function uploadWithProgress(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path);
    request.setRequestHeader("Authorization", `Bearer ${session.token}`);
    request.responseType = "json";
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) resolve(request.response);
      else reject(failure(request.status, request.response, true));
    };
    request.onerror = () => reject(new ApiError(0, "Network error - the upload did not reach the server."));
    request.send(form);
  });
}

// ------------------------------------------------------------------- views --

const views = ["view-auth", "view-projects", "view-project"];

function show(viewId) {
  for (const id of views) $(id).hidden = id !== viewId;
  $("session").hidden = !session;
  if (session) {
    $("session-login").textContent = session.login;
    $("session-avatar").textContent = session.login.charAt(0);
  }
}

/** Route by the URL hash: "" = list, "#/project/7" = one project. */
function render() {
  if (!session) {
    show("view-auth");
    return;
  }
  const match = location.hash.match(/^#\/project\/(\d+)$/);
  if (match) {
    show("view-project");
    loadProject(Number(match[1]));
  } else {
    show("view-projects");
    loadProjects();
  }
}

// Back/forward buttons and links change the hash; messages stay visible across it.
window.addEventListener("hashchange", render);

// A file dropped outside the drop zone would make the browser open it and
// leave the app - ignore drops anywhere else.
for (const type of ["dragover", "drop"]) {
  window.addEventListener(type, (event) => {
    if (!event.target.closest || !event.target.closest("#dropzone")) event.preventDefault();
  });
}

// ------------------------------------------------------- sign in / register --

function selectTab(register) {
  $("tab-login").setAttribute("aria-selected", String(!register));
  $("tab-register").setAttribute("aria-selected", String(register));
  $("form-login").hidden = register;
  $("form-register").hidden = !register;
  clearStatus();
}
$("tab-login").addEventListener("click", () => selectTab(false));
$("tab-register").addEventListener("click", () => selectTab(true));

async function signIn(login, password) {
  const token = await api("POST", "/login", { json: { login, password }, auth: false });
  saveSession(login.toLowerCase(), token);
}

$("form-login").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  busy(form, async () => {
    try {
      await signIn(form.login.value.trim(), form.password.value);
      form.reset();
      clearStatus();
      render();
    } catch (error) {
      showStatus(error.message, "error");
    }
  });
});

$("form-register").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const data = {
    login: form.login.value.trim(),
    password: form.password.value,
    repeat_password: form.repeat_password.value,
  };
  busy(form, async () => {
    try {
      await api("POST", "/auth", { json: data, auth: false });
      await signIn(data.login, data.password); // straight in after registering
      form.reset();
      showStatus("Account created - welcome!");
      render();
    } catch (error) {
      showStatus(error.message, "error");
    }
  });
});

$("logout").addEventListener("click", () => signOut());
$("home-link").addEventListener("click", (event) => {
  event.preventDefault();
  location.hash = "";
  render();
});

// ------------------------------------------------------------ project list --

function skeletonCard() {
  return el("li", { class: "project-card skeleton", "aria-hidden": "true" },
    el("div", { class: "line wide" }), el("div", { class: "line" }), el("div", { class: "line mid" }));
}

function projectCard(p) {
  return el("li", {},
    el("a", { class: "project-card", href: `#/project/${p.id}` },
      el("div", { class: "head" },
        el("span", { class: `tile ${p.role}` }, icon("folder")),
        el("span", { class: "name" }, p.name),
        el("span", { class: `badge ${p.role}` }, p.role)),
      el("p", { class: "desc" }, p.description || "No description"),
      usageBar(p.total_size_bytes),
      el("div", { class: "foot" },
        el("span", {}, plural(p.documents.length, "document")),
        el("span", {}, `updated ${timeAgo(p.updated_at)}`))));
}

async function loadProjects() {
  const list = $("project-list");
  $("project-empty").hidden = true;
  list.setAttribute("aria-busy", "true");
  list.replaceChildren(skeletonCard(), skeletonCard(), skeletonCard());
  try {
    const projects = await api("GET", "/projects");
    list.replaceChildren(...projects.map(projectCard));
    $("project-empty").hidden = projects.length > 0;
    const owned = projects.filter((p) => p.role === "owner").length;
    $("project-count").textContent = projects.length
      ? `${plural(projects.length, "project")} · you own ${owned}, participate in ${projects.length - owned}`
      : "";
  } catch (error) {
    list.replaceChildren();
    if (error.status !== 401) showStatus(error.message, "error");
  } finally {
    list.setAttribute("aria-busy", "false");
  }
}

$("empty-create").addEventListener("click", () => $("form-create").name.focus());

$("form-create").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  busy(form, async () => {
    try {
      const project = await api("POST", "/projects", {
        json: { name: form.name.value, description: form.description.value },
      });
      form.reset();
      showStatus(`Project "${project.name}" created.`);
      location.hash = `#/project/${project.id}`;
    } catch (error) {
      showStatus(error.message, "error");
    }
  });
});

// ---------------------------------------------------------- project detail --

let current = null; // the project shown in the detail view: {info, documents}

function skeletonDocument() {
  return el("li", { class: "document skeleton", "aria-hidden": "true" },
    el("div", { class: "info" }, el("div", { class: "line wide" }), el("div", { class: "line mid" })));
}

async function loadProject(id) {
  const fresh = !current || current.info.id !== id;
  if (fresh) {
    // Opening another project: show placeholders instead of the previous one.
    $("project-card").setAttribute("aria-busy", "true");
    $("project-name").textContent = "Loading…";
    $("project-role").textContent = "";
    $("project-description").textContent = "";
    $("project-usage").replaceChildren();
    $("project-meta").textContent = "";
    $("document-empty").hidden = true;
    $("document-list").replaceChildren(skeletonDocument(), skeletonDocument());
    for (const node of document.querySelectorAll(".owner-only")) node.hidden = true;
  }
  try {
    // Two calls, two endpoints of the contract: details + the document list.
    const [info, documents] = await Promise.all([
      api("GET", `/project/${id}/info`),
      api("GET", `/project/${id}/documents`),
    ]);
    current = { info, documents };
    renderProject();
  } catch (error) {
    if (error.status === 404) {
      // Not a member, or no such project - the API does not say which.
      current = null;
      location.hash = "";
      showStatus("Project not found.", "error");
    } else if (error.status !== 401) {
      showStatus(error.message, "error");
    }
  } finally {
    $("project-card").setAttribute("aria-busy", "false");
  }
}

function renderProject() {
  const { info, documents } = current;
  const isOwner = info.role === "owner";

  $("project-name").textContent = info.name;
  $("project-role").textContent = info.role;
  $("project-role").className = `badge ${info.role}`;
  $("project-description").textContent = info.description || "No description.";
  $("project-usage").replaceChildren(usageBar(info.total_size_bytes));
  $("project-meta").textContent =
    `${plural(documents.length, "document")} · created ${timeAgo(info.created_at)} · ` +
    `updated ${timeAgo(info.updated_at)}`;

  const edit = $("form-edit");
  edit.name.value = info.name;
  edit.description.value = info.description;

  for (const node of document.querySelectorAll(".owner-only")) node.hidden = !isOwner;

  $("document-list").replaceChildren(...documents.map((doc) => documentItem(doc, isOwner)));
  $("document-empty").hidden = documents.length > 0;
}

function documentItem(doc, isOwner) {
  const replaceInput = el("input", { type: "file", accept: ACCEPTED.join(","), hidden: "" });
  replaceInput.addEventListener("change", () => replaceDocument(doc, replaceInput));
  const isPdf = doc.content_type === "application/pdf";

  const actions = [
    el("button", { type: "button", title: "Download", onclick: () => downloadDocument(doc) },
      icon("download"), "Download"),
    el("button", { type: "button", title: "Replace with a new version", onclick: () => replaceInput.click() },
      icon("swap"), "Replace"),
    replaceInput,
  ];
  if (isOwner) {
    actions.push(el("button", {
      type: "button", class: "danger", title: "Delete", "aria-label": `Delete ${doc.filename}`,
      onclick: () => deleteDocument(doc),
    }, icon("trash")));
  }
  return el("li", { class: "document" },
    el("span", { class: `tile${isPdf ? " pdf" : ""}` }, icon("file")),
    el("div", { class: "info" },
      el("div", { class: "name" }, doc.filename),
      el("div", { class: "sub" }, `${isPdf ? "PDF" : "Word"} · ${formatBytes(doc.size_bytes)} · updated ${timeAgo(doc.updated_at)}`)),
    el("div", { class: "actions" }, ...actions));
}

$("back").addEventListener("click", () => (location.hash = ""));

$("form-edit").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  busy(form, async () => {
    try {
      current.info = await api("PUT", `/project/${current.info.id}/info`, {
        json: { name: form.name.value, description: form.description.value },
      });
      $("edit-details").open = false;
      renderProject();
      showStatus("Details saved.");
    } catch (error) {
      showStatus(error.message, "error");
    }
  });
});

// --------------------------------------------------- drag-and-drop upload --

const dropzone = $("dropzone");
const fileInput = $("file-input");

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
fileInput.addEventListener("change", () => {
  const files = [...fileInput.files];
  fileInput.value = ""; // allow choosing the same file again
  uploadFiles(files);
});

// dragenter/dragleave also fire for the zone's children; count them to know
// when the pointer really left the zone.
let dragDepth = 0;
dropzone.addEventListener("dragenter", (event) => {
  event.preventDefault();
  dragDepth += 1;
  dropzone.classList.add("dragging");
});
dropzone.addEventListener("dragover", (event) => event.preventDefault()); // allows dropping
dropzone.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dropzone.classList.remove("dragging");
});
dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  dropzone.classList.remove("dragging");
  uploadFiles([...event.dataTransfer.files]);
});

/** Quick checks before sending; the server checks everything again. */
function precheck(files) {
  if (files.length > MAX_FILES_PER_UPLOAD) return `At most ${MAX_FILES_PER_UPLOAD} files per upload.`;
  for (const file of files) {
    const name = file.name.toLowerCase();
    if (!ACCEPTED.some((ext) => name.endsWith(ext))) return `"${file.name}" is not a PDF or DOCX file.`;
    if (file.size > MAX_FILE_BYTES) return `"${file.name}" is larger than 10 MB.`;
  }
  const total = files.reduce((sum, f) => sum + f.size, 0);
  if (current.info.total_size_bytes + total > PROJECT_LIMIT_BYTES) {
    return `Not enough space: ${formatBytes(total)} would exceed the 50 MB project limit.`;
  }
  return null;
}

async function uploadFiles(files) {
  if (files.length === 0 || !current) return;
  const problem = precheck(files);
  if (problem) {
    showStatus(problem, "error");
    return;
  }
  const form = new FormData();
  for (const file of files) form.append("files", file);

  const panel = $("upload-progress");
  const fill = $("upload-bar");
  const label = files.length === 1 ? `Uploading "${files[0].name}"` : `Uploading ${files.length} files`;
  $("upload-label").textContent = `${label}…`;
  $("upload-percent").textContent = "0 %";
  fill.className = "bar-fill upload";
  fill.style.width = "0%";
  panel.hidden = false;
  dropzone.classList.add("busy");
  clearStatus();

  try {
    const added = await uploadWithProgress(`/project/${current.info.id}/documents`, form, (share) => {
      const percent = Math.round(share * 100);
      fill.style.width = `${percent}%`;
      // 100 % = all bytes sent; the server still checks and stores them.
      $("upload-percent").textContent = percent < 100 ? `${percent} %` : "checking…";
    });
    showStatus(`Uploaded: ${added.map((d) => d.filename).join(", ")}`);
    await loadProject(current.info.id);
  } catch (error) {
    showStatus(error.message, "error"); // 413 limit, 415 wrong type, ...
  } finally {
    panel.hidden = true;
    dropzone.classList.remove("busy");
  }
}

async function replaceDocument(doc, input) {
  const file = input.files[0];
  input.value = "";
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    await api("PUT", `/document/${doc.id}`, { form });
    showStatus(`"${doc.filename}" replaced by "${file.name}".`);
    await loadProject(current.info.id);
  } catch (error) {
    showStatus(error.message, "error");
  }
}

/** File name from Content-Disposition: prefer the exact UTF-8 filename*. */
function filenameFrom(header, fallback) {
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header || "");
  if (utf8) return decodeURIComponent(utf8[1]);
  const plain = /filename="([^"]+)"/i.exec(header || "");
  return plain ? plain[1] : fallback;
}

// A link cannot send the Authorization header, so the file is fetched with it
// and handed to the browser as a temporary blob: URL.
async function downloadDocument(doc) {
  try {
    const response = await api("GET", `/document/${doc.id}`, { raw: true });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = el("a", { href: url, download: filenameFrom(response.headers.get("Content-Disposition"), doc.filename) });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    showStatus(error.message, "error");
  }
}

async function deleteDocument(doc) {
  if (!confirm(`Delete "${doc.filename}"? This cannot be undone.`)) return;
  try {
    await api("DELETE", `/document/${doc.id}`);
    showStatus(`"${doc.filename}" deleted.`);
    await loadProject(current.info.id);
  } catch (error) {
    showStatus(error.message, "error");
  }
}

$("form-invite").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  busy(form, async () => {
    try {
      const member = await api("POST", `/project/${current.info.id}/invite?user=${encodeURIComponent(form.login.value.trim())}`);
      form.reset();
      showStatus(`${member.login} can now see and edit this project.`);
    } catch (error) {
      showStatus(error.message, "error"); // 404 unknown user, 409 already a member
    }
  });
});

$("delete-project").addEventListener("click", async () => {
  const { id, name } = current.info;
  if (!confirm(`Delete the project "${name}" and all its documents? This cannot be undone.`)) return;
  try {
    await api("DELETE", `/project/${id}`);
    current = null;
    location.hash = "";
    showStatus(`Project "${name}" deleted.`);
  } catch (error) {
    showStatus(error.message, "error");
  }
});

// ------------------------------------------------------------------ start --
render();
