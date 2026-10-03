// Interfaz de consulta: manda la pregunta a /api/consultar y pinta respuesta, normas y pasajes.
"use strict";

const $ = (sel) => document.querySelector(sel);
const FORMATOS = { multiple_choice: "Selección múltiple", semi_open: "Semiabierta", open_ended: "Abierta" };
const EJEMPLOS = {
  semi: { pregunta: "¿Cuáles son los elementos esenciales de validez de un contrato?", formato: "" },
  mc: {
    pregunta: "¿En cuál de los siguientes casos procede la acción judicial de grupo?",
    formato: "multiple_choice",
    opciones: {
      A: "Cuando un grupo de personas busca proteger derechos fundamentales individuales de aplicación inmediata.",
      B: "Cuando un grupo de ciudadanos busca defender el interés colectivo ambiental o del espacio público.",
      C: "Cuando un conjunto de personas resulta afectado por un mismo hecho que les causa perjuicios individuales derivados de una causa común.",
      D: "Cuando se pretende declarar la inconstitucionalidad de una norma con fuerza de ley.",
    },
  },
  open: {
    pregunta: "Sara, de 38 años, fue diagnosticada con esclerosis múltiple. Su EPS le niega un medicamento " +
      "formulado por la neuróloga tratante argumentando que no está incluido en el plan de beneficios. " +
      "¿Qué acción procede y cómo debería resolverse el caso?",
    formato: "open_ended",
  },
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Resalta en la respuesta lo que parece una cita (artículos, leyes, decretos, sentencias, códigos).
const RE_CITA = new RegExp([
  String.raw`\bart(?:[íi]culos?|s?\.)\s*\d+[A-Za-z]?(?:\s*(?:,|y|e)\s*\d+[A-Za-z]?)*`,
  String.raw`\b(?:Ley|Decreto(?:\s+Ley)?|Acto\s+Legislativo|Resoluci[óo]n)\s+(?:No\.?\s*)?\d+\s+de\s+\d{4}`,
  String.raw`\b(?:Sentencia\s+)?(?:C|T|SU|SL|SC|SP|STC|STL)\s?-\s?\d+\s+de\s+\d{2,4}`,
  String.raw`\b(?:Constituci[óo]n\s+Pol[íi]tica|C[óo]digo\s+(?:Civil|Penal|de\s+Comercio|General\s+del\s+Proceso|Sustantivo\s+del\s+Trabajo|Procesal\s+del\s+Trabajo|de\s+Procedimiento\s+Penal|de\s+la\s+Infancia\s+y\s+la\s+Adolescencia)|Estatuto\s+(?:Tributario|del\s+Consumidor)|CPACA)`,
].join("|"), "gi");
const conCitas = (texto) => esc(texto).replace(RE_CITA, (m) => `<mark class="cita">${m}</mark>`);

const ICONO_OK = `<svg class="norma__icono" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2 4 5v6c0 5 3.4 9.7 8 11 4.6-1.3 8-6 8-11V5l-8-3Zm-1.2 14.2-3.5-3.5 1.4-1.4 2.1 2.1 4.9-4.9 1.4 1.4-6.3 6.3Z"/></svg>`;
const ICONO_ALERTA = `<svg class="norma__icono" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 2 1 21h22L12 2Zm1 15h-2v-2h2v2Zm0-4h-2V9h2v4Z"/></svg>`;

// ---------------------------------------------------------------- estado del servidor
let listo = false;
let modo = "";
async function revisarEstado() {
  const pill = $("#estado"), texto = $("#estado-texto");
  try {
    const r = await fetch("/api/estado");
    const e = await r.json();
    pill.className = "estado estado--" + (e.estado === "listo" ? "listo" : e.estado);
    listo = e.estado === "listo";
    modo = e.modo || "";
    if (e.estado === "listo") {
      texto.textContent = e.modo === "demo" ? "Demo lista" : "Listo";
      pill.title = [e.modelo && `Modelo: ${e.modelo}`, e.hibrido !== undefined && `Híbrido: ${e.hibrido ? "sí" : "no"}`,
        e.reranker && `Reranker: ${e.reranker}`, e.carga_s !== undefined && `Carga: ${e.carga_s} s`]
        .filter(Boolean).join("\n");
      $("#aviso-demo").hidden = e.modo !== "demo";
      return;
    }
    if (e.estado === "error") {
      texto.textContent = "Error al cargar";
      pill.title = e.detalle || "";
      mostrarError("No se pudieron cargar los índices o el modelo:\n" + (e.detalle || ""));
      return;
    }
    texto.textContent = "Cargando índices…";
  } catch {
    pill.className = "estado estado--error";
    texto.textContent = "Sin conexión";
  }
  setTimeout(revisarEstado, 3000);
}

// ---------------------------------------------------------------- formulario
const formulario = $("#formulario");
const selectFormato = $("#formato");
const campoOpciones = $("#opciones");

function actualizarOpciones() {
  campoOpciones.hidden = selectFormato.value !== "multiple_choice";
}
selectFormato.addEventListener("change", actualizarOpciones);

document.querySelectorAll("[data-ejemplo]").forEach((b) => b.addEventListener("click", () => {
  const ej = EJEMPLOS[b.dataset.ejemplo];
  $("#pregunta").value = ej.pregunta;
  selectFormato.value = ej.formato;
  for (const letra of "ABCD") campoOpciones.querySelector(`[name=${letra}]`).value = ej.opciones?.[letra] || "";
  actualizarOpciones();
  $("#pregunta").focus();
}));

$("#pregunta").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) formulario.requestSubmit();
});

formulario.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const pregunta = $("#pregunta").value.trim();
  if (pregunta.length < 3) return;
  const formato = selectFormato.value || null;
  let opciones = null;
  if (formato === "multiple_choice") {
    opciones = {};
    for (const letra of "ABCD") {
      const v = campoOpciones.querySelector(`[name=${letra}]`).value.trim();
      if (v) opciones[letra] = v;
    }
  }
  await consultar({ pregunta, formato, opciones });
});

let reloj = null;
function cargando(si) {
  $("#enviar").disabled = si;
  $("#enviar").textContent = si ? "Consultando…" : "Consultar";
  $("#cargando").hidden = !si;
  clearInterval(reloj);
  if (si) {
    const t0 = Date.now();
    $("#cronometro").textContent = "0 s";
    reloj = setInterval(() => { $("#cronometro").textContent = `${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
  }
}

function mostrarError(msg) {
  const caja = $("#error");
  caja.textContent = msg;
  caja.hidden = false;
}

async function consultar(cuerpo) {
  $("#error").hidden = true;
  $("#vacio").hidden = true;
  $("#salida").hidden = true;
  cargando(true);
  $("#resultado").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const r = await fetch("/api/consultar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cuerpo),
    });
    const datos = await r.json().catch(() => ({}));
    if (!r.ok) {
      const det = Array.isArray(datos.detail) ? datos.detail.map((d) => d.msg).join("; ") : datos.detail;
      throw new Error(det || `Error ${r.status}`);
    }
    pintar(cuerpo.pregunta, datos);
  } catch (e) {
    mostrarError(e.message || String(e));
    $("#vacio").hidden = false;
  } finally {
    cargando(false);
  }
}

// ---------------------------------------------------------------- resultado
function pintar(pregunta, datos) {
  const sub = datos.respuesta;
  $("#pregunta-eco").textContent = `«${pregunta}»`;

  const meta = [
    `<span class="etiqueta etiqueta--turquesa">${esc(FORMATOS[sub.formato] || sub.formato)}</span>`,
    `<span class="etiqueta">${(sub.latencia_ms / 1000).toFixed(1)} s</span>`,
    `<span class="etiqueta">${datos.pasajes.length} pasajes</span>`,
    sub.abstencion ? `<span class="etiqueta etiqueta--naranja">Abstención</span>` : "",
    datos.traza?.formato_origen === "detectado" ? `<span class="etiqueta">formato detectado</span>` : "",
    datos.traza?.demo ? `<span class="etiqueta etiqueta--naranja">Demostración</span>` : "",
  ];
  $("#metadatos").innerHTML = meta.join("");
  $("#respuesta").innerHTML = sub.abstencion ? htmlAbstencion() : htmlRespuesta(sub, datos.opciones || {});
  pintarNormas(datos.normas, sub.abstencion);
  pintarPasajes(datos.pasajes);
  $("#json").textContent = JSON.stringify(sub, null, 2);
  $("#traza").textContent = JSON.stringify(datos.traza, null, 2);
  $("#salida").hidden = false;
}

function htmlAbstencion() {
  return `<div class="abstencion">${ICONO_ALERTA}<div><strong>El sistema se abstiene de responder.</strong>
    No encontró en su corpus fundamento suficiente para una respuesta confiable. Reformule la pregunta
    o sea más específico (norma, artículo o sentencia).</div></div>`;
}

function htmlRespuesta(sub, opciones) {
  if (sub.formato === "multiple_choice") {
    const letras = [...new Set([sub.respuesta_correcta, ...Object.keys(sub.descarte_opciones || {}),
      ...Object.keys(opciones)])].filter(Boolean).sort();
    const filas = letras.map((l) => {
      const elegida = l === sub.respuesta_correcta;
      const motivo = elegida ? "Opción elegida" : (sub.descarte_opciones || {})[l] || "";
      return `<div class="opcion ${elegida ? "opcion--elegida" : ""}">
        <span class="opcion__letra">${esc(l)}</span>
        <div>${opciones[l] ? `<span>${esc(opciones[l])}</span>` : ""}
          <span class="opcion__motivo">${conCitas(motivo)}</span></div></div>`;
    }).join("");
    return `<h4>Respuesta: opción ${esc(sub.respuesta_correcta)}</h4>${filas}
      <h4>Justificación</h4><p>${conCitas(sub.justificacion)}</p>`;
  }
  if (sub.formato === "semi_open") {
    const palabras = (sub.palabras_clave || []).map((p) => `<span>${esc(p)}</span>`).join("");
    return `<h4>Respuesta</h4><p>${conCitas(sub.respuesta)}</p>
      ${palabras ? `<h4>Palabras clave</h4><div class="palabras">${palabras}</div>` : ""}
      <h4>Referencia legal</h4><p class="referencia-legal">${conCitas(sub.referencia_legal || "—")}</p>`;
  }
  return [["Marco normativo", "marco_normativo"], ["Análisis", "analisis"],
    ["Jurisprudencia", "jurisprudencia"], ["Conclusión", "conclusion"]]
    .map(([t, k]) => `<h4>${t}</h4><p>${conCitas(sub[k] || "—")}</p>`).join("");
}

function pintarNormas(normas, abstencion) {
  const lista = $("#normas");
  $("#n-normas").textContent = normas.length;
  if (!normas.length) {
    lista.innerHTML = `<li class="sin-datos">${abstencion ? "Sin respuesta, sin citas." :
      "La respuesta no cita normas que el extractor reconozca."}</li>`;
    return;
  }
  lista.innerHTML = normas.map((n, i) => {
    const donde = n.pasajes.length ? ` · pasaje${n.pasajes.length > 1 ? "s" : ""} ${n.pasajes.join(", ")}` : "";
    const detalle = !n.respaldada ? "Sin respaldo en los pasajes recuperados"
      : n.mismo_articulo ? `Respaldada${donde}` : `Respaldada por la norma, no el mismo artículo${donde}`;
    return `<li><button type="button" class="norma ${n.respaldada ? "" : "norma--sin-respaldo"}"
      data-i="${i}" aria-pressed="false">${n.respaldada ? ICONO_OK : ICONO_ALERTA}
      <span><span class="norma__nombre">${esc(n.etiqueta)}</span>
      <span class="norma__detalle">${esc(detalle)}</span></span></button></li>`;
  }).join("");
  lista.querySelectorAll(".norma").forEach((b) => b.addEventListener("click", () => {
    const activo = b.getAttribute("aria-pressed") === "true";
    lista.querySelectorAll(".norma").forEach((x) => x.setAttribute("aria-pressed", "false"));
    resaltarPasajes(activo ? null : normas[+b.dataset.i].pasajes);
    if (!activo) b.setAttribute("aria-pressed", "true");
  }));
}

function resaltarPasajes(numeros) {
  const items = document.querySelectorAll(".pasaje");
  items.forEach((el) => {
    const n = +el.dataset.n;
    el.classList.toggle("pasaje--resaltado", !!numeros && numeros.includes(n));
    el.classList.toggle("pasaje--atenuado", !!numeros && !numeros.includes(n));
  });
  const primero = numeros && document.querySelector(`.pasaje[data-n="${numeros[0]}"]`);
  if (primero) primero.scrollIntoView({ behavior: "smooth", block: "center" });
}

function pintarPasajes(pasajes) {
  const lista = $("#pasajes");
  $("#n-pasajes").textContent = pasajes.length;
  if (!pasajes.length) {
    lista.innerHTML = `<li class="sin-datos">No se entregan pasajes (el sistema se abstuvo).</li>`;
    return;
  }
  const maximo = Math.max(...pasajes.map((p) => p.score ?? 0), 1e-9);
  lista.innerHTML = pasajes.map((p) => {
    const cuerpo = p.texto.replace(/^\s*\[[^\]]*\]\s*/, "");
    const largo = cuerpo.length > 420;
    const pct = Math.max(4, Math.round(100 * (p.score ?? 0) / maximo));
    const normas = p.normas.slice(0, 6).map((n) => `<span class="mini">${esc(n)}</span>`).join("");
    return `<li class="tarjeta pasaje" data-n="${p.n}">
      <div class="pasaje__cabeza">
        <span class="pasaje__n">${p.n}</span>
        <span class="pasaje__ref">${esc(p.referencia)}</span>
        ${p.usado ? `<span class="etiqueta etiqueta--naranja">Usado en la respuesta</span>` : ""}
        <span class="puntaje" title="Puntaje del recuperador${p.score_rerank != null ? ` · reranker ${p.score_rerank.toFixed(3)}` : ""}">
          <span class="puntaje__barra"><i style="width:${pct}%"></i></span>${p.score != null ? p.score.toFixed(3) : "—"}</span>
      </div>
      <p class="pasaje__texto ${largo ? "recortado" : ""}">${esc(cuerpo)}</p>
      <div class="pasaje__pie">
        <span class="pasaje__doc">${esc(p.doc_id)}${p.inicio != null && p.fin ? ` [${p.inicio}–${p.fin}]` : ""}</span>
        ${normas}
        ${largo ? `<button type="button" class="enlace" data-expandir>Ver completo</button>` : ""}
      </div></li>`;
  }).join("");
  lista.querySelectorAll("[data-expandir]").forEach((b) => b.addEventListener("click", () => {
    const texto = b.closest(".pasaje").querySelector(".pasaje__texto");
    const abierto = texto.classList.toggle("recortado");
    b.textContent = abierto ? "Ver completo" : "Ver menos";
  }));
}

$("#copiar").addEventListener("click", async (ev) => {
  ev.preventDefault();
  try {
    await navigator.clipboard.writeText(JSON.stringify(JSON.parse($("#json").textContent)));
    ev.target.textContent = "Copiado";
  } catch {
    ev.target.textContent = "No se pudo copiar";
  }
  setTimeout(() => { ev.target.textContent = "Copiar"; }, 1500);
});

revisarEstado();
