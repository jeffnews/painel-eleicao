#!/usr/bin/env python3
"""
Projecao por municipio para o painel de apuracao (2o turno, Presidente).

O que faz, a cada execucao:
 1. Le a lista de municipios do TSE (uma vez) e o resultado do 1o turno de cada um (uma vez, em lotes).
 2. Atualiza o resultado atual do 2o turno SO dos municipios que ainda nao terminaram de apurar,
    em lotes, com limite de pedidos por segundo e tempo maximo por execucao.
 3. Compara cada municipio com ele mesmo no 1o turno, aplica a variacao aos que faltam
    (agrupando municipios parecidos) e escreve tse/projecao.json com faixa de erro.

Usa somente a biblioteca padrao do Python. Pastas e limites podem ser mudados por variaveis de ambiente.
"""
import gzip
import json
import os
import random
import sys
import threading
import time
import zlib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# ---------------- configuracao ----------------
BASE = os.environ.get("TSE_BASE", "https://resultados.tse.jus.br/oficial/ele2026")
DIR = os.environ.get("TSE_DIR", "tse")
T1 = ("6257", "006257")          # 1o turno: (codigo da eleicao, codigo com 6 digitos)
T2 = ("6258", "006258")          # 2o turno
CARGO = "c0001"                  # 1 = Presidente
CAND = ("13", "22")              # numeros dos finalistas, na ordem do painel

ORCAMENTO_S = float(os.environ.get("ORCAMENTO_S", "70"))     # tempo maximo baixando, por execucao
MAX_PEDIDOS = int(os.environ.get("MAX_PEDIDOS", "1200"))     # pedidos por execucao
REQ_POR_S = float(os.environ.get("REQ_POR_S", "15"))         # limite global de pedidos por segundo
CONCORRENCIA = int(os.environ.get("CONCORRENCIA", "8"))      # pedidos simultaneos
LOTE = 120                                                   # tamanho do lote entre checagens de tempo

REPLICAS = int(os.environ.get("REPLICAS", "200"))            # reamostragens para a faixa de erro
K_ESTADO = 40000.0                                           # peso do padrao nacional (em votos-base)
K_NAC_GRUPO = 150000.0                                       # grupo nacional -> padrao do mesmo tamanho
K_NAC_TAMANHO = 300000.0                                     # padrao por tamanho -> padrao geral
NTAM = 3                                                     # classes de tamanho (por peso de votos)
MIN_APURADO = 0.25                                           # abaixo disso: "cedo demais" (Brasil)
MIN_APURADO_UF = 0.20                                        # idem, por estado
PISO_PP_BR = 0.3                                             # incerteza minima (pontos percentuais)
PISO_PP_UF = 0.8
RISCO_BR = 1.2                                               # incerteza extra = RISCO * (1 - apurado)
RISCO_UF = 2.0
BASE_MINIMA = 0.90                                           # fracao de municipios com 1o turno para projetar
FAIXAS = (0.35, 0.45, 0.55, 0.65)                            # cortes da % de A no 1o turno (5 grupos)

_t0 = time.time()


def log(*a):
    print("[projecao]", *a, flush=True)


# ---------------- arquivos ----------------
def caminho(nome):
    return os.path.join(DIR, nome)


def ler_json(nome, padrao):
    try:
        with open(caminho(nome), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return padrao


def gravar_json(nome, obj):
    os.makedirs(DIR, exist_ok=True)
    tmp = caminho(nome) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, caminho(nome))


# ---------------- rede ----------------
class Limite:
    def __init__(self, por_s):
        self.intervalo = 1.0 / max(por_s, 0.1)
        self.prox = time.time()
        self.lock = threading.Lock()

    def esperar(self):
        with self.lock:
            agora = time.time()
            alvo = max(self.prox, agora)
            self.prox = alvo + self.intervalo
        if alvo > agora:
            time.sleep(alvo - agora)


LIMITE = Limite(REQ_POR_S)
CONTA = {"ok": 0, "nao": 0, "erro": 0}
CONTA_LOCK = threading.Lock()


def baixar(url):
    """Retorna ('ok', json) | ('nao', None) quando o arquivo nao existe | ('erro', None)."""
    ultimo = "erro"
    for tentativa in range(2):
        LIMITE.esperar()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "painel-eleicao (GitHub Actions)", "Accept-Encoding": "gzip"}
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                enc = ""
                try:
                    enc = r.headers.get("Content-Encoding") or ""
                except Exception:
                    pass
                if enc == "gzip":
                    raw = gzip.decompress(raw)
                dados = json.loads(raw.decode("utf-8"))
            with CONTA_LOCK:
                CONTA["ok"] += 1
            return "ok", dados
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                with CONTA_LOCK:
                    CONTA["nao"] += 1
                return "nao", None
            ultimo = "erro"
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), FileNotFoundError):
                with CONTA_LOCK:
                    CONTA["nao"] += 1
                return "nao", None
            ultimo = "erro"
        except FileNotFoundError:
            with CONTA_LOCK:
                CONTA["nao"] += 1
            return "nao", None
        except Exception:
            ultimo = "erro"
        time.sleep(0.4)
    with CONTA_LOCK:
        CONTA["erro"] += 1
    return ultimo, None


def num(v):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return 0.0


def ler_arquivo(j):
    cands = {}
    carg = (j.get("carg") or [{}])[0]
    for ag in carg.get("agr", []):
        for p in ag.get("par", []):
            for c in p.get("cand", []):
                cands[str(c.get("n"))] = int(num(c.get("vap")))
    s = j.get("s") or {}
    v = j.get("v") or {}
    return {
        "a": cands.get(CAND[0], 0),
        "b": cands.get(CAND[1], 0),
        "val": int(num(v.get("vv")) or num(v.get("vvc")) or sum(cands.values())),
        "ts": int(num(s.get("ts"))),
        "st": int(num(s.get("st"))),
    }


def url_mun(turno, uf, cd):
    return "%s/%s/dados/%s/%s%s-%s-e%s-u.json" % (BASE, turno[0], uf, uf, cd, CARGO, turno[1])


def buscar_em_lotes(alvos, funcao, deadline):
    """alvos: lista de (uf, cd). funcao(uf, cd) -> resultado. Respeita tempo e concorrencia."""
    saida = []
    with ThreadPoolExecutor(max_workers=CONCORRENCIA) as ex:
        for i in range(0, len(alvos), LOTE):
            if time.time() > deadline:
                break
            lote = alvos[i : i + LOTE]
            saida.extend(zip(lote, ex.map(lambda t: funcao(*t), lote)))
    return saida


# ---------------- etapas de download ----------------
def carregar_lista():
    lista = ler_json("mun-lista.json", None)
    if lista:
        return lista
    st, j = baixar("%s/%s/config/mun-e%s-cm.json" % (BASE, T2[0], T2[1]))
    if st != "ok" or not j:
        log("nao foi possivel ler a lista de municipios (%s)" % st)
        return None
    lista = {}
    for ab in j.get("abr", []):
        uf = str(ab.get("cd", "")).lower()
        lista[uf] = [str(m.get("cd")) for m in ab.get("mu", [])]
    gravar_json("mun-lista.json", lista)
    log("lista de municipios: %d estados, %d municipios" % (len(lista), sum(len(v) for v in lista.values())))
    return lista


def completar_t1(lista, t1, deadline, limite_pedidos):
    faltam = [(uf, cd) for uf, cds in lista.items() for cd in cds if cd not in t1.get(uf, {})]
    if not faltam or limite_pedidos <= 0:
        return 0, len(faltam)

    def f(uf, cd):
        return baixar(url_mun(T1, uf, cd))

    feitos = 0
    for (uf, cd), (st, j) in buscar_em_lotes(faltam[:limite_pedidos], f, deadline):
        if st == "ok" and j:
            d = ler_arquivo(j)
            t1.setdefault(uf, {})[cd] = [d["a"], d["b"], d["val"], d["ts"]]
            feitos += 1
        elif st == "nao":
            t1.setdefault(uf, {})[cd] = None
            feitos += 1
    gravar_json("mun-t1.json", t1)
    return feitos, len(faltam) - feitos


def atualizar_t2(lista, t2, deadline, limite_pedidos):
    """Prioridade: municipios em andamento (todos, a cada rodada); depois uma fatia rotativa
    dos que ainda nao comecaram. Concluidos nunca sao buscados de novo."""
    rodada = int(time.time() // 60)
    andamento, novos = [], []
    for uf, cds in lista.items():
        for cd in cds:
            r = t2.get(uf, {}).get(cd)
            if r is not None:
                a, b, ts, st = r
                if ts > 0 and st >= ts:
                    continue  # concluido
                if st > 0:
                    andamento.append((uf, cd))
                    continue
            novos.append((zlib.crc32(("%s%s%d" % (uf, cd, rodada)).encode()), uf, cd))
    novos.sort()
    ordem = andamento + [(uf, cd) for _, uf, cd in novos]
    ordem = ordem[:limite_pedidos]
    if not ordem:
        return 0, len(andamento), len(novos)

    def f(uf, cd):
        return baixar(url_mun(T2, uf, cd))

    feitos = 0
    for (uf, cd), (st, j) in buscar_em_lotes(ordem, f, deadline):
        if st == "ok" and j:
            d = ler_arquivo(j)
            t2.setdefault(uf, {})[cd] = [d["a"], d["b"], d["ts"], d["st"]]
            feitos += 1
        elif st == "nao":
            ant = t2.get(uf, {}).get(cd)
            t2.setdefault(uf, {})[cd] = [0, 0, ant[2] if ant else 0, 0]
    gravar_json("mun-t2.json", t2)
    return feitos, len(andamento), len(novos)


# ---------------- projecao ----------------
def cortes_tamanho(lista, t1):
    """Cortes de tamanho por tercis de peso de votos do 1o turno (fixos, pois o 1o turno nao muda)."""
    tam = []
    for uf, cds in lista.items():
        for cd in cds:
            b = (t1.get(uf) or {}).get(cd)
            if b and b[0] + b[1] > 0:
                tam.append(b[0] + b[1])
    tam.sort()
    total = float(sum(tam)) or 1.0
    cortes, acc, alvo = [], 0.0, 1
    for v in tam:
        acc += v
        while alvo < NTAM and acc >= total * alvo / NTAM:
            cortes.append(v)
            alvo += 1
    while len(cortes) < NTAM - 1:
        cortes.append(float("inf"))
    return cortes


def grupo_de(a1, b1, cortes):
    p = a1 / float(a1 + b1)
    g = 0
    for corte in FAIXAS:
        if p >= corte:
            g += 1
    z = 0
    for c in cortes:
        if (a1 + b1) > c:
            z += 1
    return (g, z)


def montar_unidades(lista, t1, t2):
    cortes = cortes_tamanho(lista, t1)
    unidades = []
    com_base = 0
    total = 0
    for uf, cds in lista.items():
        for cd in cds:
            total += 1
            b1 = (t1.get(uf) or {}).get(cd)
            if not b1 or b1[0] + b1[1] <= 0:
                continue
            com_base += 1
            a1, bb1, _val1, ts1 = b1
            r = (t2.get(uf) or {}).get(cd)
            if r:
                a2, b2, ts2, st2 = r[:4]
                f = (st2 / float(ts2)) if ts2 > 0 else 0.0
                f = max(0.0, min(1.0, f))
            else:
                a2 = b2 = 0
                f = 0.0
            unidades.append({"uf": uf, "g": grupo_de(a1, bb1, cortes), "a1": a1, "b1": bb1, "f": f,
                             "a2": a2 if f > 0 else 0, "b2": b2 if f > 0 else 0})
    return unidades, com_base, total


def razoes(somas):
    """somas: {(uf,(g,z)): [ca, ba, cb, bb]} so de unidades de referencia. Retorna funcao (uf,gz)->(Ra,Rb).
    Hierarquia de pesos: estado x perfil -> perfil nacional -> tamanho nacional -> geral."""
    nac, tam = {}, {}
    tot = [0.0, 0.0, 0.0, 0.0]
    for (uf, gz), sm in somas.items():
        n = nac.setdefault(gz, [0.0, 0.0, 0.0, 0.0])
        t = tam.setdefault(gz[1], [0.0, 0.0, 0.0, 0.0])
        for i in range(4):
            n[i] += sm[i]
            t[i] += sm[i]
            tot[i] += sm[i]
    ra_all = tot[0] / tot[1] if tot[1] > 0 else 1.0
    rb_all = tot[2] / tot[3] if tot[3] > 0 else 1.0
    r_tam = {}
    for z, t in tam.items():
        r_tam[z] = ((t[0] + K_NAC_TAMANHO * ra_all) / (t[1] + K_NAC_TAMANHO),
                    (t[2] + K_NAC_TAMANHO * rb_all) / (t[3] + K_NAC_TAMANHO))
    r_nac = {}
    for gz, n in nac.items():
        b0 = r_tam.get(gz[1], (ra_all, rb_all))
        r_nac[gz] = ((n[0] + K_NAC_GRUPO * b0[0]) / (n[1] + K_NAC_GRUPO),
                     (n[2] + K_NAC_GRUPO * b0[1]) / (n[3] + K_NAC_GRUPO))

    def obter(uf, gz):
        base = r_nac.get(gz) or r_tam.get(gz[1]) or (ra_all, rb_all)
        sm = somas.get((uf, gz))
        if not sm:
            return base
        return ((sm[0] + K_ESTADO * base[0]) / (sm[1] + K_ESTADO),
                (sm[2] + K_ESTADO * base[1]) / (sm[3] + K_ESTADO))

    return obter


def agrupar(unidades):
    grupos = {}
    for u in unidades:
        d = grupos.setdefault((u["uf"], u["g"]), {"refs": [], "ca": 0.0, "cb": 0.0, "ra": 0.0, "rb": 0.0})
        f = u["f"]
        d["ca"] += u["a2"]
        d["cb"] += u["b2"]
        d["ra"] += (1.0 - f) * u["a1"]
        d["rb"] += (1.0 - f) * u["b1"]
        if f > 0:
            d["refs"].append((u["a2"], f * u["a1"], u["b2"], f * u["b1"]))
    return grupos


def projetar(grupos, amostras=None):
    """Retorna {uf: [A, B]}. amostras: {(uf,g): lista de refs reamostradas} (ou None = referencias reais)."""
    somas = {}
    for k, d in grupos.items():
        refs = d["refs"] if amostras is None else amostras.get(k, [])
        if refs:
            s = [0.0, 0.0, 0.0, 0.0]
            for x in refs:
                s[0] += x[0]
                s[1] += x[1]
                s[2] += x[2]
                s[3] += x[3]
            somas[k] = s
    obter = razoes(somas)
    res = {}
    for (uf, g), d in grupos.items():
        ra, rb = obter(uf, g)
        r = res.setdefault(uf, [0.0, 0.0])
        r[0] += d["ca"] + ra * d["ra"]
        r[1] += d["cb"] + rb * d["rb"]
    return res


def pct(v):
    return round(v, 2)


def margem_pp(a, b):
    return (a - b) / (a + b) * 100.0 if (a + b) > 0 else 0.0


def veredito(margem, lo, hi, apurado, minimo):
    if apurado >= 0.999:
        return "fim"
    if apurado < minimo:
        return "cedo"
    if lo > 0:
        return "a"
    if hi < 0:
        return "b"
    return "indef"


def calcular(lista, t1, t2):
    unidades, com_base, total = montar_unidades(lista, t1, t2)
    cobertura = {"municipios": total, "com_base": com_base}
    out = {"gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"), "versao": 1,
           "cand": list(CAND), "cobertura": cobertura}
    if total == 0 or com_base < BASE_MINIMA * total:
        out["brasil"] = {"veredito": "base"}
        out["estados"] = {}
        return out

    grupos = agrupar(unidades)
    real = projetar(grupos)

    # progresso (por votos-base)
    base_uf, cont_uf = {}, {}
    concl = andam = 0
    for u in unidades:
        v = u["a1"] + u["b1"]
        base_uf[u["uf"]] = base_uf.get(u["uf"], 0.0) + v
        cont_uf[u["uf"]] = cont_uf.get(u["uf"], 0.0) + u["f"] * v
        if u["f"] >= 1.0:
            concl += 1
        elif u["f"] > 0:
            andam += 1
    base_br = sum(base_uf.values())
    apur_br = sum(cont_uf.values()) / base_br if base_br else 0.0
    cobertura.update({"concluidos": concl, "em_andamento": andam, "apurado_base_pct": pct(apur_br * 100)})

    # reamostragem (bootstrap) dentro de cada grupo (estado x perfil)
    rnd = random.Random(20261025)
    margens_br, margens_uf, pa_br = [], {}, []
    chaves = [k for k, d in grupos.items() if d["refs"]]
    for _ in range(REPLICAS):
        am = {}
        for k in chaves:
            refs = grupos[k]["refs"]
            am[k] = rnd.choices(refs, k=len(refs))
        pr = projetar(grupos, am)
        A = sum(v[0] for v in pr.values())
        B = sum(v[1] for v in pr.values())
        margens_br.append(margem_pp(A, B))
        pa_br.append(A / (A + B) * 100.0 if (A + B) > 0 else 50.0)
        for uf, v in pr.items():
            margens_uf.setdefault(uf, []).append(margem_pp(v[0], v[1]))

    def faixa(lista_m, piso):
        s = sorted(lista_m)
        lo = s[int(0.05 * (len(s) - 1))]
        hi = s[int(0.95 * (len(s) - 1))]
        return lo - piso, hi + piso

    A = sum(v[0] for v in real.values())
    B = sum(v[1] for v in real.values())
    m = margem_pp(A, B)
    lo, hi = faixa(margens_br, PISO_PP_BR + RISCO_BR * (1.0 - apur_br)) if margens_br else (m, m)
    if apur_br >= 0.999:
        lo = hi = m
    out["brasil"] = {
        "a": int(round(A)), "b": int(round(B)),
        "pa": pct(A / (A + B) * 100.0) if (A + B) > 0 else 0, "pb": pct(B / (A + B) * 100.0) if (A + B) > 0 else 0,
        "margem_pp": pct(m), "lo_pp": pct(lo), "hi_pp": pct(hi),
        "veredito": veredito(m, lo, hi, apur_br, MIN_APURADO),
    }
    estados = {}
    for uf, v in real.items():
        mu = margem_pp(v[0], v[1])
        apur = cont_uf.get(uf, 0.0) / base_uf[uf] if base_uf.get(uf) else 0.0
        l2, h2 = faixa(margens_uf.get(uf, [mu]), PISO_PP_UF + RISCO_UF * (1.0 - apur))
        if apur >= 0.999:
            l2 = h2 = mu
        estados[uf] = {
            "a": int(round(v[0])), "b": int(round(v[1])),
            "pa": pct(v[0] / (v[0] + v[1]) * 100.0) if (v[0] + v[1]) > 0 else 0,
            "pb": pct(v[1] / (v[0] + v[1]) * 100.0) if (v[0] + v[1]) > 0 else 0,
            "margem_pp": pct(mu), "lo_pp": pct(l2), "hi_pp": pct(h2),
            "apurado_base_pct": pct(apur * 100), "veredito": veredito(mu, l2, h2, apur, MIN_APURADO_UF),
        }
    out["estados"] = estados
    return out


# ---------------- principal ----------------
def main():
    os.makedirs(DIR, exist_ok=True)
    deadline = _t0 + ORCAMENTO_S
    lista = carregar_lista()
    if not lista:
        return 1
    t1 = ler_json("mun-t1.json", {})
    t2 = ler_json("mun-t2.json", {})

    f1, falta1 = completar_t1(lista, t1, deadline, MAX_PEDIDOS)
    log("1o turno: %d baixados agora, %d ainda faltam" % (f1, falta1))
    restante = max(0, MAX_PEDIDOS - f1)
    f2, andam, novos = atualizar_t2(lista, t2, deadline, restante if falta1 == 0 else max(restante, MAX_PEDIDOS // 4))
    log("2o turno: %d atualizados (em andamento: %d; ainda sem apuracao: %d)" % (f2, andam, novos))

    saida = calcular(lista, t1, t2)
    gravar_json("projecao.json", saida)
    b = saida.get("brasil", {})
    log("pedidos ok=%d inexistentes=%d erros=%d | %.0fs" % (CONTA["ok"], CONTA["nao"], CONTA["erro"], time.time() - _t0))
    log("resultado: %s" % json.dumps(b, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
