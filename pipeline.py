#!/usr/bin/env python3
"""
Pipeline de classificação e geração do dashboard de imóveis de Piracicaba.
"""
import json
import re
import sys
import hashlib
from datetime import date

BAIRRO_NIVEL = {
    "nova piracicaba": 1,
    "jardim europa": 1,
    "sao dimas": 2,
    "jardim elite": 2,
    "vila monteiro": 2,
    "higienopolis": 2,
    "vila rezende": 2,
    "alemaes": 2,
    "campestre": 3,
    "piracicamirim": 3,
    "alto": 3,
    "cidade alta": 3,
    "bairro alto": 3,
    "santa terezinha": 4,
    "santa teresinha": 4,
    "mario dedini": 4,
    "jardim itapua": 4,
    "bosques do lenheiro": 4,
    "parque residencial piracicaba": 4,
    "balbo": 4,
}

NIVEL_SEGURO_MAX = 3
NIVEL_NAO_RECOMENDADO = 4

TETO_VENDA = 1_200_000
TETO_ALUGUEL = 6_000

TIER_LABELS = {
    1: "Chácara em condomínio fechado (3+ qts, 1 suíte)",
    2: "Casa em condomínio fechado com edícula (3 qts)",
    20: "Casa em condomínio 3 qts — verificar edícula no anúncio",
    3: "Casa em condomínio fechado (4 qts)",
    4: "Casa em rua com edícula, bairro seguro",
    40: "Casa em rua, bairro seguro — verificar edícula no anúncio",
    5: "Casa em rua, terreno grande (400m²+)",
    6: "Apartamento grande (4+ qts, 250m²+)",
    99: "Fora dos critérios prioritários (outros)",
}

TIER_COLORS = {
    1: "#7c3aed", 2: "#2563eb", 20: "#3b82f6", 3: "#0891b2",
    4: "#059669", 40: "#10b981", 5: "#65a30d", 6: "#ca8a04", 99: "#6b7280",
}


def _norm(s):
    if not s:
        return ""
    s = s.lower().strip()
    s = (s.replace("á", "a").replace("ã", "a").replace("â", "a")
           .replace("é", "e").replace("ê", "e")
           .replace("í", "i")
           .replace("ó", "o").replace("õ", "o").replace("ô", "o")
           .replace("ú", "u").replace("ç", "c"))
    return s


def get_bairro_nivel(bairro):
    b = _norm(bairro)
    for nome, nivel in BAIRRO_NIVEL.items():
        if nome in b:
            return nivel
    return None


def is_bairro_seguro(bairro):
    nivel = get_bairro_nivel(bairro)
    return nivel is not None and nivel <= NIVEL_SEGURO_MAX


def is_bairro_nao_recomendado(bairro):
    return get_bairro_nivel(bairro) == NIVEL_NAO_RECOMENDADO


def make_id(item):
    key = item.get("link") or (item.get("titulo", "") + item.get("bairro", ""))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def classify_tier(item):
    tipo = _norm(item.get("tipo", ""))
    quartos = item.get("quartos") or 0
    suites = item.get("suites") or 0
    area = item.get("area_m2") or 0
    area_terreno = item.get("area_terreno_m2") or 0
    condo = bool(item.get("condominio_fechado"))
    edicula = bool(item.get("edicula"))
    bairro_seguro = is_bairro_seguro(item.get("bairro", ""))

    is_chacara = "chacara" in tipo or "sitio" in tipo or "fazenda" in tipo
    is_casa = "casa" in tipo or "sobrado" in tipo
    is_apto = "apartamento" in tipo or "apto" in tipo

    edicula_desconhecida = item.get("edicula") is None

    if is_chacara and condo and quartos >= 3 and suites >= 1:
        return 1
    if is_casa and condo and edicula and quartos >= 3:
        return 2
    if is_casa and condo and quartos == 3 and edicula_desconhecida:
        return 20
    if is_casa and condo and quartos >= 4:
        return 3
    if is_casa and not condo and edicula and bairro_seguro:
        return 4
    if is_casa and not condo and bairro_seguro and edicula_desconhecida:
        return 40
    if is_casa and not condo and area_terreno >= 400:
        return 5
    if is_apto and quartos >= 4 and area >= 250:
        return 6
    return 99


def within_budget(item):
    valor = item.get("valor") or 0
    if item.get("transacao") == "venda":
        return 0 < valor <= TETO_VENDA
    if item.get("transacao") == "aluguel":
        return 0 < valor <= TETO_ALUGUEL
    return False


def is_chacara_tipo(item):
    tipo = _norm(item.get("tipo", ""))
    return "chacara" in tipo or "sitio" in tipo or "fazenda" in tipo


def is_chacara_condominio(item):
    # Chácara em condomínio fechado é exceção ao teto de orçamento (pedido do
    # usuário em 13/08/2026: "chácaras de condomínio são ouro, buscar mesmo
    # além ou aquém do orçamento"). Vale tanto para acima do teto quanto para
    # valores muito baixos (pechincha).
    return is_chacara_tipo(item) and bool(item.get("condominio_fechado"))


# Tipos que NUNCA batem com nenhum dos 6 tiers prioritários — não fazem
# sentido nesta busca (pedido do usuário em 13/08/2026: parar de trazer
# "lixo" fora do tipo/metragem/valor procurados). Terreno/lote não tem
# quartos; salas/pontos comerciais e barracões não são residência; kitnet/
# studio/flat são unidades muito pequenas para uma família.
TIPOS_IRRELEVANTES = {
    "terreno", "lote", "lote terreno", "comercial", "sala comercial",
    "ponto comercial", "loja", "salao", "barracao", "predio comercial",
    "kitnet", "studio", "flat",
}


def is_tipo_relevante(item):
    tipo = _norm(item.get("tipo", ""))
    if any(t in tipo for t in TIPOS_IRRELEVANTES):
        return False
    is_apto = "apartamento" in tipo or "apto" in tipo
    if is_apto:
        # Apartamento só pode bater o tier 6 (4+ quartos, 250m²+). Qualquer
        # apartamento bem abaixo disso nunca vai virar tier 1-6 — é ruído.
        # Damos uma margem (150m²/3 qts) pra não descartar algo que a
        # extração tenha subestimado.
        area = item.get("area_m2") or 0
        quartos = item.get("quartos") or 0
        if area and area < 150 and quartos < 4:
            return False
    return True


def build_dataset(raw_listings, existing_dataset, today=None):
    today = today or date.today().isoformat()
    # Remove do histórico existente qualquer registro de tipo irrelevante que
    # tenha entrado em execuções anteriores (antes deste filtro existir).
    by_id = {
        item["id"]: item for item in existing_dataset if is_tipo_relevante(item)
    }
    seen_today = set()

    for raw in raw_listings:
        if not is_tipo_relevante(raw):
            continue
        chacara_condo_excecao = is_chacara_condominio(raw)
        if not chacara_condo_excecao and not within_budget(raw):
            continue
        if is_bairro_nao_recomendado(raw.get("bairro", "")):
            continue
        iid = make_id(raw)
        seen_today.add(iid)
        tier = classify_tier(raw)
        if iid in by_id:
            rec = by_id[iid]
            rec["last_seen"] = today
            rec["dias_na_lista"] = (
                date.fromisoformat(today) - date.fromisoformat(rec["first_seen"])
            ).days + 1
            for k in ("valor", "titulo", "foto"):
                if raw.get(k):
                    rec[k] = raw[k]
            rec["fora_orcamento"] = chacara_condo_excecao and not within_budget(raw)
        else:
            by_id[iid] = {
                **raw,
                "id": iid,
                "tier": tier,
                "tier_label": TIER_LABELS.get(tier, "Outros"),
                "bairro_seguro": is_bairro_seguro(raw.get("bairro", "")),
                "bairro_nivel": get_bairro_nivel(raw.get("bairro", "")),
                "first_seen": today,
                "last_seen": today,
                "dias_na_lista": 1,
                "excluded": False,
                "favorite": False,
                "fora_orcamento": chacara_condo_excecao and not within_budget(raw),
            }

    return list(by_id.values())


def render_dashboard(dataset, out_path, generated_at=None):
    generated_at = generated_at or date.today().isoformat()
    visible = [d for d in dataset if not d.get("excluded")]
    visible.sort(key=lambda d: (d["tier"], d.get("bairro_nivel") or 9, -d.get("valor", 0)))
    data_json = json.dumps(visible, ensure_ascii=False)

    html = HTML_TEMPLATE.replace("__DATA_JSON__", data_json).replace(
        "__GENERATED_AT__", generated_at
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Imóveis Piracicaba — Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{
    --bg:#0f1115; --card:#181b22; --card2:#1f232c; --text:#e6e8ec; --muted:#9aa3b2;
    --border:#2a2f3a; --accent:#f59e0b;
  }
  *{box-sizing:border-box;}
  body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
       background:var(--bg); color:var(--text);}
  header{padding:20px 24px;border-bottom:1px solid var(--border);
         display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;
         position:sticky;top:0;background:var(--bg);z-index:10;}
  header h1{font-size:18px;margin:0;}
  header .meta{color:var(--muted);font-size:13px;}
  .toolbar{display:flex;gap:8px;flex-wrap:wrap;padding:16px 24px;border-bottom:1px solid var(--border);
           position:sticky;top:64px;background:var(--bg);z-index:9;}
  select,input,button{background:var(--card2);color:var(--text);border:1px solid var(--border);
         border-radius:8px;padding:8px 10px;font-size:13px;}
  button{cursor:pointer;}
  button.primary{background:var(--accent);color:#111;border:none;font-weight:600;}
  .stats{display:flex;gap:10px;padding:12px 24px;flex-wrap:wrap;}
  .pill{background:var(--card2);border:1px solid var(--border);border-radius:20px;padding:6px 12px;font-size:12px;color:var(--muted);}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;padding:20px 24px;}
  .card{background:var(--card);border:1px solid var(--border);border-radius:14px;overflow:hidden;
        display:flex;flex-direction:column;transition:transform .1s;}
  .card:hover{transform:translateY(-2px);}
  .card.fav{border-color:var(--accent);}
  .thumb{width:100%;height:150px;object-fit:cover;background:#000;}
  .thumb.none{display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:12px;}
  .tier-badge{display:inline-block;font-size:11px;font-weight:700;padding:3px 8px;border-radius:6px;color:#fff;margin-bottom:6px;}
  .body{padding:12px 14px;display:flex;flex-direction:column;gap:6px;flex:1;}
  .title{font-size:14px;font-weight:600;line-height:1.3;}
  .row{font-size:12.5px;color:var(--muted);display:flex;justify-content:space-between;}
  .price{font-size:16px;font-weight:700;color:var(--text);margin-top:4px;}
  .tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;}
  .tag{font-size:11px;background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:2px 6px;color:var(--muted);}
  .tag.safe{color:#4ade80;border-color:#265d3d;}
  .days{font-size:11px;color:var(--muted);}
  .actions{display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--border);}
  .actions button{flex:1;font-size:12px;}
  .btn-fav.active{background:var(--accent);color:#111;border-color:var(--accent);}
  .btn-exc{color:#f87171;}
  a.link-btn{color:inherit;text-decoration:none;flex:1;display:flex;}
  a.link-btn button{width:100%;}
  .empty{padding:60px;text-align:center;color:var(--muted);}
  footer{padding:20px 24px;color:var(--muted);font-size:12px;text-align:center;}
</style>
</head>
<body>
<header>
  <div>
    <h1>🏠 Imóveis em Piracicaba</h1>
    <div class="meta">Atualizado em __GENERATED_AT__ · dados extraídos de QuintoAndar, ZAP, VivaReal e Imovelweb</div>
  </div>
  <div class="meta" id="sync-status">☁️ Sincronizando favoritos/exclusões...</div>
</header>

<div class="toolbar">
  <select id="f-tier"><option value="">Todas as prioridades</option></select>
  <select id="f-transacao">
    <option value="">Compra e aluguel</option>
    <option value="venda">Só compra</option>
    <option value="aluguel">Só aluguel</option>
  </select>
  <select id="f-tipo"><option value="">Todos os tipos</option></select>
  <select id="f-bairro"><option value="">Todos os bairros</option></select>
  <select id="f-dias">
    <option value="">Qualquer tempo na lista</option>
    <option value="1">Novo hoje</option>
    <option value="3">3+ dias</option>
    <option value="7">7+ dias</option>
    <option value="14">14+ dias</option>
  </select>
  <select id="f-fav">
    <option value="">Todos</option>
    <option value="fav">⭐ Só favoritos</option>
  </select>
  <input id="f-preco" type="number" placeholder="Preço máx (R$)">
  <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);padding:0 6px;">
    <input type="checkbox" id="f-hide99" checked> Esconder "fora dos critérios" (tier 99)
  </label>
  <button class="primary" id="btn-reset">Limpar filtros</button>
</div>

<div class="stats" id="stats"></div>
<div class="grid" id="grid"></div>
<footer>Robô de busca de imóveis · dados extraídos automaticamente, sempre confira o anúncio original antes de decidir.</footer>

<script type="module">
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getFirestore, collection, doc, setDoc, onSnapshot } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";

const firebaseConfig = {
  apiKey: "AIzaSyBP9_qsL5Udn8lrhWlPbNA8ATLvWF3YkE0",
  authDomain: "imoveis-piracicaba.firebaseapp.com",
  projectId: "imoveis-piracicaba",
  storageBucket: "imoveis-piracicaba.firebasestorage.app",
  messagingSenderId: "245806472867",
  appId: "1:245806472867:web:0796f0101c9a5de1cde916",
};
const fbApp = initializeApp(firebaseConfig);
const db = getFirestore(fbApp);
const STATUS_COLLECTION = "imoveis_status";

const DATA = __DATA_JSON__;
const TIER_LABELS = {1:"🥇 Chácara em condomínio",2:"🥈 Casa em condomínio c/ edícula",20:"🥈? Casa em condomínio — verificar edícula",3:"🥉 Casa em condomínio 4qts",4:"🏡 Casa em rua c/ edícula (bairro seguro)",40:"🏡? Casa em rua, bairro seguro — verificar edícula",5:"🏠 Casa em rua, terreno grande",6:"🏢 Apartamento grande",99:"📋 Outros"};
const TIER_COLORS = {1:"#7c3aed",2:"#2563eb",20:"#3b82f6",3:"#0891b2",4:"#059669",40:"#10b981",5:"#65a30d",6:"#ca8a04",99:"#6b7280"};

const LS_KEY = "piracicaba_overrides_cache_v1";
function loadCache(){ try{return JSON.parse(localStorage.getItem(LS_KEY))||{};}catch(e){return {};} }
function saveCache(o){ localStorage.setItem(LS_KEY, JSON.stringify(o)); }
let overrides = loadCache();
let firestoreReady = false;

onSnapshot(collection(db, STATUS_COLLECTION), (snap)=>{
  snap.docChanges().forEach(change=>{
    overrides[change.doc.id] = change.doc.data();
  });
  firestoreReady = true;
  saveCache(overrides);
  const statusEl = document.getElementById("sync-status");
  if(statusEl) statusEl.textContent = "☁️ Favoritos/exclusões sincronizados (funciona em qualquer dispositivo)";
  render();
}, (err)=>{
  console.error("Firestore sync falhou, usando apenas cache local:", err);
  const statusEl = document.getElementById("sync-status");
  if(statusEl) statusEl.textContent = "⚠️ Sem conexão com a sincronização — usando dados salvos neste navegador";
  render();
});

function getState(item){
  const o = overrides[item.id] || {};
  return {
    excluded: o.excluded ?? item.excluded ?? false,
    favorite: o.favorite ?? item.favorite ?? false,
  };
}
function setState(id, patch){
  overrides[id] = {...(overrides[id]||{}), ...patch};
  saveCache(overrides);
  render();
  setDoc(doc(db, STATUS_COLLECTION, id), patch, {merge:true}).catch(err=>{
    console.error("Não foi possível sincronizar com o Firestore:", err);
  });
}

function fmtMoney(v){ return "R$ " + Number(v).toLocaleString("pt-BR"); }

function populateSelect(id, values){
  const sel = document.getElementById(id);
  values.forEach(v=>{
    const opt = document.createElement("option");
    opt.value = v.value; opt.textContent = v.label;
    sel.appendChild(opt);
  });
}

function uniqueSorted(arr){ return [...new Set(arr.filter(Boolean))].sort(); }

populateSelect("f-tier", [1,2,20,3,4,40,5,6,99].map(t=>({value:t, label:TIER_LABELS[t]})));
populateSelect("f-tipo", uniqueSorted(DATA.map(d=>d.tipo)).map(t=>({value:t,label:t})));
populateSelect("f-bairro", uniqueSorted(DATA.map(d=>d.bairro)).map(b=>({value:b,label:b})));

function currentFilters(){
  return {
    tier: document.getElementById("f-tier").value,
    transacao: document.getElementById("f-transacao").value,
    tipo: document.getElementById("f-tipo").value,
    bairro: document.getElementById("f-bairro").value,
    dias: document.getElementById("f-dias").value,
    fav: document.getElementById("f-fav").value,
    preco: document.getElementById("f-preco").value,
    hide99: document.getElementById("f-hide99").checked,
  };
}

function render(){
  const f = currentFilters();
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  let shown = 0;

  const filtered = DATA.filter(item=>{
    const st = getState(item);
    if(st.excluded) return false;
    if(f.hide99 && item.tier === 99 && !f.tier) return false;
    if(f.tier && String(item.tier) !== f.tier) return false;
    if(f.transacao && item.transacao !== f.transacao) return false;
    if(f.tipo && item.tipo !== f.tipo) return false;
    if(f.bairro && item.bairro !== f.bairro) return false;
    if(f.dias && item.dias_na_lista < Number(f.dias)) return false;
    if(f.fav === "fav" && !st.favorite) return false;
    if(f.preco && item.valor > Number(f.preco)) return false;
    return true;
  });

  filtered.forEach(item=>{
    const st = getState(item);
    shown++;
    const card = document.createElement("div");
    card.className = "card" + (st.favorite ? " fav" : "");
    const thumb = item.foto
      ? `<img class="thumb" src="${item.foto}" onerror="this.style.display='none'">`
      : `<div class="thumb none">sem foto</div>`;
    card.innerHTML = `
      ${thumb}
      <div class="body">
        <span class="tier-badge" style="background:${TIER_COLORS[item.tier]}">${TIER_LABELS[item.tier]}</span>
        <div class="title">${item.titulo || "(sem título)"}</div>
        <div class="row"><span>${item.bairro || "-"}</span><span>${item.tipo || "-"}</span></div>
        <div class="row"><span>${item.quartos ?? "-"} qts · ${item.suites ?? 0} suítes</span><span>${item.area_m2 ?? "-"} m²</span></div>
        <div class="price">${fmtMoney(item.valor)}${item.transacao === "aluguel" ? "/mês" : ""}</div>
        <div class="tags">
          <span class="tag">${item.transacao === "aluguel" ? "Aluguel" : "Compra"}</span>
          <span class="tag">${item.fonte}</span>
          ${item.bairro_nivel ? `<span class="tag safe">bairro nível ${item.bairro_nivel}</span>` : ''}
          ${item.condominio_fechado ? '<span class="tag">condomínio fechado</span>' : ''}
          ${item.edicula ? '<span class="tag">edícula</span>' : ''}
          ${item.fora_orcamento ? '<span class="tag" style="color:#fbbf24;border-color:#78350f;">⚠️ fora do orçamento</span>' : ''}
        </div>
        <div class="days">Na lista há ${item.dias_na_lista} dia(s) · desde ${item.first_seen} · visto por último em ${item.last_seen}</div>
      </div>
      <div class="actions">
        <a class="link-btn" href="${item.link}" target="_blank"><button>Ver anúncio ↗</button></a>
        <button class="btn-fav ${st.favorite ? 'active' : ''}" data-id="${item.id}">⭐</button>
        <button class="btn-exc" data-id="${item.id}">🗑️</button>
      </div>
    `;
    card.querySelector(".btn-fav").onclick = ()=> setState(item.id, {favorite: !st.favorite});
    card.querySelector(".btn-exc").onclick = ()=>{
      if(confirm("Excluir este imóvel? Ele não vai aparecer novamente.")) setState(item.id, {excluded: true});
    };
    grid.appendChild(card);
  });

  if(shown === 0){
    grid.innerHTML = '<div class="empty">Nenhum imóvel encontrado com esses filtros.</div>';
  }

  const total = DATA.filter(i=>!getState(i).excluded).length;
  const favCount = DATA.filter(i=>getState(i).favorite).length;
  const novos = DATA.filter(i=>i.dias_na_lista===1 && !getState(i).excluded).length;
  document.getElementById("stats").innerHTML = `
    <span class="pill">${shown} exibidos / ${total} ativos</span>
    <span class="pill">⭐ ${favCount} favoritos</span>
    <span class="pill">🆕 ${novos} novos hoje</span>
  `;
}

["f-tier","f-transacao","f-tipo","f-bairro","f-dias","f-fav","f-hide99"].forEach(id=>{
  document.getElementById(id).addEventListener("change", render);
});
document.getElementById("f-preco").addEventListener("input", render);
document.getElementById("btn-reset").onclick = ()=>{
  ["f-tier","f-transacao","f-tipo","f-bairro","f-dias","f-fav"].forEach(id=>document.getElementById(id).value="");
  document.getElementById("f-preco").value="";
  document.getElementById("f-hide99").checked = true;
  render();
};

render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raw_path, dataset_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(raw_path, encoding="utf-8") as f:
        raw_listings = json.load(f)
    try:
        with open(dataset_path, encoding="utf-8") as f:
            existing = json.load(f)
    except FileNotFoundError:
        existing = []
    dataset = build_dataset(raw_listings, existing)
    with open(dataset_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    render_dashboard(dataset, out_path)
    print(f"OK: {len(dataset)} imóveis no dataset, dashboard em {out_path}")
