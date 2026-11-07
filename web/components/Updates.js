// Settings → Основное → «Обновление программы»: shows the installed version, «Проверить обновления» asks the
// server to look (one fetch; nothing changes) and lists what is new in plain words, «Обновить» makes a copy of
// the data, brings the new version in and restarts the app. The server refuses with a plain reason when it
// cannot (not a git install, own edits in the program's files, diverged, no network, a reply being written).
// After the update this component waits for the app to come back (a different `boot` id from GET /updates)
// and reloads the page by itself. When the app was not started by start.sh / start.bat it cannot restart
// itself: it says so and what to do, and still reloads the page once the person has started it.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { ask } from "./Dialogs.js";

const WAIT_MS = 5 * 60 * 1000;

export function Updates({ onError }) {
  const [info, setInfo] = useState(null);
  const [found, setFound] = useState(null);
  const [busy, setBusy] = useState("");
  const [after, setAfter] = useState(null); // { restart, from, to, waited }
  const timer = useRef(null);

  useEffect(() => { api.get("/updates").then(setInfo).catch(() => {}); return () => clearInterval(timer.current); }, []);

  const check = async () => {
    setBusy("check");
    try { setFound(await api.post("/updates/check")); } catch (e) { onError(e.message); }
    setBusy("");
  };

  const waitForReturn = (bootBefore, restart) => {
    const started = Date.now();
    clearInterval(timer.current);
    timer.current = setInterval(async () => {
      try {
        const now = await api.get("/updates");
        if (now.boot && now.boot !== bootBefore) { clearInterval(timer.current); location.reload(); }
      } catch (e) { /* the app is restarting: no answer is expected */ }
      if (Date.now() - started > WAIT_MS) {
        clearInterval(timer.current);
        setAfter((a) => ({ ...a, gaveUp: true }));
      }
    }, 1500);
  };

  const apply = async () => {
    const ok = await ask("Обновить программу? Сначала будет сделана копия ваших данных, затем программа перезапустится. "
      + "Если что-то пойдёт не так, она останется прежней.", { ok: "Обновить" });
    if (!ok) return;
    setBusy("apply");
    try {
      const boot = (info && info.boot) || "";
      const done = await api.post("/updates/apply");
      setAfter({ restart: done.restart, from: done.from, to: done.to });
      waitForReturn(boot, done.restart);
    } catch (e) {
      onError(e.message);
      setBusy("");
    }
  };

  if (after) {
    return html`<div data-slot="updates">
      <h2>Обновление программы</h2>
      <p>Обновлено: ${after.from.commit} → ${after.to.commit}${after.to.subject ? ` («${after.to.subject}»)` : ""}. Копия данных сделана.</p>
      ${after.restart === "auto"
        ? html`<p>Программа перезапускается. Эта страница обновится сама, когда она вернётся.</p>`
        : html`<p><strong>Сама программа не перезапустится</strong>: она запущена не через start.sh или start.bat, поэтому остановилась.
          Запустите её снова так же, как обычно, эта страница обновится сама, как только она заработает.</p>`}
      ${after.gaveUp && html`<p class="hint">Программа пока не вернулась. Проверьте окно, где она запущена, и запустите её заново.
        Страницу можно просто обновить вручную.</p>`}
    </div>`;
  }

  const current = found ? found.current : info;
  return html`<div data-slot="updates">
    <h2>Обновление программы</h2>
    ${current && html`<p class="muted">Сейчас: версия ${current.version}${current.commit ? `, сборка ${current.commit}` : ""}${current.date ? ` от ${current.date}` : ""}${current.subject ? `, «${current.subject}»` : ""}.</p>`}
    <div class="row">
      <button disabled=${Boolean(busy)} onClick=${check}>${busy === "check" ? "Проверяю…" : "Проверить обновления"}</button>
      ${found && found.can_update && html`<button class="primary" disabled=${Boolean(busy)} onClick=${apply}>${busy === "apply" ? "Обновляю…" : "Обновить"}</button>`}
    </div>
    ${found && found.state === "up_to_date" && html`<p>У вас последняя версия.${found.ahead ? " В вашей копии есть свои правки кода, которых нет на сервере." : ""}</p>`}
    ${found && found.state === "available" && html`<p>Есть новая версия${found.latest ? `: ${found.latest.commit}${found.latest.date ? ` от ${found.latest.date}` : ""}` : ""}. Новых изменений: ${found.behind}.</p>`}
    ${found && found.changes.length > 0 && html`<ul class="updates-changes">
      ${found.changes.map((c) => html`<li key=${c.commit}>${c.subject}</li>`)}
      ${found.behind > found.changes.length && html`<li class="muted">…и ещё ${found.behind - found.changes.length}</li>`}
    </ul>`}
    ${found && found.reason && html`<p class="updates-reason"><strong>Обновить сейчас нельзя.</strong> ${found.reason}${found.how ? ` ${found.how}` : ""}</p>`}
    ${info && !info.supervised && html`<p class="hint">Программа запущена не через start.sh или start.bat: после обновления она остановится, и её нужно будет запустить снова.</p>`}
  </div>`;
}
