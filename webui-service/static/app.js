/* fnmusic-ext WebUI — 原生 JS，无框架无外部资产。 */
"use strict";

// 飞牛桌面用 HTTPS 打开管理窗，页面必须挂在同源路径 /app/fnmusic-ext 下。
// WebUI 自己也会剥掉这个前缀。管理接口只认飞牛网关注入的管理员头。
const APP_BASE = "/app/fnmusic-ext";

// 直连模式自动跳转：port 8774 直连时维护功能（整理/清理/用户列表）不可用，
// 自动跳转到飞牛桌面网关以获得完整功能。
(function redirectToDesktopIfNeeded() {
  try {
    if (window.location.port === "8774") {
      const desktopUrl = window.location.protocol + "//" + window.location.hostname + ":5666/app/fnmusic-ext/";
      window.location.replace(desktopUrl);
    }
  } catch (_) { /* ignore */ }
})();

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const PROVIDER_LABEL = { musicdl: "musicdl 聚合音源", musicbox: "网易云音乐盒子", lxmusic: "洛雪自定义源", none: "未配置" };
const PROC_LABEL = { musicdl: "musicdl", musicbox: "musicbox", lxserver: "lxserver", lxmusic: "lxmusic", webui: "WebUI" };

let configValues = {};   // GET /api/config 的 values
let configPresence = {}; // .env 显式存在的键（区分默认空与管理员清空）
let configSchema = {};   // 旧后端缺新键时禁用对应控件且不提交未知键
let platforms = { enabled: [], registered: [] };
let dirty = false;
let currentPage = "overview";
let lxSourceListDirty = false; // 避免高级策略保存时重复触发原源列表的激活/校验流程
const DOWNLOAD_LAYOUT_LABELS = {
  flat: "歌曲",
  artist: "歌手/歌曲",
  album: "专辑/歌曲",
  artist_album: "歌手/专辑/歌曲",
};
let qrTimer = null;
let lxVerifiedUrl = null; // 已通过测试的 lx URL（保存时免二次校验提示用）
let lxSourceList = [];    // 本地待保存配置；运行状态轮询不可覆盖 active
let lxSourceOrder = [];   // 洛雪稳定源 ID 顺序；停用时保留历史位置
let lxRuntimeSnapshot = null; // /api/status 的只读运行快照
const LX_STATE_LABEL = { unconfigured: "未配置", loading: "初始化中", ready: "可用", degraded: "部分可用", failed: "全部失败", unavailable: "服务不可用" };
const LX_SOURCE_STATUS_LABEL = { success: "运行就绪", failed: "运行失败", loading: "初始化中", unknown: "运行状态未知" };
let updateInfo = null;    // /api/update-check 结果（菜单红点 / NEW 标记 / 详情弹窗共用）
let publicSelection = [];
let publicUsers = [];
let publicUsersReady = false;
let publicUsersSeq = 0;
let publicUserGuid = "";
let publicSelectionMap = {};
const publicUserChanges = new Set();
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
const PUBLIC_METADATA_STORAGE_KEY = "fnmusic_public_metadata_cache_v1";
const PUBLIC_METADATA_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const PUBLIC_FAILED_TTL_MS = 30 * 1000;
const MAX_PUBLIC_DETAIL_CONCURRENCY = 2;
const publicDetailAbortControllers = new Set();
const publicDetailInflight = new Map();
let publicDetailRun = null;
let publicDetailRunUser = "";
let publicProcessSnapshot = null;

async function api(path, options) {
  const resp = await fetch(APP_BASE + path, options);
  let body = {};
  try { body = await resp.json(); } catch (_) { /* 非 JSON */ }
  if (!resp.ok) {
    const err = new Error(body.error || body.detail || `HTTP ${resp.status}`);
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  return body;
}

function updateLlmLikedSettings() {
  $("#llm-liked-settings").hidden = !$("#llm-liked").checked;
  const selected = $("#llm-liked").checked || $("#llm-daily").checked;
  $("#llm-api-warning").hidden = !selected || !!($("#llm-base").value.trim() && $("#llm-key").value.trim());
}

let llmUsageOffset = 0;
let llmUsageTimer = null;
let llmUsageActive = false;
let llmUsageSequence = 0;
let llmUsageLast = null;
const LLM_USAGE_PAGE_SIZE = 30;
const usageNumber = (value) => Math.max(0, Number(value) || 0).toLocaleString();
const usageDate = (value) => /^\d{8}$/.test(String(value || ""))
  ? `${String(value).slice(0, 4)}-${String(value).slice(4, 6)}-${String(value).slice(6, 8)}` : "未知";

function renderLlmUsage(u) {
  const el = $("#llm-usage-line");
  if (!el) return;
  const available = !u.status || u.status === "ok";
  let text = available ? `今日 token：输入 ${usageNumber(u.prompt)} / 输出 ${usageNumber(u.completion)} / 总计 ${usageNumber(u.total)}`
    : `账本${u.status === "missing" ? "尚无记录" : "不可用"}：${u.statusMessage || u.status}（不能视为已用 0）`;
  text += Number(u.dailyLimit) > 0 ? `；每日上限 ${usageNumber(u.dailyLimit)}` : "；每日不限额";
  if (available) text += `；实际请求 ${usageNumber(u.requests)} 次`;
  if (u.withinLimit === false) text += "；已达上限，次日重置";
  if (Number(u.unknownUsageRequests)) text += `；${usageNumber(u.unknownUsageRequests)} 次未返回用量，未计入 token`;
  if (u.lastFailure?.reason) text += `；最近失败：${u.lastFailure.reason}${u.lastFailure.detail ? `（${String(u.lastFailure.detail).slice(0, 120)}）` : ""}`;
  text += `；北京时间 UTC+08:00；更新于 ${new Date().toLocaleTimeString()}`;
  el.textContent = text;
  el.dataset.lastUsageText = text;
  const summary = $("#llm-usage-summary");
  if (summary) summary.textContent = !available ? "累计与历史数据暂不可读取，不能视为零用量；请检查账本状态后重试。" : `累计 token ${usageNumber(u.allTime?.total)}（输入 ${usageNumber(u.allTime?.prompt)} / 输出 ${usageNumber(u.allTime?.completion)}），实际请求 ${usageNumber(u.allTime?.requests)} 次。统计起点 ${usageDate(u.statsStartedAt)}。${u.historyIncomplete ? "历史不完整：旧版已丢失的记录无法恢复。" : "仅统计本扩展记录到的调用。"}`;
  const history = $("#llm-usage-history");
  if (history) history.innerHTML = available && (u.history || []).length
    ? `<table class="usage-table"><thead><tr><th>日期（北京时间）</th><th>输入</th><th>输出</th><th>总 token</th><th>请求</th></tr></thead><tbody>${u.history.map((day) => `<tr><td>${usageDate(day.date)}</td><td>${usageNumber(day.prompt)}</td><td>${usageNumber(day.completion)}</td><td>${usageNumber(day.total)}</td><td>${usageNumber(day.requests)}${Number(day.unknownUsageRequests) ? `（${usageNumber(day.unknownUsageRequests)} 次用量未知）` : ""}</td></tr>`).join("")}</tbody></table>`
    : `<p class="hint">${available ? "暂无历史记录。" : "历史记录暂不可读取。"}</p>`;
  if ($("#llm-usage-prev")) $("#llm-usage-prev").disabled = llmUsageOffset === 0;
  if ($("#llm-usage-next")) $("#llm-usage-next").disabled = llmUsageOffset + LLM_USAGE_PAGE_SIZE >= Number(u.totalDays || 0);
  if ($("#llm-usage-page")) $("#llm-usage-page").textContent = `第 ${Math.floor(llmUsageOffset / LLM_USAGE_PAGE_SIZE) + 1} 页，共 ${usageNumber(u.totalDays)} 个记录日`;
}

async function refreshLlmUsage() {
  const el = $("#llm-usage-line");
  if (!el) return;
  const seq = ++llmUsageSequence;
  try {
    const { usage: u } = await api(`/api/llm/usage?limit=${LLM_USAGE_PAGE_SIZE}&offset=${llmUsageOffset}`);
    if (seq !== llmUsageSequence) return;
    if (!u || typeof u !== "object") throw new Error("账本响应无效");
    llmUsageLast = u;
    renderLlmUsage(u);
  } catch (_) {
    if (seq !== llmUsageSequence) return;
    if (llmUsageLast) {
      const previousText = el.dataset.lastUsageText || "";
      el.textContent = `刷新失败，显示上次成功结果（可能已过期）：${previousText}`;
    } else el.textContent = "Token 用量暂不可用，请重试（不能视为已用 0）。";
  }
}

function setLlmUsageActive(active) {
  llmUsageActive = active;
  clearInterval(llmUsageTimer);
  llmUsageTimer = null;
  if (active && !document.hidden) {
    void refreshLlmUsage();
    llmUsageTimer = setInterval(refreshLlmUsage, 30000);
  }
}
if (typeof document.addEventListener === "function") document.addEventListener("visibilitychange", () => setLlmUsageActive(llmUsageActive));
$("#llm-usage-refresh")?.addEventListener("click", () => refreshLlmUsage());
$("#llm-usage-prev")?.addEventListener("click", () => { llmUsageOffset = Math.max(0, llmUsageOffset - LLM_USAGE_PAGE_SIZE); void refreshLlmUsage(); });
$("#llm-usage-next")?.addEventListener("click", () => { if (llmUsageOffset + LLM_USAGE_PAGE_SIZE < Number(llmUsageLast?.totalDays || 0)) { llmUsageOffset += LLM_USAGE_PAGE_SIZE; void refreshLlmUsage(); } });

function likedNumber(selector, label, min, max, integer = false) {
  const text = $(selector).value.trim();
  const num = Number(text);
  if (!text || !Number.isFinite(num) || num < min || (max != null && num > max) || (integer && !Number.isInteger(num))) {
    throw new Error(label + (integer ? "必须是 0–100 的整数" : "必须是大于或等于 0.5 的有限数字"));
  }
  return num;
}

function lxTimeoutNumber(selector, label) {
  const value = Number.parseFloat($(selector).value);
  if (!Number.isFinite(value) || value < 1 || value > 300) {
    throw new Error(`${label}必须是 1–300 秒的数字`);
  }
  return value;
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

/* -------------------------------------------------------------- 帮助弹窗 */
let helpReturnFocus = null;
let helpPreviousOverflow = "";
let helpBackgroundState = [];

function helpFocusables() {
  return $$("#help-modal button, #help-modal [href], #help-modal input, #help-modal select, #help-modal textarea")
    .filter((el) => !el.disabled);
}

function openHelp(helpId, trigger = null) {
  const template = document.querySelector(`template[data-help-id="${helpId}"]`);
  if (!template) return;
  if (!$("#help-modal").hidden) return;
  const title = (trigger && trigger.getAttribute && trigger.getAttribute("aria-label")) || "帮助";
  helpReturnFocus = trigger || document.activeElement;
  $("#help-title").textContent = title.replace(/帮助$/, "");
  $("#help-body").replaceChildren(template.content.cloneNode(true));
  $("#help-modal").hidden = false;
  helpPreviousOverflow = document.body.style.overflow || "";
  helpBackgroundState = $$(".layout, #save-bar").map((element) => [element, element.inert]);
  helpBackgroundState.forEach(([element]) => { element.inert = true; });
  document.body.style.overflow = "hidden";
  $("#help-close-btn").focus();
}

function closeHelp() {
  if ($("#help-modal").hidden) return;
  $("#help-modal").hidden = true;
  $("#help-body").replaceChildren();
  document.body.style.overflow = helpPreviousOverflow;
  helpBackgroundState.forEach(([element, inert]) => { element.inert = inert; });
  helpBackgroundState = [];
  if (helpReturnFocus && typeof helpReturnFocus.focus === "function") helpReturnFocus.focus();
  helpReturnFocus = null;
}

$$("[data-help]").forEach((btn) => btn.addEventListener("click", () => openHelp(btn.dataset.help, btn)));
$("#help-close-btn").addEventListener("click", closeHelp);
$("#help-modal").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget) closeHelp();
});
window.addEventListener("keydown", (ev) => {
  if ($("#help-modal").hidden || !$("#cleanup-modal").hidden) return;
  if (ev.key === "Escape") {
    ev.preventDefault();
    closeHelp();
  } else if (ev.key === "Tab") {
    const items = helpFocusables();
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault();
      first.focus();
    }
  }
});

/* -------------------------------------------------------------- 导航 */
let activeBottomMenu = null;
let activeBottomTrigger = null;

function isNavGroupExpanded(group) {
  try {
    return typeof window !== "undefined" && window.localStorage
      ? window.localStorage.getItem("fnmusic-nav-" + group + "-expanded") === "true"
      : false;
  } catch (_) { return false; }
}

function setNavGroupExpanded(group, expanded, save = true) {
  const titleBtn = $("#nav-" + group + "-title");
  const subsEl = $("#nav-" + group + "-subs");
  if (titleBtn && typeof titleBtn.setAttribute === "function") {
    titleBtn.setAttribute("aria-expanded", String(expanded));
  }
  if (subsEl) subsEl.hidden = !expanded;
  if (save) {
    try {
      if (typeof window !== "undefined" && window.localStorage) {
        window.localStorage.setItem("fnmusic-nav-" + group + "-expanded", String(expanded));
      }
    } catch (_) {}
  }
}

function closeBottomSubMenu(restoreFocus = true) {
  if (!activeBottomMenu) return;
  activeBottomMenu.hidden = true;
  if (activeBottomTrigger) {
    if (typeof activeBottomTrigger.setAttribute === "function") {
      activeBottomTrigger.setAttribute("aria-expanded", "false");
    }
    if (restoreFocus && typeof activeBottomTrigger.focus === "function") {
      activeBottomTrigger.focus();
    }
  }
  activeBottomMenu = null;
  activeBottomTrigger = null;
}

function openBottomSubMenu(trigger, menu) {
  if (activeBottomMenu === menu) {
    closeBottomSubMenu(false);
    return;
  }
  closeBottomSubMenu(false);
  activeBottomMenu = menu;
  activeBottomTrigger = trigger;
  menu.hidden = false;
  if (typeof trigger.setAttribute === "function") {
    trigger.setAttribute("aria-expanded", "true");
  }
  const firstItem = menu.querySelector ? menu.querySelector(".bottom-sub-item") : null;
  if (firstItem && typeof firstItem.focus === "function") firstItem.focus();
}

function switchPage(page) {
  currentPage = page;
  $$(".page").forEach((el) => el.classList.toggle("active", el.id === "page-" + page));
  $$("[data-page]").forEach((el) => el.classList.toggle("active", el.dataset.page === page));
  // 储存父标题：边听边存/目录设置/保存到 NAS 任一子页激活时强调（子项保持各自的实心高亮）
  const isStorageSub = page === "tee" || page === "dirs" || page === "save";
  const isAdvancedSub = page === "llm" || page === "lx";

  const storageTitle = $("#nav-storage-title");
  if (storageTitle) storageTitle.classList.toggle("active", isStorageSub);
  const advancedTitle = $("#nav-advanced-title");
  if (advancedTitle) advancedTitle.classList.toggle("active", isAdvancedSub);

  const bottomStorage = $("#bottom-storage-trigger");
  if (bottomStorage) bottomStorage.classList.toggle("active", isStorageSub);
  const bottomAdvanced = $("#bottom-advanced-trigger");
  if (bottomAdvanced) bottomAdvanced.classList.toggle("active", isAdvancedSub);

  if (isStorageSub) setNavGroupExpanded("storage", true, true);
  if (isAdvancedSub) setNavGroupExpanded("advanced", true, true);

  closeBottomSubMenu(false);

  if (page === "save") loadSaveStatus().catch(() => {});

  if (typeof setLlmUsageActive === "function") {
    setLlmUsageActive(page === "llm");
  }
  if (page === "about") loadAbout().catch((exc) => { $("#about-logs").textContent = "加载失败：" + exc.message; });
}

$$("[data-page]").forEach((btn) => btn.addEventListener("click", () => switchPage(btn.dataset.page)));

// 一级菜单纯折叠：不跳页、不标脏、首访收起、记忆到 localStorage
["storage", "advanced"].forEach((group) => {
  const expanded = isNavGroupExpanded(group);
  setNavGroupExpanded(group, expanded, false);
  const titleBtn = $("#nav-" + group + "-title");
  if (titleBtn) {
    titleBtn.addEventListener("click", (ev) => {
      if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
      const cur = titleBtn.getAttribute ? titleBtn.getAttribute("aria-expanded") === "true" : false;
      setNavGroupExpanded(group, !cur, true);
    });
  }
});

// 移动端底导航父项弹子菜单：外点、Escape、focus 返回
[
  ["#bottom-storage-trigger", "#bottom-storage-menu"],
  ["#bottom-advanced-trigger", "#bottom-advanced-menu"],
].forEach(([triggerSel, menuSel]) => {
  const trigger = $(triggerSel);
  const menu = $(menuSel);
  if (!trigger || !menu) return;
  trigger.addEventListener("click", (ev) => {
    if (ev && typeof ev.stopPropagation === "function") ev.stopPropagation();
    openBottomSubMenu(trigger, menu);
  });
});

window.addEventListener("click", (ev) => {
  if (!activeBottomMenu || activeBottomMenu.hidden) return;
  const target = ev.target;
  const inMenu = activeBottomMenu.contains ? activeBottomMenu.contains(target) : false;
  const inTrigger = activeBottomTrigger && activeBottomTrigger.contains ? activeBottomTrigger.contains(target) : false;
  if (!inMenu && !inTrigger) {
    closeBottomSubMenu(true);
  }
});

window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && activeBottomMenu && !activeBottomMenu.hidden) {
    if (typeof ev.preventDefault === "function") ev.preventDefault();
    closeBottomSubMenu(true);
  }
});

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

function formatVersionLabel(version) {
  return `v${String(version || "-").replace(/^v/i, "")}`;
}

/* -------------------------------------------------------------- 概览 */
async function loadStatus() {
  try {
    const st = await api("/api/status");
    publicProcessSnapshot = { at: Date.now(), processes: st.processes || {} };
    for (const item of publicSelection) {
      if (item._detail_status === "stopped"
          && publicProcessSnapshot.processes[publicUiProviderOf(item)]?.state === "RUNNING") {
        item._detail_status = "idle";
        item._detail_failed = false;
      }
    }
    void ensurePublicSelectionDetails();
    $("#brand-version").textContent = formatVersionLabel(st.version);
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
    lxRuntimeSnapshot = st.lx_source || null;
    syncLxSourceOrder();
    renderLxSourceList(); // 只同步运行徽标，不重载配置或丢弃尚未保存的编辑。
    renderLxSourceOrder();
    const lxCard = $("#ov-lx-card");
    if (st.current_provider === "lxmusic" && st.lx_source) {
      lxCard.hidden = false;
      const ls = st.lx_source;
      const rows = [];
      const sources = ls.sources || [];
      const ready = sources.filter((s) => s.status === "success").length;
      const failed = sources.filter((s) => s.status === "failed").length;
      const state = ls.state || (ls.error ? "unavailable" : sources.length ? "loading" : "unconfigured");
      rows.push(`<div class="kv"><b>状态</b>${escapeHtml(LX_STATE_LABEL[state] || state)}</div>`);
      rows.push(`<div class="kv"><b>源统计</b>就绪 ${escapeHtml(ls.ready_count ?? ready)} · 失败 ${escapeHtml(ls.failed_count ?? failed)} · 待定 ${escapeHtml(ls.pending_count ?? (sources.length - ready - failed))}</div>`);
      if (sources.length) {
        rows.push(`<div class="kv"><b>已配置源</b>${sources.length} 个</div>`);
        sources.forEach((s) => {
          const status = s.status || "unknown";
          rows.push(`<div class="kv"><b>${escapeHtml(s.name || s.id || "-")}${s.version ? " v" + escapeHtml(s.version) : ""}</b>${escapeHtml(LX_SOURCE_STATUS_LABEL[status] || status)}${s.error ? "：" + escapeHtml(s.error) : ""}</div>`);
        });
        const plats = new Set();
        sources.forEach((s) => (s.platforms || []).forEach((p) => plats.add(p)));
        rows.push(`<div class="kv"><b>平台</b>${escapeHtml([...plats].join("、")) || "-"}</div>`);
      } else if (ls.source) {
        rows.push(`<div class="kv"><b>源名称</b>${escapeHtml(ls.source.name || "-")} ${ls.source.version ? "v" + escapeHtml(ls.source.version) : ""}</div>`);
        rows.push(`<div class="kv"><b>平台</b>${escapeHtml(Object.keys(ls.source.platforms || {}).join("、")) || "-"}</div>`);
      }
      if (ls.last_error || ls.error) rows.push(`<div class="kv"><b>错误</b>${escapeHtml(ls.last_error || ls.error)}</div>`);
      $("#ov-lx").innerHTML = rows.join("");
    } else {
      lxCard.hidden = true;
    }
  } catch (exc) {
    // 旧快照不能无限期显示绿色“运行就绪”；保留用户尚未保存的编辑。
    lxRuntimeSnapshot = null;
    renderLxSourceList();
    $("#ov-provider-body").textContent = "状态加载失败：" + exc.message;
    $("#ov-processes").innerHTML = '<span class="chip"><span class="dot"></span>进程状态未知（获取失败）</span>';
    $("#ov-services").innerHTML = '<span class="state-line"><span class="dot"></span>服务状态未知（获取失败）</span>';
    $("#ov-lx").innerHTML = '<div class="kv"><b>状态</b>运行状态未知（获取失败）</div>';
  }
}

/* -------------------------------------------------------------- 配置读写 */
async function loadConfig() {
  const cfg = await api("/api/config");
  configValues = cfg.values;
  configPresence = cfg.presence || {};
  configSchema = cfg.schema || {};
  const expectedBuild = typeof window !== "undefined" ? window.FNMUSIC_WEBUI_BUILD || "" : "";
  const apiBuild = cfg.webui_build || "";
  const mismatch = Boolean(expectedBuild && apiBuild && expectedBuild !== apiBuild)
    || Boolean(expectedBuild && !apiBuild);
  $("#webui-build-mismatch").hidden = !mismatch;
  applyConfigToForm();
  clearDirty();
  if (configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER) await loadMusicUsers();
}

function applyConfigToForm() {
  const v = configValues;
  const provider = v.FNMUSIC_NETEASE_ENABLED === "true" ? "musicbox"
    : v.FNMUSIC_MUSICDL_ENABLED === "true" ? "musicdl"
    : v.FNMUSIC_LX_ENABLED === "true" ? "lxmusic" : "";
  $$("input[name=provider]").forEach((el) => { el.checked = el.value === provider; });
  syncProviderPanels(provider);
  if (provider === "musicbox") syncNeteaseAccount();
  const hasAutoSwitch = Boolean(configSchema.FNMUSIC_LX_AUTO_SWITCH);
  const hasPlatformPreferred = Boolean(configSchema.FNMUSIC_LX_PLATFORM_PREFERRED);
  $("#lx-auto-switch").disabled = !hasAutoSwitch;
  $("#lx-auto-switch").checked = hasAutoSwitch
    ? ["true", "1", "yes", "on"].includes(String(v.FNMUSIC_LX_AUTO_SWITCH ?? "true").trim().toLowerCase()) : false;
  $("#lx-platform-preferred").disabled = !hasPlatformPreferred;
  $("#lx-platform-preferred").checked = hasPlatformPreferred
    ? ["true", "1", "yes", "on"].includes(String(v.FNMUSIC_LX_PLATFORM_PREFERRED || "false").trim().toLowerCase()) : false;
  const playQuality = v.FNMUSIC_PLAY_QUALITY || "lossless";
  $$("input[name=play-quality]").forEach((el) => { el.checked = el.value === playQuality; });
  const downloadQuality = v.FNMUSIC_DOWNLOAD_QUALITY || "app";
  $$("input[name=download-quality]").forEach((el) => { el.checked = el.value === downloadQuality; });
  [
    ["#direct-netease", "FNMUSIC_NETEASE_DIRECT_PLAY"],
    ["#direct-musicdl", "FNMUSIC_MUSICDL_DIRECT_PLAY"],
    ["#direct-lx", "FNMUSIC_LX_DIRECT_PLAY"],
  ].forEach(([selector, key]) => {
    const el = $(selector);
    el.disabled = !configSchema[key];
    el.checked = configSchema[key] ? ["true", "1", "yes", "on"].includes(String(v[key] || "").trim().toLowerCase()) : false;
  });
  $("#recommend-hot").checked = v.FNMUSIC_RECOMMEND_HOT === "true";
  $("#recommend-daily").checked = v.FNMUSIC_RECOMMEND_DAILY === "true";
  $("#public-playlists-enabled").checked = v.FNMUSIC_PUBLIC_PLAYLISTS_ENABLED === "true";
  publicSelectionDetailSeq++;
  publicUserChanges.clear();
  if (configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER) {
    try {
      const mapping = JSON.parse(v.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER || "{}");
      publicSelectionMap = mapping && typeof mapping === "object" && !Array.isArray(mapping) ? mapping : {};
    } catch (_) { publicSelectionMap = {}; }
    publicSelection = Array.isArray(publicSelectionMap[publicUserGuid]) ? publicSelectionMap[publicUserGuid] : [];
  } else try {
    publicSelection = JSON.parse(v.FNMUSIC_PUBLIC_PLAYLISTS_SELECTION || "[]");
    if (!Array.isArray(publicSelection)) publicSelection = [];
  } catch (_) { publicSelection = []; }
  $("#public-playlists-browser").hidden = !$("#public-playlists-enabled").checked;
  renderMusicUsers();
  renderPublicSelected();
  // 刷新/保存后重建的 publicSelection 只有身份字段：浏览器可见时补回展示名称
  void ensurePublicSelectionDetails();
  $("#tee-enabled").checked = v.FNMUSIC_TEE_SAVE_ENABLED === "true";
  $("#auto-cover").checked = v.FNMUSIC_AUTO_COVER !== "false";
  $("#lyric-auto-dl").checked = v.FNMUSIC_LYRIC_AUTO_DL === "true";
  $("#fav-autobind").checked = v.FNMUSIC_FAV_AUTO_BIND === "true";
  $("#dir-download").value = v.FNMUSIC_TEE_SAVE_DIR || "";
  $("#download-layout").value = v.FNMUSIC_DOWNLOAD_LAYOUT || "flat";
  $("#download-layout").disabled = !configSchema.FNMUSIC_DOWNLOAD_LAYOUT;
  $("#tee-save-mode").value = ["auto", "direct", "staged"].includes(v.FNMUSIC_TEE_SAVE_MODE) ? v.FNMUSIC_TEE_SAVE_MODE : "auto";
  $("#tee-save-mode").disabled = !configSchema.FNMUSIC_TEE_SAVE_MODE;
  $("#dir-cache").value = v.FNMUSIC_CACHE_DIR || "";
  $("#tee-max").value = v.FNMUSIC_TEE_CACHE_MAX || "2";
  $("#bind-timeout").value = v.FNMUSIC_OFFICIAL_BIND_TIMEOUT_S || "120";
  $("#handoff-max").value = v.FNMUSIC_TEE_HANDOFF_MAX != null ? v.FNMUSIC_TEE_HANDOFF_MAX : "3";
  $("#scan-path").value = v.FNMUSIC_LIBRARY_SCAN_PATH || "";
  $("#scan-delay").value = v.FNMUSIC_SCAN_DELAY_S ?? "3";
  $("#scan-cooldown").value = v.FNMUSIC_SCAN_COOLDOWN_S ?? "90";
  updateScanLabels();
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
  $("#lx-resolver-timeout").value = v.LX_RESOLVER_TIMEOUT || "8.0";
  $("#lx-url-timeout").value = v.LX_URL_TIMEOUT || "15.0";
  $("#lx-probe-timeout").value = v.LX_PROBE_TIMEOUT || "3.0";
  lxSourceListDirty = false;
  try {
    const rawList = v.LX_SOURCE_LIST;
    lxSourceList = rawList ? JSON.parse(rawList) : [];
    if (!Array.isArray(lxSourceList)) lxSourceList = [];
  } catch (_) {
    lxSourceList = [];
  }
  try {
    const parsedOrder = JSON.parse(v.FNMUSIC_LX_SOURCE_ORDER || "[]");
    lxSourceOrder = Array.isArray(parsedOrder)
      ? [...new Set(parsedOrder.filter((id) => typeof id === "string" && id.trim()).map((id) => id.trim()))]
      : [];
  } catch (_) { lxSourceOrder = []; }
  // 兼容旧数据：列表无任何 active 标记时按 LX_SOURCE_URL 推导
  const savedUrl = (v.LX_SOURCE_URL || "").trim();
  if (lxSourceList.length && !lxSourceList.some((item) => "active" in item)) {
    lxSourceList.forEach((item) => { item.active = !!(savedUrl && item.url === savedUrl); });
  }
  // .env 里有激活地址但不在列表中（异常状态）：补进列表展示真实激活态
  if (savedUrl && !lxSourceList.some((item) => item.url === savedUrl)) {
    lxSourceList.unshift({ name: lxDefaultName(savedUrl), url: savedUrl, active: true });
  }
  syncLxSourceOrder();
  updateLxAutoSwitchSettings();
  renderLxSourceList();
  renderLxSourceOrder();
  syncLxAddButton();
  renderPlatformChips();
}

function collectConfig() {
  const provider = ($$("input[name=provider]").find((el) => el.checked) || {}).value || "";
  const values = {
    FNMUSIC_MUSICDL_ENABLED: provider === "musicdl",
    FNMUSIC_NETEASE_ENABLED: provider === "musicbox",
    FNMUSIC_LX_ENABLED: provider === "lxmusic",
    ...(configSchema.FNMUSIC_NETEASE_DIRECT_PLAY ? { FNMUSIC_NETEASE_DIRECT_PLAY: $("#direct-netease").checked } : {}),
    ...(configSchema.FNMUSIC_MUSICDL_DIRECT_PLAY ? { FNMUSIC_MUSICDL_DIRECT_PLAY: $("#direct-musicdl").checked } : {}),
    ...(configSchema.FNMUSIC_LX_DIRECT_PLAY ? { FNMUSIC_LX_DIRECT_PLAY: $("#direct-lx").checked } : {}),
    ...(configSchema.FNMUSIC_PLAY_QUALITY ? {
      FNMUSIC_PLAY_QUALITY: ($$("input[name=play-quality]").find((el) => el.checked) || {}).value || "lossless",
    } : {}),
    ...(configSchema.FNMUSIC_DOWNLOAD_QUALITY ? {
      FNMUSIC_DOWNLOAD_QUALITY: ($$("input[name=download-quality]").find((el) => el.checked) || {}).value || "app",
    } : {}),
    ...(configSchema.FNMUSIC_LX_AUTO_SWITCH ? { FNMUSIC_LX_AUTO_SWITCH: $("#lx-auto-switch").checked } : {}),
    ...(configSchema.FNMUSIC_LX_PLATFORM_PREFERRED && $("#lx-auto-switch").checked
      ? { FNMUSIC_LX_PLATFORM_PREFERRED: $("#lx-platform-preferred").checked } : {}),
    ...(configSchema.FNMUSIC_LX_SOURCE_ORDER
      ? { FNMUSIC_LX_SOURCE_ORDER: JSON.stringify(lxSourceOrder) } : {}),
    FNMUSIC_RECOMMEND_HOT: $("#recommend-hot").checked,
    FNMUSIC_RECOMMEND_DAILY: $("#recommend-daily").checked,
    FNMUSIC_PUBLIC_PLAYLISTS_ENABLED: $("#public-playlists-enabled").checked,
    FNMUSIC_TEE_SAVE_ENABLED: $("#tee-enabled").checked,
    FNMUSIC_AUTO_COVER: $("#auto-cover").checked,
    FNMUSIC_LYRIC_AUTO_DL: $("#lyric-auto-dl").checked,
    FNMUSIC_FAV_AUTO_BIND: $("#fav-autobind").checked,
    FNMUSIC_TEE_SAVE_DIR: $("#dir-download").value.trim(),
    ...(configSchema.FNMUSIC_DOWNLOAD_LAYOUT ? { FNMUSIC_DOWNLOAD_LAYOUT: $("#download-layout").value } : {}),
    ...(configSchema.FNMUSIC_TEE_SAVE_MODE ? { FNMUSIC_TEE_SAVE_MODE: ["auto", "direct", "staged"].includes($("#tee-save-mode").value) ? $("#tee-save-mode").value : "auto" } : {}),
    FNMUSIC_CACHE_DIR: $("#dir-cache").value.trim(),
    FNMUSIC_TEE_CACHE_MAX: parseInt($("#tee-max").value || "2", 10),
    FNMUSIC_OFFICIAL_BIND_TIMEOUT_S: parseInt($("#bind-timeout").value || "120", 10) || 120,
    FNMUSIC_TEE_HANDOFF_MAX: parseInt($("#handoff-max").value || "3", 10) || 0,
    FNMUSIC_LIBRARY_SCAN_PATH: $("#scan-path").value.trim(),
    FNMUSIC_SCAN_DELAY_S: parseFloat($("#scan-delay").value) || 0,
    FNMUSIC_SCAN_COOLDOWN_S: parseFloat($("#scan-cooldown").value) || 0,
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
  if (!$("#panel-lxmusic").hidden) {
    values.LX_RESOLVER_TIMEOUT = lxTimeoutNumber("#lx-resolver-timeout", "单次解析超时");
    values.LX_URL_TIMEOUT = lxTimeoutNumber("#lx-url-timeout", "取链总超时");
    values.LX_PROBE_TIMEOUT = lxTimeoutNumber("#lx-probe-timeout", "直链探活超时");
  }
  // 隐藏的参数不提交：关闭用途不清空已保存值，也不让隐藏的无效编辑阻塞保存。
  if (values.FNMUSIC_LLM_LIKED_ENABLED) {
    values.FNMUSIC_LIKED_REFRESH_HOURS = likedNumber("#liked-refresh-hours", "猜你喜欢生成间隔", 0.5, null);
    values.FNMUSIC_LIKED_SIZE = likedNumber("#liked-size", "猜你喜欢歌曲数量", 0, 100, true);
  }
  if (configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER) {
    stashPublicUser();
    if (publicUsersReady && publicUserChanges.size) {
      const changes = {};
      for (const user of publicUserChanges) {
        changes[user] = (publicSelectionMap[user] || []).map(({ provider, source, kind, id }) =>
          ({ provider, source, kind, id }));
      }
      values.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER = JSON.stringify(changes);
    }
  } else if ($("#public-playlists-enabled").checked || publicSelection.length || configPresence.FNMUSIC_PUBLIC_PLAYLISTS_SELECTION) {
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

function filterConfigBySchema(values) {
  const supported = new Set(Object.keys(configSchema || {}));
  // 正常后端总会返回 Schema；无 Schema 的旧测试桩/旧服务保留原有兼容流程。
  if (!supported.size) return values;
  return Object.fromEntries(Object.entries(values).filter(([key]) => supported.has(key)));
}

async function saveConfig() {
  let values;
  try { values = filterConfigBySchema(collectConfig()); } catch (exc) { toast(exc.message, "fail"); return; }
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
  const syncOrderThroughProxy = values.FNMUSIC_LX_ENABLED === true
    && $("#lx-auto-switch").checked
    && Boolean(configSchema.FNMUSIC_LX_SOURCE_ORDER);
  const requestedOrder = lxSourceOrder.slice();
  if (syncOrderThroughProxy) delete values.FNMUSIC_LX_SOURCE_ORDER;
  if (currentPage === "lx" && !lxSourceListDirty) delete values.LX_SOURCE_LIST;
  try {
    const result = await api("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values }),
    });
    let orderApplied = false;
    if (syncOrderThroughProxy) {
      // 配置保存可能为新激活源补入 lxserver ID；将它们追加到用户排序之后再调用专用代理。
      const savedConfig = await api("/api/config");
      let savedOrder = [];
      try {
        const parsed = JSON.parse(savedConfig.values.FNMUSIC_LX_SOURCE_ORDER || "[]");
        if (Array.isArray(parsed)) savedOrder = parsed.filter((id) => typeof id === "string");
      } catch (_) { /* 共享配置格式错误时保留当前顺序，接口会给出明确校验错误 */ }
      const finalOrder = requestedOrder.slice();
      savedOrder.forEach((id) => { if (!finalOrder.includes(id)) finalOrder.push(id); });
      if (JSON.stringify(finalOrder) !== JSON.stringify(savedOrder)) {
        try {
          const reorder = await api("/api/lx/source/reorder", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source_ids: finalOrder }),
          });
          orderApplied = Boolean((reorder.changed || []).includes("FNMUSIC_LX_SOURCE_ORDER"));
          if (orderApplied) result.changed = [...new Set([...(result.changed || []), "FNMUSIC_LX_SOURCE_ORDER"])];
        } catch (exc) {
          await loadConfig();
          await loadStatus();
          const isBusy = exc.status === 409 || exc.body?.category === "source_operation_busy";
          const reason = isBusy
            ? (exc.message && exc.message.includes("忙") ? exc.message : "洛雪源正忙，请稍候重试")
            : exc.message;
          toast(`其他配置已保存；洛雪源排序失败，${reason}；已恢复保存顺序`, "fail");
          return;
        }
      }
    }
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
      if (w.category && !["runtime", "runtime_error"].includes(w.category)) {
        const reason = { service_unavailable: "服务暂不可用", initialization_pending: "初始化待定", source_operation_busy: "源操作忙碌" }[w.category] || "状态暂不可确认";
        parts.push(`洛雪源「${w.source || lxDefaultName(w.url)}」${reason}，激活配置已保留${w.message ? `（${w.message}）` : ""}`);
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
    lxRuntimeSnapshot = null;
    renderLxSourceList();
    toast("保存失败：" + exc.message, "fail");
    await loadStatus();
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

function stashPublicUser(changed = false) {
  if (!configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER || !publicUserGuid) return;
  publicSelectionMap[publicUserGuid] = publicSelection;
  if (changed) publicUserChanges.add(publicUserGuid);
}

function renderMusicUsers() {
  const panel = $("#public-users-panel");
  if (!panel) return;
  const perUser = !!configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER;
  panel.hidden = !perUser || !$("#public-playlists-enabled").checked;
  if (!perUser) return;
  const selector = $("#public-user");
  selector.innerHTML = '<option value="">请先选择飞牛音乐用户</option>' + publicUsers.map((user) =>
    `<option value="${escapeHtml(user.guid)}">${escapeHtml(user.name || user.guid)}${user.role === "admin" ? "（管理员）" : ""}</option>`).join("");
  selector.value = publicUserGuid;
  selector.disabled = !publicUsersReady || !publicUsers.length;
  $("#public-playlists-browser").hidden = panel.hidden || !publicUsersReady || !publicUserGuid;
}

async function loadMusicUsers() {
  const seq = ++publicUsersSeq;
  $("#public-users-retry").disabled = true;
  $("#public-users-note").textContent = "正在读取飞牛音乐用户…";
  try {
    const data = await api("/api/maintenance/music-users");
    if (seq !== publicUsersSeq) return;
    if (data.ok === false || !Array.isArray(data.users)) throw new Error(data.error || "用户列表响应无效");
    publicUsers = data.users;
    publicUsersReady = true;
    if (!publicUserChanges.size && data.public_playlists_by_user) {
      publicSelectionMap = data.public_playlists_by_user;
    }
    if (!publicUsers.some((user) => user.guid === publicUserGuid)) publicUserGuid = "";
    publicSelection = publicSelectionMap[publicUserGuid] || [];
    $("#public-users-note").textContent = publicUsers.length
      ? `共 ${publicUsers.length} 位有效用户。先选择用户，再配置歌单；新用户默认没有公共歌单。`
      : "飞牛音乐中还没有有效用户。请先登录飞牛音乐，再刷新用户。";
  } catch (exc) {
    if (seq !== publicUsersSeq) return;
    publicUsersReady = false;
    invalidatePublicBrowse();
    publicSelectionDetailSeq++;
    abortActivePublicDetailRequests();
    const isDirectRestricted = (exc && (exc.status === 403 || exc.status === 501))
      || String(exc && exc.message || "").includes("403")
      || String(exc && exc.message || "").includes("501")
      || String(exc && exc.message || "").includes("直连模式")
      || String(exc && exc.message || "").includes("安全架构")
      || String(exc && exc.message || "").includes("管理员");
    if (isDirectRestricted) {
      $("#public-users-note").textContent = "安全架构限制：直连模式不支持读取飞牛音乐用户，请在飞牛桌面内打开管理台。";
    } else {
      $("#public-users-note").textContent = "读取用户失败：" + exc.message + "。请点击刷新用户重试。";
    }
  } finally {
    if (seq === publicUsersSeq) {
      $("#public-users-retry").disabled = false;
      renderMusicUsers();
      renderPublicSelected();
      if (publicUsersReady) void ensurePublicSelectionDetails();
      updateResidentAvailability();
    }
  }
}

async function selectPublicUser(guid) {
  stashPublicUser();
  invalidatePublicBrowse();
  publicSelectionDetailSeq++;
  abortActivePublicDetailRequests();
  publicUserGuid = publicUsersReady && publicUsers.some((user) => user.guid === guid) ? guid : "";
  publicSelection = publicSelectionMap[publicUserGuid] || [];
  renderMusicUsers();
  renderPublicSelected();
  void ensurePublicSelectionDetails();
  if (publicUserGuid && $("#public-playlists-enabled").checked) await showPublicBrowser();
}

$("#public-user").addEventListener("change", () => selectPublicUser($("#public-user").value));
$("#public-users-retry").addEventListener("click", async () => {
  await loadMusicUsers();
  if (publicUsersReady && publicUserGuid) await showPublicBrowser();
});

function anyPublicMusicboxSelection() {
  stashPublicUser();
  const selections = configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER
    ? Object.values(publicSelectionMap) : [publicSelection];
  return selections.some((selection) => Array.isArray(selection) && selection.some((item) => item.provider === "musicbox"));
}

function publicEntryKey(item) {
  return [item.provider, item.source, item.provider === "musicbox" ? "playlist" : item.kind, item.id].join("|");
}

function selectedPublicEntry(item) {
  return publicSelection.find((it) => publicEntryKey(it) === publicEntryKey(item));
}

function setPublicSelection(item, selected) {
  if (configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER && (!publicUsersReady || !publicUserGuid)) return false;
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
    setPublicDetailCache(publicEntryKey(item), item, true);
  } else {
    publicSelection = publicSelection.filter((it) => publicEntryKey(it) !== publicEntryKey(item));
  }
  stashPublicUser(true);
  renderPublicSelected();
  updateResidentAvailability();
  markDirty("公共歌单选择已修改");
  renderPublicList();
  void ensurePublicSelectionDetails();
  return true;
}

function movePublicSelection(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= publicSelection.length) return;
  const items = publicSelection.splice(index, 1);
  publicSelection.splice(next, 0, items[0]);
  stashPublicUser(true);
  renderPublicSelected();
  markDirty("公共歌单顺序已修改");
}

function loadPublicMetadataStorage() {
  try {
    if (typeof localStorage === "undefined") return {};
    const raw = localStorage.getItem(PUBLIC_METADATA_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch (_) {
    return {};
  }
}

function savePublicMetadataStorage(data) {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(PUBLIC_METADATA_STORAGE_KEY, JSON.stringify(data));
  } catch (_) {}
}

function initPublicDetailCacheFromStorage() {
  const stored = loadPublicMetadataStorage();
  const now = Date.now();
  let changed = false;
  for (const [key, record] of Object.entries(stored)) {
    if (!record || typeof record !== "object") continue;
    if (record.failed) {
      delete stored[key];
      changed = true;
    } else if (record.name && String(record.name).trim() && record.cachedAt && (now - record.cachedAt < PUBLIC_METADATA_TTL_MS)) {
      publicDetailCache.set(key, {
        name: String(record.name).trim(),
        cover_url: record.cover_url || "",
        track_count: Number(record.track_count) || 0,
        cachedAt: record.cachedAt,
      });
    } else {
      delete stored[key];
      changed = true;
    }
  }
  if (changed) {
    savePublicMetadataStorage(stored);
  }
}
initPublicDetailCacheFromStorage();

function setPublicDetailCache(key, entry, isFromNetwork = false) {
  const existing = publicDetailCache.get(key);
  const now = Date.now();
  if (entry.failed) {
    const failedAt = entry.failedAt || now;
    // 失败只保留在内存缓存中（30s 瞬时失败），绝不写入 localStorage 持久化
    publicDetailCache.set(key, { failed: true, failedAt });
    return;
  }
  const name = String(entry.name || "").trim();
  if (!name) return;
  const cachedAt = (isFromNetwork || !existing?.cachedAt) ? now : existing.cachedAt;
  const itemData = {
    name,
    cover_url: entry.cover_url || "",
    track_count: Number(entry.track_count) || 0,
    cachedAt,
  };
  publicDetailCache.set(key, itemData);

  // 仅在网络返回或新元数据时写入 localStorage，避免每次 render 频繁读写
  if (isFromNetwork || !existing) {
    const parts = key.split("|");
    const stored = loadPublicMetadataStorage();
    stored[key] = {
      provider: parts[0] || "",
      source: parts[1] || "",
      kind: parts[2] || "",
      id: parts[3] || "",
      name: itemData.name,
      cover_url: itemData.cover_url,
      track_count: itemData.track_count,
      cachedAt: itemData.cachedAt,
    };
    savePublicMetadataStorage(stored);
  }
}

function renderPublicSelected() {
  const box = $("#public-selected");
  if (!box) return;
  box.innerHTML = publicSelection.length ? publicSelection.map((item, index) => {
    // Unified cache entry preserves its original expiry when rendering.
    if (item._name) {
      const key = publicEntryKey(item);
      if (!publicDetailCache.has(key)) {
        setPublicDetailCache(key, {
          name: item._name, cover_url: item._cover_url || "", track_count: item._track_count || 0,
        });
      }
    }
    // 状态判定：idle / loading / success / error / unavailable
    // 只在 inflight 显示 loading，空 name 失败、缓存不当成功；兼容原有 _detail_failed 断言
    let title = "";
    if (item._name) {
      title = escapeHtml(item._name);
    } else if (item._detail_status === "stopped") {
      title = "音源未运行";
    } else if (item._detail_status === "unavailable") {
      title = "音源不可用";
    } else if (item._detail_status === "error" || item._detail_failed) {
      title = "名称获取失败";
    } else if (item._detail_status === "loading") {
      title = "正在获取歌单名称…";
    } else {
      title = "等待获取歌单名称";
    }
    const showRetry = item._detail_failed || ["error", "unavailable", "stopped"].includes(item._detail_status);
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
        ${showRetry ? `<button class="btn small" data-public-retry="${index}">重试</button>` : ""}
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
      stashPublicUser(true);
      void ensurePublicSelectionDetails();
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
async function fetchPublicItemDetail(item, signal) {
  const query = new URLSearchParams({
    provider: publicUiProviderOf(item), source: item.source, kind: item.kind,
  });
  const data = await api(`/api/public-playlists/${encodeURIComponent(item.id)}?${query.toString()}`, { signal });
  if (data.ok === false) throw new Error(data.error || "歌单详情请求失败");
  const detail = data.data || {};
  const name = typeof detail.name === "string" ? detail.name.trim() : "";
  if (!name) throw new Error("歌单名称为空");
  return { ...detail, name };
}

function applyPublicDetailCache(item) {
  const key = publicEntryKey(item);
  const cached = publicDetailCache.get(key);
  if (!cached) return false;
  if (cached.failed) {
    if (Date.now() - cached.failedAt < PUBLIC_FAILED_TTL_MS) {
      item._detail_failed = true;
      item._detail_status = "error";
      return true;
    }
    publicDetailCache.delete(key);
    const stored = loadPublicMetadataStorage();
    delete stored[key];
    savePublicMetadataStorage(stored);
    return false;
  }
  if (cached.cachedAt && (Date.now() - cached.cachedAt > PUBLIC_METADATA_TTL_MS)) {
    publicDetailCache.delete(key);
    const stored = loadPublicMetadataStorage();
    delete stored[key];
    savePublicMetadataStorage(stored);
    return false;
  }
  const name = String(cached.name || "").trim();
  if (!name) return false;
  item._name = name;
  item._cover_url = cached.cover_url || "";
  item._track_count = Number(cached.track_count) || 0;
  item._detail_failed = false;
  item._detail_status = "success";
  return true;
}

function abortActivePublicDetailRequests() {
  for (const ctrl of publicDetailAbortControllers) {
    try { ctrl.abort(); } catch (_) {}
  }
  publicDetailAbortControllers.clear();
  publicDetailInflight.clear();
  publicDetailRun = null;
}

async function ensurePublicSelectionDetails() {
  if (configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER && (!publicUsersReady || !publicUserGuid)) return;
  // Removing entries cancels only their own work. Browsing never cancels names.
  const keys = new Set(publicSelection.map(publicEntryKey));
  for (const ctrl of publicDetailAbortControllers) {
    if (!keys.has(ctrl.publicKey)) ctrl.abort();
  }
  if (publicDetailRun && publicDetailRunUser === publicUserGuid) return publicDetailRun;
  publicDetailRunUser = publicUserGuid;
  const run = runPublicSelectionDetails();
  publicDetailRun = run;
  try {
    await run;
  } finally {
    if (publicDetailRun === run) {
      publicDetailRun = null;
      // Entries added while workers were busy join the next bounded queue.
      if (publicSelection.some((item) => !item._name && !item._detail_failed
          && !["error", "unavailable"].includes(item._detail_status))) {
        void ensurePublicSelectionDetails();
      }
    }
  }
}

async function runPublicSelectionDetails() {
  const seq = ++publicSelectionDetailSeq;
  const currentUserGuid = publicUserGuid;

  for (const item of publicSelection) {
    if (item._name) {
      item._detail_status = "success";
      item._detail_failed = false;
      continue;
    }
    if (!applyPublicDetailCache(item)) {
      const process = publicProcessSnapshot?.processes[publicUiProviderOf(item)];
      const stopped = publicProcessSnapshot && Date.now() - publicProcessSnapshot.at < 30000
        && ["STOPPED", "EXITED", "FATAL"].includes(process?.state);
      item._detail_status = stopped ? "stopped" : "idle";
      item._detail_failed = Boolean(stopped);
    }
  }
  renderPublicSelected();

  const toFetch = [];
  for (const item of publicSelection) {
    if (item._name || item._detail_failed || item._detail_status === "error" || item._detail_status === "unavailable") continue;
    toFetch.push(item);
  }
  if (!toFetch.length) return;

  let queueIndex = 0;
  let activeWorkers = 0;

  return new Promise((resolve) => {
    function scheduleNext() {
      if (seq !== publicSelectionDetailSeq || publicUserGuid !== currentUserGuid) {
        resolve();
        return;
      }
      if (queueIndex >= toFetch.length && activeWorkers === 0) {
        resolve();
        return;
      }
      while (activeWorkers < MAX_PUBLIC_DETAIL_CONCURRENCY && queueIndex < toFetch.length) {
        if (seq !== publicSelectionDetailSeq || publicUserGuid !== currentUserGuid) {
          resolve();
          return;
        }
        const item = toFetch[queueIndex++];
        const key = publicEntryKey(item);
        if (!publicSelection.includes(item)) continue;

        if (publicDetailInflight.has(key)) {
          activeWorkers++;
          item._detail_status = "loading";
          renderPublicSelected();
          publicDetailInflight.get(key).then(() => {
            activeWorkers--;
            if (seq === publicSelectionDetailSeq && publicUserGuid === currentUserGuid) {
              for (const it of publicSelection) {
                if (publicEntryKey(it) === key) applyPublicDetailCache(it);
              }
              renderPublicSelected();
            }
            scheduleNext();
          }).catch(() => {
            activeWorkers--;
            if (seq === publicSelectionDetailSeq && publicUserGuid === currentUserGuid) {
              for (const it of publicSelection) {
                if (publicEntryKey(it) === key) applyPublicDetailCache(it);
              }
              renderPublicSelected();
            }
            scheduleNext();
          });
          continue;
        }

        activeWorkers++;
        item._detail_status = "loading";
        renderPublicSelected();

        const controller = new AbortController();
        controller.publicKey = key;
        let unavailable = false;
        publicDetailAbortControllers.add(controller);
        const timer = setTimeout(() => {
          try { controller.abort(); } catch (_) {}
        }, 10000);
        if (timer && typeof timer.unref === "function") timer.unref();

        const fetchPromise = (async () => {
          try {
            const detail = await fetchPublicItemDetail(item, controller.signal);
            if (seq !== publicSelectionDetailSeq || publicUserGuid !== currentUserGuid
                || !publicSelection.includes(item)) return;
            setPublicDetailCache(key, {
              name: detail.name || "",
              cover_url: detail.cover_url || "",
              track_count: detail.track_count || 0,
            }, true);
          } catch (exc) {
            if (seq !== publicSelectionDetailSeq || publicUserGuid !== currentUserGuid) return;
            if (!publicSelection.includes(item)) return;
            setPublicDetailCache(key, { failed: true, failedAt: Date.now() });
            unavailable = exc.status === 503 && /未运行|not.running|not.started/i.test(exc.message);
          } finally {
            clearTimeout(timer);
            publicDetailAbortControllers.delete(controller);
            if (publicDetailInflight.get(key) === fetchPromise) publicDetailInflight.delete(key);
            activeWorkers--;
            if (seq === publicSelectionDetailSeq && publicUserGuid === currentUserGuid) {
              for (const it of publicSelection) {
                if (publicEntryKey(it) === key) applyPublicDetailCache(it);
              }
              if (unavailable) item._detail_status = "unavailable";
              renderPublicSelected();
            }
            scheduleNext();
          }
        })();

        publicDetailInflight.set(key, fetchPromise);
      }
    }

    scheduleNext();
  });
}

async function retryPublicDetail(index) {
  const item = publicSelection[index];
  if (!item) return;
  const key = publicEntryKey(item);
  publicDetailCache.delete(key);
  try {
    const stored = loadPublicMetadataStorage();
    delete stored[key];
    savePublicMetadataStorage(stored);
  } catch (_) {}
  delete item._detail_failed;
  delete item._cap_retried;
  item._detail_status = "idle";
  renderPublicSelected();
  // explicit retry 可通过现有 startPreview 拉起 item provider 再重试，自动补名不启动预览
  const uiProvider = publicUiProviderOf(item);
  try {
    await startPreview(uiProvider);
  } catch (_) {}
  publicProcessSnapshot = null;
  delete item._detail_failed;
  item._detail_status = "idle";
  await ensurePublicSelectionDetails();
}

function annotatePublicSelectionAvailability() {
  const browsingProvider = publicState.provider;
  for (const item of publicSelection) {
    if (publicUiProviderOf(item) !== browsingProvider) continue;
    const cap = ((publicCapabilities || {}).sources || []).find((source) => source.id === item.source);
    item._reason = cap && !cap.playback_available ? cap.reason || "当前平台没有播放能力" : "";
    if (cap && !cap.playback_available) {
      item._detail_status = "unavailable";
      item._detail_failed = true;
    }
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
    annotatePublicSelectionAvailability();
    // 音源就绪时自动恢复先于预览启动的失败条目，但通过 _cap_retried 防止后续 capabilities 刷新反复清失败
    for (const item of publicSelection) {
      if (publicUiProviderOf(item) !== provider) continue;
      if (item._cap_retried) continue;
      const key = publicEntryKey(item);
      if (publicDetailCache.get(key)?.failed) {
        publicDetailCache.delete(key);
        delete item._detail_failed;
        item._detail_status = "idle";
        item._cap_retried = true;
      }
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
  void ensurePublicSelectionDetails();
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
  if (configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER && (!publicUsersReady || !publicUserGuid)) {
    renderMusicUsers();
    return;
  }
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
  renderMusicUsers();
  if (!enabled) {
    invalidatePublicBrowse();
    publicState.loading = false;
    publicSelectionDetailSeq++;
    abortActivePublicDetailRequests();
    $("#public-playlists-browser").hidden = true;
    return;
  }
  if (configSchema.FNMUSIC_PUBLIC_PLAYLISTS_BY_USER) {
    if (!publicUsersReady) await loadMusicUsers();
  } else if (!publicSelection.length && !configPresence.FNMUSIC_PUBLIC_PLAYLISTS_SELECTION) await initPublicDefaults();
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
    && anyPublicMusicboxSelection();
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
  const publicNeedsMusicbox = anyPublicMusicboxSelection();
  if ($("#netease-playlist-resident").checked && !$("#netease-my-playlists").checked && !publicNeedsMusicbox) {
    $("#netease-my-playlists").checked = true;
  }
});

/* -------------------------------------------------------------- lx 源测试 */
function escapeHtml(str) {
  return String(str ?? "")
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

function findLxRuntimeSource(item, sources) {
  const list = Array.isArray(sources) ? sources : [];
  const itemUrl = (item && item.url) || "";
  if (itemUrl) {
    const exact = list.find((s) => s && s.url && s.url === itemUrl);
    if (exact) return exact;
  }
  if (itemUrl.toLowerCase().startsWith("file://")) {
    let base = itemUrl.replace(/\\/g, "/").split("/").pop() || "";
    try { base = decodeURIComponent(base); } catch (_) { /* 忽略 */ }
    const stem = base.replace(/\.js$/i, "");
    const byFile = list.filter((s) => s && (
      (base && (s.id === base || s.name === base))
      || (stem && (s.id === stem || s.name === stem))
    ));
    if (byFile.length) return byFile.length === 1 ? byFile[0] : undefined;
    const byName = list.filter((s) => s && item.name && (s.name === item.name || s.id === item.name));
    if (byName.length === 1) return byName[0];
  }
  return undefined;
}

// 只用持久化 source.id、准确 URL 或本地上传文件 ID 建立排序身份，不按显示名称猜测。
function lxStableSourceId(item) {
  if (item && typeof item.id === "string" && item.id.trim()) return item.id.trim();
  if (item && typeof item.source_id === "string" && item.source_id.trim()) return item.source_id.trim();
  const sources = Array.isArray(lxRuntimeSnapshot?.sources) ? lxRuntimeSnapshot.sources : [];
  const url = String(item?.url || "");
  const exactUrl = sources.filter((source) => source && source.url === url && source.id);
  if (exactUrl.length === 1) return String(exactUrl[0].id);
  if (url.toLowerCase().startsWith("file://")) {
    let filename = url.replace(/\\/g, "/").split("/").pop() || "";
    try { filename = decodeURIComponent(filename); } catch (_) { /* 保留原始路径尾段 */ }
    const ids = sources.filter((source) => source && source.id === filename).map((source) => String(source.id));
    if (ids.length === 1) return ids[0];
  }
  return "";
}

function syncLxSourceOrder() {
  lxSourceList.forEach((item) => {
    const id = lxStableSourceId(item);
    if (id && !item.id) {
      item.id = id;
      lxSourceListDirty = true;
    }
    if (id && item.active && !lxSourceOrder.includes(id)) lxSourceOrder.push(id);
  });
}

function getLxSourceOrder() {
  return lxSourceOrder.slice();
}

function activeLxOrderItems() {
  return lxSourceList.filter((item) => item && item.active).map((item, index) => ({
    item, index, id: lxStableSourceId(item),
  })).sort((left, right) => {
    const leftPosition = left.id ? lxSourceOrder.indexOf(left.id) : -1;
    const rightPosition = right.id ? lxSourceOrder.indexOf(right.id) : -1;
    if (leftPosition >= 0 && rightPosition >= 0) return leftPosition - rightPosition;
    return left.index - right.index;
  });
}

function canReorderLxSources() {
  if (!$("#lx-auto-switch").checked || !configSchema.FNMUSIC_LX_AUTO_SWITCH
      || !configSchema.FNMUSIC_LX_SOURCE_ORDER) return false;
  const ids = activeLxOrderItems().map((entry) => entry.id);
  return ids.every(Boolean) && new Set(ids).size === ids.length;
}

function updateLxAutoSwitchSettings() {
  const autoSwitch = $("#lx-auto-switch");
  const preferredRow = $("#lx-platform-preferred-row");
  const preferred = $("#lx-platform-preferred");
  const orderCard = $("#lx-source-order-card");
  const enabled = Boolean(configSchema.FNMUSIC_LX_AUTO_SWITCH && autoSwitch.checked);
  preferredRow.hidden = !enabled;
  preferred.disabled = !configSchema.FNMUSIC_LX_PLATFORM_PREFERRED || !enabled;
  const reorderable = canReorderLxSources();
  orderCard.classList.toggle("is-disabled", !reorderable);
  orderCard.setAttribute("aria-disabled", String(!reorderable));
  renderLxSourceOrder();
}

function reorderLxSourceTo(sourceId, targetIndex) {
  if (!canReorderLxSources()) return false;
  const visibleIds = activeLxOrderItems().map((entry) => entry.id);
  const oldIndex = visibleIds.indexOf(sourceId);
  if (oldIndex < 0 || targetIndex < 0 || targetIndex >= visibleIds.length || oldIndex === targetIndex) return false;
  visibleIds.splice(targetIndex, 0, visibleIds.splice(oldIndex, 1)[0]);
  const activeIds = new Set(visibleIds);
  let pointer = 0;
  const next = lxSourceOrder.map((id) => activeIds.has(id) ? visibleIds[pointer++] : id);
  while (pointer < visibleIds.length) next.push(visibleIds[pointer++]);
  if (next.every((id, index) => id === lxSourceOrder[index]) && next.length === lxSourceOrder.length) return false;
  lxSourceOrder = next;
  renderLxSourceOrder();
  markDirty("洛雪源顺序已修改，保存后生效");
  return true;
}

function moveLxSource(sourceId, offset) {
  if (!canReorderLxSources()) return false;
  const visibleIds = activeLxOrderItems().map((entry) => entry.id);
  const index = visibleIds.indexOf(sourceId);
  if (index < 0) return false;
  return reorderLxSourceTo(sourceId, index + offset);
}

let draggingLxSourceId = "";

function renderLxSourceOrder() {
  const container = $("#lx-source-order-list");
  const note = $("#lx-source-order-note");
  if (!container || !note) return;
  syncLxSourceOrder();
  const rows = activeLxOrderItems();
  const reorderable = canReorderLxSources();
  if (!rows.length) {
    container.innerHTML = '<span class="muted">暂无已激活源</span>';
    note.textContent = "";
    return;
  }
  container.innerHTML = rows.map(({ item, id }, index) => {
    const label = escapeHtml(item.name || lxDefaultName(item.url));
    const disabled = !reorderable || !id;
    const idText = id ? "" : "（待同步稳定 ID）";
    return `<div class="lx-order-item" role="listitem" draggable="${disabled ? "false" : "true"}"
        tabindex="${disabled ? "-1" : "0"}" aria-disabled="${disabled}" data-source-id="${escapeHtml(id)}">
      <span class="lx-order-handle" aria-hidden="true">⋮⋮</span>
      <span class="lx-order-name">${label}${idText}</span>
      <div class="lx-order-buttons">
        <button class="btn small lx-order-up" type="button" data-source-id="${escapeHtml(id)}" aria-label="${label}上移" ${disabled || index === 0 ? "disabled" : ""}>上移</button>
        <button class="btn small lx-order-down" type="button" data-source-id="${escapeHtml(id)}" aria-label="${label}下移" ${disabled || index === rows.length - 1 ? "disabled" : ""}>下移</button>
      </div>
    </div>`;
  }).join("");
  if (!$("#lx-auto-switch").checked) {
    note.textContent = "自动切换已关闭，排序已禁用；已保存顺序不会清空。";
  } else if (rows.some((row) => !row.id)) {
    note.textContent = "部分旧源还没有稳定 ID。切到洛雪主音源后，WebUI 会按已验证 URL 或上传文件身份同步；不会按名称猜测。";
  } else {
    note.textContent = "可拖动排序，也可用上移/下移按钮操作。";
  }

  container.querySelectorAll(".lx-order-up").forEach((button) => {
    button.addEventListener("click", () => moveLxSource(button.dataset.sourceId, -1));
  });
  container.querySelectorAll(".lx-order-down").forEach((button) => {
    button.addEventListener("click", () => moveLxSource(button.dataset.sourceId, 1));
  });
  container.querySelectorAll(".lx-order-item").forEach((row) => {
    row.addEventListener("dragstart", (event) => {
      if (!canReorderLxSources()) { event.preventDefault(); return; }
      draggingLxSourceId = row.dataset.sourceId || "";
      if (event.dataTransfer) event.dataTransfer.setData("text/plain", draggingLxSourceId);
    });
    row.addEventListener("touchstart", (event) => {
      if (!canReorderLxSources()) event.preventDefault();
    }, { passive: false });
    row.addEventListener("dragover", (event) => {
      if (canReorderLxSources()) event.preventDefault();
    });
    row.addEventListener("drop", (event) => {
      if (!canReorderLxSources()) return;
      event.preventDefault();
      const sourceId = (event.dataTransfer && event.dataTransfer.getData("text/plain")) || draggingLxSourceId;
      const targetIndex = activeLxOrderItems().findIndex((entry) => entry.id === row.dataset.sourceId);
      reorderLxSourceTo(sourceId, targetIndex);
      draggingLxSourceId = "";
    });
    row.addEventListener("dragend", () => { draggingLxSourceId = ""; });
    row.addEventListener("keydown", (event) => {
      if (!canReorderLxSources()) {
        if (event.key.startsWith("Arrow")) event.preventDefault();
        return;
      }
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        moveLxSource(row.dataset.sourceId, event.key === "ArrowUp" ? -1 : 1);
      }
    });
  });
}

function renderLxSourceList() {
  const container = $("#lx-source-list");
  if (!container) return;

  if (!lxSourceList.length) {
    container.innerHTML = `<span class="muted pad">暂无洛雪音乐源，上传 .js 或填写脚本地址后点击「添加」加入列表</span>`;
    return;
  }

  const saved = lxSavedActiveUrls();
  container.innerHTML = lxSourceList.map((item, idx) => {
    const isActive = !!item.active;
    const wasActive = saved.has(item.url);
    const configLabel = isActive !== wasActive
      ? (isActive ? "待保存激活" : "待保存取消") : wasActive ? "已配置启用" : "未启用";
    const runtime = findLxRuntimeSource(item, (lxRuntimeSnapshot || {}).sources);
    const status = runtime?.status || "unknown";
    const runtimeLabel = !lxRuntimeSnapshot ? "运行状态未知"
      : lxRuntimeSnapshot.state === "unavailable" ? "服务不可用"
      : runtime ? (LX_SOURCE_STATUS_LABEL[status] || status) : wasActive ? "运行状态未知" : "未运行";
    const runtimeError = runtime?.error || "";
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
            ${isActive || wasActive ? `<span class="lx-badge">${configLabel}</span>` : `<span class="muted">${configLabel}</span>`}
            <span class="chip"><span class="dot ${runtime && status === "success" ? "ok" : runtime && status === "failed" ? "err" : ""}"></span>${escapeHtml(runtimeLabel)}</span>
          </div>
          <div class="lx-item-url" title="${safeUrl}">${safeUrl}</div>
          ${runtimeError ? `<div class="muted">${escapeHtml(runtimeError)}</div>` : ""}
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
  lxSourceListDirty = true;
  syncLxSourceOrder();
  renderLxSourceList();
  renderLxSourceOrder();
  markDirty("已标记激活，需点击下方「保存并生效」（新激活源会先自动校验）");
  toast(`已标记激活「${item.name || lxDefaultName(item.url)}」，请点击下方「保存并生效」`, "ok");
}

function deactivateLxSource(idx) {
  const item = lxSourceList[idx];
  if (!item || !item.active) return;
  item.active = false;
  lxSourceListDirty = true;
  renderLxSourceList();
  renderLxSourceOrder();
  markDirty("已标记取消激活，需点击下方「保存并生效」");
  toast(`已标记取消激活「${item.name || lxDefaultName(item.url)}」，保存后停用`, "ok");
}

function deleteLxSource(idx) {
  const item = lxSourceList[idx];
  if (!item) return;
  const name = item.name || lxDefaultName(item.url);
  const sourceId = lxStableSourceId(item);
  lxSourceList.splice(idx, 1);
  lxSourceListDirty = true;
  if (sourceId) lxSourceOrder = lxSourceOrder.filter((id) => id !== sourceId);
  renderLxSourceList();
  renderLxSourceOrder();
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
  lxSourceListDirty = true;
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
$("#download-layout").addEventListener("change", () => {
  markDirty("存放方式需保存后生效；已有歌曲可稍后点击「整理」调整");
});
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
["#tee-max", "#llm-base", "#llm-key", "#llm-model", "#liked-refresh-hours", "#liked-size", "#search-timeout", "#search-deep-max", "#bind-timeout", "#handoff-max", "#scan-path", "#lx-resolver-timeout", "#lx-url-timeout", "#lx-probe-timeout"].forEach((sel) =>
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
$$("input[name=play-quality]").forEach((el) => el.addEventListener("change", () => markDirty("在线播放音质需保存后生效")));
$$("input[name=download-quality]").forEach((el) => el.addEventListener("change", () => markDirty("APP下载音质需保存后生效")));
$("#lx-auto-switch").addEventListener("change", () => {
  updateLxAutoSwitchSettings();
  markDirty("洛雪自动切源设置需保存后生效");
});
$("#lx-platform-preferred").addEventListener("change", () => markDirty("洛雪平台优选设置需保存后生效"));
["#recommend-hot", "#recommend-daily", "#search-deep", "#tee-enabled", "#fav-autobind", "#auto-cover", "#lyric-auto-dl", "#netease-my-playlists", "#netease-playlist-resident"].forEach((sel) =>
  $(sel).addEventListener("change", () => markDirty()));
["#direct-netease", "#direct-musicdl", "#direct-lx"].forEach((sel) =>
  $(sel).addEventListener("change", () => markDirty("播放直连策略需保存后生效")));

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

function updateScanLabels() {
  $("#scan-delay-label").textContent = `（${$("#scan-delay").value || 0} 秒）`;
  $("#scan-cooldown-label").textContent = `（${$("#scan-cooldown").value || 0} 秒）`;
}
$("#scan-delay").addEventListener("input", updateScanLabels);
$("#scan-cooldown").addEventListener("input", updateScanLabels);

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
  $("#about-version").textContent = formatVersionLabel(info.version);
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
  // 打赏二维码是打包时的可选资源（CI 从 WECHAT_QRCODE / ALIPAY_QRCODE 生成）；开源发布包里没有则不展示
  const donate = $("#about-donate");
  const wechatWrap = $("#about-donate-wechat");
  const alipayWrap = $("#about-donate-alipay");
  const wechatImg = $("#about-donate-wechat-img");
  const alipayImg = $("#about-donate-img");

  const hasWechat = Boolean(info.donate_wechat);
  const hasAlipay = info.donate_alipay !== undefined
    ? Boolean(info.donate_alipay)
    : Boolean(info.donate);

  const syncDonateCard = () => {
    if (!donate) return;
    const wechatVisible = wechatWrap ? !wechatWrap.hidden : hasWechat;
    const alipayVisible = alipayWrap ? !alipayWrap.hidden : hasAlipay;
    donate.hidden = !wechatVisible && !alipayVisible;
  };

  if (wechatWrap) wechatWrap.hidden = !hasWechat;
  if (wechatImg) {
    wechatImg.onerror = () => {
      if (wechatWrap) wechatWrap.hidden = true;
      wechatImg.removeAttribute("src");
      syncDonateCard();
    };
    if (hasWechat) {
      wechatImg.src = `${APP_BASE}/static/wechat.png`;
    } else {
      wechatImg.removeAttribute("src");
    }
  }

  if (alipayWrap) alipayWrap.hidden = !hasAlipay;
  if (alipayImg) {
    alipayImg.onerror = () => {
      if (alipayWrap) alipayWrap.hidden = true;
      alipayImg.removeAttribute("src");
      syncDonateCard();
    };
    if (hasAlipay) {
      alipayImg.src = `${APP_BASE}/static/alipay.png`;
    } else {
      alipayImg.removeAttribute("src");
    }
  }
  syncDonateCard();
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
  $("#update-modal-version").textContent = formatVersionLabel(latest.version || updateInfo.current_version);
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
  const modal = $("#cleanup-modal").hidden ? $("#organize-modal") : $("#cleanup-modal");
  if (modal.hidden) return;
  if (ev.key === "Escape") {
    ev.preventDefault();
    if (modal === $("#cleanup-modal")) closeCleanupModal();
    else closeOrganizeModal();
  } else if (ev.key === "Tab") {
    // 对话框仅有两个按钮，将键盘焦点限制在其中，默认落在取消。
    ev.preventDefault();
    const cancel = modal === $("#cleanup-modal") ? $("#cleanup-cancel-btn") : $("#organize-cancel-btn");
    const confirm = modal === $("#cleanup-modal") ? $("#cleanup-confirm-btn") : $("#organize-confirm-btn");
    (document.activeElement === cancel ? confirm : cancel).focus();
  }
});

/* ------------------------------------------------------ 下载目录整理 Beta */
let organizeInfo = null;
let organizeConfirmation = null;
let organizeJob = null;
let organizeSubmitting = false;
let organizePolling = false;
let organizeTimer = null;
const ORGANIZE_PATH = "/api/maintenance/organize";

function renderOrganizeJob(job) {
  const labels = { running: "整理中", completed: "整理完成", failed: "整理失败" };
  const layout = DOWNLOAD_LAYOUT_LABELS[job.layout] || job.layout || "-";
  let text = `任务 ${job.id} · ${labels[job.status]} · 阶段：${job.phase || "-"}` +
    ` · 已扫描 ${job.scanned ?? 0} · 已移动 ${job.moved ?? 0}` +
    ` · 未变化 ${job.unchanged ?? 0} · 已跳过 ${job.skipped ?? 0} · 失败 ${job.failed ?? 0}`;
  if (job.status === "completed") {
    text += "。整理后请在飞牛音乐中重新扫描曲库";
  }
  $("#organize-status").textContent = text;
  $("#organize-status").dataset.layout = layout;
  const lines = [];
  if (job.journal) lines.push(`迁移日志：${job.journal}`);
  for (const item of (Array.isArray(job.details) ? job.details : [])) {
    lines.push(`详情：${typeof item === "string" ? item : JSON.stringify(item)}`);
  }
  for (const item of (Array.isArray(job.errors) ? job.errors : [])) {
    lines.push(`错误：${typeof item === "string" ? item : JSON.stringify(item)}`);
  }
  $("#organize-details").textContent = lines.join("\n");
  $("#organize-details").hidden = !lines.length;
  $("#organize-open-btn").textContent = job.status === "running" ? "查看当前整理任务" : "整理";
}

function acceptOrganizeJob(response) {
  const job = response && response.job;
  if (!response || response.ok !== true || !job || !job.id ||
      !["running", "completed", "failed"].includes(job.status)) {
    throw new Error("整理任务响应格式无效");
  }
  organizeJob = job;
  renderOrganizeJob(job);
  clearTimeout(organizeTimer);
  organizeTimer = null;
  if (job.status === "running") organizeTimer = setTimeout(pollOrganizeJob, 1000);
}

async function loadOrganizeStatus() {
  try {
    const response = await api(ORGANIZE_PATH);
    if (!response || response.ok !== true || typeof response.root !== "string" ||
        !DOWNLOAD_LAYOUT_LABELS[response.layout]) throw new Error("整理状态响应格式无效");
    organizeInfo = { root: response.root, layout: response.layout };
    if (response.job) acceptOrganizeJob(response);
  } catch (exc) {
    organizeInfo = null;
    $("#organize-status").textContent = `整理不可用：${exc.message}`;
  }
}

async function pollOrganizeJob() {
  if (organizePolling || !organizeJob || organizeJob.status !== "running") return;
  clearTimeout(organizeTimer);
  organizeTimer = null;
  organizePolling = true;
  try {
    acceptOrganizeJob(await api(ORGANIZE_PATH + "/" + encodeURIComponent(organizeJob.id)));
  } catch (exc) {
    $("#organize-status").textContent =
      `任务 ${organizeJob.id} · 查询失败：${exc.message}；点击整理重试查询，不会重复提交。`;
  } finally {
    organizePolling = false;
  }
}

function closeOrganizeModal() {
  $("#organize-modal").hidden = true;
  organizeConfirmation = null;
  $("#organize-open-btn").focus();
}

function organizeSettingsSaved() {
  const savedLayout = configValues.FNMUSIC_DOWNLOAD_LAYOUT || "flat";
  const savedDir = (configValues.FNMUSIC_TEE_SAVE_DIR || "").trim();
  return ($("#download-layout").value || "flat") === savedLayout &&
    $("#dir-download").value.trim() === savedDir &&
    organizeInfo && organizeInfo.layout === savedLayout;
}

async function openOrganizeModal() {
  if (organizeSubmitting) return;
  if (organizeJob && organizeJob.status === "running") {
    renderOrganizeJob(organizeJob);
    pollOrganizeJob();
    return;
  }
  await loadOrganizeStatus();
  if (!organizeInfo) return;
  if (organizeJob && organizeJob.status === "running") return;
  if (!organizeSettingsSaved()) {
    toast("请先保存下载目录和存放方式，再执行整理", "fail");
    return;
  }
  const label = DOWNLOAD_LAYOUT_LABELS[organizeInfo.layout];
  $("#organize-warning").textContent =
    `将按“${label}”整理“${organizeInfo.root}”内的全部歌曲，并同步处理可识别的歌词和封面。` +
    "此功能为 Beta，移动文件可能导致歌曲暂时无法播放，收藏或歌单关联也可能受影响，" +
    "整理后可能需要在飞牛音乐中重新扫描曲库。建议先备份，是否继续？";
  $("#organize-modal").hidden = false;
  organizeConfirmation = { ...organizeInfo };
  $("#organize-cancel-btn").focus();
}

async function startOrganize() {
  if ($("#organize-modal").hidden || organizeSubmitting || !organizeConfirmation) return;
  if (!organizeSettingsSaved()) {
    closeOrganizeModal();
    toast("请先保存下载目录和存放方式，再执行整理", "fail");
    return;
  }
  if (organizeJob && organizeJob.status === "running") {
    closeOrganizeModal();
    renderOrganizeJob(organizeJob);
    return;
  }
  organizeSubmitting = true;
  const confirmed = organizeConfirmation;
  $("#organize-open-btn").disabled = true;
  closeOrganizeModal();
  $("#organize-status").textContent = "正在提交整理任务…";
  $("#organize-details").textContent = "";
  $("#organize-details").hidden = true;
  try {
    acceptOrganizeJob(await api(ORGANIZE_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        confirm_beta: true,
        root: confirmed.root,
        layout: confirmed.layout,
      }),
    }));
  } catch (exc) {
    $("#organize-status").textContent = "整理请求失败：" + exc.message;
  } finally {
    organizeSubmitting = false;
    $("#organize-open-btn").disabled = false;
  }
}

$("#organize-open-btn").addEventListener("click", openOrganizeModal);
$("#organize-cancel-btn").addEventListener("click", closeOrganizeModal);
$("#organize-confirm-btn").addEventListener("click", startOrganize);
$("#organize-modal").addEventListener("click", (ev) => {
  if (ev.target === ev.currentTarget) closeOrganizeModal();
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
  "completed_bytes_reported", "reconciliation_required", "paused",
]);
const PLAYLIST_JOB_STATES = new Set([
  "queued", "running", "cancelling", "cancelled", "partial",
  "completed_with_issues", "completed_bytes_reported", "reconciliation_required", "paused",
]);
const PLAYLIST_ADVANCE_DISPOSITIONS = new Map([
  ["completed", new Set(["continue", "abort"])],
  ["commit_unknown", new Set(["freeze"])],
  ["no_work", new Set(["continue"])],
  ["paused", new Set(["abort"])],
]);
// Contract §5.2: a completed run may continue only for an exact persisted
// outcome/reason/disposition triple. A contradictory tuple must stop the UI
// loop before another advance POST. JSON tuple keys avoid delimiter collisions.
const PLAYLIST_ADVANCE_RESULT_DISPOSITIONS = new Map([
  [JSON.stringify(["published", "bytes_published"]), "continue"],
  [JSON.stringify(["conflict", "destination_conflict"]), "continue"],
  [JSON.stringify(["rejected", "service_unavailable"]), "continue"],
  [JSON.stringify(["rejected", "no_quality_available"]), "continue"],
  [JSON.stringify(["rejected", "quality_unverified"]), "continue"],
  [JSON.stringify(["rejected", "quality_identity_mismatch"]), "continue"],
  [JSON.stringify(["rejected", "trial_only"]), "continue"],
  [JSON.stringify(["rejected", "download_target_untrusted"]), "continue"],
  [JSON.stringify(["rejected", "download_failed"]), "continue"],
  [JSON.stringify(["rejected", "integrity_mismatch"]), "continue"],
  [JSON.stringify(["rejected", "invalid_audio"]), "continue"],
  [JSON.stringify(["rejected", "tag_failed"]), "continue"],
  [JSON.stringify(["failed", "disabled"]), "abort"],
  [JSON.stringify(["failed", "config_invalid"]), "abort"],
  [JSON.stringify(["failed", "busy"]), "abort"],
  [JSON.stringify(["failed", "stage_unavailable"]), "abort"],
  [JSON.stringify(["rejected", "invalid_target"]), "abort"],
  [JSON.stringify(["commit_unknown", "commit_unknown"]), "freeze"],
]);

function playlistIsRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function playlistHasExactKeys(value, expected) {
  if (!playlistIsRecord(value)) return false;
  const keys = Object.keys(value);
  return keys.length === expected.length
    && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

const PLAYLIST_DETAIL_LEGACY_KEYS = [
  "status", "title", "artist", "album", "quality_actual", "quality_actual_status",
  "format", "cover_status", "cover_embedded", "lyric_status",
];
const PLAYLIST_DETAIL_KEYS = PLAYLIST_DETAIL_LEGACY_KEYS.concat(["published_sha256", "published_size"]);
const PLAYLIST_DETAIL_COVER_STATUSES = new Set([
  "preserved", "embedded", "unavailable", "unsupported", "error",
  "not_published", "not_attempted", "unknown",
]);
const PLAYLIST_DETAIL_LYRIC_STATUSES = new Set([
  "published", "unavailable", "error", "conflict", "not_attempted", "unknown",
]);

function playlistDetailDetectionProbe(value) {
  return value.replace(/[\p{Cf}\p{M}]/gu, "").normalize("NFKC");
}

function playlistHasUnsafeDetailText(value) {
  if (/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(value)) return true;
  for (const character of value) {
    const point = character.codePointAt(0);
    if (point >= 0xd800 && point <= 0xdfff) return true;
  }
  const probe = playlistDetailDetectionProbe(value);
  return /(?:https?:\/\/|ftp:\/\/|www\.)/i.test(probe)
    || /(?:^|[^A-Za-z0-9_])(?:authorization|set-cookie|cookie|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|secret)\s*[:=]/i.test(probe);
}

function playlistHasAbsolutePathText(value) {
  const pathProbe = playlistDetailDetectionProbe(value);
  return /(^|[^A-Za-z0-9_.-])\/(?!\s)[^/\s]+(?:\s+[^/\s]+)*(?:\/[^/\s]+(?:\s+[^/\s]+)*)*/u.test(pathProbe)
    || /^\s*\/(?!\s)[^/]+(?:\/[^/]*)?\s*$/u.test(pathProbe)
    || /(?:^|[\s([{;,])(?:path|file|directory|folder|location|路径|文件路径|目录|位置)\s*[:=：]\s*\/(?!\s)[^/]+(?:\/[^/]*)*/iu.test(pathProbe)
    || /(^|[^A-Za-z0-9_.-])\/(?:vol(?:ume)?\d+|home|users?|tmp|var|mnt|media|root|etc|opt|srv|proc|sys|dev|run|boot|data|storage|shares?|private|workspace|downloads?|documents?)(?:\/|$)/iu.test(pathProbe)
    || /(^|[^A-Za-z0-9_])[a-z]:[\\/][^\r\n]*/iu.test(pathProbe)
    || /(^|[^\\/])(?:\\\\|\/\/)[^\\/\s]+[\\/][^\\/\s]+/u.test(pathProbe);
}

function playlistValidPositionDetail(detail) {
  const legacy = playlistHasExactKeys(detail, PLAYLIST_DETAIL_LEGACY_KEYS);
  const hasFileEvidence = playlistHasExactKeys(detail, PLAYLIST_DETAIL_KEYS);
  if ((!legacy && !hasFileEvidence) || detail.status !== "published") return false;
  for (const key of ["title", "artist", "album"]) {
    if (typeof detail[key] !== "string" || detail[key].length > 512) {
      return false;
    }
  }
  if (detail.quality_actual_status === "reported") {
    if (typeof detail.quality_actual !== "string"
        || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(detail.quality_actual)) return false;
  } else if (detail.quality_actual_status !== "unknown" || detail.quality_actual !== "") {
    return false;
  }
  if (hasFileEvidence && (typeof detail.published_sha256 !== "string"
      || !/^[0-9a-f]{64}$/.test(detail.published_sha256)
      || !Number.isSafeInteger(detail.published_size) || detail.published_size <= 0)) return false;
  return ["flac", "mp3", "unknown"].includes(detail.format)
    && PLAYLIST_DETAIL_COVER_STATUSES.has(detail.cover_status)
    && (detail.cover_embedded === null || typeof detail.cover_embedded === "boolean")
    && PLAYLIST_DETAIL_LYRIC_STATUSES.has(detail.lyric_status);
}

function playlistValidPositionRows(positions) {
  if (!Array.isArray(positions)) return false;
  for (let index = 0; index < positions.length; index += 1) {
    const row = positions[index];
    if (!playlistHasExactKeys(row, [
      "position", "source_id", "state", "reason", "reason_message", "cleanup_pending", "detail",
    ]) || row.position !== index + 1 || !Number.isInteger(row.position)
        || (row.source_id !== null && (typeof row.source_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(row.source_id)))
        || !PLAYLIST_POSITION_LABELS[row.state]
        || (row.reason !== null && (typeof row.reason !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(row.reason)))
        || typeof row.reason_message !== "string"
        || typeof row.cleanup_pending !== "boolean"
        || (row.detail !== null && (!playlistValidPositionDetail(row.detail) || row.state !== "published"))) {
      return false;
    }
  }
  return true;
}

function playlistIsFixedBusyResponse(response) {
  if (!playlistHasExactKeys(response, ["status", "ok", "body"])
      || response.status !== 409 || response.ok !== false) return false;
  const body = response.body;
  if (!playlistHasExactKeys(body, ["ok", "code", "message", "data"])
      || body.ok !== false || body.code !== "busy"
      || body.message !== PLAYLIST_CODE_MESSAGES.busy) return false;
  return playlistHasExactKeys(body.data, []);
}

function playlistValidAdvanceResponse(response, expectedJobId) {
  if (!response || response.status !== 200 || response.ok !== true) return false;
  const body = response.body;
  if (!playlistIsRecord(body) || body.ok !== true || body.code !== "ok"
      || !playlistIsRecord(body.data)) return false;
  const run = body.data.run;
  const job = body.data.job;
  if (!playlistIsRecord(run) || !playlistIsRecord(job)
      || job.job_id !== expectedJobId || !PLAYLIST_JOB_STATES.has(job.state)) return false;
  const allowedDispositions = PLAYLIST_ADVANCE_DISPOSITIONS.get(run.status);
  if (!allowedDispositions || !allowedDispositions.has(run.disposition)) return false;
  const runKeys = ["detail", "disposition", "outcome", "position", "reason", "status"];
  if (Object.keys(run).length !== runKeys.length || runKeys.some((key) => !Object.prototype.hasOwnProperty.call(run, key))) {
    return false;
  }
  if (run.position !== null && (!Number.isInteger(run.position) || run.position < 1)) return false;
  if (run.outcome !== null && typeof run.outcome !== "string") return false;
  if (run.reason !== null && typeof run.reason !== "string") return false;
  if (run.detail !== null && !playlistValidPositionDetail(run.detail)) return false;
  if (run.detail !== null && (run.outcome !== "published" || run.reason !== "bytes_published")) return false;
  if (run.status === "completed") {
    if (run.position === null || typeof run.outcome !== "string" || typeof run.reason !== "string") return false;
    return PLAYLIST_ADVANCE_RESULT_DISPOSITIONS.get(JSON.stringify([run.outcome, run.reason]))
      === run.disposition;
  }
  if (run.status === "commit_unknown") {
    return run.position !== null && run.outcome === "commit_unknown" && run.reason === "commit_unknown"
      && PLAYLIST_ADVANCE_RESULT_DISPOSITIONS.get(JSON.stringify([run.outcome, run.reason]))
        === run.disposition;
  }
  return run.position === null && run.outcome === null && run.reason === null && run.detail === null;
}

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
    case "paused": return "已暂停：前一位置要求停止，需人工处理";
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
    if (playlistValidPositionDetail(raw.detail)) {
      const detail = raw.detail;
      const safeDetailText = (value) => playlistHasUnsafeDetailText(value)
        || playlistHasAbsolutePathText(value) ? "" : value;
      metaParts.push("曲名 " + escapeHtml(safeDetailText(detail.title) || "未知"));
      metaParts.push("歌手 " + escapeHtml(safeDetailText(detail.artist) || "未知"));
      metaParts.push("专辑 " + escapeHtml(safeDetailText(detail.album) || "未知"));
      metaParts.push("音质 " + escapeHtml(
        detail.quality_actual_status === "reported" ? detail.quality_actual : "未知",
      ));
      metaParts.push("格式 " + escapeHtml({ flac: "FLAC", mp3: "MP3", unknown: "未知" }[detail.format]));
      const coverLabel = {
        preserved: "已保留", embedded: "已写入", unavailable: "未获取",
        unsupported: "格式不支持", error: "写入失败", not_published: "未发布",
        not_attempted: "未尝试", unknown: "未知",
      }[detail.cover_status] || "未知";
      const embeddedLabel = detail.cover_embedded === true ? "已内嵌"
        : detail.cover_embedded === false ? "未内嵌" : "内嵌状态未知";
      metaParts.push("封面 " + escapeHtml(coverLabel + "，" + embeddedLabel));
      const lyricLabel = {
        published: "已保存", unavailable: "未获取", error: "保存失败",
        conflict: "同名冲突", not_attempted: "未尝试", unknown: "未知",
      }[detail.lyric_status] || "未知";
      metaParts.push("歌词 " + escapeHtml(lyricLabel));
      if (Object.prototype.hasOwnProperty.call(detail, "published_sha256")) {
        metaParts.push("已提交字节 " + escapeHtml(String(detail.published_size)) + " B · SHA-256 "
          + escapeHtml(detail.published_sha256));
      } else {
        metaParts.push("已提交字节指纹未知");
      }
    }
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
  const blockedForStart = state === "reconciliation_required" || state === "cancelling"
    || state === "paused" || state === "running";
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
      && (state === "queued" || state === "running"
          || state === "reconciliation_required" || state === "paused");
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
  if (response && response.ok === true && body.ok === true && data.job && typeof data.job === "object"
      && playlistValidPositionRows(data.positions)) {
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
  if (status === "paused") return "任务已暂停：此前位置要求停止，未继续处理后续歌曲。";
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
        // The request may have reached the server and completed even though
        // its response was lost. A GET can display current state but cannot
        // prove the old worker stopped; never issue another advance here.
        playlistState.message = "推进请求连接中断：正在只读核对任务，暂不重试。";
        playlistRenderJob();
        const reconciled = await playlistRefreshJob(id);
        playlistState.message = reconciled
          ? "上次推进请求未收到结果。已读取当前状态，请人工核对位置与保存结果后再处理。"
          : "上次推进请求未收到结果，且任务状态暂时无法读取；已停止，请手动刷新核对。";
        playlistRenderJob();
        break;
      }
      const body = playlistIsRecord(response && response.body) ? response.body : {};
      const code = typeof body.code === "string" ? body.code : "";
      // A 504 is ambiguous even if its body carries another error code (for
      // example, a proxy-generated "busy"). Observe once, then stop.
      if (code === "timeout" || (response && response.status === 504)) {
        playlistState.message = "保存超时：正在核对任务状态…";
        playlistRenderJob();
        const refreshed = await playlistRefreshJob(id);
        if (!refreshed || !playlistState.job) {
          playlistState.message = "保存超时且未能读取任务状态，已停止推进，请稍后刷新。";
          break;
        }
        playlistState.message = PLAYLIST_TERMINAL_STATES.has(playlistState.job.state)
          ? "上次请求超时：任务已结束或暂停，状态已读取。"
          : "上次请求超时：已读取当前状态，但无法确认旧请求结束；已停止，请人工核对后再操作。";
        playlistRenderJob();
        break;
      }
      // Retry only the exact fixed 409 busy envelope (including its empty
      // data object). Any extra/malformed fields may describe a committed run;
      // fail closed and observe with GET instead of issuing another POST.
      if (playlistIsFixedBusyResponse(response)) {
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
      if (!playlistValidAdvanceResponse(response, id)) {
        // Malformed or unrecognized responses may follow a committed call.
        // GET is observational only; never issue another POST in this loop.
        await playlistRefreshJob(id);
        playlistState.message = "推进失败：" + playlistJobMessage(body, "内部错误") + "（已停止本页推进）";
        playlistRenderJob();
        break;
      }
      {
        const data = response.body.data;
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
        if (PLAYLIST_TERMINAL_STATES.has(jobState)) {
          if (jobState !== "paused") playlistClearStoredJob();
          break;
        }
        if (runStatus === "paused" || run.disposition === "abort") break;
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
    if (playlistState.job.state === "paused") {
      // Keep the job id so a page refresh can still show why the ledger is
      // paused. This stage intentionally has no resume/reconciliation API.
      playlistState.message = "已恢复暂停任务；后续位置未执行，当前版本不支持自动恢复，请人工处理。";
    } else {
      playlistClearStoredJob();
      playlistState.message = "已恢复上次任务：任务已结束，可重新读取清单后新建任务。";
    }
    playlistRenderJob();
    return { resumed: false, reason: "terminal", jobId: playlistState.jobId };
  }
  playlistState.message = "已恢复上次未完成的任务进度，继续推进。";
  playlistRenderJob();
  // A stored job may have an advance whose response was lost. GET is only an
  // observation: it cannot prove that the old worker stopped, so page load
  // must never issue another POST automatically. The user can inspect the
  // refreshed position list and explicitly choose whether to continue.
  playlistState.message = "已恢复任务状态；页面不会自动继续。请先核对位置结果，再手动操作。";
  playlistRenderJob();
  return { resumed: false, reason: "manual_confirmation", jobId: playlistState.jobId };
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
  await loadOrganizeStatus();
  checkUpdate();  // 进页面即查（后端 TTL 缓存兜住 GitHub 限额），不阻塞启动
  await loadPlatforms(false);  // 页面加载不自动拉起预览进程，等用户点选音源
  if ($("#public-playlists-enabled").checked) await showPublicBrowser();
  setInterval(loadStatus, 15000);
})();
