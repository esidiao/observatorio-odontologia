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


def inteiro(valor):
    texto = (valor or "").strip()
    return int(texto) if texto.lstrip("-").isdigit() else 0


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

        elif dimensao == DIM_SEDE:
            d["vagas_ead"] += vagas
            d["n_cursos_ead"] += 1
            d["_ies_ead"].add(cod_ies)

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
        elif dimensao == DIM_POLO:
            d["matriculas_ead"] += matriculas

        for destino, coluna in (
                ("_mat_fem", "QT_MAT_FEM"), ("_mat_noturno", "QT_MAT_NOTURNO"),
                ("_mat_preta", "QT_MAT_PRETA"), ("_mat_parda", "QT_MAT_PARDA"),
                ("_mat_indigena", "QT_MAT_INDIGENA"),
                ("_mat_cornd", "QT_MAT_CORND"),
                ("_mat_financ", "QT_MAT_FINANC"),
                ("_mat_apoio", "QT_MAT_APOIO_SOCIAL"),
                ("_mat_reserva", "QT_MAT_RESERVA_VAGA")):
            d[destino] += inteiro(linha.get(coluna))
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


FORMATO_PRESENCIAL = "1"
FORMATO_EAD = "2"
FORMATO_SEMIPRESENCIAL = "3"

SITUACAO_ATIVIDADE = "1"
SITUACAO_EXTINTO = "2"
SITUACAO_EXTINCAO = "3"


def carregar_fatia(nome, ano, obrigatoria=True):
    caminho = DADOS / f"{nome}_{ano}.csv"
    if not caminho.exists():
        if obrigatoria:
            raise SystemExit(
                f"[ERRO] {caminho} ausente. Rode etl/extrair_censo.py --ano {ano}.")
        return []
    with open(caminho, encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _perfil_por_curso(alunos):
    """
    Soma o perfil por `CO_CURSO`, a partir da tabela de aluno.

    A tabela traz a MESMA pessoa em três níveis de agregação, e o extrator já
    guardou só o nível de curso. Esta função confere isso de novo em vez de
    confiar: se um dia a fatia vier com outro nível junto, a soma dobraria sem
    nada avisar, e dobrar silenciosamente é o defeito mais caro que existe num
    pipeline de estatística.
    """
    niveis = {(l.get("TP_NIVEL_AGREGACAO") or "").strip() for l in alunos}
    if niveis - {"2"}:
        raise SystemExit(
            f"[ERRO] a fatia de aluno traz os níveis de agregação {sorted(niveis)}; "
            "só o nível 2 (curso) pode ser somado. Reextraia.")
    por_curso = defaultdict(lambda: defaultdict(int))
    for l in alunos:
        alvo = por_curso[(l.get("CO_CURSO") or "").strip()]
        for destino, coluna in (
                ("_mat_fem", "QT_MAT_FEM"), ("_mat_noturno", "QT_MAT_NOTURNO"),
                ("_mat_preta", "QT_MAT_PRETA"), ("_mat_parda", "QT_MAT_PARDA"),
                ("_mat_indigena", "QT_MAT_INDIGENA"),
                ("_mat_cornd", "QT_MAT_CORND"),
                ("_mat_financ", "QT_MAT_FINANC"),
                ("_mat_apoio", "QT_MAT_APOIO_SOCIAL"),
                ("_mat_reserva", "QT_MAT_RESERVA_VAGA")):
            alvo[destino] += inteiro(l.get(coluna))
    return por_curso


def agregar_normalizado(cursos, locais, alunos, ies, mun_por_uf,
                        nomes_municipio, populacao):
    ufs = {uf: _uf_vazia_normalizado(uf, mun_por_uf.get(uf, 0), populacao.get(uf))
           for uf in mun_por_uf}
    vagas_ies = defaultdict(lambda: defaultdict(int))
    vagas_mantenedora = defaultdict(lambda: defaultdict(int))
    municipios = defaultdict(lambda: defaultdict(lambda: {
        "vagas_presencial": 0, "cursos_presencial": 0, "matriculas": 0,
        "concluintes": 0, "polos_ead": 0, "matriculas_ead": 0, "ies": set(),
    }))
    sem_localizacao = {"linhas": 0, "matriculas": 0, "concluintes": 0,
                       "ingressos": 0, "vagas": 0, "formatos": {}}
    perfil = _perfil_por_curso(alunos)
    uf_do_curso = {}
    # Quanto da capacidade foi colocado numa UF pela sede da mantenedora, e
    # nao pelo local de oferta. Publicado: e uma atribuicao, nao uma medida.
    por_ies = {"linhas": 0, "vagas": 0, "matriculas": 0}

    for linha in cursos:
        formato = (linha.get("TP_MODALIDADE_ENSINO") or "").strip()
        situacao = (linha.get("TP_SITUACAO") or "").strip()
        uf = (linha.get("SG_UF_LO") or "").strip().upper() or None
        cod_mun = (linha.get("CO_MUNICIPIO_LO") or "").strip()
        cod_ies = (linha.get("CO_IES") or "").strip()
        cod_curso = (linha.get("CO_CURSO") or "").strip()

        vagas = inteiro(linha.get("QT_VAGA_TOTAL"))
        matriculas = inteiro(linha.get("QT_MATRICULA_TOTAL"))
        concluintes = inteiro(linha.get("QT_CONCLUINTE_TOTAL"))
        ingressos = inteiro(linha.get("QT_INGRESSO_TOTAL"))

        # SEM LOCAL DE OFERTA: cai para a UF-sede da mantenedora.
        #
        # Quantas linhas chegam sem `SG_UF_LO`, e quanta capacidade elas
        # carregam, é medido a cada execução e publicado em
        # `metadados.sem_localizacao.atribuidas_pela_sede_da_ies` — número
        # que pertence a cada edição e a cada curso, e por isso não está
        # escrito aqui.
        #
        # Descartá-las seria publicar um país menor do que ele é, com o total
        # continuando plausível. O modelo de 2024 já resolvia isso: a linha de sede vinha
        # sem `SG_UF` e era atribuida por `CO_IES` -> `SG_UF_IES`. A regra
        # continua a mesma; o que mudou foi o nome da coluna.
        #
        # O que nao se faz e inventar: se a IES tambem nao tem UF, a linha vai
        # para `sem_localizacao` e aparece na pagina, em vez de ser rateada.
        # SO A OFERTA SEM ENDERECO PROPRIO PODE SER ATRIBUIDA.
        #
        # O modelo de 2024 distinguia duas coisas que em 2025 viraram a mesma
        # ausencia de `SG_UF_LO`:
        #
        #   TP_DIMENSAO 3 (sede de EaD) vinha sem UF POR NATUREZA, e era
        #     atribuida pela mantenedora. Legitimo: o curso a distancia nao
        #     tem endereco, e a sede e o lugar que a fonte conhece.
        #   TP_DIMENSAO 4 (sem localizacao) era a fonte dizendo que NAO SABE
        #     onde aquilo esta. Atribuir essa por mantenedora e inventar dado,
        #     e ha teste proibindo desde o ciclo anterior.
        #
        # Sem a coluna de dimensao, o que separa os dois casos e o FORMATO: um
        # curso presencial tem endereco por definicao, entao presencial sem
        # localizacao e ausencia de informacao, nao oferta deslocalizada.
        # Medido em 2025: as 29 linhas sem UF sao 19 a distancia e 10
        # semipresenciais, nenhuma presencial — mas a regra fica escrita, e
        # nao depende de a proxima edicao ter a mesma sorte.
        atribuida_pela_ies = False
        if (not uf or uf not in ufs) and formato != FORMATO_PRESENCIAL:
            uf_ies = ies.get(cod_ies, {}).get("uf_sede")
            if uf_ies in ufs:
                uf = uf_ies
                atribuida_pela_ies = True

        if not uf or uf not in ufs:
            if matriculas or concluintes or ingressos or vagas:
                sem_localizacao["linhas"] += 1
                sem_localizacao["matriculas"] += matriculas
                sem_localizacao["concluintes"] += concluintes
                sem_localizacao["ingressos"] += ingressos
                sem_localizacao["vagas"] += vagas
                chave = formato or "(vazio)"
                sem_localizacao["formatos"][chave] = (
                    sem_localizacao["formatos"].get(chave, 0) + 1)
            continue

        uf_do_curso[cod_curso] = uf
        d = ufs[uf]
        if atribuida_pela_ies:
            por_ies["linhas"] += 1
            por_ies["vagas"] += vagas
            por_ies["matriculas"] += matriculas
        ativo = situacao == SITUACAO_ATIVIDADE

        # --- capacidade: separa por situação
        if ativo:
            if formato == FORMATO_PRESENCIAL:
                d["vagas_presencial"] += vagas
                d["n_cursos_presencial"] += 1
                d["_ies_presencial"].add(cod_ies)
            elif formato == FORMATO_SEMIPRESENCIAL:
                d["vagas_semipresencial"] += vagas
                d["n_cursos_semipresencial"] += 1
                d["_ies_presencial"].add(cod_ies)
            elif formato == FORMATO_EAD:
                d["vagas_ead"] += vagas
                d["n_cursos_ead"] += 1
                d["_ies_ead"].add(cod_ies)
            if (linha.get("IN_CAPITAL_LO") or "").strip() == "1":
                d["vagas_capital"] += vagas
        else:
            d["vagas_em_extincao"] += vagas
            d["n_cursos_em_extincao"] += 1
            d["matriculas_em_extincao"] += matriculas
            d["ingressos_em_extincao"] += ingressos
            if formato == FORMATO_EAD:
                d["_ies_ead"].add(cod_ies)

        # Presencial e semipresencial ocupam o município: os dois exigem
        # atividade presencial. O curso a distância, não.
        presente_no_municipio = formato in (FORMATO_PRESENCIAL,
                                            FORMATO_SEMIPRESENCIAL)
        if presente_no_municipio and cod_mun:
            if ativo:
                d["_municipios_oferta"].add(cod_mun)
            m = municipios[uf][cod_mun]
            if ativo and formato == FORMATO_PRESENCIAL:
                m["vagas_presencial"] += vagas
                m["cursos_presencial"] += 1
            m["matriculas"] += matriculas
            m["concluintes"] += concluintes
            m["ies"].add(cod_ies)

        # --- pessoas: contam em qualquer situação
        d["matriculas"] += matriculas
        d["concluintes"] += concluintes
        d["ingressos"] += ingressos
        if formato == FORMATO_PRESENCIAL:
            d["matriculas_presencial"] += matriculas
        elif formato == FORMATO_SEMIPRESENCIAL:
            d["matriculas_semipresencial"] += matriculas
        elif formato == FORMATO_EAD:
            d["matriculas_ead"] += matriculas

        for destino, valor in perfil.get(cod_curso, {}).items():
            d[destino] += valor
        if (linha.get("TP_REDE") or "").strip() == "1":
            d["_mat_publica"] += matriculas

        if vagas > 0 and ativo:
            vagas_ies[uf][cod_ies] += vagas
            # A mantenedora vem do cadastro de IES, nao da linha do curso: a
            # tabela de curso de 2025 traz CO_IES e NO_IES, e nenhuma coluna
            # de mantenedora. A primeira versao deste trecho lia
            # NO_MANTENEDORA da propria linha, que nao existe ali -- e o
            # "or NO_IES" logo atras escondia a falta, fazendo o HHI por
            # mantenedora medir concentracao por INSTITUICAO com outro nome.
            mant = ies.get(cod_ies, {}).get("mantenedora") or f"IES:{cod_ies}"
            vagas_mantenedora[uf][mant] += vagas

    # --- polos: tabela própria, contados por LOCAL distinto
    #
    # Contar linhas inflaria: a tabela tem uma linha por (local x curso), então
    # um polo que atende três cursos apareceria três vezes. Contar
    # CO_LOCAL_OFERTA distinto é correto quer as bandeiras se sobreponham ou
    # não — e é por isso que a contagem não soma sede com polo.
    # SÃO TRÊS POPULAÇÕES, E SOMÁ-LAS PRODUZ UM NÚMERO QUE NÃO MEDE NADA.
    #
    # A bandeira `IN_LOCAL_OFERTA_POLO` marca, na mesma tabela:
    #
    #     polo de curso a distância em extinção   a rede que está sendo desfeita
    #     polo de curso ativo não presencial      a rede que ficou de pé
    #     polo de curso PRESENCIAL                endereço de aula presencial
    #
    # Somar os três publica um alcance que mistura a rede que morre com a que
    # nasce, e acrescenta endereços de curso presencial que não são alcance de
    # oferta a distância de jeito nenhum.
    #
    # São duas contagens, pela mesma regra da capacidade: a rede ativa e a
    # rede em extinção, separadas, e o presencial fora das duas.
    formato_do_curso = {}
    situacao_do_curso = {}
    for linha in cursos:
        cod = (linha.get("CO_CURSO") or "").strip()
        formato_do_curso[cod] = (linha.get("TP_MODALIDADE_ENSINO") or "").strip()
        situacao_do_curso[cod] = (linha.get("TP_SITUACAO") or "").strip()

    polos_por_uf = defaultdict(set)
    polos_mun_por_uf = defaultdict(set)
    polos_ext_por_uf = defaultdict(set)
    polos_ext_mun_por_uf = defaultdict(set)
    # Polo que a fonte não localiza: contado e declarado, nunca atribuído.
    #
    # Antes a linha caía num `continue` sem contador, e o total nacional
    # (que é a soma das UFs) ficava alguns polos abaixo das linhas da fonte,
    # sem nada explicando a diferença. Um polo é por definição um ENDEREÇO:
    # se a fonte não diz onde é, não há o que inferir — ao contrário da oferta
    # a distância sem endereço, que é atribuída à UF-sede porque não tem
    # endereço por natureza.
    polos_sem_uf = {True: set(), False: set()}
    for linha in locais:
        if (linha.get("IN_LOCAL_OFERTA_POLO") or "").strip() != "1":
            continue
        uf = (linha.get("SG_UF_LO") or "").strip().upper()
        cod_curso_sem_uf = (linha.get("CO_CURSO") or "").strip()
        if uf not in ufs:
            if formato_do_curso.get(cod_curso_sem_uf) != FORMATO_PRESENCIAL:
                polos_sem_uf[
                    situacao_do_curso.get(cod_curso_sem_uf) == SITUACAO_ATIVIDADE
                ].add((linha.get("CO_LOCAL_OFERTA") or "").strip())
            continue
        cod_curso = (linha.get("CO_CURSO") or "").strip()
        # Polo de curso presencial nao e alcance de oferta a distancia.
        if formato_do_curso.get(cod_curso) == FORMATO_PRESENCIAL:
            continue
        cod_local = (linha.get("CO_LOCAL_OFERTA") or "").strip()
        cod_mun = (linha.get("CO_MUNICIPIO_LO") or "").strip()
        ativo = situacao_do_curso.get(cod_curso) == SITUACAO_ATIVIDADE
        alvo_polos = polos_por_uf if ativo else polos_ext_por_uf
        alvo_muns = polos_mun_por_uf if ativo else polos_ext_mun_por_uf
        alvo_polos[uf].add(cod_local)
        if cod_mun:
            alvo_muns[uf].add(cod_mun)
            if ativo:
                municipios[uf][cod_mun]["polos_ead"] += 1

    for uf, d in ufs.items():
        # Contagem de LOCAIS DISTINTOS, nao de linhas: a tabela tem uma linha
        # por (local x curso), e um polo que atende tres cursos apareceria
        # tres vezes. E nao se somam bandeiras: `IN_SEDE` e
        # `IN_LOCAL_OFERTA_POLO` não são exclusivos — há local que é os
        # dois —, então somar sede com polo contaria o mesmo endereço
        # duas vezes.
        d["ead_polos_registros"] = len(polos_por_uf.get(uf, ()))
        d["_municipios_polo"] = polos_mun_por_uf.get(uf, set())
        d["polos_em_extincao"] = len(polos_ext_por_uf.get(uf, ()))
        d["polos_em_extincao_municipios"] = len(
            polos_ext_mun_por_uf.get(uf, ()))

    # Quanto da capacidade foi posto numa UF pela sede da mantenedora, e nao
    # pelo local de oferta. Publicado porque e ATRIBUICAO, nao medida: quem
    # ler o numero da UF tem direito de saber que parte dele chegou ali por
    # inferencia.
    sem_localizacao["atribuidas_pela_sede_da_ies"] = por_ies
    # Publicado mesmo quando é zero: campo que só aparece quando há problema
    # ensina o leitor a não procurá-lo.
    sem_localizacao["polos_sem_uf"] = {
        "ativos": len(polos_sem_uf[True]),
        "em_extincao": len(polos_sem_uf[False]),
    }
    _fechar(ufs, vagas_ies, vagas_mantenedora)
    return ufs, _fechar_municipios(municipios, nomes_municipio), sem_localizacao


def _uf_vazia_normalizado(uf, mun_total, populacao):
    d = _uf_vazia(uf, mun_total, populacao)
    d.update({
        "vagas_semipresencial": 0, "n_cursos_semipresencial": 0,
        "matriculas_semipresencial": 0,
        "vagas_em_extincao": 0, "n_cursos_em_extincao": 0,
        "matriculas_em_extincao": 0, "ingressos_em_extincao": 0,
    })
    return d


def _uf_vazia(uf, mun_total, populacao):
    return {
        "uf": uf,
        "regiao": REGIOES.get(uf),
        "municipios_total": mun_total,
        "populacao": populacao,
        "vagas_presencial": 0, "vagas_ead": 0, "vagas_capital": 0,
        "n_cursos_presencial": 0, "n_cursos_ead": 0,
        "ead_polos_registros": 0,
        "matriculas": 0, "matriculas_presencial": 0, "matriculas_ead": 0,
        "concluintes": 0, "ingressos": 0,
        "_ies_presencial": set(), "_ies_ead": set(),
        "_municipios_oferta": set(), "_municipios_polo": set(),
        "_mat_fem": 0, "_mat_noturno": 0, "_mat_preta": 0, "_mat_parda": 0,
        "_mat_indigena": 0, "_mat_cornd": 0, "_mat_financ": 0,
        "_mat_apoio": 0, "_mat_reserva": 0, "_mat_publica": 0,
    }


def _fechar(ufs, vagas_ies, vagas_mantenedora):
    from indices import cr, hhi

    for uf, d in ufs.items():
        oferta = d.pop("_municipios_oferta")
        polos = d.pop("_municipios_polo")
        ies_pres = d.pop("_ies_presencial")
        ies_ead = d.pop("_ies_ead")

        d["municipios_oferta"] = len(oferta)
        d["municipios_deserto"] = d["municipios_total"] - len(oferta)
        d["ead_polos_municipios"] = len(polos)
        d["municipios_so_ead"] = len(polos - oferta)
        d["cobertura_municipal"] = (
            round(len(oferta) / d["municipios_total"], 4)
            if d["municipios_total"] else None)

        d["n_ies_presencial"] = len(ies_pres)
        d["n_ies_ead"] = len(ies_ead)
        d["n_ies"] = len(ies_pres | ies_ead)
        d["n_mantenedoras"] = len(vagas_mantenedora.get(uf, {})) or None

        # Capacidade ATIVA: presencial + semipresencial + a distancia.
        # O semipresencial e categoria nova (Censo 2025) e vale zero nas
        # edicoes anteriores, entao a soma serve para as duas. Esquece-lo
        # tiraria 95.464 vagas do pais em 2025 sem nada acusar, porque o
        # total continuaria sendo um numero plausivel.
        d["vagas_total"] = (d["vagas_presencial"] + d["vagas_ead"]
                            + d.get("vagas_semipresencial", 0))
        d["pct_ead"] = pct(d["vagas_ead"], d["vagas_total"])
        # Quanto da capacidade ativa NAO e inteiramente presencial. Em 2024
        # isso e a propria EaD; de 2025 em diante e o semipresencial, que foi
        # o formato para onde a oferta a distancia migrou. O indicador
        # atravessa a mudanca de lei porque pergunta a mesma coisa nas duas
        # edicoes: que fatia da oferta nao exige presenca integral.
        d["pct_nao_presencial"] = pct(
            d["vagas_ead"] + d.get("vagas_semipresencial", 0),
            d["vagas_total"])
        d["vagas_por_100k"] = (
            round(100_000 * d["vagas_total"] / d["populacao"], 1)
            if d["populacao"] else None)

        d["HHI"] = hhi(vagas_ies.get(uf, {}))
        d["HHI_mantenedora"] = hhi(vagas_mantenedora.get(uf, {}))
        d["CR2"] = cr(vagas_ies.get(uf, {}), 2)
        d["CR10"] = cr(vagas_ies.get(uf, {}), 10)

        base = d["matriculas"]
        d["pct_mulheres"] = pct(d.pop("_mat_fem"), base)
        d["pct_noturno"] = pct(d.pop("_mat_noturno"), base)
        d["pct_ppi"] = pct(d.pop("_mat_preta") + d.pop("_mat_parda")
                           + d.pop("_mat_indigena"), base)
        d["pct_cor_nao_declarada"] = pct(d.pop("_mat_cornd"), base)
        d["pct_financiamento"] = pct(d.pop("_mat_financ"), base)
        d["pct_apoio_social"] = pct(d.pop("_mat_apoio"), base)
        d["pct_reserva_vaga"] = pct(d.pop("_mat_reserva"), base)
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
    p.add_argument("--ano", type=int, default=2025)
    p.add_argument("--saida", default=str(DATA / "bruto.json"))
    args = p.parse_args()

    prov_censo = {}
    caminho_prov = DADOS / f"_censo_{args.ano}.json"
    if caminho_prov.exists():
        prov_censo = json.loads(caminho_prov.read_text(encoding="utf-8"))

    linhas = carregar_cursos(args.ano)
    ies = carregar_ies(args.ano)
    mun_por_uf, nomes, populacao, ano_pop = contexto_ibge()

    # Qual leitor usar vem da PROVENIÊNCIA, que o extrator gravou a partir do
    # que havia dentro do zip — não de uma comparação com o ano. Ano é
    # calendário; esquema é fato do arquivo, e foi o arquivo que mudou.
    esquema = prov_censo.get("esquema", "unico")
    print(f"[INGESTAO] esquema: {esquema}")
    sem_localizacao = None
    if esquema == "normalizado":
        locais = carregar_fatia("local_oferta_odontologia", args.ano)
        alunos = carregar_fatia("aluno_odontologia", args.ano)
        print(f"[INGESTAO] {len(linhas)} cursos, {len(locais)} locais de "
              f"oferta, {len(alunos)} linhas de aluno (nível curso)")
        ufs, municipios, sem_localizacao = agregar_normalizado(
            linhas, locais, alunos, ies, mun_por_uf, nomes, populacao)
    else:
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
    print(f"[INGESTAO] municípios com oferta presencial: "
          f"{sum(d['municipios_oferta'] for d in ufs.values())}")
    print(f"[INGESTAO] polos: {sum(d['ead_polos_registros'] for d in ufs.values())} "
          f"registros em {sum(d['ead_polos_municipios'] for d in ufs.values())} municípios")
    if any("vagas_em_extincao" in d for d in ufs.values()):
        print(f"[INGESTAO] vagas semipresenciais: "
              f"{sum(d.get('vagas_semipresencial') or 0 for d in ufs.values())}")
        print(f"[INGESTAO] vagas em curso encerrado ou em extinção: "
              f"{sum(d.get('vagas_em_extincao') or 0 for d in ufs.values())}")
        print(f"[INGESTAO] polos em extinção: "
              f"{sum(d.get('polos_em_extincao') or 0 for d in ufs.values())} "
              f"registros em "
              f"{sum(d.get('polos_em_extincao_municipios') or 0 for d in ufs.values())}"
              " municípios")

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
            # O que a fonte registra e não localiza. Publicado mesmo quando é
            # zero: um campo que só aparece quando há problema ensina o leitor
            # a não procurá-lo, e some justamente quando passa a importar.
            "sem_localizacao": sem_localizacao,
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
