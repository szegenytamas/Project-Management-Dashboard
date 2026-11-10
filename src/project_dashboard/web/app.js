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
//   * The page's Content-Security-Policy allows scripts from this site only,
//     so even injected markup could not run code.

const TOKEN_KEY = "pd.session";

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

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const formatDate = (iso) => new Date(iso).toLocaleString();

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

  const errorBody = await response.json().catch(() => null);
  const message = errorMessage(response.status, errorBody);
  if (response.status === 401 && auth) {
    signOut("Your session has ended - please sign in again.");
  }
  // 429 already says how long to wait ("try again in 42 seconds").
  throw new ApiError(response.status, message);
}

// ------------------------------------------------------------------- views --

const views = ["view-auth", "view-projects", "view-project"];

function show(viewId) {
  for (const id of views) $(id).hidden = id !== viewId;
  $("session").hidden = !session;
  if (session) $("session-login").textContent = session.login;
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

async function loadProjects() {
  try {
    const projects = await api("GET", "/projects");
    const list = $("project-list");
    list.replaceChildren(
      ...projects.map((p) =>
        el("li", {},
          el("a", { href: `#/project/${p.id}` },
            el("span", { class: "title" }, p.name, el("span", { class: `badge ${p.role}` }, p.role)),
            el("span", { class: "sub" },
              `${p.documents.length} document${p.documents.length === 1 ? "" : "s"} · ` +
                `${formatBytes(p.total_size_bytes)} · updated ${formatDate(p.updated_at)}`),
          ),
        ),
      ),
    );
    $("project-empty").hidden = projects.length > 0;
  } catch (error) {
    if (error.status !== 401) showStatus(error.message, "error");
  }
}

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

async function loadProject(id) {
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
      location.hash = "";
      showStatus("Project not found.", "error");
    } else if (error.status !== 401) {
      showStatus(error.message, "error");
    }
  }
}

function renderProject() {
  const { info, documents } = current;
  const isOwner = info.role === "owner";

  $("project-name").textContent = info.name;
  $("project-role").textContent = info.role;
  $("project-role").className = `badge ${info.role}`;
  $("project-description").textContent = info.description || "No description.";
  $("project-meta").textContent =
    `${documents.length} document${documents.length === 1 ? "" : "s"} · ` +
    `${formatBytes(info.total_size_bytes)} of 50 MB · created ${formatDate(info.created_at)}`;

  const edit = $("form-edit");
  edit.name.value = info.name;
  edit.description.value = info.description;

  for (const node of document.querySelectorAll(".owner-only")) node.hidden = !isOwner;

  $("document-rows").replaceChildren(...documents.map((doc) => documentRow(doc, isOwner)));
  $("document-empty").hidden = documents.length > 0;
}

function documentRow(doc, isOwner) {
  const replaceInput = el("input", { type: "file", accept: ".pdf,.docx", hidden: "" });
  replaceInput.addEventListener("change", () => replaceDocument(doc, replaceInput));

  const actions = [
    el("button", { type: "button", onclick: () => downloadDocument(doc) }, "Download"),
    el("button", { type: "button", onclick: () => replaceInput.click() }, "Replace"),
    replaceInput,
  ];
  if (isOwner) {
    actions.push(el("button", { type: "button", class: "danger", onclick: () => deleteDocument(doc) }, "Delete"));
  }
  return el("tr", {},
    el("td", { class: "name" }, doc.filename),
    el("td", {}, formatBytes(doc.size_bytes)),
    el("td", {}, formatDate(doc.updated_at)),
    el("td", {}, ...actions),
  );
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

// Upload starts as soon as files are chosen.
$("form-upload").files.addEventListener("change", async (event) => {
  const input = event.target;
  if (input.files.length === 0) return;
  const form = new FormData();
  for (const file of input.files) form.append("files", file);
  showStatus(`Uploading ${input.files.length} file(s)…`);
  try {
    const added = await api("POST", `/project/${current.info.id}/documents`, { form });
    showStatus(`Uploaded: ${added.map((d) => d.filename).join(", ")}`);
    await loadProject(current.info.id);
  } catch (error) {
    showStatus(error.message, "error"); // 413 limit, 415 wrong type, ...
  } finally {
    input.value = ""; // allow choosing the same file again
  }
});

async function replaceDocument(doc, input) {
  const file = input.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    await api("PUT", `/document/${doc.id}`, { form });
    showStatus(`"${doc.filename}" replaced by "${file.name}".`);
    await loadProject(current.info.id);
  } catch (error) {
    showStatus(error.message, "error");
  } finally {
    input.value = "";
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
    location.hash = "";
    showStatus(`Project "${name}" deleted.`);
  } catch (error) {
    showStatus(error.message, "error");
  }
});

// ------------------------------------------------------------------ start --
render();
