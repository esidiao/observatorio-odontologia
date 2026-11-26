"""
etl/ingestao.py
Agrega o recorte do Censo em indicadores por UF e por município.

Uso:
    python etl/ingestao.py --ano 2024

Entrada:  etl/dados/curso_odontologia_<ano>.csv  (de extrair_censo.py)
          etl/dados/ies_<ano>.csv
Saída:    data/bruto.json

A EaD TEM DUAS CAMADAS, E SOMAR INGENUAMENTE ZERA AS VAGAS
-----------------------------------------------------------
`TP_DIMENSAO` separa as três naturezas de linha, e as três se comportam de
maneira diferente:

  1  presencial   — vagas E matrículas na UF do curso
  2  polo EaD     — matrículas na UF do polo, ZERO vagas
  3  sede EaD     — vagas da mantenedora, ZERO matrículas, SG_UF em branco

Em Odontologia a camada de EaD está VAZIA: as 650 linhas do recorte são de
dimensão 1. Isso torna o tratamento mais necessário, não menos — porque aqui
nenhum erro de soma se denuncia sozinho. Num curso com EaD grande, somar a
camada errada derruba um número visível; aqui o resultado errado e o resultado
certo têm a mesma aparência: zero. É por isso que a ausência é conferida no
recorte bruto, por TP_DIMENSAO, e não pelo total (ver tests/test_validacao.py).

E a distinção entre zero e nada continua valendo: `vagas_ead = 0` é medida — as
27 UFs estão nesse caso —, enquanto `None` seria ausência de apuração.
Confundir as duas transformaria o achado central deste observatório numa
lacuna.

As vagas de sede são atribuídas à UF-SEDE DA MANTENEDORA, via
`CO_IES` -> `SG_UF_IES` no cadastro de IES. Sem isso não há a quem atribuí-las:
a linha de sede vem com `SG_UF` em branco.

SEMIPRESENCIAL NÃO É EaD (CENSO 2025 EM DIANTE)
-----------------------------------------------
Até 2024 `TP_MODALIDADE_ENSINO` só tinha 1 (presencial) e 2 (a distância). Em
2025 ele passou a ser o formato de oferta do Decreto 12.456/2025, com a
categoria nova 3 = semipresencial. A camada de sede (dimensão 3, na tradução de
`censo2025_adaptador.py`) junta EaD e semipresencial; aqui elas se separam pela
modalidade: `vagas_semipresencial` e `n_cursos_semipresencial` são campos
próprios, `vagas_total` soma as três e `pct_ead` continua sendo só EaD.
Modalidade fora de {1, 2, 3}, ou incoerente com a camada, ABORTA — cair calada
num dos ramos reclassificaria vagas sem ninguém ver. Em Odontologia 2025 as
676 ofertas são presenciais: a semipresencial vale zero medido, como a EaD.

PERFIL SÓ ONDE O RECORTE FOI DIVULGADO
--------------------------------------
No Censo 2025 o INEP suprime ("-") recortes com 3 ou menos alunos por local de
oferta. Suprimido não é zero: cada percentual de perfil usa como denominador
apenas as matrículas das linhas em que aquele recorte veio preenchido. Em
Odontologia 2025 nenhuma linha de curso tem recorte suprimido (medido), então o
denominador é o total — mas a regra não depende disso.

POR QUE A CONCENTRAÇÃO PRECISA INCLUIR A EaD
---------------------------------------------
`HHI_mantenedora`, `HHI`, `CR2` e `CR10` são calculados sobre a capacidade
TOTAL (presencial + EaD atribuída). Calculá-los só sobre o presencial esconde
exatamente os grandes grupos que concentram o mercado: no observatório de
Farmácia, SP dava HHI de mantenedora 0,06 assim, contra 0,36 do valor real.
"""
import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from rede import ler_json

REPO = Path(__file__).parent.parent
DADOS = REPO / "etl" / "dados"
DATA = REPO / "data"

API_MUNICIPIOS = ("https://servicodados.ibge.gov.br/api/v1/localidades/"
                  "municipios?view=nivelado")
API_POPULACAO = ("https://servicodados.ibge.gov.br/api/v3/agregados/6579/"
                 "periodos/-1/variaveis/9324?localidades=N3[all]")

DIM_PRESENCIAL, DIM_POLO, DIM_SEDE = "1", "2", "3"

REGIOES = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte", "RO": "Norte",
    "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste",
    "PB": "Nordeste", "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste",
    "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste",
    "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}

csv.field_size_limit(1 << 24)


MOD_PRESENCIAL, MOD_EAD, MOD_SEMIPRESENCIAL = "1", "2", "3"
MODALIDADES_POR_DIMENSAO = {
    DIM_PRESENCIAL: {MOD_PRESENCIAL},
    DIM_POLO: {MOD_EAD, MOD_SEMIPRESENCIAL},
    DIM_SEDE: {MOD_EAD, MOD_SEMIPRESENCIAL},
}

# Recortes de perfil: campo interno -> coluna do Censo.
RECORTES_PERFIL = (
    ("_mat_fem", "QT_MAT_FEM"), ("_mat_noturno", "QT_MAT_NOTURNO"),
    ("_mat_preta", "QT_MAT_PRETA"), ("_mat_parda", "QT_MAT_PARDA"),
    ("_mat_indigena", "QT_MAT_INDIGENA"), ("_mat_cornd", "QT_MAT_CORND"),
    ("_mat_financ", "QT_MAT_FINANC"), ("_mat_apoio", "QT_MAT_APOIO_SOCIAL"),
    ("_mat_reserva", "QT_MAT_RESERVA_VAGA"),
)


def inteiro(valor):
    """
    Contagem do Censo como inteiro; vazio vale 0.

    Qualquer outra coisa ABORTA. A versão anterior devolvia 0 para tudo que
    não fosse dígito, e foi assim que "1234.0" (float escrito por pandas) ou
    "9.731" (milhar dentro da célula, Censo 2025) virariam zero sem erro.
    """
    texto = (valor or "").strip()
    if not texto:
        return 0
    if texto.lstrip("-").isdigit():
        return int(texto)
    raise SystemExit(f"[INGESTAO] contagem em formato inesperado: {valor!r}. "
                     "Corrija a origem (adaptador?) em vez de ler como zero.")


def divulgado(valor):
    """O recorte veio preenchido? Vazio ou "-" é supressão, não zero."""
    return (valor or "").strip() not in ("", "-")


def pct(parte, todo, casas=1):
    """Percentual, ou None quando o denominador não existe.

    Zero no denominador devolve None, não zero: "nenhuma matrícula" não
    autoriza afirmar "0% de mulheres".
    """
    if not todo:
        return None
    return round(100 * parte / todo, casas)


# --------------------------------------------------------------------------- #
# Contexto territorial — do IBGE, nunca de tabela fixa no código
# --------------------------------------------------------------------------- #

def contexto_ibge():
    """
    Devolve (municipios_por_uf, capital_por_uf, populacao_por_uf, nomes).

    A contagem de municípios vem da API, não de um dicionário no código. O
    "Brasil tem 5.570" está desatualizado desde 01/01/2025, quando Boa Esperança
    do Norte (MT) foi instalada: são 5.571, com MT em 142. Uma tabela fixa
    envelhece em silêncio e leva junto todo indicador que usa municípios como
    denominador — que aqui são o ICT, o ICAF e o ICRE.
    """
    municipios = ler_json(API_MUNICIPIOS)
    por_uf = defaultdict(int)
    nome_por_codigo = {}
    for m in municipios:
        uf = m["UF-sigla"]
        por_uf[uf] += 1
        nome_por_codigo[str(m["municipio-id"])] = (m["municipio-nome"], uf)

    total = sum(por_uf.values())
    print(f"[IBGE] {total} municípios em {len(por_uf)} UFs (MT={por_uf['MT']})")
    if total != 5571:
        print(f"[IBGE] atenção: total {total} difere de 5571. "
              "Se o IBGE instalou ou extinguiu município, é notícia real; "
              "confira antes de publicar.")

    populacao = {}
    bloco = ler_json(API_POPULACAO)
    codigo_uf = {str(i): s for i, s in zip(
        [11, 12, 13, 14, 15, 16, 17, 21, 22, 23, 24, 25, 26, 27, 28, 29,
         31, 32, 33, 35, 41, 42, 43, 50, 51, 52, 53],
        ["RO", "AC", "AM", "RR", "PA", "AP", "TO", "MA", "PI", "CE", "RN",
         "PB", "PE", "AL", "SE", "BA", "MG", "ES", "RJ", "SP", "PR", "SC",
         "RS", "MS", "MT", "GO", "DF"])}
    ano_pop = None
    for serie in bloco[0]["resultados"][0]["series"]:
        uf = codigo_uf.get(serie["localidade"]["id"])
        for ano, valor in serie["serie"].items():
            ano_pop = ano
            if uf and str(valor).isdigit():
                populacao[uf] = int(valor)
    print(f"[IBGE] população de {len(populacao)} UFs (estimativa {ano_pop})")

    return dict(por_uf), nome_por_codigo, populacao, ano_pop


# --------------------------------------------------------------------------- #
# Leitura do recorte
# --------------------------------------------------------------------------- #

def carregar_ies(ano):
    """CO_IES -> {uf_sede, mantenedora, nome}."""
    caminho = DADOS / f"ies_{ano}.csv"
    if not caminho.exists():
        raise SystemExit(f"[ERRO] {caminho} ausente. Rode etl/extrair_censo.py antes.")
    ies = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            codigo = (linha.get("CO_IES") or "").strip()
            if not codigo:
                continue
            mantenedora = ((linha.get("CO_MANTENEDORA") or "").strip()
                           or (linha.get("NO_MANTENEDORA") or "").strip()
                           or f"IES:{codigo}")
            ies[codigo] = {
                "uf_sede": (linha.get("SG_UF_IES") or "").strip().upper() or None,
                "mantenedora": mantenedora,
                "nome_mantenedora": (linha.get("NO_MANTENEDORA") or "").strip() or None,
                "nome": (linha.get("NO_IES") or "").strip() or None,
            }
    return ies


def carregar_cursos(ano):
    caminho = DADOS / f"curso_odontologia_{ano}.csv"
    if not caminho.exists():
        raise SystemExit(f"[ERRO] {caminho} ausente. Rode etl/extrair_censo.py antes.")
    with open(caminho, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


# --------------------------------------------------------------------------- #
# Agregação
# --------------------------------------------------------------------------- #

def agregar(linhas, ies, mun_por_uf, nomes_municipio, populacao):
    ufs = {uf: _uf_vazia(uf, mun_por_uf.get(uf, 0), populacao.get(uf))
           for uf in mun_por_uf}

    vagas_ies = defaultdict(lambda: defaultdict(int))
    vagas_mantenedora = defaultdict(lambda: defaultdict(int))
    municipios = defaultdict(lambda: defaultdict(lambda: {
        "vagas_presencial": 0, "cursos_presencial": 0, "matriculas": 0,
        "concluintes": 0, "polos_ead": 0, "matriculas_ead": 0, "ies": set(),
    }))
    sede_sem_uf = 0

    for linha in linhas:
        dimensao = (linha.get("TP_DIMENSAO") or "").strip()
        modalidade = (linha.get("TP_MODALIDADE_ENSINO") or "").strip()
        esperadas = MODALIDADES_POR_DIMENSAO.get(dimensao)
        if esperadas is not None and modalidade not in esperadas:
            raise SystemExit(
                f"[INGESTAO] TP_MODALIDADE_ENSINO={modalidade!r} numa linha de "
                f"TP_DIMENSAO={dimensao} (CO_CURSO {linha.get('CO_CURSO')}). "
                f"Esperado {sorted(esperadas)}. Modalidade nova ou incoerente "
                "não pode cair calada num ramo: confira o dicionário do INEP.")
        semi = modalidade == MOD_SEMIPRESENCIAL
        uf_linha = (linha.get("SG_UF") or "").strip().upper() or None
        cod_ies = (linha.get("CO_IES") or "").strip()
        dados_ies = ies.get(cod_ies, {})
        vagas = inteiro(linha.get("QT_VG_TOTAL"))
        matriculas = inteiro(linha.get("QT_MAT"))
        concluintes = inteiro(linha.get("QT_CONC"))
        ingressos = inteiro(linha.get("QT_ING"))
        cod_mun = (linha.get("CO_MUNICIPIO") or "").strip()

        # A UF a que a linha pertence depende da camada. Só a sede é atribuída
        # por mantenedora; presencial e polo ficam onde estão.
        if dimensao == DIM_SEDE:
            uf = dados_ies.get("uf_sede")
            if not uf:
                sede_sem_uf += 1
                continue
        else:
            uf = uf_linha
        if not uf or uf not in ufs:
            continue

        d = ufs[uf]

        if dimensao == DIM_PRESENCIAL:
            d["vagas_presencial"] += vagas
            d["n_cursos_presencial"] += 1
            d["_ies_presencial"].add(cod_ies)
            d["_municipios_oferta"].add(cod_mun)
            if (linha.get("IN_CAPITAL") or "").strip() == "1":
                d["vagas_capital"] += vagas
            m = municipios[uf][cod_mun]
            m["vagas_presencial"] += vagas
            m["cursos_presencial"] += 1
            m["matriculas"] += matriculas
            m["concluintes"] += concluintes
            m["ies"].add(cod_ies)

        elif dimensao == DIM_SEDE and semi:
            d["vagas_semipresencial"] += vagas
            d["n_cursos_semipresencial"] += 1
            d["_ies_semi"].add(cod_ies)

        elif dimensao == DIM_SEDE:
            d["vagas_ead"] += vagas
            d["n_cursos_ead"] += 1
            d["_ies_ead"].add(cod_ies)

        elif dimensao == DIM_POLO and semi:
            # Local de oferta de curso semipresencial: tem alunos (que somam
            # no fluxo da UF, abaixo), mas NÃO é polo de EaD.
            d["matriculas_semipresencial"] += matriculas
            m = municipios[uf][cod_mun]
            m["matriculas"] += matriculas
            m["concluintes"] += concluintes

        elif dimensao == DIM_POLO:
            d["ead_polos_registros"] += 1
            d["_municipios_polo"].add(cod_mun)
            m = municipios[uf][cod_mun]
            m["polos_ead"] += 1
            m["matriculas_ead"] += matriculas
            m["matriculas"] += matriculas
            m["concluintes"] += concluintes

        # Fluxo e perfil somam sobre TODAS as camadas com alunos: presencial e
        # polo. A sede não tem aluno, então não contamina nada aqui.
        d["matriculas"] += matriculas
        d["concluintes"] += concluintes
        d["ingressos"] += ingressos
        if dimensao == DIM_PRESENCIAL:
            d["matriculas_presencial"] += matriculas
        elif dimensao == DIM_POLO and not semi:
            d["matriculas_ead"] += matriculas

        # Cada recorte soma numerador E denominador só nas linhas em que veio
        # divulgado. Linha suprimida fica fora dos dois, em vez de entrar no
        # denominador com numerador zero e puxar o percentual para baixo.
        for destino, coluna in RECORTES_PERFIL:
            valor = linha.get(coluna)
            if divulgado(valor):
                d[destino] += inteiro(valor)
                d["_base" + destino] += matriculas
        if (linha.get("TP_REDE") or "").strip() == "1":
            d["_mat_publica"] += matriculas

        # Concentração: sobre a capacidade TOTAL, presencial mais EaD atribuída.
        if vagas > 0 and dimensao in (DIM_PRESENCIAL, DIM_SEDE):
            vagas_ies[uf][cod_ies] += vagas
            vagas_mantenedora[uf][dados_ies.get("mantenedora", f"IES:{cod_ies}")] += vagas

    if sede_sem_uf:
        print(f"[INGESTAO] atenção: {sede_sem_uf} linhas de sede EaD sem UF de "
              "mantenedora no cadastro de IES — vagas não atribuídas a nenhuma UF.")

    _fechar(ufs, vagas_ies, vagas_mantenedora)
    return ufs, _fechar_municipios(municipios, nomes_municipio)


def _uf_vazia(uf, mun_total, populacao):
    return {
        "uf": uf,
        "regiao": REGIOES.get(uf),
        "municipios_total": mun_total,
        "populacao": populacao,
        "vagas_presencial": 0, "vagas_ead": 0, "vagas_capital": 0,
        "vagas_semipresencial": 0,
        "n_cursos_presencial": 0, "n_cursos_ead": 0,
        "n_cursos_semipresencial": 0,
        "ead_polos_registros": 0,
        "matriculas": 0, "matriculas_presencial": 0, "matriculas_ead": 0,
        "matriculas_semipresencial": 0,
        "concluintes": 0, "ingressos": 0,
        "_ies_presencial": set(), "_ies_ead": set(), "_ies_semi": set(),
        "_municipios_oferta": set(), "_municipios_polo": set(),
        "_mat_publica": 0,
        **{destino: 0 for destino, _ in RECORTES_PERFIL},
        **{"_base" + destino: 0 for destino, _ in RECORTES_PERFIL},
    }


def _fechar(ufs, vagas_ies, vagas_mantenedora):
    from indices import cr, hhi

    for uf, d in ufs.items():
        oferta = d.pop("_municipios_oferta")
        polos = d.pop("_municipios_polo")
        ies_pres = d.pop("_ies_presencial")
        ies_ead = d.pop("_ies_ead")
        ies_semi = d.pop("_ies_semi")

        d["municipios_oferta"] = len(oferta)
        d["municipios_deserto"] = d["municipios_total"] - len(oferta)
        d["ead_polos_municipios"] = len(polos)
        d["municipios_so_ead"] = len(polos - oferta)
        d["cobertura_municipal"] = (
            round(len(oferta) / d["municipios_total"], 4)
            if d["municipios_total"] else None)

        d["n_ies_presencial"] = len(ies_pres)
        d["n_ies_ead"] = len(ies_ead)
        d["n_ies"] = len(ies_pres | ies_ead | ies_semi)
        d["n_mantenedoras"] = len(vagas_mantenedora.get(uf, {})) or None

        # As três modalidades somam o total; a participação da EaD continua
        # sendo só EaD — a semipresencial não entra no numerador.
        d["vagas_total"] = (d["vagas_presencial"] + d["vagas_ead"]
                            + d["vagas_semipresencial"])
        d["pct_ead"] = pct(d["vagas_ead"], d["vagas_total"])
        d["vagas_por_100k"] = (
            round(100_000 * d["vagas_total"] / d["populacao"], 1)
            if d["populacao"] else None)

        d["HHI"] = hhi(vagas_ies.get(uf, {}))
        d["HHI_mantenedora"] = hhi(vagas_mantenedora.get(uf, {}))
        d["CR2"] = cr(vagas_ies.get(uf, {}), 2)
        d["CR10"] = cr(vagas_ies.get(uf, {}), 10)

        base = d["matriculas"]
        bases = {destino: d.pop("_base" + destino)
                 for destino, _ in RECORTES_PERFIL}
        d["pct_mulheres"] = pct(d.pop("_mat_fem"), bases["_mat_fem"])
        d["pct_noturno"] = pct(d.pop("_mat_noturno"), bases["_mat_noturno"])
        # PPI soma três recortes, e só tem denominador único se os três foram
        # divulgados nas mesmas linhas. Se as bases diferirem, houve supressão
        # parcial, e o percentual sai nulo em vez de misturar denominadores.
        base_ppi = {bases["_mat_preta"], bases["_mat_parda"],
                    bases["_mat_indigena"]}
        soma_ppi = (d.pop("_mat_preta") + d.pop("_mat_parda")
                    + d.pop("_mat_indigena"))
        d["pct_ppi"] = (pct(soma_ppi, base_ppi.pop())
                        if len(base_ppi) == 1 else None)
        d["pct_cor_nao_declarada"] = pct(d.pop("_mat_cornd"), bases["_mat_cornd"])
        d["pct_financiamento"] = pct(d.pop("_mat_financ"), bases["_mat_financ"])
        d["pct_apoio_social"] = pct(d.pop("_mat_apoio"), bases["_mat_apoio"])
        d["pct_reserva_vaga"] = pct(d.pop("_mat_reserva"), bases["_mat_reserva"])
        d["pct_rede_publica"] = pct(d.pop("_mat_publica"), base)
        d["taxa_conclusao"] = pct(d["concluintes"], base)

        # Uma UF sem curso presencial NÃO vira zero: fica explicitamente sem
        # oferta, e os indicadores que dependem de oferta presencial ficam
        # nulos. AP, MS e RR estão exatamente nesse caso no Censo 2024.
        d["tem_oferta_presencial"] = d["n_cursos_presencial"] > 0
        if not d["tem_oferta_presencial"]:
            for chave in ("vagas_capital", "pct_noturno", "taxa_conclusao"):
                if not d.get(chave):
                    d[chave] = None


def _fechar_municipios(municipios, nomes):
    saida = {}
    for uf, mapa in municipios.items():
        lista = []
        for codigo, m in mapa.items():
            nome, _uf = nomes.get(codigo, (None, uf))
            lista.append({
                "codigo": codigo,
                "nome": nome,
                "uf": uf,
                "vagas_presencial": m["vagas_presencial"],
                "cursos_presencial": m["cursos_presencial"],
                "n_ies": len(m["ies"]),
                "matriculas": m["matriculas"],
                "matriculas_ead": m["matriculas_ead"],
                "concluintes": m["concluintes"],
                "polos_ead": m["polos_ead"],
                "tem_curso_presencial": m["cursos_presencial"] > 0,
            })
        saida[uf] = sorted(lista, key=lambda x: (-x["vagas_presencial"],
                                                 x["nome"] or ""))
    return saida


# --------------------------------------------------------------------------- #

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ano", type=int, default=2024)
    p.add_argument("--saida", default=str(DATA / "bruto.json"))
    args = p.parse_args()

    prov_censo = {}
    caminho_prov = DADOS / f"_censo_{args.ano}.json"
    if caminho_prov.exists():
        prov_censo = json.loads(caminho_prov.read_text(encoding="utf-8"))

    linhas = carregar_cursos(args.ano)
    ies = carregar_ies(args.ano)
    mun_por_uf, nomes, populacao, ano_pop = contexto_ibge()

    ufs, municipios = agregar(linhas, ies, mun_por_uf, nomes, populacao)

    com_presencial = [u for u, d in ufs.items() if d["tem_oferta_presencial"]]
    sem_presencial = sorted(set(ufs) - set(com_presencial))
    print(f"[INGESTAO] {len(linhas)} linhas -> {len(ufs)} UFs")
    print(f"[INGESTAO] com oferta presencial: {len(com_presencial)}")
    print(f"[INGESTAO] sem oferta presencial: {sem_presencial}")
    print(f"[INGESTAO] vagas presenciais: "
          f"{sum(d['vagas_presencial'] for d in ufs.values())}")
    print(f"[INGESTAO] vagas EaD (sede): "
          f"{sum(d['vagas_ead'] for d in ufs.values())}")
    print(f"[INGESTAO] vagas semipresenciais (sede): "
          f"{sum(d['vagas_semipresencial'] for d in ufs.values())}")
    print(f"[INGESTAO] municípios com oferta presencial: "
          f"{sum(d['municipios_oferta'] for d in ufs.values())}")
    print(f"[INGESTAO] polos: {sum(d['ead_polos_registros'] for d in ufs.values())} "
          f"registros em {sum(d['ead_polos_municipios'] for d in ufs.values())} municípios")

    saida = {
        "metadados": {
            "curso": "Odontologia",
            "ano_censo": args.ano,
            "proveniencia_censo": prov_censo,
            "populacao_ibge": {
                "agregado": 6579, "variavel": 9324, "nivel": "N3",
                "ano": ano_pop,
            },
            "municipios_ibge": {
                "fonte": API_MUNICIPIOS,
                "total": sum(mun_por_uf.values()),
            },
            "ufs_com_oferta_presencial": len(com_presencial),
            "ufs_sem_oferta_presencial": sem_presencial,
            "gerado_em": date.today().isoformat(),
        },
        "ufs": ufs,
        "municipios": municipios,
    }
    Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.saida).write_text(
        json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[INGESTAO] -> {args.saida}")


if __name__ == "__main__":
    main()
