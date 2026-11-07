// 🎙 Голос, web part.
//  - 🎤 beside the chat box and the assistant's box: tap, speak, tap again. The recording goes to the
//    server (action "transcribe"), the text comes back into the box to be checked (or is sent at once
//    when the setting says so).
//  - 🔊 on every message reads it aloud; the 🔊/🔇 button beside the box switches "read replies aloud"
//    for the chat or for the assistant. Audio comes from the server as base64 and plays from a blob:
//    URL (or, with the browser's own voice, from speechSynthesis).
// The microphone works only in a secure context (https or localhost); phones refuse sound that no tap
// started, so the first tap anywhere "unlocks" one shared <audio> element that all speech then reuses.
// A failure here shows a toast and nothing else: sending and receiving messages never wait for voice.

export const MAX_SECONDS = 120;
export const MAX_BYTES = 10 * 1024 * 1024;
const MIN_BYTES = 800; // shorter than this is silence or a mis-tap
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus", "audio/ogg"];

// Why the microphone cannot be used here, in words for the person ("" = it can).
export function micProblem({ secure, host, hasGetUserMedia, hasRecorder }) {
  if (!secure) {
    return `Микрофон не работает по адресу ${host}: браузеры разрешают его только по https или по адресу localhost / 127.0.0.1. `
      + "Откройте White House как http://127.0.0.1:порт (на телефоне с Termux так и открывается) или настройте https.";
  }
  if (!hasGetUserMedia || !hasRecorder) return "Этот браузер не умеет записывать звук. Попробуйте Chrome или Firefox.";
  return "";
}

// The first recording format the browser can make (Chrome / Firefox: webm, Safari: mp4); "" = its default.
export function pickMime(Recorder) {
  if (!Recorder || typeof Recorder.isTypeSupported !== "function") return "";
  return MIME_CANDIDATES.find((m) => Recorder.isTypeSupported(m)) || "";
}

export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function toBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function blobUrl(base64, mime) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: mime || "audio/mpeg" }));
}

// A silent tenth of a second, played once from a tap to unlock the audio element on phones.
function silentUrl() {
  const rate = 8000;
  const samples = 800;
  const buffer = new ArrayBuffer(44 + samples * 2);
  const v = new DataView(buffer);
  const text = (at, s) => { for (let i = 0; i < s.length; i += 1) v.setUint8(at + i, s.charCodeAt(i)); };
  text(0, "RIFF"); v.setUint32(4, 36 + samples * 2, true); text(8, "WAVEfmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  text(36, "data"); v.setUint32(40, samples * 2, true);
  return URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
}

export default function setup(t) {
  const synth = typeof window !== "undefined" ? window.speechSynthesis : null;
  const player = new Audio();
  player.preload = "auto";
  let silent = "";
  let unlocked = false;

  // ---- one shared state: recording, speaking --------------------------------------------------
  const rec = { state: "idle", target: "", started: 0, recorder: null, stream: null, timer: null, cancelled: false };
  const now = { key: "", state: "idle" }; // what is spoken: "m:<message id>" / "a:<assistant message id>"
  let token = 0;
  let stopHook = null;
  const service = (kind) => t.settings[kind + "_service"] || "off";

  // ---- unlocking sound after a tap -----------------------------------------------------------
  const unlock = () => {
    if (unlocked) return;
    unlocked = true;
    try {
      silent = silent || silentUrl();
      player.src = silent;
      const p = player.play();
      if (p && p.catch) p.catch(() => { unlocked = false; });
    } catch (e) { unlocked = false; }
    try {
      if (synth && service("tts") === "browser") {
        const u = new SpeechSynthesisUtterance(" ");
        u.volume = 0;
        synth.speak(u);
      }
    } catch (e) { /* the voice of the browser may be missing */ }
  };
  for (const type of ["pointerdown", "touchend", "keydown"]) {
    window.addEventListener(type, unlock, { capture: true, passive: true, signal: t.signal });
  }

  // ---- speech to text ------------------------------------------------------------------------
  const recording = (target) => rec.state === "recording" && rec.target === target;

  async function transcribe(blob, seconds, target) {
    rec.state = "busy";
    rec.target = target;
    t.refresh();
    try {
      if (blob.size < MIN_BYTES) { t.toast("Запись пустая: ничего не записалось. Скажите ещё раз."); return; }
      if (blob.size > MAX_BYTES) { t.toast("Запись слишком большая. Говорите короче: до 2 минут."); return; }
      const audio = toBase64(new Uint8Array(await blob.arrayBuffer()));
      const result = await t.call("transcribe", { audio, mime: blob.type, seconds });
      if (!result.text) { t.toast("Ничего не распознано. Скажите ещё раз, ближе к микрофону."); return; }
      t.insertText(result.text, { target, send: Boolean(result.send) });
    } catch (e) {
      t.toast(e.message);
    } finally {
      rec.state = "idle";
      rec.target = "";
      t.refresh();
    }
  }

  function finishRecording(chunks, type, seconds, target) {
    if (rec.stream) rec.stream.getTracks().forEach((x) => x.stop());
    clearInterval(rec.timer);
    rec.stream = null;
    rec.recorder = null;
    if (rec.cancelled) { rec.state = "idle"; rec.target = ""; t.refresh(); return; }
    transcribe(new Blob(chunks, { type }), seconds, target);
  }

  async function startRecording(target) {
    const problem = micProblem({
      secure: window.isSecureContext, host: location.host,
      hasGetUserMedia: Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
      hasRecorder: typeof MediaRecorder !== "undefined",
    });
    if (problem) { t.toast(problem); return; }
    if (service("stt") === "off") { t.toast("Распознавание выключено: выберите сервис в ⚙ → Дополнения → Голос."); return; }
    stop(); // a spoken reply must not leak into the recording
    rec.state = "busy";
    rec.target = target;
    rec.cancelled = false;
    t.refresh();
    try {
      const status = await t.call("status");
      if (!status.stt.ready) {
        t.toast("Для распознавания не задан ключ. Впишите его в ⚙ → Дополнения → Голос.");
        rec.state = "idle"; rec.target = ""; t.refresh();
        return;
      }
      rec.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = pickMime(MediaRecorder);
      const recorder = mime ? new MediaRecorder(rec.stream, { mimeType: mime }) : new MediaRecorder(rec.stream);
      const chunks = [];
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.onstop = () => finishRecording(chunks, recorder.mimeType || mime || "audio/webm",
        (Date.now() - rec.started) / 1000, target);
      rec.recorder = recorder;
      rec.started = Date.now();
      rec.state = "recording";
      recorder.start();
      rec.timer = setInterval(() => {
        if ((Date.now() - rec.started) / 1000 >= MAX_SECONDS) {
          t.toast("Запись остановлена: больше 2 минут за раз нельзя.");
          stopRecording();
        }
        t.refresh();
      }, 250);
    } catch (e) {
      if (rec.stream) rec.stream.getTracks().forEach((x) => x.stop());
      rec.stream = null;
      rec.state = "idle";
      rec.target = "";
      const name = e && e.name;
      if (name === "NotAllowedError" || name === "SecurityError") {
        t.toast("Доступ к микрофону запрещён. Разрешите его для этого сайта в настройках браузера (значок у адреса).");
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        t.toast("Микрофон не найден.");
      } else {
        t.toast(e.message || "Не удалось начать запись");
      }
    }
    t.refresh();
  }

  function stopRecording() {
    if (rec.recorder && rec.recorder.state !== "inactive") rec.recorder.stop();
  }

  const micClick = (target) => () => {
    if (rec.state === "busy") return;
    if (recording(target)) { stopRecording(); return; }
    if (rec.state === "recording") { t.toast("Сейчас идёт запись для другого поля. Остановите её."); return; }
    startRecording(target);
  };
  const micIcon = (target) => () => (recording(target) ? "⏹" : rec.state === "busy" && rec.target === target ? "⏳" : "🎤");
  const micTitle = (target) => () => (recording(target)
    ? `Идёт запись ${clock((Date.now() - rec.started) / 1000)}. Нажмите, чтобы остановить`
    : "Сказать голосом: нажмите, говорите, нажмите ещё раз");
  const micShown = () => service("stt") !== "off";
  for (const [target, place] of [["chat", t.composerButton], ["assistant", t.assistantButton]]) {
    place({ icon: micIcon(target), title: micTitle(target), onClick: micClick(target), show: micShown });
  }

  // ---- text to speech ------------------------------------------------------------------------
  function stop() {
    token += 1;
    const hook = stopHook;
    stopHook = null;
    if (hook) hook();
    player.onended = null;
    player.onerror = null;
    try { player.pause(); } catch (e) { /* nothing was playing */ }
    try { if (synth) synth.cancel(); } catch (e) { /* ignore */ }
    if (now.state !== "idle") { now.key = ""; now.state = "idle"; t.refresh(); }
  }

  function playPiece(piece) {
    return new Promise((resolve, reject) => {
      const url = blobUrl(piece.audio, piece.mime);
      const done = (error) => {
        player.onended = null;
        player.onerror = null;
        stopHook = null;
        URL.revokeObjectURL(url);
        if (error) reject(error); else resolve();
      };
      stopHook = () => done(null);
      player.onended = () => done(null);
      player.onerror = () => done(new Error("Браузер не смог проиграть звук"));
      player.src = url;
      const p = player.play();
      if (p && p.catch) p.catch((e) => done(e));
    });
  }

  function speakBrowser(first, mine) {
    if (!synth) throw new Error("Этот браузер не умеет озвучивать текст");
    const voices = synth.getVoices();
    const wanted = (first.voice || "").toLowerCase();
    const lang = (first.lang || "").toLowerCase();
    const voice = (wanted && voices.find((v) => v.name.toLowerCase() === wanted))
      || voices.find((v) => v.lang.toLowerCase() === lang)
      || voices.find((v) => v.lang.toLowerCase().startsWith(lang.split("-")[0]));
    const one = (text) => new Promise((resolve, reject) => {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = first.lang || "ru-RU";
      if (voice) u.voice = voice;
      u.onend = () => { stopHook = null; resolve(); };
      u.onerror = (e) => {
        stopHook = null;
        if (e.error === "canceled" || e.error === "interrupted") resolve();
        else reject(new Error(e.error === "not-allowed" ? "NOT_ALLOWED" : `Голос браузера: ${e.error || "ошибка"}`));
      };
      stopHook = () => resolve();
      synth.speak(u);
    });
    return (async () => {
      for (const part of first.parts) {
        if (mine !== token) return;
        await one(part);
      }
    })();
  }

  async function speak(key, payload) {
    stop();
    const mine = token + 1; // stop() already moved the token: this call owns the next one
    token = mine;
    now.key = key;
    now.state = "loading";
    t.refresh();
    try {
      const status = await t.call("status");
      if (!status.tts.ready) throw new Error("Для озвучки не задан ключ. Впишите его в ⚙ → Дополнения → Голос.");
      const first = await t.call("speak", { ...payload, part: 0 });
      if (mine !== token) return;
      if (first.empty) { t.toast("Читать нечего: в сообщении только размышления, код или действия."); return; }
      if (first.truncated) t.toast("Прочитаю только начало: стоит ограничение длины (настройки «Голос»).");
      now.state = "playing";
      t.refresh();
      if (first.browser) { await speakBrowser(first, mine); return; }
      let current = first;
      for (let i = 0; i < first.parts; i += 1) {
        const next = i + 1 < first.parts ? t.call("speak", { ...payload, part: i + 1 }) : null;
        if (next) next.catch(() => {}); // an abandoned prefetch must not be an unhandled error
        if (mine !== token) return;
        await playPiece(current);
        if (mine !== token || !next) return;
        current = await next;
      }
    } catch (e) {
      if (mine !== token) return;
      const blocked = (e && (e.name === "NotAllowedError" || e.message === "NOT_ALLOWED"));
      t.toast(blocked
        ? "Браузер не дал начать чтение сам. Нажмите 🔊 у сообщения один раз: после этого дальше будет читаться само."
        : e.message);
    } finally {
      if (mine === token) { now.key = ""; now.state = "idle"; t.refresh(); }
    }
  }

  const live = (key) => now.key === key && now.state !== "idle";
  t.messageButton({
    icon: "🔊",
    title: (m) => (live("m:" + m.id) ? "Остановить чтение" : "Прочитать вслух"),
    onClick: (m) => {
      unlock();
      if (live("m:" + m.id)) { stop(); return null; }
      return speak("m:" + m.id, { message_id: m.id, source: "chat", character_id: m.character_id || "" });
    },
  });
  t.messageFooter((m) => {
    if (!live("m:" + m.id)) return null;
    return now.state === "loading" ? "🔊 готовлю звук…" : "🔊 читаю вслух…";
  });

  // A new reply: read it if asked to. (Phones allow this only after a tap on the page: see unlock.)
  t.on("message", (d) => {
    if (d.role !== "assistant" || !d.id || !t.settings.read_chat || service("tts") === "off") return null;
    return speak("m:" + d.id, { message_id: d.id, source: "chat", character_id: d.characterId || "" });
  });
  t.on("assistant_message", (d) => {
    if (!d.id || !d.text || !t.settings.read_assistant || service("tts") === "off") return null;
    return speak("a:" + d.id, { text: d.text, source: "assistant" });
  });
  t.on("send", () => stop()); // you answered: stop talking

  // The switch beside the box: read replies aloud, on/off, for the chat or for the assistant.
  const readOn = (place) => Boolean(t.settings["read_" + place]);
  const ttsShown = () => service("tts") !== "off";
  for (const [place, add] of [["chat", t.composerButton], ["assistant", t.assistantButton]]) {
    add({
      icon: () => (readOn(place) ? "🔊" : "🔇"),
      title: () => (readOn(place) ? "Читать ответы вслух: включено (нажмите, чтобы выключить)"
        : "Читать ответы вслух: выключено (нажмите, чтобы включить)"),
      show: ttsShown,
      onClick: async () => {
        unlock(); // this tap is what lets the phone play sound later
        const on = !readOn(place);
        await t.call("set_read", { place, on });
        await t.reloadSettings();
        if (!on) stop();
        t.toast(on ? "Ответы будут читаться вслух" : "Чтение вслух выключено");
        t.refresh();
      },
    });
  }

  // The browser's own voices are known only to the browser: tell the server so the settings can list them.
  let voicesSent = "";
  const reportVoices = () => {
    if (!synth) return;
    const list = synth.getVoices().map((v) => ({ name: v.name, lang: v.lang }));
    const stamp = JSON.stringify(list);
    if (!list.length || stamp === voicesSent) return;
    voicesSent = stamp;
    t.call("browser_voices", { voices: list }).catch(() => {});
  };
  reportVoices();
  if (synth && synth.addEventListener) synth.addEventListener("voiceschanged", reportVoices);

  return () => {
    stop();
    rec.cancelled = true;
    stopRecording();
    if (rec.stream) rec.stream.getTracks().forEach((x) => x.stop());
    clearInterval(rec.timer);
    if (synth && synth.removeEventListener) synth.removeEventListener("voiceschanged", reportVoices);
    if (silent) URL.revokeObjectURL(silent);
  };
}
