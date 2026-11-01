/* fnmusic-ext WebUI — 原生 JS，无框架无外部资产。 */
"use strict";

// 飞牛桌面用 HTTPS 打开管理窗，页面必须挂在同源路径 /app/fnmusic-ext 下。
// WebUI 自己也会剥掉这个前缀。管理接口只认飞牛网关注入的管理员头。
const APP_BASE = "/app/fnmusic-ext";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const PROVIDER_LABEL = { musicdl: "musicdl 聚合音源", musicbox: "网易云音乐盒子", lxmusic: "洛雪自定义源", none: "未配置" };
const PROC_LABEL = { musicdl: "musicdl", musicbox: "musicbox", lxserver: "lxserver", lxmusic: "lxmusic", webui: "WebUI" };

let configValues = {};   // GET /api/config 的 values
let configPresence = {}; // .env 显式存在的键（区分默认空与管理员清空）
let platforms = { enabled: [], registered: [] };
let dirty = false;
let qrTimer = null;
let lxVerifiedUrl = null; // 已通过测试的 lx URL（保存时免二次校验提示用）
let lxSourceList = [];    // 洛雪源列表 [{ name, url, active }]，active 可多开
let updateInfo = null;    // /api/update-check 结果（菜单红点 / NEW 标记 / 详情弹窗共用）
let publicSelection = [];
let publicCapabilities = null;
let publicState = {
  provider: "musicbox", channel: "charts", source: "netease", category: "", sort: "",
  keyword: "", page: 1, items: [], hasMore: false, total: 0, cursor: "",
  cursors: { 1: "" }, seq: 0, loading: false, bulk: null,
};
// 已选歌单的展示元数据缓存（key=publicEntryKey）：保存后重新回填、跨 provider
// 补齐时复用，避免每次渲染都打详情接口
const publicDetailCache = new Map();
let publicSelectionDetailSeq = 0;

async function api(path, options) {
  const resp = await fetch(APP_BASE + path, options);
  let body = {};
  try { body = await resp.json(); } catch (_) { /* 非 JSON */ }
  if (!resp.ok) throw new Error(body.error || body.detail || `HTTP ${resp.status}`);
  return body;
}

function updateLlmLikedSettings() {
  $("#llm-liked-settings").hidden = !$("#llm-liked").checked;
  const selected = $("#llm-liked").checked || $("#llm-daily").checked;
  $("#llm-api-warning").hidden = !selected || !!($("#llm-base").value.trim() && $("#llm-key").value.trim());
}

async function refreshLlmUsage() {
  const el = $("#llm-usage-line");
  if (!el) return;
  try {
    const { usage: u } = await api("/api/llm/usage");
    const used = Number(u.total) || 0;
    const limit = Number(u.dailyLimit) || 0;
    let text = `今日 token 用量 ${used.toLocaleString()}`;
    text += limit > 0 ? ` / 上限 ${limit.toLocaleString()}` : "（未设上限）";
    text += `，已请求 ${Number(u.requests) || 0} 次`;
    if (u.withinLimit === false) text += "，已达上限：今日暂停调用，次日自动重置";
    const f = u.lastFailure;
    if (f && f.reason) text += `；最近失败：${f.reason}${f.detail ? `（${String(f.detail).slice(0, 120)}）` : ""}`;
    el.textContent = text;
  } catch (_) {
    el.textContent = "今日 token 用量：暂不可用";
  }
}

function likedNumber(selector, label, min, max, integer = false) {
  const text = $(selector).value.trim();
  const num = Number(text);
  if (!text || !Number.isFinite(num) || num < min || (max != null && num > max) || (integer && !Number.isInteger(num))) {
    throw new Error(label + (integer ? "必须是 0–100 的整数" : "必须是大于或等于 0.5 的有限数字"));
  }
  return num;
}

function toast(message, kind) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast " + (kind || "");
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, 3800);
}

function markDirty(note) {
  dirty = true;
  const bar = $("#save-bar");
  if (bar) bar.classList.add("show");
  const noteEl = $("#save-note");
  if (noteEl) noteEl.textContent = note || "有未保存的修改";
}

function clearDirty() {
  dirty = false;
  const bar = $("#save-bar");
  if (bar) bar.classList.remove("show");
  const noteEl = $("#save-note");
  if (noteEl) noteEl.textContent = "";
}

/* -------------------------------------------------------------- 导航 */
function switchPage(page) {
  $$(".page").forEach((el) => el.classList.toggle("active", el.id === "page-" + page));
  $$("[data-page]").forEach((el) => el.classList.toggle("active", el.dataset.page === page));
  // 储存父标题：边听边存/目录设置/保存到 NAS 任一子页激活时强调（子项保持各自的实心高亮）
  const storageTitle = $("#nav-storage-title");
  if (storageTitle) storageTitle.classList.toggle("active", page === "tee" || page === "dirs" || page === "save");
  if (page === "save") loadSaveStatus().catch(() => {});
  if (page === "about") loadAbout().catch((exc) => { $("#about-logs").textContent = "加载失败：" + exc.message; });
}
$$("[data-page]").forEach((btn) => btn.addEventListener("click", () => switchPage(btn.dataset.page)));
const storageTitleBtn = $("#nav-storage-title");
if (storageTitleBtn) storageTitleBtn.addEventListener("click", () => switchPage("tee"));

/* -------------------------------------------------------------- 主题 / 外观
   默认「跟随系统」：飞牛 Web 宿主里以宿主主题为准（getPlatformConfig 取初值，
   $on('os/theme') 监听后续切换，见 fnOS「页面交互」文档）；宿主不可用或独立浏览器
   退回 prefers-color-scheme。用户选择只存当前浏览器，不进 .env，也不参与保存条。 */
const THEME_KEY = "fnmusic-theme";
const HOST_THEME_KEY = "fnmusic-host-theme";
const themeMedia = (typeof window !== "undefined" && typeof window.matchMedia === "function")
  ? window.matchMedia("(prefers-color-scheme: dark)")
  : null;
let themePref = "auto";  // auto | light | dark —— 「设置 → 外观」里的选择
let hostTheme = null;    // 飞牛宿主报告的主题：light | dark（无宿主时为 null）

function normalizeTheme(value) {
  return value === "dark" || value === "light" ? value : null;
}

function readThemePref() {
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch (_) { /* 隐私模式/直连没有 localStorage：按跟随系统处理 */ }
  return "auto";
}

function updateHostTheme(value) {
  hostTheme = normalizeTheme(value);
  try {
    if (hostTheme) window.localStorage.setItem(HOST_THEME_KEY, hostTheme);
    else window.localStorage.removeItem(HOST_THEME_KEY);
  } catch (_) { /* 宿主主题仍可在本次页面生效 */ }
}

function effectiveTheme() {
  if (themePref !== "auto") return themePref;
  if (hostTheme) return hostTheme;
  return themeMedia && themeMedia.matches ? "dark" : "light";
}

function applyTheme() {
  const theme = effectiveTheme();
  // 与飞牛文档示例一致：主题写到 <html data-theme>，CSS 按此覆盖配色变量
  if (typeof document !== "undefined" && document.documentElement && document.documentElement.dataset) {
    document.documentElement.dataset.theme = theme;
  }
  $$("input[name=theme]").forEach((el) => { el.checked = el.value === themePref; });
}

function setThemePref(pref) {
  themePref = normalizeTheme(pref) || "auto";
  try {
    if (themePref === "auto") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, themePref);
  } catch (_) { /* 存不了就只对本次会话生效 */ }
  applyTheme();
}

async function initTheme() {
  themePref = readThemePref();
  const pathname = (typeof window !== "undefined" && window.location && window.location.pathname) || "";
  const embedded = pathname.startsWith("/app/");
  hostTheme = null;
  if (embedded) {
    try { hostTheme = normalizeTheme(window.localStorage.getItem(HOST_THEME_KEY)); } catch (_) {}
  }
  applyTheme();
  $$("input[name=theme]").forEach((el) => el.addEventListener("change", () => {
    if (el.checked) setThemePref(el.value);
  }));
  // 独立浏览器 / 设备级深浅色变化（宿主主题优先，变化由下面的 $on 刷新）
  if (themeMedia && typeof themeMedia.addEventListener === "function") {
    themeMedia.addEventListener("change", () => { if (themePref === "auto" && !hostTheme) applyTheme(); });
  }
  if (!embedded) return;  // 直连 8774：没有飞牛宿主，不碰 SDK
  const sdk = await loadTrimSdk();
  if (!sdk) {
    updateHostTheme(null);
    applyTheme();
    return;
  }
  try {
    // 移动端 App 内嵌页也能取到主题；独立浏览器没有宿主桥，抛错后走媒体查询
    const config = await sdk.getPlatformConfig();
    updateHostTheme(config && (config.theme || (config.os && config.os.theme)));
  } catch (_) {
    updateHostTheme(null);
  }
  applyTheme();
  if (!sdk.isWeb || sdk.isStandaloneWeb) return;  // 只有 Web 宿主支持 $on 主题事件
  try {
    if (typeof sdk.$on === "function") {
      // 仅 Web 宿主支持；移动端 App 内嵌页与独立浏览器没有该事件
      await sdk.$on("os/theme", (theme) => {
        updateHostTheme(theme);
        applyTheme();
      });
    }
  } catch (_) { /* 宿主不支持事件推送时退回媒体查询 */ }
}

/* -------------------------------------------------------------- 概览 */
async function loadStatus() {
  try {
    const st = await api("/api/status");
    $("#brand-version").textContent = `v${st.version}`;
    $("#sidebar-foot").textContent = `${PROVIDER_LABEL[st.current_provider] || st.current_provider}`;
    $("#ov-provider-body").innerHTML =
      `<span class="state-line"><span class="dot ok"></span>${PROVIDER_LABEL[st.current_provider] || st.current_provider}</span>`;
    $("#ov-processes").innerHTML = Object.entries(st.processes).map(([name, p]) => {
      const ok = p.state === "RUNNING";
      const cls = ok ? "ok" : p.state === "FATAL" ? "err" : "";
      return `<span class="chip"><span class="dot ${cls}"></span>${escapeHtml(PROC_LABEL[name] || name)} · ${escapeHtml(p.state)}</span>`;
    }).join("");
    $("#ov-services").innerHTML = Object.entries(st.services).map(([name, s]) => {
      if (!s.reachable && s.note) return `<span class="state-line"><span class="dot"></span>${escapeHtml(PROC_LABEL[name])}：${escapeHtml(s.note)}</span>`;
      if (!s.reachable) {
        // 后端探活失败时透出 healthz 返回的具体原因（如 lxserver unreachable），
        // 便于区分"进程异常"与"进程在但底层服务不可达"
        const why = String(s.detail || "").trim();
        return `<span class="state-line"><span class="dot err"></span>${escapeHtml(PROC_LABEL[name])}：不可达${why ? "（" + escapeHtml(why) + "）" : ""}</span>`;
      }
      return `<span class="state-line"><span class="dot ok"></span>${escapeHtml(PROC_LABEL[name])}：正常</span>`;
    }).join("");
    const lxCard = $("#ov-lx-card");
    if (st.current_provider === "lxmusic" && st.lx_source) {
      lxCard.hidden = false;
      const ls = st.lx_source;
      const rows = [];
      rows.push(`<div class="kv"><b>状态</b>${ls.initialized ? "已加载" : "未加载"}</div>`);
      const sources = ls.sources || [];
      if (sources.length) {
        rows.push(`<div class="kv"><b>已激活源</b>${sources.length} 个：${sources.map((s) =>
          `${escapeHtml(s.name || "-")}${s.version ? " v" + escapeHtml(s.version) : ""}`).join("、")}</div>`);
        const plats = new Set();
        sources.forEach((s) => (s.platforms || []).forEach((p) => plats.add(p)));
        rows.push(`<div class="kv"><b>平台</b>${escapeHtml([...plats].join("、")) || "-"}</div>`);
      } else if (ls.source) {
        rows.push(`<div class="kv"><b>源名称</b>${escapeHtml(ls.source.name || "-")} ${ls.source.version ? "v" + escapeHtml(ls.source.version) : ""}</div>`);
        rows.push(`<div class="kv"><b>平台</b>${escapeHtml(Object.keys(ls.source.platforms || {}).join("、")) || "-"}</div>`);
      }
      if (ls.last_error) rows.push(`<div class="kv"><b>错误</b>${escapeHtml(ls.last_error)}</div>`);
      $("#ov-lx").innerHTML = rows.join("");
    } else {
      lxCard.hidden = true;
    }
  } catch (exc) {
    $("#ov-provider-body").textContent = "状态加载失败：" + exc.message;
  }
}

/* -------------------------------------------------------------- 配置读写 */
async function loadConfig() {
  const cfg = await api("/api/config");
  configValues = cfg.values;
  configPresence = cfg.presence || {};
  applyConfigToForm();
  clearDirty();
}

function applyConfigToForm() {
  const v = configValues;
  const provider = v.FNMUSIC_NETEASE_ENABLED === "true" ? "musicbox"
    : v.FNMUSIC_MUSICDL_ENABLED === "true" ? "musicdl"
    : v.FNMUSIC_LX_ENABLED === "true" ? "lxmusic" : "";
  $$("input[name=provider]").forEach((el) => { el.checked = el.value === provider; });
  syncProviderPanels(provider);
  if (provider === "musicbox") syncNeteaseAccount();
  const quality = v.FNMUSIC_QUALITY_MODE || "high";
  $$("input[name=quality]").forEach((el) => { el.checked = el.value === quality; });
  const dlQuality = v.FNMUSIC_DL_QUALITY || "app";
  $$("input[name=dl-quality]").forEach((el) => { el.checked = el.value === dlQuality; });
  $("#recommend-hot").checked = v.FNMUSIC_RECOMMEND_HOT === "true";
  $("#recommend-daily").checked = v.FNMUSIC_RECOMMEND_DAILY === "true";
  $("#public-playlists-enabled").checked = v.FNMUSIC_PUBLIC_PLAYLISTS_ENABLED === "true";
  try {
    publicSelection = JSON.parse(v.FNMUSIC_PUBLIC_PLAYLISTS_SELECTION || "[]");
    if (!Array.isArray(publicSelection)) publicSelection = [];
  } catch (_) { publicSelection = []; }
  $("#public-playlists-browser").hidden = !$("#public-playlists-enabled").checked;
  renderPublicSelected();
  // 刷新/保存后重建的 publicSelection 只有身份字段：浏览器可见时补回展示名称
  void ensurePublicSelectionDetails();
  $("#tee-enabled").checked = v.FNMUSIC_TEE_SAVE_ENABLED === "true";
  $("#auto-cover").checked = v.FNMUSIC_AUTO_COVER !== "false";
  $("#lyric-auto-dl").checked = v.FNMUSIC_LYRIC_AUTO_DL === "true";
  $("#fav-autobind").checked = v.FNMUSIC_FAV_AUTO_BIND === "true";
  $("#dir-download").value = v.FNMUSIC_TEE_SAVE_DIR || "";
  $("#dir-cache").value = v.FNMUSIC_CACHE_DIR || "";
  $("#tee-max").value = v.FNMUSIC_TEE_CACHE_MAX || "2";
  $("#bind-timeout").value = v.FNMUSIC_OFFICIAL_BIND_TIMEOUT_S || "120";
  $("#handoff-max").value = v.FNMUSIC_TEE_HANDOFF_MAX != null ? v.FNMUSIC_TEE_HANDOFF_MAX : "3";
  $("#scan-path").value = v.FNMUSIC_LIBRARY_SCAN_PATH || "";
  updateTeeCountLabel();
  updateBindTimeoutLabel();
  $("#llm-base").value = v.FNMUSIC_LLM_BASE_URL || "";
  $("#llm-key").value = v.FNMUSIC_LLM_API_KEY || "";
  $("#llm-model").value = v.FNMUSIC_LLM_MODEL || "";
  $("#llm-protocol").value = v.FNMUSIC_LLM_PROTOCOL === "responses" ? "responses" : "chat";
  $("#llm-max-tokens").value = v.FNMUSIC_LLM_MAX_TOKENS || "8192";
  $("#llm-daily-limit").value = v.FNMUSIC_LLM_DAILY_TOKEN_LIMIT ?? "1000000";
  $("#llm-extra-body").value = v.FNMUSIC_LLM_EXTRA_BODY || "";
  $("#llm-liked").checked = v.FNMUSIC_LLM_LIKED_ENABLED === "true";
  $("#llm-daily").checked = v.FNMUSIC_LLM_DAILY_ENABLED === "true";
  $("#liked-refresh-hours").value = v.FNMUSIC_LIKED_REFRESH_HOURS ?? "0.5";
  $("#liked-size").value = v.FNMUSIC_LIKED_SIZE ?? "20";
  updateLlmLikedSettings();
  refreshLlmUsage();
  $("#search-timeout").value = v.FNMUSIC_SEARCH_TIMEOUT || "15";
  $("#search-deep").checked = v.FNMUSIC_SEARCH_DEEP_PAGE !== "false";
  $("#search-deep-max").value = v.FNMUSIC_SEARCH_DEEP_MAX_PAGES || "10";
  $("#netease-my-playlists").checked = v.FNMUSIC_NETEASE_MY_PLAYLISTS === "true";
  $("#netease-playlist-resident").checked = v.FNMUSIC_NETEASE_PLAYLIST_RESIDENT === "true";
  $("#lx-url").value = "";
  lxVerifiedUrl = null;
  try {
    const rawList = v.LX_SOURCE_LIST;
    lxSourceList = rawList ? JSON.parse(rawList) : [];
    if (!Array.isArray(lxSourceList)) lxSourceList = [];
  } catch (_) {
    lxSourceList = [];
  }
  // 兼容旧数据：列表无任何 active 标记时按 LX_SOURCE_URL 推导
  const savedUrl = (v.LX_SOURCE_URL || "").trim();
  if (lxSourceList.length && !lxSourceList.some((item) => "active" in item)) {
    lxSourceList.forEach((item) => { item.active = !!(savedUrl && item.url === savedUrl); });
  }
  // .env 里有激活地址但不在列表中（异常状态）：补进列表展示真实激活态
  if (savedUrl && !lxSourceList.some((item) => item.url === savedUrl)) {
    lxSourceList.unshift({ name: lxDefaultName(savedUrl), url: savedUrl, active: true });
  }
  renderLxSourceList();
  syncLxAddButton();
  renderPlatformChips();
}

function collectConfig() {
  const provider = ($$("input[name=provider]").find((el) => el.checked) || {}).value || "";
  const values = {
    FNMUSIC_MUSICDL_ENABLED: provider === "musicdl",
    FNMUSIC_NETEASE_ENABLED: provider === "musicbox",
    FNMUSIC_LX_ENABLED: provider === "lxmusic",
    FNMUSIC_QUALITY_MODE: ($$("input[name=quality]").find((el) => el.checked) || {}).value || "high",
    FNMUSIC_DL_QUALITY: ($$("input[name=dl-quality]").find((el) => el.checked) || {}).value || "app",
    FNMUSIC_RECOMMEND_HOT: $("#recommend-hot").checked,
    FNMUSIC_RECOMMEND_DAILY: $("#recommend-daily").checked,
    FNMUSIC_PUBLIC_PLAYLISTS_ENABLED: $("#public-playlists-enabled").checked,
    FNMUSIC_TEE_SAVE_ENABLED: $("#tee-enabled").checked,
    FNMUSIC_AUTO_COVER: $("#auto-cover").checked,
    FNMUSIC_LYRIC_AUTO_DL: $("#lyric-auto-dl").checked,
    FNMUSIC_FAV_AUTO_BIND: $("#fav-autobind").checked,
    FNMUSIC_TEE_SAVE_DIR: $("#dir-download").value.trim(),
    FNMUSIC_CACHE_DIR: $("#dir-cache").value.trim(),
    FNMUSIC_TEE_CACHE_MAX: parseInt($("#tee-max").value || "2", 10),
    FNMUSIC_OFFICIAL_BIND_TIMEOUT_S: parseInt($("#bind-timeout").value || "120", 10) || 120,
    FNMUSIC_TEE_HANDOFF_MAX: parseInt($("#handoff-max").value || "3", 10) || 0,
    FNMUSIC_LIBRARY_SCAN_PATH: $("#scan-path").value.trim(),
    FNMUSIC_LLM_BASE_URL: $("#llm-base").value.trim(),
    FNMUSIC_LLM_API_KEY: $("#llm-key").value.trim(),
    FNMUSIC_LLM_MODEL: $("#llm-model").value.trim(),
    FNMUSIC_LLM_PROTOCOL: $("#llm-protocol").value === "responses" ? "responses" : "chat",
    FNMUSIC_LLM_MAX_TOKENS: parseInt($("#llm-max-tokens").value || "8192", 10) || 8192,
    FNMUSIC_LLM_DAILY_TOKEN_LIMIT: parseInt($("#llm-daily-limit").value ?? "1000000", 10),
    FNMUSIC_LLM_EXTRA_BODY: $("#llm-extra-body").value.trim(),
    FNMUSIC_LLM_LIKED_ENABLED: $("#llm-liked").checked,
    FNMUSIC_LLM_DAILY_ENABLED: $("#llm-daily").checked,
    FNMUSIC_SEARCH_TIMEOUT: parseInt($("#search-timeout").value || "15", 10) || 15,
    FNMUSIC_SEARCH_DEEP_PAGE: $("#search-deep").checked,
    FNMUSIC_SEARCH_DEEP_MAX_PAGES: parseInt($("#search-deep-max").value || "10", 10) || 10,
    FNMUSIC_NETEASE_MY_PLAYLISTS: $("#netease-my-playlists").checked,
    FNMUSIC_NETEASE_PLAYLIST_RESIDENT: $("#netease-playlist-resident").checked,
    LX_SOURCE_LIST: JSON.stringify(lxSourceList),
  };
  // 隐藏的参数不提交：关闭用途不清空已保存值，也不让隐藏的无效编辑阻塞保存。
  if (values.FNMUSIC_LLM_LIKED_ENABLED) {
    values.FNMUSIC_LIKED_REFRESH_HOURS = likedNumber("#liked-refresh-hours", "猜你喜欢生成间隔", 0.5, null);
    values.FNMUSIC_LIKED_SIZE = likedNumber("#liked-size", "猜你喜欢歌曲数量", 0, 100, true);
  }
  if ($("#public-playlists-enabled").checked || publicSelection.length || configPresence.FNMUSIC_PUBLIC_PLAYLISTS_SELECTION) {
    values.FNMUSIC_PUBLIC_PLAYLISTS_SELECTION = JSON.stringify(
      publicSelection.map(({ provider, source, kind, id }) => ({ provider, source, kind, id }))
    );
  }
  const dlDir = values.FNMUSIC_TEE_SAVE_DIR;
  const cacheDir = values.FNMUSIC_CACHE_DIR;
  for (const [label, p] of [["下载目录", dlDir], ["歌曲缓存目录", cacheDir]]) {
    if (p && (!p.startsWith("/") || p.split("/").includes(".."))) {
      throw new Error(label + "必须是以 / 开头的绝对路径，且不能包含 ..");
    }
  }
  if (dlDir && cacheDir && (dlDir === cacheDir || dlDir.startsWith(cacheDir + "/") || cacheDir.startsWith(dlDir + "/"))) {
    throw new Error("歌曲缓存目录与下载目录不能相同或互为父子目录");
  }
  if (provider === "musicdl") {
    values.FNMUSIC_ONLINE_SOURCES = platforms.enabled.join(",");
    values.MUSICDL_SOURCES = platforms.enabled.join(",");
  }
  if (provider === "lxmusic") {
    let savedAllInactive = false;
    try {
      const saved = JSON.parse(configValues.LX_SOURCE_LIST || "[]");
      savedAllInactive = configValues.FNMUSIC_LX_ENABLED === "true"
        && saved.length > 0 && saved.every((i) => i.active === false)
        && !(configValues.LX_SOURCE_URL || "").trim();
    } catch (_) { /* 忽略 */ }
    if (!lxSourceList.some((item) => item.active) && !savedAllInactive) {
      throw new Error("请至少激活一个洛雪源");
    }
    // LX_SOURCE_URL 由后端按第一个激活项派生写入
  }
  return values;
}

async function saveConfig() {
  let values;
  try { values = collectConfig(); } catch (exc) { toast(exc.message, "fail"); return; }
  // 目录门禁：变更过的目录先经宿主网关校验可写，不可写直接阻断保存
  // （直连 8774 等校验不可用的环境降级放行，不阻塞配置保存）
  for (const [key, label] of [["FNMUSIC_TEE_SAVE_DIR", "下载目录"], ["FNMUSIC_CACHE_DIR", "歌曲缓存目录"]]) {
    const next = values[key] || "";
    if (next && next !== (configValues[key] || "")) {
      const res = await fsCheckDir(next);
      if (res.mode === "fail") { toast(label + "不可用：" + res.message, "fail"); return; }
    }
  }
  const btn = $("#save-btn");
  btn.disabled = true;
  // 新激活的源保存时要逐个端到端校验（每源可达 1-2 分钟），给出预期提示
  const pendingNew = lxPendingActivationCount();
  btn.textContent = pendingNew > 0
    ? `保存中（校验 ${pendingNew} 个新激活源，可能需要 1-2 分钟）…`
    : "保存中…";
  try {
    const result = await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    const failed = (result.actions || []).filter((a) => !a.ok && !a.auto_disabled);
    const warnings = result.warnings || [];
    const parts = [];
    if (result.changed && result.changed.length) parts.push(`已保存 ${result.changed.length} 项`);
    else if (warnings.length) parts.push("配置已保存");
    (result.actions || []).forEach((a) => {
      if (a.kind === "process") parts.push(`进程 ${a.program} ${a.op} ${a.ok ? "成功" : "失败"}`);
      if (a.kind === "resident" && a.ok) {
        parts.push(a.op === "start" ? "网易云歌单常驻进程已启动" : "网易云歌单常驻进程已停止");
      }
      if (a.kind === "lx_activate" && !a.auto_disabled) {
        const label = a.source ? `「${a.source}」` : "";
        const op = a.op === "deactivate" ? "取消激活" : "激活";
        parts.push(`洛雪源${label}${op}${a.ok ? "成功" : "失败"}`);
      }
    });
    warnings.forEach((w) => {
      if (w.category === "service_unavailable") {
        parts.push(`洛雪源「${w.source || lxDefaultName(w.url)}」服务暂不可用，激活配置已保留${w.message ? `（${w.message}）` : ""}`);
      } else {
        parts.push(`洛雪源「${w.source || lxDefaultName(w.url)}」失效，无法激活，已自动取消激活${w.message ? `（${w.message}）` : ""}`);
      }
    });
    if (failed.length) {
      toast((parts.join("；") || "") + ` —— ${failed.map((f) => f.error).join("；")}`, "fail");
    } else {
      toast(parts.join("；") || "配置无变化", "ok");
    }
    clearDirty();
    await loadConfig();
    await loadStatus();
    refreshLlmUsage();
  } catch (exc) {
    toast("保存失败：" + exc.message, "fail");
  } finally {
    btn.disabled = false;
    btn.textContent = "保存并生效";
  }
}
$("#save-btn").addEventListener("click", saveConfig);

/* -------------------------------------------------------------- 音源选择 */
function savedProvider() {
  const v = configValues;
  return v.FNMUSIC_NETEASE_ENABLED === "true" ? "musicbox"
    : v.FNMUSIC_MUSICDL_ENABLED === "true" ? "musicdl"
    : v.FNMUSIC_LX_ENABLED === "true" ? "lxmusic" : "";
}

// 点选未启用的音源：临时拉起其进程供预览（不写配置；5 分钟内未保存自动停止）
async function startPreview(provider) {
  try {
    const r = await api("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    if (r.preview) toast(`已临时启动 ${PROVIDER_LABEL[provider]}（预览）：5 分钟内未保存将自动停止`);
    return true;
  } catch (exc) {
    toast(`临时启动 ${PROVIDER_LABEL[provider]} 失败：${exc.message}`, "fail");
    return false;
  }
}

function syncProviderPanels(provider) {
  $$(".provider-card").forEach((el) => el.classList.toggle("selected", el.dataset.provider === provider));
  $("#panel-musicbox").hidden = provider !== "musicbox";
  $("#panel-musicdl").hidden = provider !== "musicdl";
  $("#panel-lxmusic").hidden = provider !== "lxmusic";
}
$$("input[name=provider]").forEach((el) =>
  el.addEventListener("change", async () => {
    syncProviderPanels(el.value);
    if (el.value === "musicbox") syncNeteaseAccount();
    markDirty("音源切换需保存后生效");
    if (el.value && el.value !== savedProvider() && await startPreview(el.value)) {
      if (el.value === "musicdl") await loadPlatforms(false);
    }
  }));

/* -------------------------------------------------------------- musicdl 平台 */
function setPlatformFallback() {
  platforms = { enabled: (configValues.FNMUSIC_ONLINE_SOURCES || "").split(",").filter(Boolean), registered: [] };
  $("#platform-note").textContent = "musicdl 进程未运行，暂无法获取平台列表（点选 musicdl 音源可临时启动预览）";
  renderPlatformChips();
}

async function fetchPlatforms() {
  const data = await api("/api/platforms");
  platforms = { enabled: data.enabled || [], registered: data.registered || [] };
  $("#platform-note").textContent = `共 ${platforms.registered.length} 个注册平台，已启用 ${platforms.enabled.length} 个`;
  renderPlatformChips();
}

async function loadPlatforms(autoPreview = true) {
  try {
    await fetchPlatforms();
  } catch (_) {
    // 进程未运行：autoPreview（用户点选/刷新触发）时临时拉起后重试；页面加载不自动拉起
    if (!autoPreview) { setPlatformFallback(); return; }
    try {
      if (await startPreview("musicdl")) await fetchPlatforms();
      else setPlatformFallback();
    } catch (_) {
      setPlatformFallback();
    }
  }
}

function renderPlatformChips() {
  const keyword = $("#platform-search").value.trim().toLowerCase();
  const container = $("#platform-list");
  const enabledSet = new Set(platforms.enabled);
  const items = platforms.registered.filter((p) => !keyword || p.toLowerCase().includes(keyword));
  container.innerHTML = items.length
    ? items.map((p) => `<span class="chip ${enabledSet.has(p) ? "on" : ""}" data-platform="${p}">${p}</span>`).join("")
    : `<span class="muted">无匹配平台</span>`;
  container.querySelectorAll(".chip[data-platform]").forEach((chip) =>
    chip.addEventListener("click", () => {
      const p = chip.dataset.platform;
      const set = new Set(platforms.enabled);
      if (set.has(p)) { set.delete(p); chip.classList.remove("on"); }
      else { set.add(p); chip.classList.add("on"); }
      platforms.enabled = Array.from(set);
      markDirty("musicdl 平台已修改");
    }));
}
$("#platform-search").addEventListener("input", renderPlatformChips);
$("#platform-reload").addEventListener("click", loadPlatforms);

/* -------------------------------------------------------------- 公共歌单 */
const PUBLIC_PROVIDER_OPTIONS = [
  { value: "musicbox", label: "网易云音乐" },
  { value: "lxmusic", label: "洛雪内置歌单" },
];
const PUBLIC_SOURCE_LABEL = { netease: "网易", wy: "网易", kw: "酷我", kg: "酷狗", tx: "QQ", mg: "咪咕" };

function publicEntryKey(item) {
  return [item.provider, item.source, item.provider === "musicbox" ? "playlist" : item.kind, item.id].join("|");
}

function selectedPublicEntry(item) {
  return publicSelection.find((it) => publicEntryKey(it) === publicEntryKey(item));
}

function setPublicSelection(item, selected) {
  if (selected) {
    if (selectedPublicEntry(item)) return true;
    if (publicSelection.length >= 50) {
      toast("公共歌单最多选择 50 份", "fail");
      return false;
    }
    publicSelection.push({
      provider: item.provider, source: item.source, kind: item.kind, id: String(item.id),
      _name: item.name || "", _cover_url: item.cover_url || "", _track_count: item.track_count || 0,
    });
  } else {
    publicSelection = publicSelection.filter((it) => publicEntryKey(it) !== publicEntryKey(item));
  }
  renderPublicSelected();
  updateResidentAvailability();
  markDirty("公共歌单选择已修改");
  renderPublicList();
  return true;
}

function movePublicSelection(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= publicSelection.length) return;
  const items = publicSelection.splice(index, 1);
  publicSelection.splice(next, 0, items[0]);
  renderPublicSelected();
  markDirty("公共歌单顺序已修改");
}

function renderPublicSelected() {
  const box = $("#public-selected");
  if (!box) return;
  box.innerHTML = publicSelection.length ? publicSelection.map((item, index) => {
    // 浏览/默认榜单已提供的元数据同样进缓存，保存回填后无需依赖音源仍在运行。
    if (item._name) publicDetailCache.set(publicEntryKey(item), {
      name: item._name, cover_url: item._cover_url || "", track_count: item._track_count || 0,
    });
    // 名称是展示元数据不进配置：未取到时给出状态而不是把数字 ID 当标题
    const title = item._name ? escapeHtml(item._name)
      : item._detail_failed ? "名称获取失败" : "正在获取歌单名称…";
    return `
    <div class="public-selected">
      <img src="${escapeHtml(item._cover_url || "")}" alt="" ${item._cover_url ? "" : "hidden"}>
      <div class="min">
        <b>${title}</b>
        <span>${escapeHtml(PUBLIC_SOURCE_LABEL[item.source] || item.source)} · ${item.kind === "chart" ? "榜单" : "歌单"} · ${item.id}</span>
        ${item._reason ? `<small>${escapeHtml(item._reason)}</small>` : ""}
        ${Number(item._track_count || 0) > 1000 ? '<small>超过 1,000 首，仅载入前 1,000 首。</small>' : ""}
      </div>
      <div class="btn-row">
        ${item._detail_failed ? `<button class="btn small" data-public-retry="${index}">重试</button>` : ""}
        <button class="btn small" data-public-up="${index}" ${index === 0 ? "disabled" : ""}>↑</button>
        <button class="btn small" data-public-down="${index}" ${index === publicSelection.length - 1 ? "disabled" : ""}>↓</button>
        <button class="btn small danger" data-public-remove="${index}">移除</button>
      </div>
    </div>
  `; }).join("") : '<p class="hint">尚未选择歌单。</p>';
  box.querySelectorAll("[data-public-up]").forEach((btn) =>
    btn.addEventListener("click", () => movePublicSelection(Number(btn.dataset.publicUp), -1)));
  box.querySelectorAll("[data-public-down]").forEach((btn) =>
    btn.addEventListener("click", () => movePublicSelection(Number(btn.dataset.publicDown), 1)));
  box.querySelectorAll("[data-public-retry]").forEach((btn) =>
    btn.addEventListener("click", () => retryPublicDetail(Number(btn.dataset.publicRetry))));
  box.querySelectorAll("[data-public-remove]").forEach((btn) =>
    btn.addEventListener("click", () => {
      publicSelection.splice(Number(btn.dataset.publicRemove), 1);
      updateResidentAvailability();
      renderPublicSelected(); renderPublicList(); markDirty("公共歌单选择已修改");
    }));
}

function renderPublicList() {
  const box = $("#public-list");
  if (!box) return;
  box.innerHTML = publicState.items.length ? publicState.items.map((item) => {
    const checked = !!selectedPublicEntry(item);
    return `
      <label class="public-item">
        <input type="checkbox" data-public-id="${escapeHtml(String(item.id))}" ${checked ? "checked" : ""}>
        <img src="${escapeHtml(item.cover_url || "")}" alt="" ${item.cover_url ? "" : "hidden"}>
        <span class="min">
          <b>${escapeHtml(item.name || item.id)}</b>
          <small>${escapeHtml(item.author || PUBLIC_SOURCE_LABEL[item.source] || item.source)} · ${Number(item.track_count || 0)} 首</small>
        </span>
      </label>`;
  }).join("") : '<p class="hint">当前入口没有返回歌单。</p>';
  box.querySelectorAll("input[data-public-id]").forEach((input) => {
    input.addEventListener("change", () => {
      const item = publicState.items.find((it) => String(it.id) === input.dataset.publicId);
      if (item) setPublicSelection(item, input.checked);
    });
  });
}

async function publicApi(target, params = {}, retry = true, provider = publicState.provider) {
  // provider 显式传参：请求期间用户切换音源时，重试仍用发起时的音源，不会串台
  const query = new URLSearchParams({ provider, ...params });
  try {
    const data = await api(`/api/public-playlists/${target}?${query.toString()}`);
    if (data.ok === false) throw new Error(data.error || "歌单请求失败");
    return data;
  } catch (exc) {
    if (!retry) throw exc;
    if (!await startPreview(provider)) throw exc;
    return await publicApi(target, params, false, provider);
  }
}

function publicUiProviderOf(item) {
  return item.provider === "lx" ? "lxmusic" : "musicbox";
}

// 已选条目的展示元数据补齐：按条目自身的 provider 请求，不依赖当前浏览的音源；
// 配置只保存身份四字段，刷新/保存后靠这里把名称补回来。不经过 startPreview
// 重试——不为取名称单独拉起另一个音源的进程，失败交给手动重试。
async function fetchPublicItemDetail(item) {
  const query = new URLSearchParams({
    provider: publicUiProviderOf(item), source: item.source, kind: item.kind,
  });
  const data = await api(`/api/public-playlists/${encodeURIComponent(item.id)}?${query.toString()}`);
  if (data.ok === false) throw new Error(data.error || "歌单详情请求失败");
  return data;
}

function applyPublicDetailCache(item) {
  const cached = publicDetailCache.get(publicEntryKey(item));
  if (!cached) return false;
  if (cached.failed) {
    if (Date.now() - cached.failedAt < 30000) { item._detail_failed = true; return true; }
    publicDetailCache.delete(publicEntryKey(item));
    return false;
  }
  item._name = cached.name || "";
  item._cover_url = cached.cover_url || "";
  item._track_count = cached.track_count || 0;
  item._detail_failed = false;
  return true;
}

async function ensurePublicSelectionDetails() {
  const browser = $("#public-playlists-browser");
  if (!browser || browser.hidden) return;
  const seq = ++publicSelectionDetailSeq;
  for (const item of publicSelection) {
    if (seq !== publicSelectionDetailSeq) return;
    if (item._name) continue;
    if (applyPublicDetailCache(item)) { renderPublicSelected(); continue; }
    item._detail_failed = false;
    renderPublicSelected();
    try {
      const response = await fetchPublicItemDetail(item);
      if (seq !== publicSelectionDetailSeq) return;
      const detail = response.data || {};
      publicDetailCache.set(publicEntryKey(item), {
        name: detail.name || "", cover_url: detail.cover_url || "", track_count: detail.track_count || 0,
      });
    } catch (_) {
      if (seq !== publicSelectionDetailSeq) return;
      publicDetailCache.set(publicEntryKey(item), { failed: true, failedAt: Date.now() });
    }
    // 等待期间条目可能被移除/重排：按身份键重新定位后再落值
    const target = publicSelection.find((it) => publicEntryKey(it) === publicEntryKey(item));
    if (target) applyPublicDetailCache(target);
    renderPublicSelected();
  }
}

async function retryPublicDetail(index) {
  const item = publicSelection[index];
  if (!item) return;
  publicDetailCache.delete(publicEntryKey(item));
  delete item._detail_failed;
  renderPublicSelected();
  await ensurePublicSelectionDetails();
}

function annotatePublicSelectionAvailability() {
  const browsingProvider = publicState.provider;
  for (const item of publicSelection) {
    if (publicUiProviderOf(item) !== browsingProvider) continue;
    const cap = ((publicCapabilities || {}).sources || []).find((source) => source.id === item.source);
    item._reason = cap && !cap.playback_available ? cap.reason || "当前平台没有播放能力" : "";
  }
}

function currentUiProvider() {
  return ($$("input[name=provider]").find((el) => el.checked) || {}).value || savedProvider();
}

async function loadPublicCapabilities(seq = publicState.seq) {
  const provider = publicState.provider;
  try {
    const data = await publicApi("capabilities", {}, true, provider);
    if (seq !== publicState.seq || provider !== publicState.provider) return;
    publicCapabilities = data;
    // 配置回填可能早于预览启动；就绪后只清除本音源的瞬时失败。
    publicSelectionDetailSeq++;
    for (const item of publicSelection) {
      if (publicUiProviderOf(item) !== provider) continue;
      const key = publicEntryKey(item);
      if (publicDetailCache.get(key)?.failed) publicDetailCache.delete(key);
      delete item._detail_failed;
    }
  } catch (exc) {
    if (seq !== publicState.seq || provider !== publicState.provider) return;
    publicCapabilities = null;
    $("#public-note").textContent = "音源不可达：" + exc.message;
  }
  renderPublicControls();
  return publicCapabilities;
}

function renderPublicControls() {
  const providerSelect = $("#public-provider");
  const channelSelect = $("#public-channel");
  const sourceSelect = $("#public-source");
  providerSelect.innerHTML = PUBLIC_PROVIDER_OPTIONS.map((item) =>
    `<option value="${item.value}">${item.label}</option>`).join("");
  providerSelect.value = publicState.provider;

  const channels = publicState.provider === "musicbox" ? [
    { id: "charts", name: "排行榜" }, { id: "recommend", name: "推荐歌单" },
    { id: "top", name: "热门与分类" }, { id: "highquality", name: "精品歌单" },
    { id: "account_recommend", name: "账号推荐歌单" },
  ] : [{ id: "top", name: "热门与分类" }, { id: "charts", name: "排行榜" }];
  const logged = !!(((publicCapabilities || {}).sources || []).find((item) => item.id === "netease") || {}).logged_in;
  channelSelect.innerHTML = channels.filter((item) =>
    item.id !== "account_recommend" || logged).map((item) =>
    `<option value="${item.id}">${item.name}</option>`).join("");
  if (![...channelSelect.options].some((opt) => opt.value === publicState.channel)) {
    publicState.channel = publicState.provider === "musicbox" ? "charts" : "top";
  }
  channelSelect.value = publicState.channel;

  sourceSelect.hidden = publicState.provider !== "lxmusic";
  const sources = ((publicCapabilities || {}).sources || []).filter((item) =>
    publicState.provider !== "lxmusic" || item.playback_available);
  sourceSelect.innerHTML = sources.map((item) =>
    `<option value="${item.id}">${item.playback_available ? "" : "⚠ "}${escapeHtml(item.name)}</option>`).join("");
  if (!sources.some((item) => item.id === publicState.source)) publicState.source = sources[0]?.id || "";
  sourceSelect.value = publicState.source;
}

function fillPublicCategories(data) {
  publicState.cursor = "";
  publicState.cursors = { 1: "" };
  const select = $("#public-category");
  const groups = Array.isArray(data.categories) ? data.categories : [];
  select.innerHTML = '<option value="">全部分类</option>' + groups.map((group) => {
    const list = Array.isArray(group.list) ? group.list : [group];
    return `<optgroup label="${escapeHtml(group.name || "分类")}">${list.map((tag) =>
      `<option value="${escapeHtml(String(tag.id ?? tag.name ?? ""))}">${escapeHtml(tag.name || tag.id)}</option>`).join("")}</optgroup>`;
  }).join("");
  publicState.category = "";
  const sorts = Array.isArray(data.sorts) ? data.sorts : [];
  publicState.hasSorts = sorts.length > 0;
  $("#public-sort").hidden = !sorts.length || publicSearchActive();
  $("#public-sort").innerHTML = sorts.map((item) =>
    `<option value="${escapeHtml(String(item.id))}">${escapeHtml(item.name || item.id)}</option>`).join("");
  publicState.sort = sorts[0]?.id || "";
}

async function loadPublicCategories(seq = publicState.seq) {
  const { provider, source, channel } = publicState;
  if (seq !== publicState.seq) return;
  if (channel === "recommend" || channel === "account_recommend") {
    fillPublicCategories({ categories: [] });
    return;
  }
  try {
    const highquality = provider === "musicbox" && channel === "highquality";
    const data = await publicApi("categories", {
      source, kind: channel === "charts" ? "chart" : "playlist",
    }, true, provider);
    if (seq !== publicState.seq) return;
    fillPublicCategories(highquality ? { categories: data.highquality_tags || data.tags || [] } : data);
  } catch (exc) {
    if (seq !== publicState.seq) return;
    fillPublicCategories({ categories: [] });
    $("#public-note").textContent = "分类加载失败：" + exc.message;
  }
}

// 必须在 capabilities/categories 的 await 之前失效旧请求并阻止翻页。
function invalidatePublicBrowse() {
  const seq = ++publicState.seq;
  publicState.loading = true;
  publicState.bulk = null;
  publicState.items = []; publicState.hasMore = false;
  renderPublicList(); renderPublicPage();
  return seq;
}

async function reloadPublicBrowser(withCapabilities = false) {
  const seq = invalidatePublicBrowse();
  if (withCapabilities) {
    await loadPublicCapabilities(seq);
    if (seq !== publicState.seq) return;
  }
  await loadPublicCategories(seq);
  if (seq !== publicState.seq) return;
  await loadPublicList(seq);
  if (seq !== publicState.seq) return;
  annotatePublicSelectionAvailability();
  renderPublicSelected();
  void ensurePublicSelectionDetails();
  return seq;
}

function publicSearchActive() {
  return !!publicState.keyword;
}

// 只有按上游分页推进的入口才提供"加载剩余"；排行榜/推荐是一次性有限目录
function paginatedPublicChannel() {
  return publicSearchActive() || publicState.channel === "top" || publicState.channel === "highquality";
}

function publicListRequestParams(page, state = publicState) {
  const params = {
    source: state.source,
    channel: state.keyword ? "search" : state.channel,
    keyword: state.keyword,
    category: state.category, sort: state.sort, page,
  };
  if (!state.keyword && state.channel === "highquality") {
    params.cursor = (state.cursors || {})[page] || "";
  }
  return params;
}

function publicItemsFromResponse(data, state) {
  return (data.items || []).map((item) => ({
    ...item,
    provider: state.provider === "musicbox" ? "musicbox" : "lx",
    source: state.source,
    kind: !state.keyword && state.channel === "charts" ? "chart" : "playlist",
  }));
}

function publicRawCount(data) {
  return Math.max((data.items || []).length, Number(data.raw_count) || 0);
}

function syncPublicSearchUi() {
  const searching = publicSearchActive();
  const clearBtn = $("#public-search-clear");
  if (clearBtn) clearBtn.hidden = !searching;
  const category = $("#public-category");
  if (category) category.hidden = searching;
  const sort = $("#public-sort");
  if (sort) sort.hidden = searching || !publicState.hasSorts;
}

function updatePublicNote() {
  const note = $("#public-note");
  if (!note || publicState.bulk) return; // 批量加载的进度文案由加载循环维护
  const suffix = publicState.total ? `，接口报告 ${publicState.total} 份` : "";
  note.textContent = publicSearchActive()
    ? `搜索“${publicState.keyword}”：返回 ${publicState.items.length} 份${suffix}。`
    : `返回 ${publicState.items.length} 份${suffix}；勾选后保存生效。`;
}

async function loadPublicList(seq = invalidatePublicBrowse()) {
  if (seq !== publicState.seq) return;
  const state = { ...publicState };
  const params = publicListRequestParams(state.page, state);
  syncPublicSearchUi();
  $("#public-note").textContent = state.keyword ? `正在搜索“${state.keyword}”…` : "正在加载歌单…";
  try {
    const data = await publicApi("", params, true, state.provider);
    if (seq !== publicState.seq) return; // 请求期间条件已变化，丢弃过期响应
    publicState.items = publicItemsFromResponse(data, state);
    publicState.hasMore = !!data.has_more && publicRawCount(data) > 0;
    publicState.total = Number(data.total || 0);
    publicState.cursor = data.next_cursor || "";
    if (params.channel === "highquality") {
      publicState.cursors = publicState.cursors || { 1: "" };
      publicState.cursors[state.page + 1] = data.next_cursor || "";
      if (!data.next_cursor || data.next_cursor === params.cursor) publicState.hasMore = false;
    }
    updatePublicNote();
  } catch (exc) {
    if (seq !== publicState.seq) return;
    publicState.items = []; publicState.hasMore = false;
    $("#public-note").textContent = (state.keyword ? "搜索失败：" : "歌单加载失败：") + exc.message;
  } finally {
    if (seq === publicState.seq) {
      publicState.loading = false;
      renderPublicList(); renderPublicPage();
    }
  }
}

// 单次"加载剩余"连续翻页的安全上限：按上游真实尽头推进，但避免无界请求串；
// 触顶后按钮仍在，可再次点击继续
const PUBLIC_BULK_MAX_PAGES = 500;

async function loadPublicRest() {
  if (publicState.loading) return;
  if (publicState.bulk?.active) { publicState.bulk.abort = true; return; }
  if (!publicState.hasMore || !paginatedPublicChannel()) return;
  const seq = publicState.seq;
  const state = { ...publicState, cursors: { ...publicState.cursors } };
  const bulk = {
    active: true, abort: false, basePage: publicState.bulk?.basePage || state.page, pages: 0,
    seen: new Set(publicState.items.map((it) => publicEntryKey(it))),
  };
  publicState.bulk = bulk;
  renderPublicPage();
  $("#public-note").textContent = "正在加载剩余歌单…";
  let stopReason = "";
  try {
    while (!bulk.abort && bulk.pages < PUBLIC_BULK_MAX_PAGES) {
      const params = publicListRequestParams(state.page + 1, state);
      const data = await publicApi("", params, true, state.provider);
      if (seq !== publicState.seq || publicState.bulk !== bulk) return; // 条件已切换
      state.page += 1;
      publicState.page = state.page;
      bulk.pages += 1;
      const rows = publicItemsFromResponse(data, state);
      let added = 0;
      for (const row of rows) {
        const key = publicEntryKey(row);
        if (bulk.seen.has(key)) continue;
        bulk.seen.add(key);
        publicState.items.push(row);
        added += 1;
      }
      publicState.hasMore = !!data.has_more;
      publicState.total = Number(data.total || 0);
      if (params.channel === "highquality") {
        state.cursors[state.page + 1] = data.next_cursor || "";
        publicState.cursors = { ...state.cursors };
        if (!data.next_cursor || data.next_cursor === params.cursor) publicState.hasMore = false;
      }
      renderPublicList();
      renderPublicPage();
      $("#public-note").textContent = `正在加载剩余歌单：已累计 ${publicState.items.length} 份（第 ${bulk.basePage}–${publicState.page} 页）…`;
      if (!publicState.hasMore) { stopReason = "done"; break; }
      if (!publicRawCount(data) || (rows.length && added === 0)) {
        // 真空页或整页重复才判停；非空原始窗口被过滤后仍按原生分页推进。
        publicState.hasMore = false;
        stopReason = "stalled"; break;
      }
    }
    if (bulk.abort) stopReason = "stopped";
    else if (!stopReason && bulk.pages >= PUBLIC_BULK_MAX_PAGES) stopReason = "capped";
  } catch (exc) {
    if (seq === publicState.seq && publicState.bulk === bulk) {
      // 失败≠取尽：页码在成功响应后才推进，重试会重新请求失败的那一页
      publicState.hasMore = true;
      $("#public-note").textContent = `加载中断：${exc.message}。已累计 ${publicState.items.length} 份，可点击“加载剩余歌单”重试。`;
    }
  } finally {
    if (publicState.bulk === bulk) {
      bulk.active = false;
      renderPublicPage();
      if (stopReason === "done") {
        $("#public-note").textContent = `已加载到尽头：第 ${bulk.basePage}–${publicState.page} 页共 ${publicState.items.length} 份${publicState.total ? `（接口报告 ${publicState.total} 份）` : ""}。`;
      } else if (stopReason === "stalled") {
        $("#public-note").textContent = `上游不再返回新歌单：已加载 ${publicState.items.length} 份（第 ${bulk.basePage}–${publicState.page} 页）。`;
      } else if (stopReason === "capped") {
        $("#public-note").textContent = `单次连续加载已达 ${PUBLIC_BULK_MAX_PAGES} 页：目前共 ${publicState.items.length} 份，如需继续可再次点击。`;
      } else if (stopReason === "stopped") {
        $("#public-note").textContent = `已停止加载：当前显示第 ${bulk.basePage}–${publicState.page} 页共 ${publicState.items.length} 份。`;
      }
    }
  }
}

function renderPublicPage() {
  $("#public-page").textContent = publicState.bulk
    ? `第 ${publicState.bulk.basePage}–${publicState.page} 页`
    : String(publicState.page);
  const busy = publicState.loading || !!publicState.bulk?.active;
  $("#public-prev").disabled = publicState.page <= 1 || busy;
  $("#public-next").disabled = !publicState.hasMore || busy;
  const loadRest = $("#public-load-rest");
  if (loadRest) {
    const bulkBusy = !!publicState.bulk?.active;
    loadRest.hidden = publicState.loading || !(paginatedPublicChannel() && (publicState.hasMore || bulkBusy));
    loadRest.disabled = !!publicState.loading;
    loadRest.textContent = bulkBusy ? "停止加载" : "加载剩余歌单";
  }
}

async function initPublicDefaults() {
  const provider = currentUiProvider() === "lxmusic" ? "lx" : "musicbox";
  if (provider === "musicbox") {
    publicSelection = [
      { provider: "musicbox", source: "netease", kind: "chart", id: "3778678", _name: "热歌榜" },
      { provider: "musicbox", source: "netease", kind: "chart", id: "3779629", _name: "新歌榜" },
      { provider: "musicbox", source: "netease", kind: "chart", id: "19723756", _name: "飙升榜" },
      { provider: "musicbox", source: "netease", kind: "chart", id: "2884035", _name: "原创榜" },
    ];
    renderPublicSelected();
    markDirty("已填入默认网易榜单");
    return;
  }
  publicState.provider = "lxmusic";
  await loadPublicCapabilities();
  const caps = publicCapabilities || {};
  const wanted = [
    { source: "kg", label: "TOP500" }, { source: "kw", label: "飙升榜" }, { source: "wy", label: "新歌榜" },
  ];
  publicSelection = [];
  for (const target of wanted) {
    const cap = (caps.sources || []).find((item) => item.id === target.source && item.playback_available);
    if (!cap) continue;
    publicState.source = target.source; publicState.channel = "charts";
    try {
      const data = await publicApi("", { source: target.source, channel: "charts" });
      const row = (data.items || []).find((item) => String(item.name || "").includes(target.label));
      if (row) publicSelection.push({
        provider: "lx", source: target.source, kind: "chart", id: String(row.id),
        _name: row.name || "", _cover_url: row.cover_url || "", _track_count: row.track_count || 0,
      });
    } catch (_) { /* 平台不可用则跳过 */ }
  }
  renderPublicSelected();
  markDirty("已按可用平台填入默认洛雪榜单");
}

async function showPublicBrowser() {
  $("#public-playlists-browser").hidden = false;
  const saved = savedProvider();
  publicState.provider = currentUiProvider() === "lxmusic" ? "lxmusic" : "musicbox";
  const seq = await reloadPublicBrowser(true);
  if (seq === publicState.seq && saved === "musicdl") $("#public-note").textContent = "当前音源是 musicdl：它没有统一推荐歌单接口；可浏览网易/洛雪，保存后按常驻规则展示网易公共歌单。";
}

$("#public-playlists-enabled").addEventListener("change", async () => {
  const enabled = $("#public-playlists-enabled").checked;
  updateResidentAvailability();
  markDirty(enabled ? "公共歌单已开启" : "公共歌单已关闭");
  if (!enabled) {
    invalidatePublicBrowse();
    publicState.loading = false;
    publicSelectionDetailSeq++;
    $("#public-playlists-browser").hidden = true;
    return;
  }
  if (!publicSelection.length && !configPresence.FNMUSIC_PUBLIC_PLAYLISTS_SELECTION) await initPublicDefaults();
  await showPublicBrowser();
});
function runPublicSearch() {
  const keyword = $("#public-keyword").value.trim();
  if (!keyword) return clearPublicSearch();
  publicState.keyword = keyword;
  publicState.page = 1;
  return loadPublicList();
}

function clearPublicSearch() {
  publicState.keyword = "";
  $("#public-keyword").value = "";
  publicState.page = 1;
  publicState.cursors = { 1: "" }; publicState.cursor = "";
  return loadPublicList();
}

$("#public-search").addEventListener("click", runPublicSearch);
$("#public-keyword").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") { ev.preventDefault(); runPublicSearch(); }
});
$("#public-search-clear").addEventListener("click", clearPublicSearch);
// 浏览条件（音源/入口/平台/分类/排序/关键词/翻页）只影响浏览视图，不改保存的配置
$("#public-provider").addEventListener("change", async () => {
  publicState.provider = $("#public-provider").value;
  publicState.page = 1; publicState.channel = publicState.provider === "musicbox" ? "charts" : "top";
  await reloadPublicBrowser(true);
});
$("#public-channel").addEventListener("change", async () => {
  publicState.channel = $("#public-channel").value; publicState.page = 1;
  await reloadPublicBrowser();
});
$("#public-source").addEventListener("change", async () => {
  publicState.source = $("#public-source").value; publicState.page = 1;
  await reloadPublicBrowser();
});
$("#public-category").addEventListener("change", async () => {
  publicState.category = $("#public-category").value; publicState.page = 1;
  publicState.cursors = { 1: "" }; publicState.cursor = "";
  await loadPublicList();
});
$("#public-sort").addEventListener("change", async () => {
  publicState.sort = $("#public-sort").value; publicState.page = 1;
  await loadPublicList();
});
$("#public-refresh").addEventListener("click", async () => {
  publicState.page = 1; // 刷新=从头重新浏览，与其它条件切换一致
  await reloadPublicBrowser(true);
});
$("#public-load-rest").addEventListener("click", () => { void loadPublicRest(); });
$("#public-prev").addEventListener("click", async () => {
  if (publicState.page > 1 && !publicState.loading && !publicState.bulk?.active) {
    // 累计视图离开后，loadPublicList 清除范围并恢复目标页的普通单页视图。
    publicState.page--; await loadPublicList();
  }
});
$("#public-next").addEventListener("click", async () => {
  if (publicState.hasMore && !publicState.loading && !publicState.bulk?.active) {
    publicState.page++; await loadPublicList();
  }
});

/* -------------------------------------------------------------- 网易扫码 */
let neteaseLoggedIn = false;

function updateResidentAvailability() {
  // 账号歌单仍需登录；公共网易歌单可匿名常驻，因此仅在两者都无需求时置灰。
  const publicNeedsMusicbox = $("#public-playlists-enabled").checked
    && publicSelection.some((item) => item.provider === "musicbox");
  $("#netease-playlist-resident").disabled = !neteaseLoggedIn && !publicNeedsMusicbox;
}

async function syncNeteaseAccount(retries = 0) {
  // 把网易账号态同步到 #qr-check（已登录显示昵称；未登录/失败清空），
  // 同时驱动「网易云歌单常驻」复选框的可用态
  neteaseLoggedIn = false;
  updateResidentAvailability();
  for (let i = 0; ; i++) {
    try {
      const st = await api("/api/netease/auth/status");
      const d = st.data || st;
      if (d.logged_in) {
        neteaseLoggedIn = true;
        $("#qr-check").textContent = `当前登录：${d.nickname || d.user_id || "已登录用户"}`;
        break;
      }
    } catch (_) { /* status 不可达视为未登录 */ }
    if (i >= retries) break;
    await new Promise((r) => setTimeout(r, 1500));
  }
  updateResidentAvailability();
}

async function startQrLogin() {
  stopQrPolling();
  $("#qr-status").textContent = "正在生成二维码…";
  $("#qr-img").hidden = true;
  syncNeteaseAccount(); // 生成前同步当前账号态（非致命，不阻塞出码）
  const callLogin = () => api("/api/netease/auth/login", { method: "POST" });
  try {
    let data;
    try {
      data = await callLogin();
    } catch (exc) {
      // musicbox 进程未运行（未点选/预览过期）：临时拉起后重试一次
      if (!await startPreview("musicbox")) throw exc;
      data = await callLogin();
    }
    const unikey = data.unikey || data.codekey || (data.data && (data.data.unikey || data.data.codekey)) || "";
    if (!unikey) throw new Error("未获取到 unikey");
    $("#qr-img").src = `${APP_BASE}/api/netease/qr?unikey=${encodeURIComponent(unikey)}`;
    $("#qr-img").hidden = false;
    $("#qr-status").textContent = "请用手机网易云音乐 App 扫码";
    pollQr(unikey);
  } catch (exc) {
    $("#qr-status").textContent = "生成失败：" + exc.message;
  }
}

async function checkQrStatus(unikey) {
  const st = await api(`/api/netease/auth/login/check?unikey=${encodeURIComponent(unikey)}`);
  // musicbox CLI 返回 {ok, data:{code}} 信封结构；兼容扁平 {code}
  const code = st?.data?.code ?? st?.code;
  if (code === 803) {
    stopQrPolling();
    $("#qr-status").textContent = "登录成功 ✓";
    syncNeteaseAccount(2); // 803 后 cookie 落盘需要一点时间，带重试取昵称
    toast("网易账号登录成功", "ok");
  } else if (code === 802) {
    $("#qr-status").textContent = "已扫码，请在手机上确认";
  } else if (code === 800) {
    stopQrPolling();
    $("#qr-status").textContent = "二维码已过期，请重新生成";
  } else {
    $("#qr-status").textContent = "等待扫码…";
  }
}

function pollQr(unikey, intervalMs = 2000) {
  stopQrPolling();
  qrTimer = setInterval(() => { checkQrStatus(unikey).catch(() => {}); }, intervalMs);
}

function stopQrPolling() {
  if (qrTimer) { clearInterval(qrTimer); qrTimer = null; }
}
$("#qr-btn").addEventListener("click", startQrLogin);

$("#netease-playlist-resident").addEventListener("change", () => {
  // 常驻为账号/公共歌单供数；仅在没有公共选择时自动带出账号歌单开关。
  const publicNeedsMusicbox = publicSelection.some((item) => item.provider === "musicbox");
  if ($("#netease-playlist-resident").checked && !$("#netease-my-playlists").checked && !publicNeedsMusicbox) {
    $("#netease-my-playlists").checked = true;
  }
});

/* -------------------------------------------------------------- lx 源测试 */
function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function lxDefaultName(url) {
  if (!url) return "自定义源";
  try {
    const raw = url.split("?")[0].split("#")[0];
    const segment = raw.split("/").filter(Boolean).pop() || "lx-source";
    return decodeURIComponent(segment);
  } catch (_) {
    return "自定义源";
  }
}

function syncLxAddButton() {
  const url = ($("#lx-url")?.value || "").trim();
  const addBtn = $("#lx-add");
  if (addBtn) addBtn.disabled = !url;
}

// 已保存的激活 URL 集合（旧数据无 active 标记时回退 LX_SOURCE_URL 单值）
function lxSavedActiveUrls() {
  const saved = new Set();
  let explicitActives = false;
  try {
    const items = JSON.parse(configValues.LX_SOURCE_LIST || "[]");
    if (Array.isArray(items)) items.forEach((i) => {
      if (i && "active" in i) explicitActives = true;
      if (i && i.active && i.url) saved.add(i.url);
    });
  } catch (_) { /* 忽略 */ }
  if (!saved.size && !explicitActives) {
    const u = (configValues.LX_SOURCE_URL || "").trim();
    if (u) saved.add(u);
  }
  return saved;
}

function lxPendingActivationCount() {
  const saved = lxSavedActiveUrls();
  return lxSourceList.filter((i) => i.active && !saved.has(i.url)).length;
}

function renderLxSourceList() {
  const container = $("#lx-source-list");
  if (!container) return;

  if (!lxSourceList.length) {
    container.innerHTML = `<span class="muted pad">暂无洛雪音乐源，上传 .js 或填写脚本地址后点击「添加」加入列表</span>`;
    return;
  }

  container.innerHTML = lxSourceList.map((item, idx) => {
    const isActive = !!item.active;
    const displayName = escapeHtml(item.name || lxDefaultName(item.url));
    const safeUrl = escapeHtml(item.url);
    const btns = isActive
      ? `<button class="btn small lx-btn-deactivate" data-idx="${idx}">取消激活</button>
         <button class="btn small danger lx-btn-del" data-idx="${idx}">删除</button>`
      : `<button class="btn small lx-btn-activate" data-idx="${idx}">激活</button>
         <button class="btn small danger lx-btn-del" data-idx="${idx}">删除</button>`;
    return `
      <div class="lx-item${isActive ? " on" : ""}">
        <div class="lx-item-main">
          <div class="lx-item-header">
            <span class="lx-item-name">${displayName}</span>
            ${isActive ? `<span class="lx-badge">已激活</span>` : ""}
          </div>
          <div class="lx-item-url" title="${safeUrl}">${safeUrl}</div>
        </div>
        <div class="lx-item-btns">${btns}</div>
      </div>`;
  }).join("");

  container.querySelectorAll(".lx-btn-activate").forEach((btn) => {
    btn.addEventListener("click", () => activateLxSource(parseInt(btn.dataset.idx, 10)));
  });
  container.querySelectorAll(".lx-btn-deactivate").forEach((btn) => {
    btn.addEventListener("click", () => deactivateLxSource(parseInt(btn.dataset.idx, 10)));
  });
  container.querySelectorAll(".lx-btn-del").forEach((btn) => {
    btn.addEventListener("click", () => deleteLxSource(parseInt(btn.dataset.idx, 10)));
  });
}

function activateLxSource(idx) {
  const item = lxSourceList[idx];
  if (!item || item.active) return;
  item.active = true;
  renderLxSourceList();
  markDirty("已标记激活，需点击下方「保存并生效」（新激活源会先自动校验）");
  toast(`已标记激活「${item.name || lxDefaultName(item.url)}」，请点击下方「保存并生效」`, "ok");
}

function deactivateLxSource(idx) {
  const item = lxSourceList[idx];
  if (!item || !item.active) return;
  item.active = false;
  renderLxSourceList();
  markDirty("已标记取消激活，需点击下方「保存并生效」");
  toast(`已标记取消激活「${item.name || lxDefaultName(item.url)}」，保存后停用`, "ok");
}

function deleteLxSource(idx) {
  const item = lxSourceList[idx];
  if (!item) return;
  const name = item.name || lxDefaultName(item.url);
  lxSourceList.splice(idx, 1);
  renderLxSourceList();
  markDirty("洛雪源列表已修改，需保存后生效");
  toast(`已从列表移除「${name}」${item.active ? "（保存后将一并停用）" : ""}`, "ok");
}

$("#lx-test").addEventListener("click", async () => {
  const url = $("#lx-url").value.trim();
  const box = $("#lx-report");
  if (!url) { toast("请先填写源 URL", "fail"); return; }
  box.hidden = false;
  box.className = "report";
  box.textContent = "测试中（下载脚本 → 沙箱初始化 → 多首抽样搜索/解析/探活）…";
  const callVerify = () => api("/api/lx/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: { url } }),
  });
  try {
    const r = await callVerify();
    if (r.ok) {
      const d = r.data || {};
      const meta = d.meta || {};
      const probe = d.probe || {};
      lxVerifiedUrl = url;
      if (meta.name && $("#lx-name") && !$("#lx-name").value.trim()) {
        $("#lx-name").value = meta.name;
      }
      box.className = "report ok";
      box.innerHTML =
        `<div class="kv"><b>源名称</b>${meta.name || "-"} ${meta.version ? "v" + meta.version : ""}（${meta.author || "未知作者"}）</div>` +
        `<div class="kv"><b>可用平台</b>${(d.platforms || []).join("、")}</div>` +
        (probe.title ? `<div class="kv"><b>实测</b>${probe.title} - ${probe.artist} [${probe.platform}/${probe.quality}] ${probe.content_type || ""}</div>` : "") +
        `<div class="kv"><b>结论</b>可用 ✓（保存后激活）</div>`;
    } else {
      const d = r.data || {};
      box.className = "report fail";
      box.innerHTML = `<div class="kv"><b>不可用</b>${d.message || r.error || "校验失败"}</div>`;
    }
  } catch (exc) {
    box.className = "report fail";
    box.textContent = "测试失败：" + exc.message;
  }
});

/* -------------------------------------------------- lx 源：文件上传 / NAS 选择 */
async function lxUploadScript(filename, script) {
  return api("/api/lx/upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, script }),
  });
}

async function lxAfterUpload(r) {
  const d = r.data || {};
  $("#lx-url").value = d.url || "";
  lxVerifiedUrl = null; // 上传地址仍需走一次"测试"
  const scriptName = (d.meta && d.meta.name) || "";
  if (scriptName && $("#lx-name") && !$("#lx-name").value.trim()) {
    $("#lx-name").value = scriptName;
  }
  $("#lx-upload-note").textContent = scriptName ? `已上传：${scriptName}` : "已上传";
  syncLxAddButton();
  toast("脚本已上传，请点「测试」验证，确认可用后点「添加」加入列表", "ok");
}

$("#lx-upload").addEventListener("click", () => $("#lx-file").click());
$("#lx-file").addEventListener("change", async () => {
  const file = $("#lx-file").files && $("#lx-file").files[0];
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".js")) { toast("只支持 .js 后缀文件", "fail"); return; }
  if (file.size > 9_000_000) { toast("脚本超过 9MB 上限", "fail"); return; }
  $("#lx-upload-note").textContent = "读取并上传中…";
  try {
    const script = await file.text();
    const r = await lxUploadScript(file.name, script);
    await lxAfterUpload(r);
  } catch (exc) {
    $("#lx-upload-note").textContent = "";
    toast("上传失败：" + exc.message, "fail");
  } finally {
    $("#lx-file").value = ""; // 允许重复选择同一文件
  }
});

/* NAS 文件选择：仅桌面环境（统一网关 /app/fnmusic-ext 内）可用。
   洛雪源选中的主机路径经 /api/host-file 代读（webui_gateway 本地处理），再走上传落盘；
   储存目录选中即完成飞牛授权（pickUserFile directory 模式）。
   SDK 全站共享单例：加载失败（直连 8774）置 false，调用方自行降级。 */
let trimSdk = null;
async function loadTrimSdk() {
  if (trimSdk !== null) return trimSdk;
  try {
    const mod = await import("/app/fnmusic-ext/static/vendor/trim-web-app.js");
    trimSdk = new mod.TrimApp();
  } catch (_) {
    trimSdk = false;
  }
  return trimSdk;
}

async function lxPickFromNas() {
  const sdk = await loadTrimSdk();
  if (!sdk) { toast("当前环境不支持 NAS 文件选择（直连 8774 时请用上传或 URL）", "fail"); return; }
  try {
    const result = await sdk.pickUserFile({
      directory: false,
      accept: [".js"],
      title: "选择洛雪源脚本",
      okText: "选择",
      sidebarGroup: ["myFiles", "otherShare", "favorites"],
    });
    const paths = (result && result.data) || [];
    if (!paths.length) return;
    const hostPath = paths[0];
    $("#lx-upload-note").textContent = "读取 NAS 文件中…";
    const resp = await fetch(APP_BASE + "/api/host-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: hostPath }),
    });
    let body = {};
    try { body = await resp.json(); } catch (_) { /* 非 JSON */ }
    if (!resp.ok) throw new Error(body.error || body.detail || `HTTP ${resp.status}`);
    const filename = hostPath.split("/").pop() || "source.js";
    const r = await lxUploadScript(filename, body.script || "");
    await lxAfterUpload(r);
  } catch (exc) {
    $("#lx-upload-note").textContent = "";
    toast("NAS 选择失败：" + exc.message, "fail");
  }
}
$("#lx-pick").addEventListener("click", lxPickFromNas);

$("#lx-add").addEventListener("click", () => {
  const url = ($("#lx-url")?.value || "").trim();
  if (!url) {
    toast("请先填写脚本地址或上传 .js 文件", "fail");
    return;
  }
  const nameInput = ($("#lx-name")?.value || "").trim();
  const name = nameInput || lxDefaultName(url);

  const existingIdx = lxSourceList.findIndex((item) => item.url === url);
  if (existingIdx >= 0) {
    lxSourceList[existingIdx].name = name;
    toast(`已更新列表中该源的名称为「${name}」`, "ok");
  } else {
    lxSourceList.push({ name, url, active: false });
    toast(`已添加「${name}」至洛雪源列表，点其「激活」并保存后生效`, "ok");
  }
  $("#lx-url").value = "";
  if ($("#lx-name")) $("#lx-name").value = "";
  syncLxAddButton();
  renderLxSourceList();
  markDirty("洛雪源列表已修改，需保存后生效");
});

/* -------------------------------------------------- 储存目录：选择/授权/校验 */
const DIR_FIELDS = [
  { input: "#dir-cache", pick: "#dir-cache-pick", check: "#dir-cache-check", label: "歌曲缓存目录" },
  { input: "#dir-download", pick: "#dir-download-pick", check: "#dir-download-check", label: "下载目录" },
];
let _fsCheckSeq = 0;                // 防抖竞态：只采纳最后一次输入的校验结果
const _authorizedDirs = new Set();  // authorizeUserFile 每个路径只尝试一次，避免重复弹授权框

/* 目录权限校验：桌面链路由宿主机网关（root，真实落盘视角）拦截应答；
   直连 8774 时 WebUI 返回 501（容器内看不到宿主路径），降级为 skip 放行。 */
async function fsCheckDir(path) {
  try {
    const r = await api("/api/fs-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!r.writable) {
      return { mode: "fail", message: r.exists ? "目录不可写（权限不足或只读挂载）" : "目录无法创建（父目录不可写）" };
    }
    if (!r.exists) return { mode: "warn", message: "目录不存在；父目录可写，保存后自动创建" };
    return { mode: "ok", message: "目录可写 ✓" };
  } catch (exc) {
    return { mode: "skip", message: "目录校验不可用（" + exc.message + "），保存时不校验权限" };
  }
}

function renderDirCheck(el, res) {
  el.hidden = false;
  el.className = "dir-check " + res.mode;
  el.textContent = res.message;
}

/* 粘贴路径补一次官方授权（飞牛 userAccess；选择器选中的路径已自动授权，无需重复）。
   授权失败不阻断：代理以 root 落盘，网关 fs-check 才是真实写入视角。 */
async function authorizeDir(path) {
  if (_authorizedDirs.has(path)) return;
  _authorizedDirs.add(path);
  try {
    const sdk = await loadTrimSdk();
    if (sdk && typeof sdk.authorizeUserFile === "function") await sdk.authorizeUserFile(path);
  } catch (_) { /* 忽略 */ }
}

function bindDirField(field) {
  const input = $(field.input);
  let timer = null;
  const schedule = () => {
    markDirty();
    clearTimeout(timer);
    const seq = ++_fsCheckSeq;
    const path = input.value.trim();
    if (!path) { $(field.check).hidden = true; return; }
    timer = setTimeout(async () => {
      await authorizeDir(path);
      const res = await fsCheckDir(path);
      if (seq === _fsCheckSeq) renderDirCheck($(field.check), res);
    }, 500);
  };
  input.addEventListener("input", schedule);
  $(field.pick).addEventListener("click", async () => {
    const sdk = await loadTrimSdk();
    if (!sdk) { toast("当前环境不支持 NAS 目录选择（直连 8774 时请直接粘贴路径）", "fail"); return; }
    try {
      const result = await sdk.pickUserFile({
        directory: true, // 目录授权只支持单选（官方文档：multiple 也会按单目录处理）
        title: "选择" + field.label,
        okText: "选择",
        sidebarGroup: ["myFiles", "otherShare", "favorites"],
      });
      const paths = (result && result.data) || [];
      if (!paths.length) return;
      input.value = paths[0];
      _authorizedDirs.add(paths[0]); // 选择器选中即完成授权
      schedule();
    } catch (exc) {
      toast("NAS 目录选择失败：" + exc.message, "fail");
    }
  });
}
DIR_FIELDS.forEach(bindDirField);

(async function detectNasPicker() {
  // 桌面网关路径下才尝试加载 SDK；探测失败（直连 8774）保持隐藏
  const pathname = (typeof window !== "undefined" && window.location && window.location.pathname) || "";
  if (pathname.startsWith("/app/")) {
    const sdk = await loadTrimSdk();
    if (sdk) {
      $("#lx-pick").hidden = false;
      DIR_FIELDS.forEach((f) => { $(f.pick).hidden = false; });
    }
  }
})();

/* -------------------------------------------------------------- 表单脏标记 */
["#tee-max", "#llm-base", "#llm-key", "#llm-model", "#liked-refresh-hours", "#liked-size", "#search-timeout", "#search-deep-max", "#bind-timeout", "#handoff-max", "#scan-path"].forEach((sel) =>
  $(sel).addEventListener("input", () => markDirty()));
["#llm-liked", "#llm-daily"].forEach((sel) => $(sel).addEventListener("change", () => {
  updateLlmLikedSettings();
  markDirty();
}));
["#llm-base", "#llm-key"].forEach((sel) => $(sel).addEventListener("input", updateLlmLikedSettings));
$("#lx-url").addEventListener("input", () => {
  // #lx-url 仅为「添加」入口输入框，激活态由列表 active 标记决定，与它无关
  syncLxAddButton();
});
if ($("#lx-name")) {
  $("#lx-name").addEventListener("input", () => markDirty());
}
$$("input[name=quality]").forEach((el) => el.addEventListener("change", () => markDirty("音质偏好需保存后生效")));
$$("input[name=dl-quality]").forEach((el) => el.addEventListener("change", () => markDirty("下载音质需保存后生效")));
["#recommend-hot", "#recommend-daily", "#search-deep", "#tee-enabled", "#fav-autobind", "#auto-cover", "#lyric-auto-dl", "#netease-my-playlists", "#netease-playlist-resident"].forEach((sel) =>
  $(sel).addEventListener("change", () => markDirty()));

function updateTeeCountLabel() {
  const n = $("#tee-max").value || configValues.FNMUSIC_TEE_CACHE_MAX || "2";
  $("#tee-count-label").textContent = `（缓存数 ${n} 首）`;
}
$("#tee-max").addEventListener("input", updateTeeCountLabel);

function updateBindTimeoutLabel() {
  const n = $("#bind-timeout").value || configValues.FNMUSIC_OFFICIAL_BIND_TIMEOUT_S || "120";
  $("#bind-timeout-label").textContent = `（${n} 秒）`;
}
$("#bind-timeout").addEventListener("input", updateBindTimeoutLabel);

window.addEventListener("beforeunload", (ev) => {
  if (dirty) ev.preventDefault();
});

/* -------------------------------------------------------------- 关于页 */
function fmtBytes(n) {
  if (!Number.isFinite(n)) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function loadAbout() {
  const info = await api("/api/about");
  $("#about-version").textContent = `v${info.version}`;
  $("#about-deploy").textContent = info.deploy_mode === "native" ? "原生部署" : "Docker 部署";
  // 免责声明来自 README「免责与版权声明」小节；解析失败时后端返回空数组，隐藏卡片
  const card = $("#about-disclaimer-card");
  const list = $("#about-disclaimer");
  const items = Array.isArray(info.disclaimer) ? info.disclaimer : [];
  if (items.length) {
    card.hidden = false;
    list.innerHTML = items.map((d) =>
      `<li><b>${escapeHtml(d.title)}</b>${d.text ? "：" + escapeHtml(d.text) : ""}</li>`).join("");
  } else {
    card.hidden = true;
  }
  const logs = $("#about-logs");
  const files = Array.isArray(info.logs) ? info.logs : [];
  logs.innerHTML = files.length
    ? files.map((f) => `<span class="state-line"><span class="dot ok"></span>${escapeHtml(f.name)} · ${fmtBytes(f.size)}</span>`).join("")
    : '<span class="muted">暂无日志文件</span>';
  // 打赏二维码是打包时的可选资源（CI 从 ALIPAY_QRCODE 生成）；开源发布包里没有则不展示
  const donate = $("#about-donate");
  if (info.donate) {
    donate.hidden = false;
    $("#about-donate-img").src = `${APP_BASE}/static/alipay.png`;
  } else {
    donate.hidden = true;
  }
  // 进入关于页顺带刷新检查更新（后端有 TTL 缓存，命中不出网）
  checkUpdate();
}

async function exportLogs() {
  const btn = $("#log-export-btn");
  const note = $("#log-export-note");
  if (btn.disabled) return;
  btn.disabled = true;
  note.textContent = "打包中…";
  try {
    const resp = await fetch(APP_BASE + "/api/logs/export");
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try { msg = (await resp.json()).error || msg; } catch (_) { /* 非 JSON */ }
      throw new Error(msg);
    }
    const blob = await resp.blob();
    const m = (resp.headers.get("content-disposition") || "").match(/filename="?([^";]+)"?/);
    const name = m ? m[1] : `fnmusic-logs-${Date.now()}.logzip`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
    note.textContent = `已导出 ${name}`;
  } catch (exc) {
    note.textContent = "";
    toast("日志导出失败：" + exc.message, "fail");
  } finally {
    btn.disabled = false;
  }
}
$("#log-export-btn").addEventListener("click", () => { exportLogs().catch(() => {}); });

/* -------------------------------------------------------------- 检查更新 */
async function checkUpdate() {
  try {
    updateInfo = await api("/api/update-check");
    renderUpdateBadge();
  } catch (_) { /* 检查失败静默：红点只是提示，不打扰用户 */ }
}

// 有新版本则红点常亮（两处"关于"入口 + 关于页 NEW 标记），不因查看而清除
function renderUpdateBadge() {
  const has = !!(updateInfo && updateInfo.has_update);
  $$(".nav-dot").forEach((el) => { el.hidden = !has; });
  $("#about-new").hidden = !has;
}

function fmtDate(iso) {
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(String(iso || ""));
  return m ? m[1] : "-";
}

function openUpdateModal() {
  if (!updateInfo || !updateInfo.has_update) return;
  const latest = updateInfo.latest || {};
  $("#update-modal-version").textContent = `v${latest.version || updateInfo.current_version}`;
  $("#update-modal-date").textContent = fmtDate(latest.published_at);
  // release 说明来自外部仓库，textContent 注入防 XSS
  $("#update-modal-changelog").textContent = latest.changelog || "暂无更新说明";
  // 升级跳转跟随检查源：GitHub 查到跳 GitHub release 页，Gitee 查到跳 Gitee 发行版页
  $("#update-upgrade-btn").href = latest.html_url || "#";
  $("#update-modal").hidden = false;
}

function closeUpdateModal() {
  $("#update-modal").hidden = true;
}

$("#about-new").addEventListener("click", openUpdateModal);
$("#update-cancel-btn").addEventListener("click", closeUpdateModal);
$("#update-modal").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget) closeUpdateModal();  // 点遮罩空白处关闭
});

/* -------------------------------------------------------------- 垃圾清理 Beta */
let cleanupJob = null;
let cleanupSubmitting = false;
let cleanupPolling = false;
let cleanupTimer = null;

function formatCleanupBytes(size) {
  const value = Number(size);
  if (!Number.isFinite(value) || value < 0) return "0.0 KB";
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function renderCleanupJob(job) {
  const labels = { running: "清理中", completed: "清理完成", failed: "清理失败" };
  $("#cleanup-status").textContent =
    `任务 ${job.id} · ${labels[job.status]} · 阶段：${job.phase || "-"}` +
    ` · 已扫描 ${job.scanned ?? 0} · 已删除 ${job.removed ?? 0}` +
    ` · 释放 ${formatCleanupBytes(job.bytes ?? 0)} · 已跳过 ${job.skipped ?? 0}`;
  const lines = [];
  for (const [name, counts] of Object.entries(job.categories || {})) {
    lines.push(`${name}：扫描 ${counts.scanned || 0}，删除 ${counts.removed || 0}，释放 ${formatCleanupBytes(counts.bytes || 0)}，跳过 ${counts.skipped || 0}`);
  }
  for (const [key, label] of [["errors", "错误"], ["details", "详情"]]) {
    for (const item of (Array.isArray(job[key]) ? job[key] : [])) {
      lines.push(`${label}：${typeof item === "string" ? item : JSON.stringify(item)}`);
    }
  }
  // 文件名、错误及详情均不可信，绝不作为 HTML 插入。
  $("#cleanup-details").textContent = lines.join("\n");
  $("#cleanup-details").hidden = !lines.length;
  $("#cleanup-open-btn").textContent = job.status === "running" ? "查看当前清理任务" : "清理垃圾文件（Beta）";
}

function acceptCleanupJob(response) {
  const job = response && response.job;
  if (!response || response.ok !== true || !job || !job.id ||
      !["running", "completed", "failed"].includes(job.status)) {
    throw new Error("清理任务响应格式无效");
  }
  cleanupJob = job;
  renderCleanupJob(job);
  clearTimeout(cleanupTimer);
  cleanupTimer = null;
  if (job.status === "running") cleanupTimer = setTimeout(pollCleanupJob, 1000);
}

async function pollCleanupJob() {
  if (cleanupPolling || !cleanupJob || cleanupJob.status !== "running") return;
  clearTimeout(cleanupTimer);
  cleanupTimer = null;
  cleanupPolling = true;
  try {
    acceptCleanupJob(await api("/api/maintenance/cleanup/" + encodeURIComponent(cleanupJob.id)));
  } catch (exc) {
    $("#cleanup-status").textContent = `任务 ${cleanupJob.id} · 查询失败：${exc.message}；点击查看当前清理任务重试，不会重复提交。`;
  } finally {
    cleanupPolling = false;
  }
}

function closeCleanupModal() {
  $("#cleanup-modal").hidden = true;
  $("#cleanup-open-btn").focus();
}

function openCleanupModal() {
  if (cleanupSubmitting) return;
  if (cleanupJob && cleanupJob.status === "running") {
    renderCleanupJob(cleanupJob);
    pollCleanupJob();
    return;
  }
  $("#cleanup-modal").hidden = false;
  $("#cleanup-cancel-btn").focus();
}

async function startCleanup() {
  // 只有可见警告框中的确认才可提交；双击和在途提交均不创建新任务。
  if ($("#cleanup-modal").hidden || cleanupSubmitting) return;
  if (cleanupJob && cleanupJob.status === "running") {
    closeCleanupModal();
    renderCleanupJob(cleanupJob);
    return;
  }
  cleanupSubmitting = true;
  $("#cleanup-open-btn").disabled = true;
  closeCleanupModal();
  $("#cleanup-status").textContent = "正在提交清理任务…";
  $("#cleanup-details").textContent = "";
  $("#cleanup-details").hidden = true;
  try {
    acceptCleanupJob(await api("/api/maintenance/cleanup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_beta: true }),
    }));
  } catch (exc) {
    $("#cleanup-status").textContent = "清理请求失败：" + exc.message;
  } finally {
    cleanupSubmitting = false;
    $("#cleanup-open-btn").disabled = false;
  }
}

$("#cleanup-open-btn").addEventListener("click", openCleanupModal);
$("#cleanup-cancel-btn").addEventListener("click", closeCleanupModal);
$("#cleanup-confirm-btn").addEventListener("click", startCleanup);
$("#cleanup-modal").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget) closeCleanupModal();
});
window.addEventListener("keydown", (ev) => {
  if ($("#cleanup-modal").hidden) return;
  if (ev.key === "Escape") {
    ev.preventDefault();
    closeCleanupModal();
  } else if (ev.key === "Tab") {
    // 对话框仅有两个按钮，将键盘焦点限制在其中，默认落在取消。
    ev.preventDefault();
    const cancel = $("#cleanup-cancel-btn");
    (document.activeElement === cancel ? $("#cleanup-confirm-btn") : cancel).focus();
  }
});

/* -------------------------------------------------------------- 保存到 NAS */
const saveState = {
  enabled: false,
  statusLoaded: false,
  searching: false,
  saving: new Set(),
  searchSeq: 0,
};

async function manualSaveApi(path, options = {}) {
  const headers = Object.assign({}, options.headers || {}, { "x-fnmusic-save": "1" });
  const resp = await fetch(APP_BASE + path, Object.assign({}, options, { headers }));
  let body = {};
  try { body = await resp.json(); } catch (_) { /* 非 JSON 响应 */ }
  return { status: resp.status, ok: resp.ok, body };
}

function manualSaveMessage(body, fallback) {
  if (body && body.code === "conflict") return "目标目录已存在同名文件，未覆盖。";
  if (body && body.code === "busy") return "已有保存任务进行中，请稍后再试。";
  if (body && typeof body.message === "string" && body.message) return body.message;
  return fallback;
}

function formatSaveSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  return (value / (1024 * 1024)).toFixed(1) + " MB";
}

function formatSaveDuration(ms) {
  const totalSeconds = Math.floor(Number(ms) / 1000);
  if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) return "";
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return minutes + ":" + seconds;
}

function setSaveControlsEnabled(enabled) {
  const input = $("#save-keyword");
  const button = $("#save-search-btn");
  if (input) input.disabled = !enabled;
  if (button) button.disabled = !enabled;
}

async function loadSaveStatus() {
  const statusEl = $("#save-status");
  if (!statusEl) return;
  try {
    const { status, ok, body } = await manualSaveApi("/api/manual-save/status");
    const data = (body && body.data) || {};
    saveState.enabled = !!(ok && body && body.ok === true && data.enabled === true);
    saveState.statusLoaded = true;
    if (saveState.enabled) {
      const target = typeof data.target_dir === "string" && data.target_dir ? "，目标目录 " + data.target_dir : "";
      statusEl.textContent = "手动保存已启用" + target + "；保存不会覆盖同名文件。";
      setSaveControlsEnabled(true);
    } else if (body && body.code === "disabled") {
      statusEl.textContent = "手动保存未启用：请在 .env 设置 FNMUSIC_MANUAL_SAVE_ENABLED=true，并填写 FNMUSIC_MANUAL_SAVE_TARGET / FNMUSIC_MANUAL_SAVE_STAGING 后重启代理。";
      setSaveControlsEnabled(false);
      const results = $("#save-results");
      if (results) results.innerHTML = "";
      const resultEl = $("#save-result");
      if (resultEl) resultEl.textContent = "";
    } else {
      statusEl.textContent = "无法读取手动保存状态（HTTP " + status + "），请确认正通过飞牛桌面打开管理台。";
      setSaveControlsEnabled(false);
    }
  } catch (exc) {
    saveState.enabled = false;
    saveState.statusLoaded = true;
    statusEl.textContent = "读取手动保存状态失败：" + (exc && exc.message ? exc.message : "网络错误");
    setSaveControlsEnabled(false);
  }
  // 整歌单卡片：单曲开关判定后再读取歌单开关/列表（单曲开关关闭时本轮不会发任何
  // playlist 请求，见 loadPlaylistSaveStatus 的守卫）。歌单卡片失败不影响单曲保存。
  try { await loadPlaylistSaveStatus(); } catch (_) { /* 忽略：歌单卡片独立降级 */ }
}

function renderSaveResults(items) {
  const container = $("#save-results");
  if (!container) return;
  if (!items.length) {
    container.innerHTML = `<span class="muted pad">没有找到匹配的歌曲</span>`;
    return;
  }
  container.innerHTML = items.map((item) => {
    const title = escapeHtml(item.title || "未知标题");
    const meta = [item.artist, item.album, item.quality, formatSaveDuration(item.duration)]
      .filter((part) => typeof part === "string" && part)
      .map((part) => escapeHtml(part))
      .join(" · ");
    return `
      <div class="lx-item save-item">
        <div class="lx-item-main">
          <div class="lx-item-header"><span class="lx-item-name">${title}</span></div>
          <div class="lx-item-url">${meta || "未知歌手"}</div>
        </div>
        <div class="lx-item-btns">
          <button class="btn small save-track-btn" type="button" data-song-id="${Number(item.song_id) || 0}">保存</button>
          <span class="muted save-track-state"></span>
        </div>
      </div>`;
  }).join("");
  container.querySelectorAll(".save-track-btn").forEach((button) => {
    button.addEventListener("click", () => { void saveOneTrack(button); });
  });
}

async function searchSaveTracks() {
  if (!saveState.enabled || saveState.searching) return;
  const input = $("#save-keyword");
  const resultEl = $("#save-result");
  const keyword = input ? input.value.trim() : "";
  if (!keyword) {
    if (resultEl) resultEl.textContent = "请输入搜索关键词";
    return;
  }
  const seq = ++saveState.searchSeq;
  saveState.searching = true;
  const button = $("#save-search-btn");
  if (button) button.disabled = true;
  if (resultEl) resultEl.textContent = "搜索中…";
  try {
    const { ok, body } = await manualSaveApi(
      "/api/manual-save/search?keyword=" + encodeURIComponent(keyword),
    );
    if (seq !== saveState.searchSeq) return;
    if (!ok || !body || body.ok !== true) {
      if (resultEl) resultEl.textContent = "搜索失败：" + manualSaveMessage(body, "服务不可用");
      renderSaveResults([]);
      return;
    }
    const items = body.data && Array.isArray(body.data.items) ? body.data.items : [];
    if (resultEl) {
      resultEl.textContent = items.length
        ? "共找到 " + items.length + " 首，点击「保存」写入目标目录"
        : "没有找到匹配的歌曲";
    }
    renderSaveResults(items);
  } catch (exc) {
    if (seq === saveState.searchSeq && resultEl) {
      resultEl.textContent = "搜索失败：" + (exc && exc.message ? exc.message : "网络错误");
    }
  } finally {
    if (seq === saveState.searchSeq) {
      saveState.searching = false;
      if (button) button.disabled = !saveState.enabled;
    }
  }
}

async function saveOneTrack(button) {
  if (!saveState.enabled) return;
  const songId = Number(button.dataset.songId);
  if (!Number.isInteger(songId) || songId <= 0) return;
  if (saveState.saving.has(songId)) return;
  saveState.saving.add(songId);
  button.disabled = true;
  const row = button.closest(".save-item");
  const stateEl = row ? row.querySelector(".save-track-state") : null;
  if (stateEl) stateEl.textContent = "保存中…";
  const resultEl = $("#save-result");
  if (resultEl) resultEl.textContent = "正在保存，请保持页面打开…";
  try {
    const { body } = await manualSaveApi("/api/manual-save/track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ song_id: songId }),
    });
    if (body && body.ok === true && body.code === "ok") {
      const data = body.data || {};
      // 音质只展示服务端上报的原始 token：不翻译档位名，也不用请求档顶替未知档。
      const qualityParts = [];
      if (data.quality_actual_status === "reported" && data.quality_actual) {
        qualityParts.push("实际档位：" + data.quality_actual);
        if (data.quality_fallback === true || data.quality_actual !== data.quality_requested) {
          const requested = typeof data.quality_requested === "string" ? data.quality_requested : "";
          if (requested) qualityParts.push("请求档位：" + requested);
          qualityParts.push("回退");
        }
      } else if (data.quality_actual_status === "unknown") {
        qualityParts.push("档位未知");
      } else if (typeof data.quality_actual === "string" && data.quality_actual) {
        // 旧引擎没有 quality_actual_status：保持原有展示，不推断档位状态。
        qualityParts.push(data.quality_actual);
      } else {
        qualityParts.push("档位未知");
      }
      const parts = [
        ...qualityParts, data.format, formatSaveSize(data.size),
        data.basename, data.target_dir,
      ].filter((part) => typeof part === "string" && part);
      const coverLabels = {
        embedded: "封面已嵌入", preserved: "源封面已保留",
        unavailable: "音源未提供封面", unsupported: "封面格式不受 MP3 支持",
        error: "封面获取或嵌入失败",
      };
      // 封面失败只展示固定原因，绝不回显 host/URL。
      const coverErrorLabels = {
        host_untrusted: "封面域名不在已验证列表（p1/p2），已跳过抓取",
        address_untrusted: "封面地址未通过公网校验，已跳过抓取",
        url_invalid: "封面地址格式无效，已跳过抓取",
        http_error: "封面获取失败",
        payload_invalid: "封面内容无效",
        decode_failed: "封面解码失败",
        embed_failed: "封面嵌入失败",
      };
      const lyricLabels = {
        published: "歌词已保存为同名 LRC", unavailable: "音源未提供歌词",
        error: "歌词获取或保存失败", conflict: "同名 LRC 已存在，未覆盖",
        not_attempted: "歌词未处理", unknown: "歌词发布结果待核对",
      };
      if (data.cover_status === "error") {
        parts.push(coverErrorLabels[data.cover_error] || "封面获取或嵌入失败");
      } else {
        parts.push(coverLabels[data.cover_status] || "封面状态未知");
      }
      parts.push(lyricLabels[data.lyric_status] || "歌词状态未知");
      parts.push(data.artist_status === "tagged" ? "歌手标签已写入" : "音源未提供歌手");
      const lyricist = data.lyricist_status === "tagged" && data.lyricist
        ? "词：" + data.lyricist : "词：未提供";
      const composer = data.composer_status === "tagged" && data.composer
        ? "曲：" + data.composer : "曲：未提供";
      parts.push(lyricist + " / " + composer);
      const metadataFailed = data.cover_status === "error"
        || data.lyric_status === "error" || data.lyric_status === "unknown";
      const prefix = metadataFailed
        ? "音频已发布；部分元数据/歌词附加项失败（文件分开发布）："
        : "音频已保存；元数据状态：";
      if (stateEl) stateEl.textContent = "已保存";
      if (resultEl) resultEl.textContent = prefix + parts.join(" · ");
    } else if (body && body.code === "conflict") {
      if (stateEl) stateEl.textContent = "已存在";
      if (resultEl) resultEl.textContent = "目标目录已存在同名文件，未覆盖。";
    } else {
      if (stateEl) stateEl.textContent = "失败";
      if (resultEl) resultEl.textContent = "保存失败：" + manualSaveMessage(body, "内部错误");
    }
  } catch (exc) {
    if (stateEl) stateEl.textContent = "失败";
    if (resultEl) resultEl.textContent = "保存失败：" + (exc && exc.message ? exc.message : "网络错误");
  } finally {
    saveState.saving.delete(songId);
    button.disabled = false;
  }
}

const saveSearchButton = $("#save-search-btn");
if (saveSearchButton) saveSearchButton.addEventListener("click", () => { void searchSaveTracks(); });
const saveKeywordInput = $("#save-keyword");
if (saveKeywordInput) {
  saveKeywordInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); void searchSaveTracks(); }
  });
}

/* --------------------------------------------------------- 手动保存歌单 */
/* 合同：docs/MANUAL_SAVE_PLAYLIST_JOB.md §3.5/§6.3/§6.4/§6.5/§7.2/§8
 * 纪律：
 * - 所有请求复用 manualSaveApi（自动带 x-fnmusic-save: 1），信封固定 {ok,code,message,data}；
 * - 文案只走固定表，未知 code 回落 internal 文案，绝不拼接服务端原始文本；
 * - 推进严格串行：一次 advance 只跑一首，成功（disposition=continue）立即下一首；
 *   只有 busy / no_work / 504 超时 / 网络错误才退避，且都不重放"未查询即重试"；
 * - 终态（completed 系列 / partial / cancelled / reconciliation_required）或队尾停止轮询；
 * - 前端不把任何 URL/Cookie/路径写进 DOM 或 console。 */
const PLAYLIST_API = "/api/manual-save/playlist";
const PLAYLIST_JOB_STORAGE_KEY = "fnmusic.save.playlist.job.v1";
const PLAYLIST_MAX_OPTIONS = 100;      // §3.2：列表最多 100 张（服务端无分页）
const PLAYLIST_BACKOFF_BASE_MS = 600;  // 退避：600 → 1200 → 2400 → 3000（上限 3s）
const PLAYLIST_BACKOFF_MAX_MS = 3000;
const PLAYLIST_MAX_NO_WORK = 5;        // §7.2：no_work 最多连续 5 次
const PLAYLIST_MAX_TRANSIENT = 5;      // busy / 网络错误连续上限，避免无限轮询
const PLAYLIST_JOB_ID_RE = /^[0-9a-f]{48}$/;
const PLAYLIST_FINGERPRINT_RE = /^[0-9a-f]{64}$/;
const PLAYLIST_RECONCILE_TEXT = "存在结果未知的位置，已停止推进。请核对 NAS 上是否已出现该文件后再人工处理。";
const PLAYLIST_TERMINAL_STATES = new Set([
  "cancelled", "partial", "completed_with_issues",
  "completed_bytes_reported", "reconciliation_required",
]);

// §3.9.1 固定 code → 固定中文（与网关逐字一致）。未登记的 code 一律回落 internal。
const PLAYLIST_CODE_MESSAGES = {
  ok: "",
  invalid_request: "请求无效",
  invalid_playlist_id: "歌单 ID 必须是正整数",
  invalid_fingerprint: "快照指纹无效",
  invalid_idempotency_key: "幂等键无效",
  not_found: "任务不存在",
  forbidden: "仅管理员可执行手动保存",
  disabled: "手动保存未启用",
  playlist_disabled: "整歌单保存未启用",
  not_logged_in: "网易云账号未登录",
  config_invalid: "手动保存配置无效",
  playlist_unavailable: "音乐服务不可用",
  playlist_unsupported: "该歌单不支持可验证快照",
  playlist_too_large: "歌单成员超过 1000 个",
  manifest_incomplete: "歌单成员清单不完整",
  source_changed: "歌单在确认后发生变化，请重新读取",
  source_artifact_mismatch: "音乐服务版本与固定证据不一致",
  idempotency_conflict: "幂等键已用于不同清单",
  capacity_exceeded: "任务账本容量已满",
  ledger_unavailable: "任务账本不可用",
  busy: "已有保存任务进行中",
  completion_unknown: "任务结果写入失败，需人工核对",
  stale_completion: "迟到的执行结果未覆盖记录",
  timeout: "保存超时",
  internal: "内部错误",
};

// §6.2 位置状态固定中文
const PLAYLIST_POSITION_LABELS = {
  queued: "等待",
  running: "保存中",
  published: "已发布",
  published_cleanup_pending: "已发布（暂存待清理）",
  conflict: "已存在（未覆盖）",
  cancelled: "已取消",
  rejected: "未保存",
  failed: "失败",
  commit_unknown: "结果未知",
  unknown: "状态未知",
};

// §6.2 位置 reason 固定中文（用于推进响应的 run.reason；位置列表用服务端 reason_message）
const PLAYLIST_REASON_MESSAGES = {
  bytes_published: "已发布",
  destination_conflict: "目标已存在同名文件，未覆盖",
  cancelled_before_publish: "已取消（未执行）",
  commit_unknown: "执行结果未知，需要人工对账",
  stage_unavailable: "暂存不可用",
  staging_failed: "暂存不可用",
  cancel_check_failed: "取消检查失败（按已取消处理）",
  disabled: "手动保存未启用",
  config_invalid: "手动保存配置无效",
  busy: "已有保存任务进行中",
  service_unavailable: "音乐服务不可用",
  no_quality_available: "没有可用的音质",
  quality_unverified: "音质响应无法核验",
  quality_identity_mismatch: "无法确认音质响应所属曲目",
  trial_only: "仅取得试听片段，未保存",
  download_target_untrusted: "下载目标未通过安全校验",
  download_failed: "下载失败",
  integrity_mismatch: "文件校验不一致",
  invalid_audio: "音频文件无效",
  tag_failed: "写入标签失败",
  invalid_target: "输入无效，未执行",
};

const playlistState = {
  enabled: false,          // 整歌单开关（GET playlists 成功即开启）
  loaded: false,
  disabledCode: "",
  playlists: [],
  selectedId: 0,
  snapshot: null,          // {playlist_id, source_fingerprint, track_count, ...}
  jobId: "",
  job: null,
  positions: [],
  running: false,          // 推进循环在跑（串行守卫）
  stopRequested: false,
  terminal: false,
  message: "",
  storageAvailable: true,
};

function playlistSleep(ms) {
  // 测试替换点：测试用可控计时桩替换本函数（不真 sleep）
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

function playlistBackoffDelay(attempt) {
  const index = Number.isInteger(attempt) && attempt > 0 ? attempt : 0;
  const delay = PLAYLIST_BACKOFF_BASE_MS * Math.pow(2, index);
  return Math.min(PLAYLIST_BACKOFF_MAX_MS, delay);
}

function playlistJobMessage(body, fallback) {
  const code = body && typeof body.code === "string" ? body.code : "";
  if (Object.prototype.hasOwnProperty.call(PLAYLIST_CODE_MESSAGES, code)) {
    return PLAYLIST_CODE_MESSAGES[code];
  }
  // §6.3：未知码 → internal 文案，绝不回显服务端 message/异常文本
  return fallback || PLAYLIST_CODE_MESSAGES.internal;
}

function playlistReasonMessage(reason) {
  if (typeof reason !== "string" || !reason) return "";
  if (Object.prototype.hasOwnProperty.call(PLAYLIST_REASON_MESSAGES, reason)) {
    return PLAYLIST_REASON_MESSAGES[reason];
  }
  return "未保存（原因未登记）";  // §6.2 固定兜底
}

function playlistJobStateText(job) {
  const state = job && typeof job.state === "string" ? job.state : "unknown";
  const total = Number.isInteger(job && job.positions_total) && job.positions_total > 0 ? job.positions_total : 0;
  switch (state) {
    case "queued": return total > 0 ? "等待开始（共 " + total + " 首）" : "等待开始";
    case "running": return "正在保存…";
    case "cancelling": return "正在取消…（当前这首会跑完）";
    case "cancelled": return "已取消";
    case "partial": return "部分完成（有失败项）";
    case "completed_with_issues": return "已完成但有失败项";
    case "completed_bytes_reported": return "已完成（仅表示字节已发布，不表示可播放/已入库）";
    case "reconciliation_required": return "需要对账：存在结果未知的位置";
    default: return "状态未知";
  }
}

function playlistProgressText(job) {
  const counts = job && job.counts && typeof job.counts === "object" ? job.counts : {};
  const count = (key) => (Number.isInteger(counts[key]) && counts[key] > 0 ? counts[key] : 0);
  const published = count("published") + count("published_cleanup_pending");
  const conflict = count("conflict");
  const failed = count("failed") + count("rejected");
  // ``commit_unknown`` 是"结果未知"，不是失败：单独计数，避免与冻结语义混淆。
  const unknown = count("commit_unknown");
  const queued = count("queued");
  const running = count("running");
  const cancelled = count("cancelled");
  let total = Number.isInteger(job && job.positions_total) && job.positions_total > 0 ? job.positions_total : 0;
  if (!total) total = published + conflict + failed + queued + running + cancelled;
  const processed = Math.max(0, total - queued - running);
  let text = "已处理 " + processed + "/" + total
    + "：已发布 " + published + " · 已存在 " + conflict
    + " · 失败 " + failed + " · 未执行 " + queued;
  if (running > 0) text += " · 保存中 " + running;
  if (unknown > 0) text += " · 结果未知 " + unknown;
  if (cancelled > 0) text += " · 已取消 " + cancelled;
  return text;
}

function playlistRenderPositions(positions) {
  const rows = Array.isArray(positions) ? positions : [];
  if (!rows.length) return "";
  const seen = new Map();
  rows.forEach((row) => {
    const sourceId = row && typeof row.source_id === "string" ? row.source_id : "";
    if (sourceId) seen.set(sourceId, (seen.get(sourceId) || 0) + 1);
  });
  return rows.map((row, index) => {
    const raw = row && typeof row === "object" ? row : {};
    const position = Number.isInteger(raw.position) && raw.position > 0 ? raw.position : index + 1;
    const state = typeof raw.state === "string" ? raw.state : "unknown";
    const stateText = PLAYLIST_POSITION_LABELS[state] || "状态未知";
    const reason = typeof raw.reason_message === "string" ? raw.reason_message : "";
    const sourceId = typeof raw.source_id === "string" ? raw.source_id : "";
    const duplicate = sourceId && (seen.get(sourceId) || 0) > 1;
    // §6.3：所有来自服务端的字符串统一 escapeHtml
    const metaParts = [reason, sourceId, duplicate ? "重复位置" : ""]
      .filter((part) => typeof part === "string" && part)
      .map((part) => escapeHtml(part));
    return '<div class="lx-item"><div class="lx-item-main">'
      + '<div class="lx-item-header"><span class="lx-item-name">'
      + escapeHtml("#" + position + " " + stateText) + "</span></div>"
      + '<div class="lx-item-url">' + metaParts.join(" · ") + "</div>"
      + "</div></div>";
  }).join("");
}

function playlistUpdateButtons() {
  const enabled = playlistState.enabled === true;
  const busy = playlistState.running === true;
  const job = playlistState.job;
  const state = job && typeof job.state === "string" ? job.state : "";
  const blockedForStart = state === "reconciliation_required" || state === "cancelling";
  const select = $("#playlist-select");
  if (select) select.disabled = !enabled || busy;
  const refreshBtn = $("#playlist-refresh-btn");
  if (refreshBtn) refreshBtn.disabled = !enabled || busy;
  const snapshotBtn = $("#playlist-snapshot-btn");
  if (snapshotBtn) {
    snapshotBtn.disabled = !enabled || busy
      || !Number.isInteger(playlistState.selectedId) || playlistState.selectedId <= 0;
  }
  const startBtn = $("#playlist-start-btn");
  if (startBtn) {
    const canStart = enabled && !busy && !blockedForStart
      && !!playlistState.snapshot && playlistState.snapshot.enumeration_complete === true;
    startBtn.disabled = !canStart;
  }
  const stopBtn = $("#playlist-stop-btn");
  if (stopBtn) stopBtn.disabled = !busy;
  const cancelBtn = $("#playlist-cancel-btn");
  if (cancelBtn) {
    // §3.8：reconciliation_required 保留取消；cancelling 不再可取消
    const canCancel = enabled && !!job
      && (state === "queued" || state === "running" || state === "reconciliation_required");
    cancelBtn.disabled = !canCancel;
  }
}

function playlistSetSnapshotStatus(text) {
  const el = $("#playlist-snapshot-status");
  if (el) el.textContent = typeof text === "string" ? text : "";
}

function playlistRenderJob() {
  const panel = $("#playlist-job-panel");
  if (!panel) return;
  const job = playlistState.job;
  if (!job) {
    panel.hidden = true;
    // 隐藏面板的同时清空旧渲染，避免刷新失败后 DOM 里残留过期进度。
    const staleList = $("#playlist-positions");
    if (staleList) staleList.innerHTML = "";
    playlistUpdateButtons();
    return;
  }
  panel.hidden = false;
  const stateEl = $("#playlist-job-state");
  if (stateEl) stateEl.textContent = "任务状态：" + playlistJobStateText(job);
  const progressEl = $("#playlist-progress");
  if (progressEl) progressEl.textContent = playlistProgressText(job);
  const messageEl = $("#playlist-run-message");
  if (messageEl) messageEl.textContent = playlistState.message || "";
  const banner = $("#playlist-reconcile-banner");
  if (banner) {
    const reconcile = job.state === "reconciliation_required"
      || (playlistState.positions || []).some((row) => row && row.state === "commit_unknown");
    banner.textContent = PLAYLIST_RECONCILE_TEXT;
    banner.hidden = !reconcile;
  }
  const list = $("#playlist-positions");
  if (list) list.innerHTML = playlistRenderPositions(playlistState.positions);
  playlistUpdateButtons();
}

function playlistRenderOptions() {
  const select = $("#playlist-select");
  const rows = Array.isArray(playlistState.playlists)
    ? playlistState.playlists.slice(0, PLAYLIST_MAX_OPTIONS)
    : [];
  const options = [];
  rows.forEach((row) => {
    const playlistId = row && row.playlist_id;
    if (!Number.isInteger(playlistId) || playlistId <= 0) return;
    const name = row && typeof row.name === "string" && row.name ? row.name : "未命名歌单";
    const trackCount = row && Number.isInteger(row.track_count) && row.track_count >= 0
      ? row.track_count : 0;
    options.push('<option value="' + playlistId + '">'
      + escapeHtml(name) + "（" + trackCount + " 首）</option>");
  });
  if (select) {
    select.innerHTML = options.length ? options.join("") : '<option value="">没有可选歌单</option>';
  }
  const ids = rows.filter((row) => Number.isInteger(row && row.playlist_id) && row.playlist_id > 0)
    .map((row) => row.playlist_id);
  if (ids.indexOf(playlistState.selectedId) < 0) {
    playlistState.selectedId = ids.length ? ids[0] : 0;
    playlistState.snapshot = null;
    playlistSetSnapshotStatus("");
  }
  if (select && playlistState.selectedId > 0) select.value = String(playlistState.selectedId);
}

/* localStorage 读取/写入全部 try/catch：隐私模式/配额/无存储时降级为会话内状态（§6.5.4） */
function playlistStorage() {
  try {
    const store = typeof globalThis !== "undefined" ? globalThis.localStorage : undefined;
    if (!store || typeof store.getItem !== "function" || typeof store.setItem !== "function") return null;
    return store;
  } catch (_) {
    return null;
  }
}

function playlistReadStoredJob() {
  try {
    const store = playlistStorage();
    if (!store) { playlistState.storageAvailable = false; return null; }
    playlistState.storageAvailable = true;
    const raw = store.getItem(PLAYLIST_JOB_STORAGE_KEY);
    if (typeof raw !== "string" || !raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const jobId = typeof parsed.job_id === "string" ? parsed.job_id : "";
    const playlistId = parsed.playlist_id;
    if (!PLAYLIST_JOB_ID_RE.test(jobId)) return null;
    if (!Number.isInteger(playlistId) || playlistId <= 0) return null;
    return {
      job_id: jobId,
      playlist_id: playlistId,
      source_fingerprint: typeof parsed.source_fingerprint === "string" ? parsed.source_fingerprint : "",
      manifest_sha256: typeof parsed.manifest_sha256 === "string" ? parsed.manifest_sha256 : "",
    };
  } catch (_) {
    playlistState.storageAvailable = false;
    return null;
  }
}

function playlistStoreJob() {
  const jobId = playlistState.jobId;
  const snapshot = playlistState.snapshot;
  if (!jobId || !PLAYLIST_JOB_ID_RE.test(jobId)) return false;
  try {
    const store = playlistStorage();
    if (!store) { playlistState.storageAvailable = false; return false; }
    store.setItem(PLAYLIST_JOB_STORAGE_KEY, JSON.stringify({
      playlist_id: snapshot && Number.isInteger(snapshot.playlist_id) ? snapshot.playlist_id : 0,
      job_id: jobId,
      source_fingerprint: snapshot && typeof snapshot.source_fingerprint === "string"
        ? snapshot.source_fingerprint : "",
      manifest_sha256: playlistState.job && typeof playlistState.job.manifest_sha256 === "string"
        ? playlistState.job.manifest_sha256 : "",
    }));
    playlistState.storageAvailable = true;
    return true;
  } catch (_) {
    playlistState.storageAvailable = false;
    return false;
  }
}

function playlistClearStoredJob() {
  try {
    const store = playlistStorage();
    if (store && typeof store.removeItem === "function") store.removeItem(PLAYLIST_JOB_STORAGE_KEY);
  } catch (_) { /* 存储不可用：忽略，会话内状态仍然有效 */ }
}

function playlistDisabledText(code, response) {
  if (code === "playlist_disabled") {
    return "整歌单保存未启用：请在 .env 设置 FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED=true 后重启代理。";
  }
  if (code === "disabled") {
    return "手动保存未启用：请在 .env 设置 FNMUSIC_MANUAL_SAVE_ENABLED=true 后重启代理。";
  }
  if (code === "not_logged_in") {
    return "网易云账号未登录：请先在「账号」里扫码登录后再使用整歌单保存。";
  }
  if (code === "forbidden") return "仅管理员可执行手动保存：请通过飞牛桌面打开管理台。";
  const status = response && Number.isInteger(response.status) ? response.status : 0;
  return "无法读取整歌单保存状态（HTTP " + status + "）："
    + playlistJobMessage(response && response.body, "服务不可用") + "。";
}

async function loadPlaylistSaveStatus() {
  const statusEl = $("#playlist-status");
  const wasEnabled = playlistState.enabled;
  playlistState.loaded = true;
  // 单曲开关关闭时不得发起任何 playlist 请求（§6.3 禁用守卫 6）
  if (!saveState.enabled) {
    playlistState.enabled = false;
    playlistState.playlists = [];
    playlistState.disabledCode = "disabled";
    if (wasEnabled) { playlistState.job = null; playlistState.positions = []; playlistState.jobId = ""; }
    if (statusEl) statusEl.textContent = "整歌单保存不可用：单曲手动保存未启用。";
    playlistRenderOptions();
    playlistRenderJob();
    playlistUpdateButtons();
    return false;
  }
  playlistState.enabled = false;
  playlistState.disabledCode = "";
  if (statusEl) statusEl.textContent = "正在读取整歌单保存状态…";
  playlistUpdateButtons();
  let response;
  try {
    response = await manualSaveApi(PLAYLIST_API + "/playlists");
  } catch (_) {
    if (statusEl) statusEl.textContent = "读取整歌单保存状态失败：网络错误，请稍后重试。";
    // 失败时清掉可能残留的旧任务面板，避免显示过期进度（按钮由 updateButtons 禁用）。
    playlistState.jobId = "";
    playlistState.job = null;
    playlistState.positions = [];
    playlistState.message = "";
    playlistState.terminal = false;
    playlistRenderJob();
    playlistUpdateButtons();
    return false;
  }
  const body = (response && response.body) || {};
  const data = (body && body.data) || {};
  if (response && response.ok === true && body.ok === true && Array.isArray(data.playlists)) {
    playlistState.enabled = true;
    playlistState.disabledCode = "";
    playlistState.playlists = data.playlists.slice(0, PLAYLIST_MAX_OPTIONS);
    if (statusEl) {
      statusEl.textContent = "整歌单保存已启用；共 " + playlistState.playlists.length
        + " 张歌单（最多显示 100 张）。";
    }
    playlistRenderOptions();
    playlistUpdateButtons();
    await playlistResumeFromStorage();  // §6.5 路径 1：localStorage 恢复
    return true;
  }
  playlistState.playlists = [];
  playlistState.disabledCode = body && typeof body.code === "string" ? body.code : "";
  if (statusEl) statusEl.textContent = playlistDisabledText(playlistState.disabledCode, response);
  playlistRenderOptions();
  playlistRenderJob();
  playlistUpdateButtons();
  return false;
}

function playlistSelectPlaylist(value) {
  const playlistId = Number(value);
  playlistState.selectedId = Number.isInteger(playlistId) && playlistId > 0 ? playlistId : 0;
  playlistState.snapshot = null;
  playlistSetSnapshotStatus("");
  playlistUpdateButtons();
}

async function playlistReadSnapshot() {
  if (!playlistState.enabled) return false;
  const playlistId = playlistState.selectedId;
  if (!Number.isInteger(playlistId) || playlistId <= 0) {
    playlistSetSnapshotStatus("请先选择歌单。");
    return false;
  }
  playlistState.snapshot = null;
  playlistSetSnapshotStatus("正在读取歌单清单…");
  playlistUpdateButtons();
  let response;
  try {
    response = await manualSaveApi(PLAYLIST_API + "/snapshot?playlist_id=" + playlistId);
  } catch (_) {
    playlistSetSnapshotStatus("读取清单失败：网络错误，请稍后重试；不能创建保存任务。");
    playlistUpdateButtons();
    return false;
  }
  const body = (response && response.body) || {};
  const data = (body && body.data) || {};
  const complete = response && response.ok === true && body.ok === true
    && data.enumeration_complete === true
    && typeof data.source_fingerprint === "string"
    && PLAYLIST_FINGERPRINT_RE.test(data.source_fingerprint);
  if (complete) {
    const trackCount = Number.isInteger(data.track_count) && data.track_count >= 0 ? data.track_count : 0;
    const positionCount = Number.isInteger(data.position_count) && data.position_count >= 0
      ? data.position_count : 0;
    const duplicate = Number.isInteger(data.duplicate_position_count) && data.duplicate_position_count > 0
      ? data.duplicate_position_count : 0;
    playlistState.snapshot = {
      playlist_id: playlistId,
      source_fingerprint: data.source_fingerprint,
      track_count: trackCount,
      position_count: positionCount,
      duplicate_position_count: duplicate,
      enumeration_complete: true,
    };
    playlistSetSnapshotStatus("共 " + trackCount + " 首（含 " + duplicate + " 个重复位置），可以开始保存。");
    playlistUpdateButtons();
    return true;
  }
  // 不完整/失败：固定文案 + 不进入可创建状态（snapshot 保持 null → 开始按钮 disabled）
  const sourceCode = data && typeof data.source_error_code === "string" && data.source_error_code
    ? "（" + escapeHtml(data.source_error_code) + "）" : "";
  const prefix = data && data.enumeration_complete === false
    ? "歌单成员清单不完整" + sourceCode
    : "读取清单失败：" + playlistJobMessage(body, "服务不可用");
  playlistSetSnapshotStatus(prefix + "；不能创建保存任务。");
  playlistUpdateButtons();
  return false;
}

async function playlistRefreshJob(jobId) {
  const rawId = typeof jobId === "string" ? jobId : playlistState.jobId;
  const id = PLAYLIST_JOB_ID_RE.test(rawId) ? rawId : "";
  if (!id) return false;
  let response;
  try {
    response = await manualSaveApi(PLAYLIST_API + "/job?job_id=" + id);
  } catch (_) {
    return false;
  }
  const body = (response && response.body) || {};
  const data = (body && body.data) || {};
  if (response && response.ok === true && body.ok === true && data.job && typeof data.job === "object") {
    const jobId_ = typeof data.job.job_id === "string" && PLAYLIST_JOB_ID_RE.test(data.job.job_id)
      ? data.job.job_id : id;
    playlistState.jobId = jobId_;
    playlistState.job = data.job;
    playlistState.positions = Array.isArray(data.positions) ? data.positions : [];
    playlistState.terminal = PLAYLIST_TERMINAL_STATES.has(data.job.state);
    playlistRenderJob();
    return true;
  }
  if (body && body.code === "not_found") {
    playlistClearStoredJob();
    playlistState.jobId = "";
    playlistState.job = null;
    playlistState.positions = [];
    playlistState.terminal = false;
    playlistState.message = "";
    playlistSetSnapshotStatus("任务不存在：请重新读取歌单清单后创建。");
    playlistRenderJob();
  }
  return false;
}

function playlistRunMessage(run) {
  const status = run && typeof run.status === "string" ? run.status : "";
  const position = run && Number.isInteger(run.position) && run.position > 0 ? run.position : 0;
  const where = position > 0 ? "位置 " + position + "：" : "";
  const reasonText = playlistReasonMessage(run && run.reason);
  if (status === "no_work") return "暂无待执行位置（可能另一窗口正在推进）。";
  if (status === "commit_unknown" || status === "completion_unknown") {
    return where + "执行结果未知，需要人工对账。";
  }
  if (status === "stale_completion") return where + "迟到的执行结果未覆盖记录。";
  if (status === "completed") return where + (reasonText || "已处理");
  return where + "保存结果：" + (reasonText || "已处理");
}

async function playlistCreateAndStart() {
  if (!playlistState.enabled) return false;
  const snapshot = playlistState.snapshot;
  if (!snapshot || snapshot.enumeration_complete !== true
      || !PLAYLIST_FINGERPRINT_RE.test(snapshot.source_fingerprint || "")) {
    playlistSetSnapshotStatus("请先成功读取歌单清单，再开始保存。");
    return false;
  }
  const previousJobId = playlistState.jobId;
  let response;
  try {
    response = await manualSaveApi(PLAYLIST_API + "/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        playlist_id: snapshot.playlist_id,
        source_fingerprint: snapshot.source_fingerprint,
      }),
    });
  } catch (_) {
    playlistSetSnapshotStatus("创建任务失败：网络错误，请稍后重试。");
    return false;
  }
  const body = (response && response.body) || {};
  const data = (body && body.data) || {};
  if (!(response && response.ok === true && body.ok === true
        && data.job && typeof data.job === "object")) {
    playlistSetSnapshotStatus("创建任务失败：" + playlistJobMessage(body, "服务不可用") + "。");
    return false;
  }
  const job = data.job;
  const jobId = typeof job.job_id === "string" && PLAYLIST_JOB_ID_RE.test(job.job_id) ? job.job_id : "";
  if (!jobId) {
    playlistSetSnapshotStatus("创建任务失败：内部错误。");
    return false;
  }
  // §3.4 派生幂等键：同歌单 + 同指纹的重复创建命中同一个 job
  const reused = playlistState.jobId === jobId || previousJobId === jobId;
  playlistState.jobId = jobId;
  playlistState.job = job;
  playlistState.positions = [];
  playlistState.terminal = PLAYLIST_TERMINAL_STATES.has(job.state);
  playlistState.message = "";
  playlistStoreJob();
  await playlistRefreshJob(jobId);
  playlistSetSnapshotStatus(reused
    ? "已存在同一任务（同一歌单 + 同一快照指纹），继续上次进度。"
    : "已创建保存任务，开始逐首推进。");
  playlistRenderJob();
  if (!PLAYLIST_TERMINAL_STATES.has(playlistState.job.state)) {
    void playlistAdvanceLoop(jobId);
  }
  return true;
}

async function playlistAdvanceLoop(jobId) {
  const rawId = typeof jobId === "string" ? jobId : playlistState.jobId;
  const id = PLAYLIST_JOB_ID_RE.test(rawId) ? rawId : "";
  if (!id) return null;
  if (playlistState.running) return null;  // 串行守卫：绝不发起第二个并行 advance
  playlistState.running = true;
  playlistState.stopRequested = false;
  playlistState.jobId = id;
  let transient = 0;
  let noWork = 0;
  try {
    while (!playlistState.stopRequested) {
      const job = playlistState.job;
      if (!job || typeof job.state !== "string") break;
      if (PLAYLIST_TERMINAL_STATES.has(job.state)) break;
      if (job.state === "cancelling") break;  // §6.1：cancelling 不允许推进（no_work）
      playlistUpdateButtons();
      let response;
      try {
        response = await manualSaveApi(PLAYLIST_API + "/advance", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_id: id }),
        });
      } catch (_) {
        transient += 1;
        if (transient >= PLAYLIST_MAX_TRANSIENT) {
          playlistState.message = "推进失败：网络错误，已停止本页推进，请稍后刷新查看任务状态。";
          break;
        }
        playlistState.message = "网络错误，正在重试（第 " + transient + " 次）…";
        playlistRenderJob();
        await playlistSleep(playlistBackoffDelay(transient - 1));
        continue;
      }
      const body = (response && response.body) || {};
      const code = body && typeof body.code === "string" ? body.code : "";
      const data = (body && body.data) || {};
      const okRun = response && response.ok === true && body.ok === true
        && data.run && typeof data.run === "object" && data.job && typeof data.job === "object";
      if (okRun) {
        transient = 0;
        const run = data.run;
        const runStatus = typeof run.status === "string" ? run.status : "";
        playlistState.job = data.job;
        playlistState.message = playlistRunMessage(run);
        if (runStatus !== "no_work") noWork = 0;
        // 逐首刷新位置列表（一次 GET job），保证进度与失败逐首可见
        const refreshed = await playlistRefreshJob(id);
        if (!refreshed) playlistRenderJob();
        const jobState = playlistState.job && typeof playlistState.job.state === "string"
          ? playlistState.job.state : "";
        if (PLAYLIST_TERMINAL_STATES.has(jobState)) { playlistClearStoredJob(); break; }
        // §3.6：freeze → 停止并转对账；abort → 停止并提示
        if (run.disposition === "freeze" || runStatus === "commit_unknown"
            || runStatus === "completion_unknown" || runStatus === "stale_completion") {
          break;
        }
        if (run.disposition === "abort") break;
        if (runStatus === "no_work") {
          noWork += 1;
          if (noWork >= PLAYLIST_MAX_NO_WORK) {
            playlistState.message = "另一窗口正在推进或状态异常，已停止本页推进。";
            playlistRenderJob();
            break;
          }
          playlistState.message = "暂无待执行位置（另一窗口可能正在推进），稍后自动重试…";
          playlistRenderJob();
          await playlistSleep(PLAYLIST_BACKOFF_BASE_MS);
          const again = await playlistRefreshJob(id);
          if (!again) { playlistState.message = "任务读取失败，已停止本页推进。"; break; }
          continue;
        }
        continue;  // completed + continue：立即进入下一首（不留空转延迟，§7.2）
      }
      if (code === "busy") {
        transient += 1;
        if (transient >= PLAYLIST_MAX_TRANSIENT) {
          playlistState.message = "另一窗口长时间占用保存任务，已停止本页推进，请稍后刷新。";
          break;
        }
        playlistState.message = "另一窗口正在保存，本页稍后自动继续…";
        playlistRenderJob();
        await playlistSleep(playlistBackoffDelay(transient - 1));
        await playlistRefreshJob(id);
        continue;
      }
      if (code === "timeout" || (response && response.status === 504)) {
        // §7.3：504 不是取消/失败——先 GET job 再判定，绝不盲目重放 advance
        playlistState.message = "保存超时：正在核对任务状态…";
        playlistRenderJob();
        const refreshed = await playlistRefreshJob(id);
        if (!refreshed || !playlistState.job) {
          playlistState.message = "保存超时且未能读取任务状态，已停止推进，请稍后刷新。";
          break;
        }
        if (PLAYLIST_TERMINAL_STATES.has(playlistState.job.state)) break;
        const stillRunning = (playlistState.positions || []).some(
          (row) => row && (row.state === "running" || row.state === "queued"));
        if (stillRunning) {
          playlistState.message = "上次保存请求超时：任务仍在执行，已停止本页推进，请稍后刷新查看进度。";
          break;
        }
        playlistState.message = "上次保存请求超时，已按账本状态继续推进。";
        playlistRenderJob();
        await playlistSleep(playlistBackoffDelay(0));
        continue;
      }
      playlistState.message = "推进失败：" + playlistJobMessage(body, "内部错误") + "（已停止本页推进）";
      playlistRenderJob();
      break;
    }
  } finally {
    playlistState.running = false;
    playlistState.terminal = !!(playlistState.job
      && PLAYLIST_TERMINAL_STATES.has(playlistState.job.state));
    playlistRenderJob();
  }
  return playlistState.message;
}

async function playlistCancelJob() {
  if (!playlistState.enabled) return false;
  const jobId = playlistState.jobId;
  if (!jobId) {
    playlistState.message = "当前没有可取消的任务。";
    playlistRenderJob();
    return false;
  }
  playlistState.message = "正在取消…";
  playlistRenderJob();
  let response;
  try {
    response = await manualSaveApi(PLAYLIST_API + "/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId }),
    });
  } catch (_) {
    playlistState.message = "取消失败：网络错误，请稍后重试。";
    playlistRenderJob();
    return false;
  }
  const body = (response && response.body) || {};
  const data = (body && body.data) || {};
  if (response && response.ok === true && body.ok === true
      && data.job && typeof data.job === "object") {
    playlistState.stopRequested = true;  // 取消后本页不再发起 advance
    playlistState.job = data.job;
    playlistState.terminal = PLAYLIST_TERMINAL_STATES.has(data.job.state);
    if (data.job.state === "cancelled") {
      playlistClearStoredJob();
      playlistState.message = "已取消：未开始的位置不会执行，已发布文件保留。";
    } else if (data.job.state === "cancelling") {
      playlistState.message = "正在取消：当前这首会跑完，之后不再开始新的位置。";
    } else {
      playlistState.message = "已请求取消：已发布文件保留。";
    }
    playlistRenderJob();
    return true;
  }
  playlistState.message = "取消失败：" + playlistJobMessage(body, "服务不可用") + "。";
  playlistRenderJob();
  return false;
}

function playlistStopLoop() {
  if (!playlistState.running) {
    playlistState.message = "当前没有正在推进的任务。";
    playlistRenderJob();
    return false;
  }
  playlistState.stopRequested = true;
  playlistState.message = "已停止推进：任务仍记录在账本中，可点「开始保存」或刷新页面后继续。";
  playlistRenderJob();
  return true;
}

async function playlistResumeFromStorage() {
  // §6.3 守卫 6：单曲开关关闭时不得发起任何 playlist 请求
  if (!saveState.enabled || !playlistState.enabled) return { resumed: false, reason: "disabled" };
  if (playlistState.running) return { resumed: false, reason: "running" };
  const stored = playlistReadStoredJob();
  if (!stored) return { resumed: false, reason: "empty" };
  if (stored.playlist_id > 0) {
    playlistState.selectedId = stored.playlist_id;
    const select = $("#playlist-select");
    if (select) select.value = String(stored.playlist_id);
  }
  const found = await playlistRefreshJob(stored.job_id);
  if (!found) {
    playlistClearStoredJob();
    return { resumed: false, reason: "unavailable" };
  }
  if (PLAYLIST_TERMINAL_STATES.has(playlistState.job.state)) {
    playlistClearStoredJob();
    playlistState.message = "已恢复上次任务：任务已结束，可重新读取清单后新建任务。";
    playlistRenderJob();
    return { resumed: false, reason: "terminal", jobId: playlistState.jobId };
  }
  playlistState.message = "已恢复上次未完成的任务进度，继续推进。";
  playlistRenderJob();
  void playlistAdvanceLoop(playlistState.jobId);  // 恢复轮询推进
  return { resumed: true, jobId: playlistState.jobId };
}

// 只读快照：诊断/测试用，不改变任何状态
function playlistUiState() {
  return {
    enabled: playlistState.enabled,
    loaded: playlistState.loaded,
    disabledCode: playlistState.disabledCode,
    playlistCount: playlistState.playlists.length,
    selectedId: playlistState.selectedId,
    snapshot: playlistState.snapshot ? Object.assign({}, playlistState.snapshot) : null,
    jobId: playlistState.jobId,
    jobState: playlistState.job && typeof playlistState.job.state === "string" ? playlistState.job.state : "",
    positionCount: playlistState.positions.length,
    running: playlistState.running,
    stopRequested: playlistState.stopRequested,
    terminal: playlistState.terminal,
    message: playlistState.message,
    storageAvailable: playlistState.storageAvailable,
  };
}

const playlistRefreshButton = $("#playlist-refresh-btn");
if (playlistRefreshButton) {
  playlistRefreshButton.addEventListener("click", () => { void loadPlaylistSaveStatus(); });
}
const playlistSelectEl = $("#playlist-select");
if (playlistSelectEl) {
  playlistSelectEl.addEventListener("change", () => { playlistSelectPlaylist(playlistSelectEl.value); });
}
const playlistSnapshotButton = $("#playlist-snapshot-btn");
if (playlistSnapshotButton) {
  playlistSnapshotButton.addEventListener("click", () => { void playlistReadSnapshot(); });
}
const playlistStartButton = $("#playlist-start-btn");
if (playlistStartButton) {
  playlistStartButton.addEventListener("click", () => { void playlistCreateAndStart(); });
}
const playlistStopButton = $("#playlist-stop-btn");
if (playlistStopButton) {
  playlistStopButton.addEventListener("click", () => { playlistStopLoop(); });
}
const playlistCancelButton = $("#playlist-cancel-btn");
if (playlistCancelButton) {
  playlistCancelButton.addEventListener("click", () => { void playlistCancelJob(); });
}
playlistUpdateButtons();

/* -------------------------------------------------------------- 启动 */
(async function boot() {
  initTheme().catch(() => {});  // 外观先应用（内部等宿主 SDK，不阻塞配置加载）
  await loadConfig();
  await loadStatus();
  checkUpdate();  // 进页面即查（后端 TTL 缓存兜住 GitHub 限额），不阻塞启动
  await loadPlatforms(false);  // 页面加载不自动拉起预览进程，等用户点选音源
  if ($("#public-playlists-enabled").checked) await showPublicBrowser();
  setInterval(loadStatus, 15000);
})();
