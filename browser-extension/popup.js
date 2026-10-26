"use strict";
const $ = id => document.getElementById(id);
function msg(text, isError = false) {
  $("response").textContent = text;
  $("response").style.color = isError ? "#f5a4a4" : "#bce9cd";
}
chrome.runtime.sendMessage({ action: "status" }, result => {
  $("auto").checked = result?.autoCapture === true;
  $("state").textContent = result?.online
    ? "● البرنامج مفتوح وجاهز لاستقبال تحميلات Chrome"
    : "● برنامج الكمبيوتر مغلق: سيستمر Chrome بالتحميل كالمعتاد";
  $("state").classList.toggle("ok", !!result?.online);
});
chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
  const url = tabs?.[0]?.url;
  if (/^https?:\/\//i.test(url || "")) $("url").value = url;
});
$("auto").addEventListener("change", () => {
  chrome.runtime.sendMessage({ action: "autoCapture", enabled: $("auto").checked }, result => {
    msg(result?.ok ? "✓ تم حفظ إعداد التحويل التلقائي" : "تعذر حفظ الإعداد", !result?.ok);
  });
});
function submit(kind) {
  const url = $("url").value.trim();
  if (!/^https?:\/\//i.test(url)) return msg("أدخل رابط HTTP/HTTPS صحيحًا", true);
  msg("جاري إرسال الرابط...");
  chrome.runtime.sendMessage({ action: "send", kind, url }, response => {
    msg(response?.ok ? "✓ أُرسل الرابط إلى مدير التحميل" : "تعذر الإرسال؛ تأكد من فتح البرنامج", !response?.ok);
  });
}
$("file").addEventListener("click", () => submit("file"));
$("video").addEventListener("click", () => msg("هذه النسخة التجريبية تدعم الروابط المباشرة فقط حاليًا", true));
