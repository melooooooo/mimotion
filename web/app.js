const state = {
  token: localStorage.getItem("mimotion_token"),
  hasZeppBinding: false,
  maskedAccount: null,
};

const $ = (id) => document.getElementById(id);
const screens = ["wechatGate", "authState", "bindView", "mainView", "historyView", "settingsView"];

function isWechat() {
  return /MicroMessenger/i.test(navigator.userAgent) || new URLSearchParams(location.search).get("dev") === "1";
}

function startWechatLogin() {
  location.replace("/wechat-login");
}

function showScreen(id) {
  screens.forEach((name) => $(name).classList.toggle("hidden", name !== id));
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 2200);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
  }
  return data;
}

function applyBinding(data) {
  state.hasZeppBinding = Boolean(data.hasZeppBinding);
  state.maskedAccount = data.maskedAccount || null;
  $("maskedAccount").textContent = state.maskedAccount || "-";
  $("settingsAccount").textContent = state.maskedAccount || "未绑定";
}

async function boot() {
  if (!isWechat()) {
    showScreen("wechatGate");
    return;
  }
  showScreen("authState");
  const params = new URLSearchParams(location.search);
  const ticket = params.get("ticket");
  try {
    if (ticket) {
      const data = await api("/api/auth/h5-exchange", {
        method: "POST",
        body: JSON.stringify({ ticket }),
      });
      state.token = data.token;
      localStorage.setItem("mimotion_token", state.token);
      applyBinding(data);
      history.replaceState({}, "", "/app/");
    } else if (state.token) {
      applyBinding(await api("/api/me"));
    } else {
      startWechatLogin();
      return;
    }
    showScreen(state.hasZeppBinding ? "mainView" : "bindView");
  } catch (error) {
    localStorage.removeItem("mimotion_token");
    if (isWechat()) {
      toast(error.message || "登录已失效，正在重新进入");
      startWechatLogin();
      return;
    }
    showScreen("wechatGate");
    toast(error.message);
  }
}

function setSteps(value) {
  const steps = Math.max(1, Math.min(98800, Number(value) || 1));
  $("stepsInput").value = steps;
  $("stepsRange").value = Math.min(30000, steps);
}

async function bindZepp(event) {
  event.preventDefault();
  const button = $("bindButton");
  button.disabled = true;
  button.textContent = "绑定中...";
  try {
    const data = await api("/api/zepp/bind", {
      method: "POST",
      body: JSON.stringify({
        account: $("accountInput").value.trim(),
        password: $("passwordInput").value,
      }),
    });
    applyBinding({ hasZeppBinding: true, maskedAccount: data.maskedAccount });
    $("passwordInput").value = "";
    toast("绑定成功");
    showScreen("mainView");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "绑定 Zepp Life";
  }
}

async function submitSteps() {
  const steps = Number($("stepsInput").value);
  const button = $("submitBtn");
  button.disabled = true;
  button.textContent = "提交中...";
  try {
    const data = await api("/api/steps/submit", {
      method: "POST",
      body: JSON.stringify({ steps }),
    });
    $("resultCard").innerHTML = `<strong>${data.success ? "提交成功" : "提交失败"}</strong><p>${data.steps} 步 · ${new Date(data.submittedAt).toLocaleString()}</p><p>${data.message}</p>`;
    $("resultCard").classList.remove("hidden");
    button.textContent = data.success ? "提交成功" : "提交步数";
    toast(data.success ? "提交成功" : data.message);
  } catch (error) {
    toast(error.message);
    button.textContent = "提交步数";
  } finally {
    button.disabled = false;
  }
}

async function loadHistory() {
  showScreen("historyView");
  const list = $("historyList");
  list.innerHTML = "<p>加载中...</p>";
  try {
    const data = await api("/api/steps/history");
    if (!data.items.length) {
      list.innerHTML = "<p>还没有提交记录</p>";
      return;
    }
    list.innerHTML = data.items
      .map(
        (item) => `<article class="historyItem">
          <div><strong>${item.steps} 步</strong><p>${new Date(item.createdAt).toLocaleString()}</p></div>
          <div class="${item.success ? "statusOk" : "statusFail"}">${item.success ? "成功" : "失败"}</div>
        </article>`
      )
      .join("");
  } catch (error) {
    list.innerHTML = `<p>${error.message}</p>`;
  }
}

async function unbind() {
  if (!confirm("确定解绑当前 Zepp Life 账号吗？")) return;
  await api("/api/zepp/bind", { method: "DELETE" });
  applyBinding({ hasZeppBinding: false });
  showScreen("bindView");
  toast("已解绑");
}

$("copyLinkBtn").addEventListener("click", async () => {
  await navigator.clipboard.writeText(location.href).catch(() => {});
  toast("链接已复制");
});
$("bindForm").addEventListener("submit", bindZepp);
$("togglePassword").addEventListener("click", () => {
  const input = $("passwordInput");
  input.type = input.type === "password" ? "text" : "password";
});
$("stepsInput").addEventListener("input", (event) => setSteps(event.target.value));
$("stepsRange").addEventListener("input", (event) => setSteps(event.target.value));
document.querySelectorAll("[data-step]").forEach((button) => button.addEventListener("click", () => setSteps(button.dataset.step)));
$("submitBtn").addEventListener("click", submitSteps);
$("historyBtn").addEventListener("click", loadHistory);
$("settingsBtn").addEventListener("click", () => showScreen("settingsView"));
$("rebindBtn").addEventListener("click", () => showScreen("bindView"));
$("unbindBtn").addEventListener("click", unbind);
document.querySelectorAll("[data-back]").forEach((button) => button.addEventListener("click", () => showScreen(`${button.dataset.back}View`)));

boot();
