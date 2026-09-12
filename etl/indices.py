"""
etl/indices.py
Fórmulas canônicas do Observatório Nacional da Formação em Odontologia e o
portão GO.

Uso:
    python etl/indices.py --autoteste
    python etl/indices.py --dados data/bruto.json --qualidade etl/qualidade_uf.csv \
                          --saida data/nacional.json

TODA FUNÇÃO AQUI DEVOLVE None QUANDO FALTA INSUMO
--------------------------------------------------
Nunca 0, nunca média, nunca valor de outra UF. `None` atravessa o pipeline até
virar "sem dados" na tela. Zero é uma afirmação — "não há nenhum" — e afirmar
isso sem fonte é inventar dado.

Em Odontologia essa distinção fica mais escorregadia do que nos observatórios
irmãos, e justamente onde mais importa. Como a EaD simplesmente NÃO EXISTE — as
650 linhas do Censo 2024 são todas presenciais, sem uma única vaga ou polo a
distância —, é tentador tratar zero e ausência como a mesma coisa. Não são. As
27 UFs têm `vagas_ead = 0`, e isso é a medida mais forte do conjunto: o Censo
varreu e não encontrou. Um estado cujo dado não pôde ser apurado teria
`vagas_ead = None`. Apagar a diferença transformaria o achado central do
observatório numa lacuna de apuração.

TRÊS ÍNDICES DE COBERTURA, SEPARADOS
-------------------------------------
ICAB, ICRE e ICSB medem coisas diferentes: existe cirurgião-dentista, existe
serviço especializado de saúde bucal, existe equipe de saúde bucal na atenção
primária. Fundi-los exigiria arbitrar um peso entre os três — e peso arbitrário
é estimativa disfarçada. Ficam separados, com escalas próprias.

O portão GO (`--autoteste`) roda antes de qualquer publicação, no CI, contra uma
UF sintética cujos valores foram calculados à mão. É sintética de propósito: um
caso real amarra o teste à edição do Censo e o transforma em teste de dados
quando ele deveria testar fórmula.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent


# --------------------------------------------------------------------------- #
# TERRITÓRIO
# --------------------------------------------------------------------------- #

def ict(vagas_capital, vagas_total, mun_oferta, mun_total):
    """
    Índice de Concentração Territorial. 0 a 1, MENOR é melhor.

        ICT = ½·(vagas_capital / vagas_total) + ½·(1 − mun_oferta / mun_total)

    Metade mede concentração na capital, metade mede vazio no interior. Uma UF
    com todas as vagas na capital e um só município com oferta tende a 1.
    """
    if not vagas_total or not mun_total:
        return None
    if vagas_capital is None or mun_oferta is None:
        return None
    return 0.5 * (vagas_capital / vagas_total) + 0.5 * (1 - mun_oferta / mun_total)


def equidade(valor_ict):
    """E = 1 − ICT. 0 a 1, MAIOR é melhor."""
    return None if valor_ict is None else 1 - valor_ict


# --------------------------------------------------------------------------- #
# QUALIDADE
# --------------------------------------------------------------------------- #

def q_qualidade(cc, enade, idd):
    """
    Componente de qualidade, 0 a 1: média de (nota − 1) / 4 sobre os conceitos
    DISPONÍVEIS. Conceitos INEP vão de 1 a 5.

    A média ignora os ausentes em vez de tratá-los como zero. Um curso sem IDD
    não é um curso com IDD péssimo — o IDD depende de nota de ingresso, que nem
    todo curso avaliado tem. Somar zero no lugar puniria a lacuna como se fosse
    desempenho.
    """
    partes = [(v - 1) / 4 for v in (cc, enade, idd) if v is not None]
    return sum(partes) / len(partes) if partes else None


def iaf(cc, enade, idd, vagas_avaliadas, vagas_total, valor_ict):
    """
    Índice de Adequação Formativa. 0 a 100, MAIOR é melhor.

        IAF = 100 · média(Q, V, E)

    Q = qualidade dos conceitos; V = fração das vagas em cursos avaliados;
    E = equidade territorial (1 − ICT).

    Devolve None se qualquer um dos três faltar: um IAF calculado sobre dois
    terços dos componentes não é comparável com um calculado sobre três, e
    publicá-los na mesma coluna produziria um ranking sem sentido.
    """
    if not vagas_total or valor_ict is None or vagas_avaliadas is None:
        return None
    q = q_qualidade(cc, enade, idd)
    if q is None:
        return None
    v = vagas_avaliadas / vagas_total
    e = equidade(valor_ict)
    return round(100 * (q + v + e) / 3, 1)


# --------------------------------------------------------------------------- #
# CONCENTRAÇÃO DE MERCADO
# --------------------------------------------------------------------------- #

def hhi(vagas_por_agente):
    """
    Herfindahl-Hirschman: Σ(sᵢ²) sobre as fatias de vagas. 0 a 1.

    Serve tanto para IES quanto para MANTENEDORA. Em Odontologia os dois valores
    tendem a ficar próximos, porque a concentração por mantenedora costuma vir
    dos grandes grupos EaD e aqui a EaD é 0,2% da capacidade. Isso é um ACHADO,
    não um defeito de cálculo — e precisa ser dito na página, senão o leitor
    conclui que alguém errou a conta.
    """
    if not vagas_por_agente:
        return None
    total = sum(vagas_por_agente.values())
    if total <= 0:
        return None
    return round(sum((v / total) ** 2 for v in vagas_por_agente.values()), 4)


def cr(vagas_por_agente, n):
    """CR_n: soma das n maiores fatias. 0 a 1."""
    if not vagas_por_agente:
        return None
    total = sum(vagas_por_agente.values())
    if total <= 0:
        return None
    maiores = sorted(vagas_por_agente.values(), reverse=True)[:n]
    return round(sum(maiores) / total, 4)


# --------------------------------------------------------------------------- #
# COBERTURA ASSISTENCIAL — três indicadores, três fontes, nenhuma fusão
# --------------------------------------------------------------------------- #
#
# O ICON do observatório de Farmácia (municípios com Farmácia Popular sobre
# municípios com oferta) é específico da profissão farmacêutica e não transfere.
# Para Odontologia a pergunta "onde há rede pública que absorve quem se forma?"
# tem TRÊS respostas, e nenhuma fonte única as dá:
#
#   ICAB  há cirurgião-dentista        CNES, família CBO 2232
#   ICRE  há serviço especializado     CNES, serviço 114 (atenção
#                                      especializada à saúde bucal)
#   ICSB  há porta de entrada na APS   CNES, equipe de saúde bucal (tbEquipe)
#
# Fundir os três exigiria decidir quanto vale "tem dentista" contra "tem centro
# de especialidades" contra "tem equipe na atenção básica". Não existe resposta
# defensável para isso, e a resposta arbitrária ficaria escondida dentro de um
# número de aparência objetiva.
#
# Os três se sobrepõem só em parte, e é por isso que são três: um município pode
# ter dentista sem ter equipe de saúde bucal cadastrada na atenção primária, e
# pode ter equipe sem ter serviço especializado — este último é o caso mais
# comum, porque a atenção especializada é regionalizada por desenho.

def icab(mun_com_dentista, mun_total):
    """
    Índice de Cobertura Assistencial Bucal — FORÇA DE TRABALHO.
    Fração dos municípios da UF com ao menos um vínculo de cirurgião-dentista
    no SUS (família CBO 2232 no CNES). 0 a 1, MAIOR é melhor.
    """
    if not mun_total or mun_com_dentista is None:
        return None
    return round(mun_com_dentista / mun_total, 4)


def icre(mun_com_esp_bucal, mun_total):
    """
    Índice de Cobertura da Rede Especializada. Fração dos municípios da UF com
    estabelecimento que declara o serviço 114 (atenção especializada à saúde
    bucal) no CNES. 0 a 1, MAIOR é melhor.

    Conta o estabelecimento em QUALQUER das vinte classificações — endodontia,
    periodontia, cirurgia, atendimento a pessoa com deficiência e as demais — e
    conta uma vez só: são especialidades do mesmo serviço, e contar
    classificações inflaria a rede pelo número de especialidades de cada
    unidade.

    Mede SERVIÇO DECLARADO, não habilitação de CEO: a habilitação não é
    relacionada a estabelecimento em tabela-fato nenhuma do CNES.
    """
    if not mun_total or mun_com_esp_bucal is None:
        return None
    return round(mun_com_esp_bucal / mun_total, 4)


def icsb(mun_com_esb, mun_total):
    """
    Índice de Cobertura por Saúde Bucal na atenção primária. Fração dos
    municípios da UF com ao menos uma equipe de saúde bucal (eSB) cadastrada no
    CNES. 0 a 1, MAIOR é melhor.

    Mede PORTA DE ENTRADA, que é coisa diferente de força de trabalho: um
    município pode ter cirurgião-dentista sem ter equipe de saúde bucal na
    atenção primária — consultório com vínculo SUS, ou dentista lotado em outro
    ponto da rede. O ICAB conta profissional; este conta estrutura da atenção
    básica.

    Sai NULO, e não zero, quando a tabela de domínio do CNES não pôde ser lida:
    sem ela não há como saber quais códigos de equipe são de saúde bucal, e zero
    afirmaria que o país inteiro não tem nenhuma.
    """
    if not mun_total or mun_com_esb is None:
        return None
    return round(mun_com_esb / mun_total, 4)


def por_100k(quantidade, populacao, casas=1):
    """Densidade por 100 mil habitantes. None se faltar qualquer um dos dois."""
    if quantidade is None or not populacao:
        return None
    return round(100_000 * quantidade / populacao, casas)


# --------------------------------------------------------------------------- #
# PORTÃO GO — autoteste contra UF sintética
# --------------------------------------------------------------------------- #

# UF sintética. Os números foram escolhidos para que cada valor esperado seja
# verificável de cabeça, sem rodar o código:
#   ICT  = ½·(600/1000) + ½·(1 − 5/25)  = 0,30 + 0,40 = 0,700
#   E    = 0,300
#   Q    = média[(3−1)/4 ×3]            = 0,500
#   V    = 500/1000                     = 0,500
#   IAF  = 100·(0,5 + 0,5 + 0,3)/3      = 43,3
#   HHI  = 0,4² + 0,3² + 0,2² + 0,1²    = 0,300
#   CR2  = (400+300)/1000               = 0,700
#   ICAB = 20/25                        = 0,800
#   ICRE = 9/25                         = 0,360
#   ICSB = 12/25                        = 0,480
#   dentistas/100k = 100000·40/500000   = 8,0
SINTETICA = {
    "uf": "ZZ",
    "vagas_total": 1000,
    "vagas_capital": 600,
    "municipios_total": 25,
    "municipios_oferta": 5,
    "populacao": 500_000,
    "CC": 3.0,
    "ENADE": 3.0,
    "IDD": 3.0,
    "vagas_avaliadas": 500,
    "vagas_por_ies": {"A": 400, "B": 300, "C": 200, "D": 100},
    "vagas_por_mantenedora": {"Grupo1": 500, "Grupo2": 500},
    "municipios_com_dentista": 20,
    "municipios_com_esp_bucal": 9,
    "municipios_com_esb": 12,
    "dentistas_sus": 40,
}

CANONICO = {
    "ICT": 0.700,
    "E": 0.300,
    "IAF": 43.3,
    "HHI": 0.300,
    "HHI_mantenedora": 0.500,
    "CR2": 0.700,
    "CR10": 1.000,
    "ICAB": 0.800,
    "ICRE": 0.360,
    "ICSB": 0.480,
    "dentistas_por_100k": 8.0,
}
TOLERANCIA = 0.001


def _calcular_sintetica():
    d = SINTETICA
    v_ict = ict(d["vagas_capital"], d["vagas_total"],
                d["municipios_oferta"], d["municipios_total"])
    return {
        "ICT": round(v_ict, 3),
        "E": round(equidade(v_ict), 3),
        "IAF": iaf(d["CC"], d["ENADE"], d["IDD"],
                   d["vagas_avaliadas"], d["vagas_total"], v_ict),
        "HHI": hhi(d["vagas_por_ies"]),
        "HHI_mantenedora": hhi(d["vagas_por_mantenedora"]),
        "CR2": cr(d["vagas_por_ies"], 2),
        "CR10": cr(d["vagas_por_ies"], 10),
        "ICAB": icab(d["municipios_com_dentista"], d["municipios_total"]),
        "ICRE": icre(d["municipios_com_esp_bucal"], d["municipios_total"]),
        "ICSB": icsb(d["municipios_com_esb"], d["municipios_total"]),
        "dentistas_por_100k": por_100k(d["dentistas_sus"], d["populacao"]),
    }


def _testar_ausencias():
    """
    Segunda metade do portão: ausência tem de propagar como None, nunca como 0
    — e zero medido tem de continuar zero, nunca virar None.

    As duas direções importam. Sem a primeira, uma UF sem CPC apareceria com
    IAF 0, indistinguível de uma UF avaliada e péssima. Sem a segunda, as 27 UFs
    sem nenhuma vaga EaD apareceriam como "sem dados", e o fato estrutural do
    curso — não existe Odontologia a distância no Censo 2024 — sumiria da tela
    disfarçado de lacuna.
    """
    casos = [
        ("IAF sem nenhum conceito",
         iaf(None, None, None, 500, 1000, 0.5), None),
        ("IAF sem vagas avaliadas",
         iaf(3.0, 3.0, 3.0, None, 1000, 0.5), None),
        ("IAF com ICT ausente",
         iaf(3.0, 3.0, 3.0, 500, 1000, None), None),
        ("Q ignora conceito ausente, não zera",
         q_qualidade(3.0, 3.0, None), 0.5),
        ("ICT sem municípios com oferta",
         ict(600, 1000, None, 25), None),
        ("ICAB sem contagem apurada",
         icab(None, 25), None),
        ("ICRE sem contagem apurada",
         icre(None, 25), None),
        ("ICSB sem domínio de equipe lido",
         icsb(None, 25), None),
        ("ICRE com contagem zero é 0, não None",
         icre(0, 25), 0.0),
        ("ICSB com contagem zero é 0, não None",
         icsb(0, 25), 0.0),
        ("densidade com quantidade zero é 0, não None",
         por_100k(0, 500_000), 0.0),
        ("densidade sem população é None",
         por_100k(40, None), None),
        ("densidade sem quantidade apurada é None",
         por_100k(None, 500_000), None),
        ("HHI sem agentes",
         hhi({}), None),
        ("HHI com total zero",
         hhi({"A": 0}), None),
    ]
    ok = True
    print("\n=== AUSÊNCIAS (None nunca vira 0, zero medido nunca vira None) ===")
    for rotulo, obtido, esperado in casos:
        passou = obtido == esperado
        ok = ok and passou
        print(f"  {'OK    ' if passou else 'FALHOU'}  {rotulo}: "
              f"obtido={obtido!r} esperado={esperado!r}")
    return ok


def autoteste():
    calculado = _calcular_sintetica()
    ok = True
    print("=== PORTÃO GO — UF sintética ===")
    for nome, esperado in CANONICO.items():
        obtido = calculado[nome]
        passou = obtido is not None and abs(obtido - esperado) <= TOLERANCIA
        ok = ok and passou
        print(f"  {'OK    ' if passou else 'FALHOU'}  {nome}: "
              f"calculado={obtido}  esperado={esperado}")

    ok = _testar_ausencias() and ok

    if ok:
        print("\n[PASSOU] Fórmulas conferem. Pode prosseguir.")
        return 0
    print("\n[FALHOU] Portão GO reprovou. Corrigir antes de publicar.", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- #
# APLICAÇÃO SOBRE OS DADOS REAIS
# --------------------------------------------------------------------------- #

def _num(valor):
    """Texto -> float, com vazio virando None. Vírgula decimal aceita."""
    if valor is None:
        return None
    texto = str(valor).strip().replace(",", ".")
    if texto == "" or texto.upper() in {"NA", "N/A", "-", "NULL", "NONE"}:
        return None
    try:
        return float(texto)
    except ValueError:
        return None


def carregar_qualidade(caminho):
    """
    CSV com UF, CC, ENADE, IDD, vagas_avaliadas.
    Célula vazia = sem dado, e continua sem dado até a saída.
    """
    qualidade = {}
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            uf = (linha.get("UF") or "").strip().upper()
            if not uf:
                continue
            qualidade[uf] = {k: _num(linha.get(k))
                             for k in ("CC", "ENADE", "IDD", "vagas_avaliadas")}
    return qualidade


def aplicar(ufs, qualidade=None):
    """
    Acrescenta os índices a cada UF do dicionário, in place. Devolve-o.

    O componente Q do IAF vem de `CPC` — a média ponderada da FAIXA do CPC,
    escala 1 a 5 — e de mais nada. `ENADE_cont` e `IDD` estão no dado e são
    publicados como indicadores próprios, mas não entram no Q: ambos vêm em
    escala contínua 0 a 5, e (v−1)/4 sobre um contínuo abaixo de 1 devolve
    componente negativo. Misturar as duas escalas dentro da mesma média produz
    um índice que ninguém consegue reproduzir a partir dos números publicados.
    """
    qualidade = qualidade or {}
    for sigla, d in ufs.items():
        q = qualidade.get(sigla, {})
        v_ict = ict(d.get("vagas_capital"), d.get("vagas_total"),
                    d.get("municipios_oferta"), d.get("municipios_total"))
        d["ICT"] = None if v_ict is None else round(v_ict, 4)
        e = equidade(v_ict)
        d["E"] = None if e is None else round(e, 4)
        for chave in ("CPC", "ENADE_cont", "IDD", "vagas_avaliadas"):
            if d.get(chave) is None:
                d[chave] = q.get(chave)
        d["IAF"] = iaf(d.get("CPC"), None, None,
                       d.get("vagas_avaliadas"), d.get("vagas_total"), v_ict)
        d["ICAB"] = icab(d.get("municipios_com_dentista"),
                         d.get("municipios_total"))
        d["ICRE"] = icre(d.get("municipios_com_esp_bucal"),
                         d.get("municipios_total"))
        d["ICSB"] = icsb(d.get("municipios_com_esb"), d.get("municipios_total"))
    return ufs


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--autoteste", action="store_true",
                   help="roda o portão GO e sai com 0 (passou) ou 1 (falhou)")
    p.add_argument("--dados", help="JSON com {'ufs': {...}}")
    p.add_argument("--qualidade", help="CSV de qualidade por UF")
    p.add_argument("--saida", help="JSON de saída")
    args = p.parse_args()

    if args.autoteste or not args.dados:
        sys.exit(autoteste())

    with open(args.dados, encoding="utf-8") as f:
        dados = json.load(f)
    qualidade = carregar_qualidade(args.qualidade) if args.qualidade else {}
    aplicar(dados["ufs"], qualidade)

    saida = args.saida or args.dados
    with open(saida, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    print(f"[INDICES] {len(dados['ufs'])} UFs -> {saida}")


if __name__ == "__main__":
    main()
