/* 物业管家（个人版）：页面交互脚本
   - 危险操作二次确认弹窗（data-confirm）
   - 顶部小区切换器自动提交
   - 复制到剪贴板（data-copy / data-copy-target）
   - 催缴短信弹窗（data-house-id）
   - 一键缴清页的金额联动
*/
(function () {
  "use strict";

  // ---------- 危险操作二次确认 ----------
  var confirmModalEl = document.getElementById("confirmModal");
  var confirmModal = confirmModalEl ? new bootstrap.Modal(confirmModalEl) : null;
  var pendingAction = null;

  function askConfirm(title, body, onOk) {
    if (!confirmModal) { if (window.confirm(body)) onOk(); return; }
    document.getElementById("confirmTitle").textContent = title || "请确认";
    document.getElementById("confirmBody").innerHTML = body;
    pendingAction = onOk;
    confirmModal.show();
  }

  var okBtn = document.getElementById("confirmOk");
  if (okBtn) {
    okBtn.addEventListener("click", function () {
      confirmModal.hide();
      if (pendingAction) { var f = pendingAction; pendingAction = null; f(); }
    });
  }

  // 带 data-confirm 的表单 / 链接
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm");
    if (!msg || form.dataset.confirmed === "1") return;
    e.preventDefault();
    var title = form.getAttribute("data-confirm-title") || "请确认";
    askConfirm(title, msg, function () {
      form.dataset.confirmed = "1";
      form.submit();
    });
  }, true);

  document.addEventListener("click", function (e) {
    var link = e.target.closest("a[data-confirm]");
    if (!link) return;
    e.preventDefault();
    askConfirm(link.getAttribute("data-confirm-title") || "请确认",
               link.getAttribute("data-confirm"),
               function () { window.location.href = link.href; });
  });

  // ---------- 顶部小区切换器 ----------
  var switchForm = document.getElementById("community-switch-form");
  var communitySelect = document.getElementById("community-select");
  if (switchForm && communitySelect) {
    communitySelect.addEventListener("change", function () {
      switchForm.action = "/community/switch/" + communitySelect.value;
      switchForm.submit();
    });
  }

  // ---------- 复制到剪贴板 ----------
  function copyText(text, done) {
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); done(true); }
      catch (err) { done(false); }
      document.body.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, fallback);
    } else { fallback(); }
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy-target]");
    if (btn) {
      var target = document.querySelector(btn.getAttribute("data-copy-target"));
      if (target) {
        copyText(target.value, function (ok) {
          btn.textContent = ok ? "✅ 已复制" : "复制失败，请手动复制";
          setTimeout(function () { btn.textContent = "📋 复制短信"; }, 2000);
        });
      }
      return;
    }
    var c = e.target.closest("[data-copy]");
    if (c) {
      copyText(c.getAttribute("data-copy"), function (ok) {
        var old = c.textContent;
        c.textContent = ok ? "已复制" : "复制失败";
        setTimeout(function () { c.textContent = old; }, 1500);
      });
    }
  });

  // ---------- 催缴短信弹窗 ----------
  var smsModalEl = document.getElementById("smsModal");
  var smsModal = smsModalEl ? new bootstrap.Modal(smsModalEl) : null;
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-house-id]");
    if (!btn || !smsModal) return;
    var hid = btn.getAttribute("data-house-id");
    document.getElementById("smsText").value = "正在生成短信…";
    document.getElementById("smsHouse").textContent = "";
    smsModal.show();
    fetch("/fee/overdue/sms/" + hid)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        document.getElementById("smsHouse").textContent = d.label || "";
        document.getElementById("smsText").value = d.text || d.msg || "";
      })
      .catch(function () {
        document.getElementById("smsText").value = "生成失败，请刷新页面重试";
      });
  });

  // ---------- 一键缴清：勾选账单联动合计 ----------
  var payallBox = document.getElementById("payall-bills");
  if (payallBox) {
    var totalInput = document.getElementById("total-amount");
    function refreshTotal() {
      var sum = 0;
      payallBox.querySelectorAll("input.bill-check").forEach(function (cb) {
        if (cb.checked) sum += parseInt(cb.getAttribute("data-remaining") || "0", 10);
      });
      totalInput.value = (sum / 100).toFixed(2);
      document.getElementById("payall-count").textContent =
        payallBox.querySelectorAll("input.bill-check:checked").length;
    }
    payallBox.addEventListener("change", refreshTotal);
    var all = document.getElementById("check-all");
    if (all) {
      all.addEventListener("change", function () {
        payallBox.querySelectorAll("input.bill-check").forEach(function (cb) {
          cb.checked = all.checked;
        });
        refreshTotal();
      });
    }
    refreshTotal();
  }

  // ---------- 全选框（通用） ----------
  document.addEventListener("change", function (e) {
    if (!e.target.classList.contains("select-all-toggle")) return;
    var scope = document.querySelector(e.target.getAttribute("data-scope") || "body");
    scope.querySelectorAll("input[type=checkbox]").forEach(function (cb) {
      if (cb !== e.target) cb.checked = e.target.checked;
    });
  });
})();
