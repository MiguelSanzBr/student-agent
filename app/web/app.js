// Todo texto vindo do servidor/modelo entra no DOM só via textContent (nunca HTML).
"use strict";
const $ = (id) => document.getElementById(id);
const log = $("log"), form = $("form"), input = $("text"), sendBtn = $("send");
// O rótulo do GEN-090 vem do código do serviço; o código nunca é exibido.
const SERVICE_LABELS = { "GEN-090": "Falar com a secretaria" };
const ERR = "Algo deu errado. Tente de novo em instantes.";

function convId() {
  let id = sessionStorage.getItem("conv");
  if (!id) { id = crypto.randomUUID(); sessionStorage.setItem("conv", id); }
  return id;
}

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function add(node) { log.appendChild(node); node.scrollIntoView({ block: "end" }); return node; }
const bubble = (text, cls) => add(el("div", text, "msg " + (cls || "bot")));

async function api(path, body) {
  const r = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch { /* corpo vazio */ }
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : ERR);
  return data;
}

function feedback(traceId) {
  const box = el("div", undefined, "fb");
  for (const [icon, label, useful] of [["👍", "Resposta útil", true], ["👎", "Resposta não útil", false]]) {
    const b = el("button", icon);
    b.type = "button"; b.setAttribute("aria-label", label);
    b.onclick = async () => {
      box.querySelectorAll("button").forEach((x) => { x.disabled = true; });
      try { await api("/feedback", { trace_id: traceId, useful }); box.replaceChildren(el("span", "Obrigado pelo retorno.")); }
      catch (e) { box.replaceChildren(el("span", e.message)); }
    };
    box.appendChild(b);
  }
  return box;
}

function draftCard(d) {
  const card = el("section", undefined, "card");
  card.setAttribute("aria-label", "Confirmação do pedido");
  card.appendChild(el("h2", SERVICE_LABELS[d.service_code] || d.service_name_pt));
  if (d.enrollment_label) card.appendChild(el("div", d.enrollment_label));
  card.appendChild(el("div", "Taxa: " + d.fee));
  card.appendChild(el("div", "Vencimento: " + d.due_date));
  card.appendChild(el("p", d.warning, "warn"));
  const lab = el("label", "Descrição (você pode editar)");
  const desc = el("textarea", undefined);
  desc.value = d.description; desc.rows = 3; desc.maxLength = 1000;
  lab.appendChild(desc); card.appendChild(lab);
  const ok = el("button", "Confirmar", "primary");
  ok.type = "button";
  const out = el("div"); out.setAttribute("role", "status");
  ok.onclick = async () => {
    ok.disabled = true; desc.disabled = true;
    try {
      const r = await api("/protocols/confirm", { draft_id: d.draft_id, description: desc.value });
      card.replaceChildren(el("p", r.mensagem));
    } catch (e) { out.textContent = e.message; ok.disabled = false; desc.disabled = false; }
  };
  card.append(ok, out);
  add(card);
}

async function send(text) {
  $("suggested").replaceChildren();
  bubble(text, "user");
  sendBtn.disabled = true;
  let progress = null, trace = "", answered = false;
  const clear = () => { if (progress) { progress.remove(); progress = null; } };
  const handle = (ev, d) => {
    if (ev === "meta") trace = d.trace_id;
    else if (ev === "progress") { clear(); progress = bubble(d.texto, "progress"); }
    else if (ev === "draft") { clear(); draftCard(d); }
    else if (ev === "answer") { clear(); answered = true; bubble(d.text); }
    else if (ev === "error") { clear(); bubble(d.message, "error"); }
    else if (ev === "done") { clear(); trace = d.trace_id || trace; }
  };
  try {
    const r = await fetch("/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: convId(), text }),
    });
    if (r.status === 401) { bubble("Sua sessão expirou. Abra o link de acesso de novo.", "error"); return; }
    if (!r.ok || !r.body) {
      let msg = ERR;
      try { const j = await r.json(); if (typeof j.detail === "string") msg = j.detail; } catch { /* sem corpo */ }
      bubble(msg, "error"); return;
    }
    const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        let ev = "", data = "";
        for (const ln of block.split("\n")) {
          if (ln.startsWith("event: ")) ev = ln.slice(7);
          else if (ln.startsWith("data: ")) data += ln.slice(6);
        }
        if (ev && ev !== "ping") handle(ev, JSON.parse(data || "{}"));
      }
    }
    if (answered && trace) add(feedback(trace));
  } catch { clear(); bubble(ERR, "error"); }
  finally { sendBtn.disabled = false; input.focus(); }
}

function suggestions(list) {
  const box = $("suggested");
  for (const q of list) {
    const b = el("button", q); b.type = "button";
    b.onclick = () => send(q);
    box.appendChild(b);
  }
}

async function start() {
  try {
    const r = await fetch("/me");
    if (!r.ok) throw new Error();
    const me = await r.json();
    bubble("Olá, " + me.first_name + "! Como posso ajudar?");
    $("demo-banner").hidden = !me.demo_mode;
    if (me.demo_mode) suggestions(me.suggested || []);
  } catch {
    bubble("Não foi possível entrar. Abra o link de acesso fornecido.", "error");
    sendBtn.disabled = true;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const t = input.value.trim();
  if (!t) return;
  input.value = "";
  send(t);
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
});
$("new-chat").onclick = () => {
  sessionStorage.removeItem("conv");
  log.replaceChildren(); $("suggested").replaceChildren();
  start();
};
start();
