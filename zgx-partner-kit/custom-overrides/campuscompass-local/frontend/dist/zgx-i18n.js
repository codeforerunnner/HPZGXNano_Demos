(function () {
  const DICT = {
    "HP ZGX NANO · PROTOTYPE": "HP ZGX NANO · PROTOTIPO",
    "How local AI handled this": "Como lo proceso la IA local",
    "FICTIONAL MESA VISTA UNIVERSITY": "UNIVERSIDAD FICTICIA MESA VISTA",
    "Synthetic records only": "Solo registros sinteticos",
    "Scenario date: February 15, 2027": "Fecha del escenario: 15 de febrero de 2027",
    "CLEAR EVIDENCE. A USEFUL NEXT STEP.": "EVIDENCIA CLARA. UN SIGUIENTE PASO UTIL.",
    "Help students move": "Ayuda a los estudiantes a pasar",
    "from questions": "de las preguntas",
    "to a plan.": "a un plan.",
    "Bring the academic record together. Understand what needs attention. Give your advisor a brief they can review, with evidence behind every finding.": "Reune el expediente academico. Detecta que requiere atencion. Entrega a tu asesor un informe revisable, con evidencia detras de cada hallazgo.",
    "BEGIN THE DEMONSTRATION": "INICIAR LA DEMOSTRACION",
    "Choose a perspective": "Elige una perspectiva",
    "These are synthetic identities with different record permissions.": "Son identidades sinteticas con distintos permisos de acceso al expediente.",
    "THE MAYA CASE": "EL CASO MAYA",
    "One requirement.": "Un requisito.",
    "Two different records.": "Dos expedientes distintos.",
    "Degree audit": "Auditoria de grado",
    "Advanced laboratory requirement is open.": "El requisito de laboratorio avanzado esta abierto.",
    "Substitution approval": "Aprobacion de sustitucion",
    "A completed course may satisfy the requirement.": "Una asignatura aprobada puede satisfacer el requisito.",
    "Find the discrepancy. Preserve the evidence.": "Encuentra la discrepancia. Preserva la evidencia.",
    "Prepare the next step for human review.": "Prepara el siguiente paso para revision humana.",
    "Advisor · Maya’s assigned advisor": "Asesor · Asesor asignado a Maya",
    "Advisor · Different student assignment": "Asesor · Asignacion a otro estudiante",
    "Advisor · Different student assignments": "Asesor · Asignacion a otros estudiantes",
    "Student · Maya’s own record": "Estudiante · Expediente propio de Maya",
    "Local pretrained models + authorized evidence + deterministic checks": "Modelos locales preentrenados + evidencia autorizada + comprobaciones deterministas",
    "This prototype is not an officially released HP product or a real university deployment.": "Este prototipo no es un producto oficial de HP ni un despliegue universitario real.",
    "← Back to introduction": "← Volver a la introduccion",
    "DEMONSTRATION IDENTITY": "IDENTIDAD DE DEMOSTRACION",
    "YOUR PERMITTED STUDENTS": "ESTUDIANTES AUTORIZADOS",
    "No students in this identity’s current scope.": "No hay estudiantes en el alcance actual de esta identidad.",
    "ADVISING WORKSPACE": "ESPACIO DE ASESORIA",
    "Your advising workspace": "Tu espacio de asesoria",
    "Student advising": "Asesoria estudiantil",
    "Student review": "Revision del estudiante",
    "Credits earned": "Creditos aprobados",
    "In progress": "En curso",
    "Cumulative GPA": "Promedio acumulado",
    "Advising question": "Pregunta de asesoria",
    "Start with the question.": "Empieza con la pregunta.",
    "Follow the evidence.": "Sigue la evidencia.",
    "Uses this student’s permitted records": "Usa los expedientes autorizados de este estudiante",
    "Compare the records. Understand the gap. Prepare a reviewable next action.": "Compara los registros. Entiende la brecha. Prepara una siguiente accion revisable.",
    "The review brings together the audit, course history, approval, and applicable policy. You decide what happens next.": "La revision reune la auditoria, el historial academico, aprobaciones y normativa aplicable. Tu decides el siguiente paso.",
    "1. Find records": "1. Buscar registros",
    "2. Check support": "2. Verificar soporte",
    "3. Review the brief": "3. Revisar informe",
    "01 / ASK": "01 / PREGUNTAR",
    "02 / FINDING": "02 / HALLAZGO",
    "03 / NEXT ACTION": "03 / SIGUIENTE ACCION",
    "A CLEARER PATH FORWARD": "UN CAMINO MAS CLARO",
    "A brief ready for human review": "Informe listo para revision humana",
    "Finding, next action, exact sources, and reviewer field": "Hallazgo, siguiente accion, fuentes exactas y campo de revisor",
    "Bring the records to the registrar": "Llevar los registros a Secretaria Academica",
    "Owner: advisor · Confirmation: registrar": "Responsable: asesor · Confirmacion: secretaria",
    "What needs attention?": "Que requiere atencion?",
    "What remains unresolved": "Que queda sin resolver",
    "What the audit records": "Que registra la auditoria",
    "What the approval establishes": "Que establece la aprobacion",
    "After reading the evidence, an advisor can save a separate reviewed revision. The original draft stays unchanged.": "Tras leer la evidencia, un asesor puede guardar una revision separada. El borrador original no se modifica.",
    "I have reviewed this brief · Save revision": "He revisado este informe · Guardar revision",
    "Preview brief ↗": "Vista previa del informe ↗",
    "Export PDF ↓": "Exportar PDF ↓",
    "Review the evidence": "Revisar la evidencia",
    "Review in progress…": "Revision en curso...",
    "Review could not continue": "La revision no pudo continuar",
    "Scoped review complete": "Revision completada",
    "SCOPED DEMONSTRATION AUDIT": "AUDITORIA DE DEMOSTRACION ACOTADA",
    "View steps": "Ver pasos",
    "Source records": "Registros fuente",
    "Evidence needed": "Evidencia necesaria",
    "Permission scope": "Alcance de permisos",
    "Registrar review needed": "Requiere revision de Secretaria",
    "The evidence": "La evidencia",
    "Exact source records in this review.": "Registros fuente exactos de esta revision.",
    "Permission checked first": "Permisos verificados primero",
    "Retrieval, source viewing, and PDF export use the same permitted student scope.": "La recuperacion, visualizacion y exportacion PDF usan el mismo alcance autorizado.",
    "Presenter scenario controls": "Controles de escenario del presentador",
    "Evidence scenario": "Escenario de evidencia",
    "Original evidence": "Evidencia original",
    "Approval missing · separate scenario": "Falta aprobacion · escenario separado",
    "The original record remains intact. Switching scenarios starts a new review.": "El registro original permanece intacto. Cambiar de escenario inicia una nueva revision.",
    "Try a record outside your assignment": "Prueba un expediente fuera de tu asignacion",
    "Maya is assigned to Alex Morgan. This request should be denied before retrieval.": "Maya esta asignada a Alex Morgan. Esta peticion debe denegarse antes de recuperar datos.",
    "Request Maya’s records": "Solicitar expedientes de Maya",
    "Human review required. No graduation certification or record changes are automated.": "Se requiere revision humana. Ninguna certificacion ni cambio de expediente es automatico.",
    "Synthetic demonstration · Fictional Mesa Vista University · Not an officially released HP product": "Demostracion sintetica · Universidad ficticia Mesa Vista · No es un producto oficial de HP",
    "EXACT SOURCE EVIDENCE": "EVIDENCIA FUENTE EXACTA",
    "Open source PDF ↗": "Abrir PDF original ↗",
    "DRAFT FOR ADVISOR REVIEW": "BORRADOR PARA REVISION DEL ASESOR",
    "Advising brief": "Informe de asesoria",
    "Download PDF ↓": "Descargar PDF ↓",
    "PRESENTER VIEW": "VISTA DEL PRESENTADOR",
    "Local models work with permitted university evidence. Deterministic checks keep calculations and released claims tied to the record.": "Los modelos locales trabajan con evidencia universitaria autorizada. Las comprobaciones deterministas vinculan calculos y afirmaciones al expediente.",
    "Refresh service status": "Actualizar estado de servicios",
    "Service responding": "Servicio activo",
    "Unavailable": "No disponible",
    "One shared generation model": "Un modelo de generacion compartido",
    "Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B provide local retrieval layers. No fine tuning is used.": "Qwen3-Embedding-0.6B y Qwen3-Reranker-0.6B aportan la recuperacion local. No se usa fine-tuning.",
    "Measured for this review": "Medido en esta revision",
    "Analysis before PDF rendering": "Analisis previo al renderizado PDF",
    "Offline verification": "Verificacion offline",
    "Review boundary": "Limite de revision",
    'Identifying the records needed': 'Identificando los registros necesarios',
    'Finding relevant records': 'Buscando registros relevantes',
    'Comparing the audit with approvals': 'Comparando auditoria con aprobaciones',
    'Checking source support': 'Comprobando soporte documental',
    'Preparing the advising brief': 'Preparando el informe de asesoria',
    'Repairing the brief composition': 'Ajustando la redaccion del informe',
    'Advising brief ready for review': 'Informe de asesoria listo para revisar',
    'Local analysis stopped': 'Analisis local detenido',
    'Local analysis could not finish. Check the local model services and run the review again.': 'El analisis local no pudo finalizar. Comprueba los servicios de modelos locales y vuelve a ejecutar la revision.',
    'An approval is recorded. The audit still needs reconciliation.': 'Consta una aprobacion registrada. La auditoria aun requiere conciliacion.',
    'More evidence is needed before resolving the requirement.': 'Se necesita mas evidencia antes de resolver el requisito.',
    'No discrepancy identified in the scoped review.': 'No se han identificado discrepancias en esta revision.',
    'Policy': 'Normativa',
    'Substitution Approval': 'Aprobacion de sustitucion',
    'Advising Note': 'Nota de asesoria',
    'Degree Audit': 'Auditoria de grado',
    'Academic transcript': 'Expediente academico',
    "Overview": "Resumen",
    "Claims": "Afirmaciones",
    "Trace": "Traza",
    "Student": "Estudiante",
    "Question": "Pregunta",
    "Not recorded": "No registrado",
    "Status unavailable": "Estado no disponible",
    "What is blocking Maya’s spring graduation, and what should her advisor do next?": "Que esta bloqueando la graduacion de primavera de Maya y que debe hacer su asesor ahora?",
    "Review boundary": "Limite de revision"
  };

  const orig = new WeakMap();
  let lang = localStorage.getItem("zgx_demo_lang") || "es";
  let applying = false;

  function translateText(s) {
    if (!s) return s;
    const trimmed = s.trim();
    if (!trimmed) return s;
    if (DICT[trimmed]) return s.replace(trimmed, DICT[trimmed]);
    let out = s;
    const SUBS = [
      ["DRAFT · UNREVIEWED", "BORRADOR · SIN REVISAR"],
      ["Expected ", "Prevista: "],
      ["Catalog ", "Plan "],
      ["recorded workflow events", "eventos de flujo registrados"],
      ["recorded workflow event", "evento de flujo registrado"],
      ["Scoped degree audit snapshot:", "Auditoria de grado acotada:"],
      ["is OPEN.", "esta ABIERTO."],
      ["is not currently applied to", "no esta aplicada actualmente a"],
      ["This snapshot does not certify satisfaction of all degree requirements.", "Esta instantanea no certifica el cumplimiento de todos los requisitos del titulo."],
      ["Approved substitution: completed", "Sustitucion aprobada: completada"],
      ["may satisfy", "puede satisfacer"],
      ["under the", "bajo el"],
      ["catalog.", "plan."],
      ["Approved by the fictional department authority. Registrar posting remains pending.", "Aprobada por la autoridad departamental ficticia. Pendiente de registro en Secretaria Academica."],
      ["Advisor: ask the registrar to compare the audit with the approved substitution, confirm its applicability, post it if appropriate, and rerun the audit.", "Asesor: solicita a Secretaria Academica que compare la auditoria con la sustitucion aprobada, confirme su validez, la registre si procede y vuelva a ejecutar la auditoria."]
    ];
    for (const [k, v] of SUBS) {
      if (out.includes(k)) out = out.split(k).join(v);
    }
    if (out !== s) return out;
    if (trimmed.endsWith("’s graduation review") || trimmed.endsWith("'s graduation review")) {
      const name = trimmed.replace(/[’']s graduation review$/, "");
      return s.replace(trimmed, "Revision de graduacion de " + name);
    }
    if (trimmed.startsWith("Expected ")) return s.replace("Expected ", "Prevista: ");
    if (trimmed.endsWith(" recorded workflow events") || trimmed.endsWith(" recorded workflow event")) {
      return s.replace(/recorded workflow events?/, "eventos de flujo registrados");
    }
    if (trimmed.startsWith("Checked ")) return s.replace("Checked ", "Comprobado ");
    if (trimmed.endsWith(" repair attempts · Live result")) {
      return s.replace(" repair attempts · Live result", " intentos de reparacion · En vivo");
    }
    if (trimmed.startsWith("PDF rendering: ")) {
      return s.replace("PDF rendering: ", "Renderizado PDF: ").replace(" seconds", " segundos");
    }
    return s;
  }

  function applyDom() {
    if (applying) return;
    applying = true;
    try {
      const root = document.getElementById("root") || document.body;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (!orig.has(node)) orig.set(node, node.nodeValue);
        const enVal = orig.get(node);
        const nv = lang === "es" ? translateText(enVal) : enVal;
        if (node.nodeValue !== nv) node.nodeValue = nv;
      }
      document.querySelectorAll("textarea").forEach((ta) => {
        const m = (ta.value || "").match(/^What is blocking ([^’']+)['’]s spring graduation, and what should (?:her|the) advisor do next\?$/);
        if (lang === "es" && m) {
          if (!ta.dataset.zgxEnVal) ta.dataset.zgxEnVal = ta.value;
          const esQ = `Que esta bloqueando la graduacion de primavera de ${m[1]} y que debe hacer su asesor ahora?`;
          if (ta.value !== esQ) {
            const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
            setter.call(ta, esQ);
            ta.dispatchEvent(new Event("input", { bubbles: true }));
          }
        } else if (lang === "en" && ta.dataset.zgxEnVal && ta.value !== ta.dataset.zgxEnVal) {
          const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
          setter.call(ta, ta.dataset.zgxEnVal);
          ta.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
      document.querySelectorAll("input[placeholder],textarea[placeholder]").forEach((el) => {
        if (!el.dataset.zgxEnPh) el.dataset.zgxEnPh = el.placeholder;
        const enPh = el.dataset.zgxEnPh;
        el.placeholder = lang === "es" ? (DICT[enPh] || enPh) : enPh;
      });
    } finally {
      applying = false;
    }
  }

  function setLang(l) {
    lang = l === "en" ? "en" : "es";
    localStorage.setItem("zgx_demo_lang", lang);
    const bEs = document.getElementById("zgx-btn-es");
    const bEn = document.getElementById("zgx-btn-en");
    if (bEs && bEn) {
      bEs.classList.toggle("active", lang === "es");
      bEn.classList.toggle("active", lang === "en");
    }
    applyDom();
  }

  function init() {
    const bEs = document.getElementById("zgx-btn-es");
    const bEn = document.getElementById("zgx-btn-en");
    if (bEs) bEs.addEventListener("click", () => setLang("es"));
    if (bEn) bEn.addEventListener("click", () => setLang("en"));
    const target = document.getElementById("root") || document.body;
    let _t = null;
    const obs = new MutationObserver(() => {
      if (applying || lang !== "es") return;
      clearTimeout(_t);
      _t = setTimeout(() => {
        obs.disconnect();
        applyDom();
        obs.observe(target, { childList: true, subtree: true });
      }, 60);
    });
    obs.observe(target, { childList: true, subtree: true });
    setLang(lang);
  }

  if (document.readyState === "loading") {
    window.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
