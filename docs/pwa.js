/* Appli installée (tablette) : service worker + nom « Présidentiel 2027 ».
 * Dans un navigateur classique, rien ne change. */
(() => {
  if ("serviceWorker" in navigator) {
    addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }

  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (!standalone) return;

  document.documentElement.classList.add("standalone");
  const FROM = "Présidentielle 2027", TO = "Présidentiel 2027";
  const fix = () => {
    if (document.title.includes(FROM)) document.title = document.title.replaceAll(FROM, TO);
    const h1 = document.querySelector(".top h1");
    if (h1 && h1.textContent !== TO) h1.textContent = TO;
  };
  const start = () => {
    fix();
    const t = document.querySelector("title");
    if (t) new MutationObserver(fix).observe(t, { childList: true, characterData: true, subtree: true });
  };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start) : start();
})();
