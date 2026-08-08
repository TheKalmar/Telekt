/* Framework-free localization. Add a language with I18N.registerLocale(). */
(function () {
  const STORAGE_KEY = "digital-company-language";
  const catalogs = {
    en: {
      "nav.overview":"Overview","nav.operations":"Operations","nav.decisions":"Decisions","nav.workspace":"Workspace","nav.configuration":"Configuration","nav.tagline":"Autonomy controlled by the company owner",
      "header.newCompany":"+ New company","header.company":"Active company","header.language":"Language",
      "view.overview.title":"Overview","view.overview.description":"Goal, budget status and the company's most important controls.",
      "view.operations.title":"Operations","view.operations.description":"Active agents, browser missions and the execution trail.",
      "view.decisions.title":"Decisions","view.decisions.description":"Approvals, human takeover and the conversation with the CEO.",
      "view.workspace.title":"Workspace","view.workspace.description":"Artifacts, integrations and everything the company has produced.",
      "view.configuration.title":"Configuration","view.configuration.description":"Models, communication, platforms and agent capabilities.",
      "config.general":"General","config.models":"Models","config.communication":"Email & approvals","config.integrations":"Integrations","config.skills":"Skills",
      "config.company.title":"Company","config.company.body":"The active company, its goal, capital, market and autonomy boundaries.","config.company.create":"Create a new company",
      "config.control.title":"Execution control","config.control.body":"The CEO operates autonomously within the defined rules and budget.","config.control.open":"Open Mission Control",
      "config.models.title":"AI models","config.models.body":"Choose local, hybrid or cloud mode and configure the Ollama model.","config.models.open":"Configure models",
      "config.runtime.title":"Local runtime","config.runtime.body":"Check Ollama availability without changing the active mode.","config.runtime.check":"Check runtime",
      "config.email.title":"Email & approvals","config.email.body":"Configure recipients for the daily brief and signed approval requests.","config.email.open":"Configure email",
      "config.decisions.title":"Open decisions","config.decisions.body":"Approval, decline reasons and human takeover stay in one place.","config.decisions.open":"Go to decisions",
      "config.platforms.title":"Platforms & services","config.platforms.body":"Shopify and future platforms the CEO can choose instead of custom development.","config.platforms.open":"Configure platforms",
      "config.browser.title":"Browser runtime","config.browser.body":"Controlled browsing for research, login checkpoints and human takeover.","config.browser.open":"Open browser cockpit",
      "panel.readiness":"Runtime readiness","panel.browserMission":"Live browser mission","panel.missionControl":"Mission control","panel.companyState":"Company state","panel.telemetry":"Operations & model telemetry","panel.companyWorkspace":"Company workspace","panel.capabilities":"Platform capabilities","panel.skills":"Agent Skill Registry","panel.goal":"Company goal","panel.timeline":"Decision & execution timeline","panel.takeover":"Human takeover","panel.approvals":"Human approvals","panel.chat":"Stakeholder chat",
      "action.start":"▶ Start / Resume","action.pause":"Ⅱ Pause","action.stop":"■ Stop","action.modelSettings":"Model settings","action.enable":"Enable","action.missingAccess":"Missing access","action.disable":"Disable","action.send":"Send","action.close":"Close","action.cancel":"Cancel","action.save":"Save settings",
      "chat.placeholder":"Change a priority, ask a question or challenge the CEO's decision...","chat.directive":"Directive — interrupts the current plan","chat.question":"Question — does not interrupt the plan",
      "runtime.available":"Ollama status: available","runtime.unavailable":"Ollama status: unavailable",
      "footer":"Local control plane · durable PostgreSQL state · deterministic permission governor"
    },
    sr: {
      "nav.overview":"Pregled","nav.operations":"Operacije","nav.decisions":"Odluke","nav.workspace":"Radni prostor","nav.configuration":"Konfiguracija","nav.tagline":"Autonomija pod kontrolom vlasnika kompanije",
      "header.newCompany":"+ Nova firma","header.company":"Aktivna firma","header.language":"Jezik",
      "view.overview.title":"Pregled","view.overview.description":"Cilj, stanje budžeta i najvažnije kontrole kompanije.",
      "view.operations.title":"Operacije","view.operations.description":"Aktivni agenti, browser misije i trag izvršenja.",
      "view.decisions.title":"Odluke","view.decisions.description":"Approvali, ljudsko preuzimanje i razgovor sa CEO-om.",
      "view.workspace.title":"Radni prostor","view.workspace.description":"Artefakti, integracije i sve što je kompanija proizvela.",
      "view.configuration.title":"Konfiguracija","view.configuration.description":"Modeli, komunikacija, platforme i sposobnosti agenata.",
      "config.general":"Osnovno","config.models":"Modeli","config.communication":"Email i approvali","config.integrations":"Integracije","config.skills":"Skillovi",
      "config.company.title":"Kompanija","config.company.body":"Aktivna firma, njen cilj, kapital, tržište i granice autonomije.","config.company.create":"Kreiraj novu firmu",
      "config.control.title":"Kontrola izvršenja","config.control.body":"CEO radi samostalno unutar definisanih pravila i budžeta.","config.control.open":"Otvori kontrolu misije",
      "config.models.title":"AI modeli","config.models.body":"Izaberi lokalni, hibridni ili cloud režim i podesi Ollama model.","config.models.open":"Podesi modele",
      "config.runtime.title":"Lokalni runtime","config.runtime.body":"Provjeri dostupnost Ollame bez mijenjanja aktivnog režima.","config.runtime.check":"Provjeri runtime",
      "config.email.title":"Email i approvali","config.email.body":"Podesi primaoce dnevnog briefa i potpisanih approval zahtjeva.","config.email.open":"Podesi email",
      "config.decisions.title":"Otvorene odluke","config.decisions.body":"Approval, razlog odbijanja i ljudsko preuzimanje ostaju objedinjeni.","config.decisions.open":"Idi na odluke",
      "config.platforms.title":"Platforme i servisi","config.platforms.body":"Shopify i buduće platforme koje CEO može izabrati umjesto vlastitog razvoja.","config.platforms.open":"Podesi platforme",
      "config.browser.title":"Browser runtime","config.browser.body":"Kontrolisani browser za istraživanje, login checkpoint i ljudsko preuzimanje.","config.browser.open":"Otvori browser cockpit",
      "panel.readiness":"Spremnost sistema","panel.browserMission":"Aktivna browser misija","panel.missionControl":"Kontrola misije","panel.companyState":"Stanje kompanije","panel.telemetry":"Operacije i telemetrija modela","panel.companyWorkspace":"Radni prostor kompanije","panel.capabilities":"Mogućnosti platformi","panel.skills":"Registar skillova agenata","panel.goal":"Cilj kompanije","panel.timeline":"Tok odluka i izvršenja","panel.takeover":"Ljudsko preuzimanje","panel.approvals":"Odluke za odobrenje","panel.chat":"Razgovor sa stakeholderom",
      "action.start":"▶ Pokreni / Nastavi","action.pause":"Ⅱ Pauziraj","action.stop":"■ Zaustavi","action.modelSettings":"Postavke modela","action.enable":"Omogući","action.missingAccess":"Nedostaje pristup","action.disable":"Onemogući","action.send":"Pošalji","action.close":"Zatvori","action.cancel":"Otkaži","action.save":"Sačuvaj postavke",
      "chat.placeholder":"Promijeni prioritet, postavi pitanje ili ospori odluku CEO-a...","chat.directive":"Direktiva — prekida trenutni plan","chat.question":"Pitanje — ne prekida plan",
      "runtime.available":"Ollama status: dostupna","runtime.unavailable":"Ollama status: nedostupna",
      "footer":"Lokalni kontrolni centar · trajno PostgreSQL stanje · deterministička kontrola dozvola"
    }
  };
  const labels = {en:"English",sr:"Srpski"};
  let language = localStorage.getItem(STORAGE_KEY) || (navigator.language.toLowerCase().startsWith("sr") ? "sr" : "en");
  if (!catalogs[language]) language = "en";
  const t = key => catalogs[language]?.[key] ?? catalogs.en[key] ?? key;
  const apply = (root=document) => {
    root.querySelectorAll("[data-i18n]").forEach(el => { el.textContent=t(el.dataset.i18n); });
    root.querySelectorAll("[data-i18n-placeholder]").forEach(el => { el.placeholder=t(el.dataset.i18nPlaceholder); });
    document.documentElement.lang=language==="sr"?"sr-Latn":language;
    const selector=document.querySelector("#languageSelect");
    if(selector){selector.innerHTML=Object.entries(labels).map(([code,label])=>`<option value="${code}">${label}</option>`).join("");selector.value=language;}
  };
  const setLanguage = code => {if(!catalogs[code])return false;language=code;localStorage.setItem(STORAGE_KEY,code);apply();window.dispatchEvent(new CustomEvent("languagechange",{detail:{language:code}}));return true;};
  const registerLocale = (code,label,messages) => {catalogs[code]={...(catalogs[code]||{}),...messages};labels[code]=label;apply();};
  window.I18N={apply,getLanguage:()=>language,registerLocale,setLanguage,t};
})();
