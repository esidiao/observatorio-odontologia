"""
etl/consolidar.py
Junta as três fontes num único conjunto publicável e aplica os índices.

Uso:
    python etl/consolidar.py

Entrada:  data/bruto.json           (Censo, de ingestao.py)
          data/qualidade.json       (CPC, de extrair_cpc.py)
          data/cobertura_cnes.json  (CNES, de extrair_cnes.py)
Saída:    data/nacional.json
          data/municipios/<UF>.json
          data/_proveniencia.json

A ORDEM IMPORTA
---------------
`ingestao.py` produz `vagas_presencial` e `vagas_ead`; só depois disso faz
sentido calcular `pct_ead`, `HHI` sobre a capacidade total e o ICT. Rodar o
consolidador antes da ingestão grava `None` em cadeia sem reclamar de nada — o
arquivo fica com as chaves certas e os valores vazios, e nenhum teste de
integridade repara, porque as chaves existem.

FONTE AUSENTE NÃO VIRA ZERO
---------------------------
Se `cobertura_cnes.json` não existir, os campos de cobertura ficam `None` e o
conjunto sai marcado com a fonte faltando na proveniência. O que não acontece é
o ICAB virar 0,0 — isso afirmaria que nenhum município do país tem
cirurgião-dentista no SUS, uma afirmação forte sustentada por um arquivo que não foi lido.

MAS ZERO LIDO CONTINUA ZERO
---------------------------
O contrário também vale, e aqui é mais fácil de errar. Quando a fonte FOI lida
e o município não tem o serviço, o valor é 0 — medida, não lacuna. É por isso
que `juntar_cobertura` distingue "não há arquivo" de "há arquivo e o município
não aparece nele": no primeiro caso escreve None, no segundo escreve 0.
"""
import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from indices import aplicar, por_100k

REPO = Path(__file__).parent.parent
DATA = REPO / "data"

# O código IBGE começa com o código da UF, então dois dígitos bastam para saber
# a que estado um município pertence. O CNES usa os mesmos 6 dígitos, sem o
# dígito verificador que o Censo traz.
UF_POR_CODIGO = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP",
    "17": "TO", "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB",
    "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
    "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
    "51": "MT", "52": "GO", "53": "DF",
}

# Campos que cada fonte entrega, nomeados UMA vez. Repetir a lista no ramo do
# "sem fonte" foi o que fez um campo sumir do conjunto em vez de sair nulo, no
# projeto de Farmácia: o campo não existia, e campo ausente não aparece como
# "sem dados" — some da página inteira, sem alarme nenhum.
CAMPOS_CNES_UF = [
    "municipios_com_dentista", "dentistas_sus", "dentistas_por_100k",
    "municipios_com_esp_bucal", "estabelecimentos_esp_bucal",
    "municipios_com_esb", "equipes_esb_total",
    "municipios_com_lrpd", "estabelecimentos_lrpd",
]
CAMPOS_CNES_MUNICIPIO = [
    "dentistas_sus", "estabelecimentos_esp_bucal", "equipes_esb",
    "estabelecimentos_lrpd", "servicos_bucais",
]


def _ler(caminho, obrigatorio=True):
    caminho = Path(caminho)
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    if obrigatorio:
        raise SystemExit(f"[ERRO] {caminho} ausente — rode o passo anterior do pipeline.")
    print(f"[CONSOLIDAR] {caminho.name} ausente; os campos dessa fonte ficam nulos.")
    return None


def _nulos(destino, campos):
    for campo in campos:
        destino[campo] = None


def juntar_qualidade(ufs, qualidade):
    """Copia os indicadores de qualidade do CPC para cada UF."""
    campos = ("n_cursos_avaliados", "n_com_cpc", "n_com_idd",
              "concluintes_participantes", "CPC", "CPC_cont", "ENADE_cont",
              "IDD", "pct_doc_mestres", "pct_doc_doutores",
              "pct_doc_regime_integral", "dim_didatico_pedagogica",
              "dim_infraestrutura", "dim_oportunidade_formacao",
              "vagas_avaliadas")
    por_uf = (qualidade or {}).get("ufs", {})
    for sigla, d in ufs.items():
        q = por_uf.get(sigla, {})
        for campo in campos:
            d[campo] = q.get(campo)
        d["tem_avaliacao"] = bool(q)
    return ufs


def juntar_cobertura(ufs, municipios_por_uf, cobertura):
    """
    Agrega a cobertura do CNES por UF e anexa aos municípios.

    O CNES identifica município por código IBGE de 6 dígitos (sem o dígito
    verificador); o Censo usa 7. A junção é pelos 6 primeiros — não por nome,
    que tem grafia divergente entre as bases e homônimos entre UFs.

    As EQUIPES têm um terceiro estado, e ele precisa sobreviver até aqui: o
    extrator devolve `equipes_esb = None` em todo município quando a tabela de
    domínio do CNES não pôde ser lida, porque sem ela não há como saber quais
    códigos de equipe são de saúde bucal. Nesse caso a contagem por UF também é
    None. Somar Nones como se fossem zeros publicaria "nenhuma equipe no país"
    a partir de uma tabela de nomes que não abriu.
    """
    if not cobertura:
        for d in ufs.values():
            _nulos(d, CAMPOS_CNES_UF)
        for lista in municipios_por_uf.values():
            for m in lista:
                _nulos(m, CAMPOS_CNES_MUNICIPIO)
        return ufs, municipios_por_uf

    por_municipio = cobertura["municipios"]
    # O critério é o mesmo do extrator: sem códigos descobertos, não há medida
    # de equipe. Lê-lo dos metadados evita inferir a ausência de um agregado
    # vazio, que também aconteceria num país sem nenhuma equipe.
    _meta_cob = cobertura.get("metadados") or {}
    esb_medido = bool(_meta_cob.get("equipes_tipos_bucais")
                      or _meta_cob.get("equipes_subtipos_bucais"))

    com_dentista = defaultdict(int)
    com_esp = defaultdict(int)
    com_esb = defaultdict(int)
    com_lrpd = defaultdict(int)
    profissionais = defaultdict(int)
    estab_esp = defaultdict(int)
    estab_lrpd = defaultdict(int)
    equipes = defaultdict(int)

    for codigo, m in por_municipio.items():
        uf = UF_POR_CODIGO.get(str(codigo)[:2])
        if not uf:
            continue
        if m.get("dentistas_sus"):
            com_dentista[uf] += 1
            profissionais[uf] += m["dentistas_sus"]
        if m.get("estabelecimentos_esp_bucal"):
            com_esp[uf] += 1
            estab_esp[uf] += m["estabelecimentos_esp_bucal"]
        if m.get("estabelecimentos_lrpd"):
            com_lrpd[uf] += 1
            estab_lrpd[uf] += m["estabelecimentos_lrpd"]
        if esb_medido and m.get("equipes_esb"):
            com_esb[uf] += 1
            equipes[uf] += m["equipes_esb"]

    for sigla, d in ufs.items():
        # A fonte FOI lida: um estado que não aparece nos agregados tem zero,
        # não "sem dados". Zero aqui é medida.
        d["municipios_com_dentista"] = com_dentista.get(sigla, 0)
        d["dentistas_sus"] = profissionais.get(sigla, 0)
        d["dentistas_por_100k"] = por_100k(profissionais.get(sigla, 0),
                                           d.get("populacao"))
        d["municipios_com_esp_bucal"] = com_esp.get(sigla, 0)
        d["estabelecimentos_esp_bucal"] = estab_esp.get(sigla, 0)
        d["municipios_com_lrpd"] = com_lrpd.get(sigla, 0)
        d["estabelecimentos_lrpd"] = estab_lrpd.get(sigla, 0)
        d["municipios_com_esb"] = com_esb.get(sigla, 0) if esb_medido else None
        d["equipes_esb_total"] = equipes.get(sigla, 0) if esb_medido else None

    for uf, lista in municipios_por_uf.items():
        for m in lista:
            cnes = por_municipio.get(str(m["codigo"])[:6], {})
            m["dentistas_sus"] = cnes.get("dentistas_sus")
            m["estabelecimentos_esp_bucal"] = cnes.get("estabelecimentos_esp_bucal")
            m["estabelecimentos_lrpd"] = cnes.get("estabelecimentos_lrpd")
            m["equipes_esb"] = cnes.get("equipes_esb") if esb_medido else None
            m["servicos_bucais"] = cnes.get("servicos")

    return ufs, municipios_por_uf


def limitacoes(ufs, qualidade, cobertura):
    """
    O que não foi medido, escrito por extenso.

    Montada a partir do conjunto, não digitada: uma limitação digitada à mão
    envelhece calada — o texto continua afirmando o que era verdade na edição
    anterior enquanto o número já mudou.
    """
    itens = []

    sem_polo = sorted(s for s, d in ufs.items()
                      if not d.get("ead_polos_registros"))
    vagas_ead = sum(d.get("vagas_ead") or 0 for d in ufs.values())
    itens.append(
        f"Não existe oferta de Odontologia a distância no Censo 2024: as "
        f"{sum(d.get('n_cursos_presencial') or 0 for d in ufs.values())} "
        f"ofertas do recorte são presenciais, com {vagas_ead} vaga EaD e nenhum "
        f"polo em {len(sem_polo)} das {len(ufs)} UFs. Esse zero é MEDIDO — o "
        "Censo varreu as três camadas de TP_DIMENSAO e não encontrou linha "
        "alguma de EaD —, não é lacuna de apuração. Os indicadores de EaD "
        "continuam publicados no conjunto de dados, valendo zero, e o site "
        "declara a ausência numa seção própria em vez de espalhar colunas "
        "vazias pelas páginas.")

    itens.append(
        "Como pct_ead é constante em zero, ele fica FORA da matriz de "
        "correlação: variável sem variância não tem correlação fraca, tem "
        "correlação indefinida. Calculá-la assim mesmo devolveria ruído com "
        "aparência de resultado.")

    if qualidade:
        avaliadas = {s for s, d in ufs.items() if d.get("tem_avaliacao")}
        sem = sorted(set(ufs) - avaliadas)
        if sem:
            itens.append(
                f"UFs com oferta e sem nenhum curso no ciclo do CPC: "
                f"{', '.join(sem)}. Os indicadores de qualidade ficam nulos "
                "nelas, não zerados.")
        else:
            itens.append(
                f"Todas as {len(ufs)} UFs têm curso avaliado no ciclo do CPC "
                f"{qualidade['metadados'].get('ciclo')}. A cobertura de "
                "indicador, porém, não é total: parte dos cursos avaliados sai "
                "sem CPC contínuo e sem IDD, e esses ficam fora das médias "
                "ponderadas em vez de entrarem como zero.")
    else:
        itens.append("O ciclo do CPC não foi lido; todos os indicadores de "
                     "qualidade estão nulos.")

    if cobertura:
        meta = cobertura.get("metadados") or {}
        itens.append(
            "A rede especializada é medida pelo SERVIÇO 114 declarado no CNES "
            "(atenção especializada à saúde bucal), não pela habilitação de "
            "Centro de Especialidades Odontológicas: os códigos de habilitação "
            "existem na tabela de domínio, e nenhuma tabela-fato desta base os "
            "relaciona a estabelecimento. Por isso o indicador não se chama "
            "CEO — chamá-lo assim afirmaria uma habilitação que não foi lida.")
        itens.append(
            "As vinte classificações do serviço 114 são especialidades do mesmo "
            "serviço (endodontia, periodontia, cirurgia, atendimento a pessoa "
            "com deficiência e outras), e não foram agrupadas em subgrupos: o "
            "estabelecimento é contado uma vez, e as classificações que ele "
            "declara aparecem no detalhe de cada município.")
        itens.append(
            "O laboratório de prótese dentária (serviço 157) é publicado como "
            "contagem própria e NÃO entra em índice nem se soma à rede "
            "especializada: é outra política, com outra unidade de conta.")
        if not (meta.get("equipes_tipos_bucais")
                or meta.get("equipes_subtipos_bucais")):
            itens.append(
                "As equipes de saúde bucal NÃO foram medidas nesta execução: a "
                "tabela de domínio de tipos de equipe do CNES não pôde ser "
                "lida, e sem ela não há como saber quais códigos são de saúde "
                "bucal. O ICSB está nulo, não zerado.")
        else:
            com_dentista = sum(d.get("municipios_com_dentista") or 0
                               for d in ufs.values())
            com_esb = sum(d.get("municipios_com_esb") or 0 for d in ufs.values())
            com_esp = sum(d.get("municipios_com_esp_bucal") or 0
                          for d in ufs.values())
            total_mun = sum(d.get("municipios_total") or 0 for d in ufs.values())
            itens.append(
                f"As três coberturas medem alcances muito diferentes: "
                f"{com_dentista} dos {total_mun} municípios têm cirurgião-"
                f"dentista vinculado ao SUS, {com_esb} têm equipe de saúde "
                f"bucal na atenção primária e {com_esp} têm serviço "
                "especializado. A distância entre o primeiro e o último número "
                "é o assunto: a atenção especializada é regionalizada por "
                "desenho, e sua escassez municipal não é falha de cadastro.")
    else:
        itens.append("A base do CNES não foi lida; a cobertura assistencial "
                     "está nula, não zerada.")

    return itens


def proveniencia(bruto, qualidade, cobertura, ufs):
    fontes = {
        "censo": {
            "presente": True,
            **(bruto["metadados"].get("proveniencia_censo") or {}),
        },
        "cpc": ({"presente": True, **qualidade["metadados"]} if qualidade
                else {"presente": False,
                      "motivo": "data/qualidade.json não gerado"}),
        "cnes": ({"presente": True, **cobertura["metadados"]} if cobertura
                 else {"presente": False,
                       "motivo": "data/cobertura_cnes.json não gerado"}),
        # As fontes do IBGE também declaram `presente`, como as demais. Sem o
        # campo, ficariam de fora de qualquer varredura que pergunte "quais
        # fontes faltaram?" — e uma fonte que nunca aparece na resposta é uma
        # fonte que ninguém percebe ter sumido.
        "ibge_municipios": {"presente": True,
                            **(bruto["metadados"].get("municipios_ibge") or {})},
        "ibge_populacao": {"presente": True,
                           **(bruto["metadados"].get("populacao_ibge") or {})},
    }
    return {
        "gerado_em": date.today().isoformat(),
        "fontes": fontes,
        "limitacoes_conhecidas": limitacoes(ufs, qualidade, cobertura),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bruto", default=str(DATA / "bruto.json"))
    p.add_argument("--qualidade", default=str(DATA / "qualidade.json"))
    p.add_argument("--cobertura", default=str(DATA / "cobertura_cnes.json"))
    p.add_argument("--saida", default=str(DATA / "nacional.json"))
    args = p.parse_args()

    bruto = _ler(args.bruto)
    qualidade = _ler(args.qualidade, obrigatorio=False)
    cobertura = _ler(args.cobertura, obrigatorio=False)

    ufs = bruto["ufs"]
    municipios = bruto["municipios"]

    juntar_qualidade(ufs, qualidade)
    juntar_cobertura(ufs, municipios, cobertura)
    aplicar(ufs)

    metadados = dict(bruto["metadados"])
    metadados["proveniencia"] = proveniencia(bruto, qualidade, cobertura, ufs)
    nacional = {"metadados": metadados, "ufs": ufs}

    Path(args.saida).write_text(
        json.dumps(nacional, ensure_ascii=False, indent=1), encoding="utf-8")
    campos = len(next(iter(ufs.values())))
    print(f"[CONSOLIDAR] {len(ufs)} UFs, {campos} campos por UF -> {args.saida}")

    destino_mun = DATA / "municipios"
    destino_mun.mkdir(parents=True, exist_ok=True)
    total = 0
    for uf, lista in municipios.items():
        (destino_mun / f"{uf}.json").write_text(
            json.dumps({"uf": uf, "municipios": lista},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        total += len(lista)
    print(f"[CONSOLIDAR] {total} municípios em {len(municipios)} arquivos "
          f"-> {destino_mun}")

    (DATA / "_proveniencia.json").write_text(
        json.dumps(metadados["proveniencia"], ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("[CONSOLIDAR] proveniência -> data/_proveniencia.json")

    faltando = [n for n, f in metadados["proveniencia"]["fontes"].items()
                if isinstance(f, dict) and f.get("presente") is False]
    if faltando:
        print(f"[CONSOLIDAR] ATENÇÃO: fontes ausentes: {faltando}. "
              "Os indicadores correspondentes estão nulos, não zerados.")


if __name__ == "__main__":
    main()
