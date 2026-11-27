"""
etl/serie.py
Série histórica: compara edições do Censo da Educação Superior.

Uso:
    python etl/serie.py --anos 2023 2024
    python etl/serie.py --anos 2021 2022 2023 2024

Saída: data/serie.json  (Brasil e por UF, só com campos comparáveis)

POR QUE DÁ PARA VOLTAR NO TEMPO SEM GAMBIARRA
----------------------------------------------
O INEP reclassificou as edições antigas na CINE e as republicou, então
`NO_CINE_ROTULO` existe em todas — e o match EXATO de rótulo, que é a regra do
projeto inteiro, vale para trás sem adaptação.

Em Odontologia o match exato não muda o resultado da edição de 2024: medido,
`Odontologia` é o único rótulo do Censo que contém "odont". Isso vale para uma
edição, não para a série: o rótulo vizinho que hoje não existe pode ter existido
antes ou passar a existir depois, e a série é justamente onde uma troca de
critério entre anos viraria "crescimento" na tela. O critério é o mesmo em todos
os anos, por isso.

O TOTAL NACIONAL DE IES NÃO É A SOMA DAS UFs
---------------------------------------------
Uma instituição que oferta em três estados aparece nas três contagens
estaduais, e somá-las devolve mais IES do que existem. A home publica o total
DISTINTO, lido do mesmo arquivo; duas páginas do mesmo site com números
diferentes para a mesma coisa é pior que um número errado sozinho. O erro é
herdado: no observatório de Fonoaudiologia a linha "Brasil" da série somava
`n_ies` por UF e publicava 3.024 onde havia 1.386.

Por isso o conjunto nacional de códigos de IES é contado durante a LEITURA,
quando os códigos ainda estão à mão, e guardado no cache do ano. Os demais
campos somam sem problema: município pertence a uma UF só.

POUCOS CAMPOS, DE PROPÓSITO
---------------------------
A série guarda apenas o que é comparável entre edições. Campos de perfil e
qualidade mudam de definição, de cobertura e até de existência entre anos; um
gráfico que os empilha lado a lado descreve mudança de metodologia como se
fosse mudança do mundo. O que sobra — vagas, matrículas, concluintes,
municípios com oferta, polos, IES — mantém o mesmo significado.

CADA ANO É LIDO E DESCARTADO
-----------------------------
O ZIP de cada edição é lido remotamente por intervalo de bytes, agregado, e o
agregado (poucos KB) é o que fica em disco. Guardar os cadastros completos de
quatro edições custaria mais de um giga para produzir um arquivo de 60 KB.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extrair_censo import (codificacao_do_membro,  # noqa: E402
                           esquema_do_zip, normalizar, url_do_censo,
                           urls_do_censo)
from rede import ZipRemotoHTTP, sondar  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DADOS = REPO / "etl" / "dados"
DATA = REPO / "data"

DIM_PRESENCIAL, DIM_POLO, DIM_SEDE = "1", "2", "3"

# Campos que significam a mesma coisa em todas as edições.
CAMPOS = [
    "vagas_presencial", "vagas_ead", "vagas_semipresencial",
    "vagas_em_extincao", "vagas_total",
    "n_cursos_presencial", "n_ies",
    "municipios_oferta", "ead_polos_registros", "ead_polos_municipios",
    "matriculas", "concluintes",
]

# O QUE CONTINUA COMPARAVEL DEPOIS DE 2025, E O QUE NAO
#
# O Censo 2025 registra o formato de oferta numa variavel que MUDOU DE SENTIDO
# — o dicionario do INEP diz que ate 2024 ela representa a modalidade de ensino
# e a partir de 2025 o formato de oferta, conforme o Decreto 12.456/2025 — e
# ganhou a categoria nova 3, semipresencial.
#
# NESTE CURSO A OFERTA A DISTANCIA NAO FOI ENCERRADA, e por isso `vagas_ead`
# continua publicado, medido, em vez de virar nulo. A nota de nao
# comparabilidade entre 2024 e 2025 fica na pagina: o que a variavel nomeia
# mudou, mesmo onde a oferta ficou de pe.
#
# `vagas_total` e a capacidade ATIVA, a mesma definicao que a pagina publica; a
# parte encerrada ou em extincao sai em `vagas_em_extincao`, ao lado.
#
# A constante abaixo existe vazia de proposito, e e APLICADA: se um dia a
# categoria for encerrada tambem aqui, basta nomea-la — e a guarda junto dela
# derruba a geracao se alguem a declarar encerrada enquanto a fonte ainda
# traz vaga ativa.
CAMPOS_SEM_SENTIDO_APOS_2024 = {}

csv.field_size_limit(1 << 24)


def inteiro(valor):
    texto = (valor or "").strip()
    return int(texto) if texto.lstrip("-").isdigit() else 0


def _agregar_normalizado(z, ano, rotulo, alvo, alvo_ies):
    """
    Agrega uma edição do esquema normalizado para a série.

    Os polos NÃO são lidos aqui. Eles moram na tabela de local de oferta, que
    tem 1,6 GB, e a série leria essa tabela inteira para extrair duas
    contagens. Em vez disso, usa-se a fatia que o `extrair_censo.py` já grava
    quando o pipeline roda aquela edição; se ela não existir, os dois campos
    saem NULOS — "não medido nesta passada" é verdade, zero não é.
    """
    enc = codificacao_do_membro(z, alvo)
    uf_sede = {}
    with z.membro_arquivo(alvo_ies,
                          encoding=codificacao_do_membro(z, alvo_ies)) as f:
        for linha in csv.DictReader(f, delimiter=";"):
            codigo = (linha.get("CO_IES") or "").strip()
            sigla = (linha.get("SG_UF_IES") or "").strip().upper()
            if codigo and sigla:
                uf_sede[codigo] = sigla

    alvo_norm = normalizar(rotulo)
    ufs = defaultdict(lambda: {
        "vagas_presencial": 0, "vagas_semipresencial": 0,
        "vagas_ead_ativa": 0, "vagas_em_extincao": 0, "vagas_total": 0,
        "n_cursos_presencial": 0, "matriculas": 0, "concluintes": 0,
        "_ies": set(), "_mun_oferta": set(),
    })
    # CONTAGEM NACIONAL DE DISTINTOS, que não se obtém somando as UFs.
    # Ver o cabeçalho deste arquivo: somar conta cada instituição uma vez por
    # estado em que ela atua. O leitor do esquema novo mantém esse contrato —
    # devolvê-lo sem o bloco nacional faria a contagem distinta virar soma de
    # UFs outra vez, e em silêncio.
    ies_do_pais = set()
    lidas = casadas = 0
    print(f"[SERIE] {ano}: lendo {alvo.rsplit('/', 1)[-1]} (esquema normalizado)")
    with z.membro_arquivo(alvo, encoding=enc) as f:
        for linha in csv.DictReader(f, delimiter=";"):
            lidas += 1
            if normalizar(linha.get("NO_CINE_ROTULO")) != alvo_norm:
                continue
            casadas += 1
            cod_ies = (linha.get("CO_IES") or "").strip()
            sigla = (linha.get("SG_UF_LO") or "").strip().upper() or None
            if not sigla:
                sigla = uf_sede.get(cod_ies)
            if not sigla:
                continue
            formato = (linha.get("TP_MODALIDADE_ENSINO") or "").strip()
            situacao = (linha.get("TP_SITUACAO") or "").strip()
            vagas = inteiro(linha.get("QT_VAGA_TOTAL"))
            cod_mun = (linha.get("CO_MUNICIPIO_LO") or "").strip()

            d = ufs[sigla]
            d["_ies"].add(cod_ies)
            ies_do_pais.add(cod_ies)
            d["matriculas"] += inteiro(linha.get("QT_MATRICULA_TOTAL"))
            d["concluintes"] += inteiro(linha.get("QT_CONCLUINTE_TOTAL"))
            # Capacidade ATIVA, a mesma definicao que a pagina publica.
            #
            # Uma versao anterior somava tudo aqui, "para manter a definicao
            # das edicoes anteriores", e o resultado foi pior que o problema:
            # `vagas_total` passava a valer 575.449 na serie e 272.855 na
            # pagina. Mesmo nome, dois sentidos, dois artefatos — e nenhum
            # leitor tem como saber qual esta vendo.
            #
            # Agora e so a ativa nos dois lugares. Para 2021-2024 isso nao
            # muda nada, porque a fonte nao distinguia situacao; para 2025 a
            # parte encerrada aparece na sua propria linha, ao lado.
            if situacao == "1":
                d["vagas_total"] += vagas
            else:
                d["vagas_em_extincao"] += vagas
            if formato == "1":
                # ATIVO nos três, e não só nas vagas.
                #
                # Contar curso e município sem olhar a situação põe o
                # MESMO nome de campo com dois valores em dois artefatos
                # publicados do mesmo projeto — a série dizendo um número e a
                # página dizendo outro, sem nada explicando a diferença.
                # Exatamente o defeito que o comentário de `vagas_total`
                # acima descreve.
                #
                # Esta função só lê o esquema normalizado, de 2025 em
                # diante. As edições anteriores passam pelo outro leitor, que
                # não tem coluna de situação para olhar — nelas "curso
                # presencial" sempre significou o que o cadastro trazia. A
                # comparação continua valendo porque o que mudou foi a fonte
                # passar a declarar o que antes não declarava; o curso
                # encerrado de 2025 em diante conta em `n_cursos_em_extincao`,
                # na página.
                if situacao == "1":
                    d["n_cursos_presencial"] += 1
                    d["vagas_presencial"] += vagas
                    if cod_mun:
                        d["_mun_oferta"].add(cod_mun)
            elif formato == "3" and situacao == "1":
                d["vagas_semipresencial"] += vagas
                # O semipresencial OCUPA o municipio: exige presenca, mesmo
                # que parcial, e a página conta assim. Sem esta linha,
                # `municipios_oferta` da série fica abaixo do da página
                # sempre que houver município só com oferta semipresencial.
                # Nas edições anteriores a categoria não existia, então nada
                # muda lá.
                if cod_mun:
                    d["_mun_oferta"].add(cod_mun)
            elif formato == "2" and situacao == "1":
                d["vagas_ead_ativa"] += vagas

    polos_reg, polos_mun = _polos_da_fatia(ano, rotulo)

    # A capacidade a distância AINDA É MEDIDA, mesmo quando não é publicada.
    #
    # Medir e mascarar são passos separados de propósito: é a medição que
    # permite a guarda abaixo dizer se a decisão de não publicar continua
    # verdadeira nesta edição. Um `None` escrito à mão não sabe se o que ele
    # esconde é zero ou seiscentas vagas.
    medido_ead = sum(d["vagas_ead_ativa"] for d in ufs.values())
    for campo, motivo in CAMPOS_SEM_SENTIDO_APOS_2024.items():
        if campo == "vagas_ead" and medido_ead:
            raise SystemExit(
                f"[ERRO] `vagas_ead` está declarado sem sentido após 2024 "
                f"({motivo}), mas o Censo {ano} traz {medido_ead} vagas a "
                "distância EM ATIVIDADE neste curso.\n"
                "As duas afirmações não podem ser verdade ao mesmo tempo. Ou a "
                "categoria sobreviveu aqui — e então o campo volta a ser "
                "publicado, com nota de não comparabilidade —, ou a leitura "
                "está errada. Escolher em silêncio é que não serve.")

    # UF QUE SÓ TEM POLO TAMBÉM É UF.
    #
    # O conjunto `ufs` vem das linhas de CURSO; os polos vêm de outra tabela.
    # Uma UF pode ter polo e nenhum curso próprio, e montar a saída só sobre
    # `ufs` fazia esses polos desaparecerem da série — publicando um total
    # nacional de polos abaixo do da página, com o mesmo nome de campo e sem
    # explicação. A página não tem esse problema porque o conjunto de UFs dela
    # vem do IBGE.
    #
    # As quantidades de curso dessa UF ficam em ZERO, e zero aqui é medida: ela
    # foi lida e não tem curso.
    for sigla in set(polos_reg) | set(polos_mun):
        if sigla not in ufs:
            ufs[sigla]

    saida = {}
    for sigla, d in ufs.items():
        saida[sigla] = {
            "vagas_presencial": d["vagas_presencial"],
            # Medido acima; nulo aqui porque está em
            # CAMPOS_SEM_SENTIDO_APOS_2024, com o motivo escrito lá.
            "vagas_ead": (None if "vagas_ead" in CAMPOS_SEM_SENTIDO_APOS_2024
                          else d["vagas_ead_ativa"]),
            "vagas_semipresencial": d["vagas_semipresencial"],
            "vagas_em_extincao": d["vagas_em_extincao"],
            "vagas_total": d["vagas_total"],
            "n_cursos_presencial": d["n_cursos_presencial"],
            "n_ies": len(d["_ies"]),
            "municipios_oferta": len(d["_mun_oferta"]),
            "ead_polos_registros": polos_reg.get(sigla),
            "ead_polos_municipios": polos_mun.get(sigla),
            "matriculas": d["matriculas"],
            "concluintes": d["concluintes"],
        }
    print(f"[SERIE] {ano}: {lidas} linhas, {casadas} do rótulo exato, "
          f"{len(saida)} UFs, {len(ies_do_pais)} IES distintas no país")
    return {"ufs": saida, "brasil": {"n_ies": len(ies_do_pais)}}


def _polos_da_fatia(ano, rotulo):
    """
    Polos por UF, lidos da fatia local — ou vazios, se ela não existir.

    Vazio aqui vira `None` no agregado, não zero: a série não mede o que não
    leu, e "não medido" é diferente de "não há".
    """
    from extrair_censo import slug
    caminho = DADOS / f"local_oferta_{slug(rotulo)}_{ano}.csv"
    if not caminho.exists():
        print(f"[SERIE] {ano}: sem a fatia de local de oferta; polos ficam nulos")
        return {}, {}
    # Mesma regra do pipeline, e pelos mesmos motivos: polo de curso
    # PRESENCIAL nao e alcance de oferta a distancia, e polo de curso em
    # extinção é outra população — a rede que está sendo desmontada. Contar
    # as três juntas publica um alcance que não corresponde a rede nenhuma.
    # Quantos são de cada população é medido por repositório, no pipeline.
    curso = DADOS / f"curso_{slug(rotulo)}_{ano}.csv"
    formato, situacao = {}, {}
    if curso.exists():
        with open(curso, encoding="utf-8", newline="") as f:
            for linha in csv.DictReader(f, delimiter=";"):
                cod = (linha.get("CO_CURSO") or "").strip()
                formato[cod] = (linha.get("TP_MODALIDADE_ENSINO") or "").strip()
                situacao[cod] = (linha.get("TP_SITUACAO") or "").strip()

    registros, municipios = defaultdict(set), defaultdict(set)
    with open(caminho, encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            if (linha.get("IN_LOCAL_OFERTA_POLO") or "").strip() != "1":
                continue
            sigla = (linha.get("SG_UF_LO") or "").strip().upper()
            if not sigla:
                continue
            cod_curso = (linha.get("CO_CURSO") or "").strip()
            if formato.get(cod_curso) == "1":
                continue
            if situacao and situacao.get(cod_curso) != "1":
                continue
            # Mesma regra do pipeline: código vazio não é um local, e
            # colapsá-lo no conjunto inventaria um polo por UF. O município
            # continua contando, porque ele é conhecido.
            cod_local = (linha.get("CO_LOCAL_OFERTA") or "").strip()
            if cod_local:
                registros[sigla].add(cod_local)
            mun = (linha.get("CO_MUNICIPIO_LO") or "").strip()
            if mun:
                municipios[sigla].add(mun)
    return ({u: len(v) for u, v in registros.items()},
            {u: len(v) for u, v in municipios.items()})


def agregar_ano(ano, rotulo="Odontologia"):
    """Lê a edição `ano` remotamente e devolve o agregado por UF."""
    cache = DADOS / f"serie_{ano}.json"
    if cache.exists():
        print(f"[SERIE] {ano}: usando agregado em cache")
        guardado = json.loads(cache.read_text(encoding="utf-8"))
        # Formato antigo: o cache era o mapa de UFs direto, sem bloco nacional.
        # Aceitá-lo é preciso — apagar caches bons custaria uma releitura de
        # 400 MB por ano — mas ele não tem como informar o total distinto, e
        # nesse caso o total sai nulo em vez de sair errado.
        if "ufs" not in guardado:
            return {"ufs": guardado, "brasil": None}
        return guardado

    url, situacao = url_do_censo(ano)
    if url is None:
        tentados = ", ".join(urls_do_censo(ano))
        raise SystemExit(
            f"[ERRO] edição {ano} do Censo não encontrada"
            + (" (falha de rede, não ausência)" if situacao is None else "")
            + f". Tentei: {tentados}")
    existe, detalhe = sondar(url)
    if existe is False:
        print(f"[SERIE] {ano}: confirmadamente não publicado — ano pulado.")
        return None
    if existe is None:
        print(f"[SERIE] {ano}: INDETERMINADO ({detalhe}). "
              "Ano pulado sem afirmar que não existe.")
        return None

    z = ZipRemotoHTTP(url)
    alvo = z.localizar("cadastro_cursos", ".csv") or z.localizar("curso", ".csv")
    alvo_ies = z.localizar("_ies_", ".csv") or z.localizar("ies", ".csv")
    if not alvo or not alvo_ies:
        print(f"[SERIE] {ano}: membros esperados não encontrados no ZIP — pulado.")
        return None

    if esquema_do_zip(z) == "normalizado":
        agregado = _agregar_normalizado(z, ano, rotulo, alvo, alvo_ies)
        DADOS.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(agregado, ensure_ascii=False, indent=1),
                         encoding="utf-8")
        return agregado

    print(f"[SERIE] {ano}: lendo {alvo.rsplit('/', 1)[-1]} ...")

    uf_sede = {}
    with z.membro_arquivo(alvo_ies) as f:
        for linha in csv.DictReader(f, delimiter=";"):
            codigo = (linha.get("CO_IES") or "").strip()
            sigla = (linha.get("SG_UF_IES") or "").strip().upper()
            if codigo and sigla:
                uf_sede[codigo] = sigla

    alvo_norm = normalizar(rotulo)
    ies_do_pais = set()
    ufs = defaultdict(lambda: {
        "vagas_presencial": 0, "vagas_ead": 0, "n_cursos_presencial": 0,
        "ead_polos_registros": 0, "matriculas": 0, "concluintes": 0,
        "_ies": set(), "_mun_oferta": set(), "_mun_polo": set(),
    })
    lidas = casadas = 0

    with z.membro_arquivo(alvo) as f:
        for linha in csv.DictReader(f, delimiter=";"):
            lidas += 1
            if normalizar(linha.get("NO_CINE_ROTULO")) != alvo_norm:
                continue
            casadas += 1
            dimensao = (linha.get("TP_DIMENSAO") or "").strip()
            cod_ies = (linha.get("CO_IES") or "").strip()
            sigla = (linha.get("SG_UF") or "").strip().upper() or None
            vagas = inteiro(linha.get("QT_VG_TOTAL"))
            cod_mun = (linha.get("CO_MUNICIPIO") or "").strip()

            if dimensao == DIM_SEDE:
                sigla = uf_sede.get(cod_ies)
            if not sigla:
                continue
            ies_do_pais.add(cod_ies)
            d = ufs[sigla]
            d["_ies"].add(cod_ies)
            d["matriculas"] += inteiro(linha.get("QT_MAT"))
            d["concluintes"] += inteiro(linha.get("QT_CONC"))

            if dimensao == DIM_PRESENCIAL:
                d["vagas_presencial"] += vagas
                d["n_cursos_presencial"] += 1
                d["_mun_oferta"].add(cod_mun)
            elif dimensao == DIM_SEDE:
                d["vagas_ead"] += vagas
            elif dimensao == DIM_POLO:
                d["ead_polos_registros"] += 1
                d["_mun_polo"].add(cod_mun)

    saida = {}
    for sigla, d in ufs.items():
        saida[sigla] = {
            "vagas_presencial": d["vagas_presencial"],
            "vagas_ead": d["vagas_ead"],
            "vagas_total": d["vagas_presencial"] + d["vagas_ead"],
            "n_cursos_presencial": d["n_cursos_presencial"],
            "n_ies": len(d["_ies"]),
            "municipios_oferta": len(d["_mun_oferta"]),
            "ead_polos_registros": d["ead_polos_registros"],
            "ead_polos_municipios": len(d["_mun_polo"]),
            "matriculas": d["matriculas"],
            "concluintes": d["concluintes"],
        }

    agregado = {"ufs": saida, "brasil": {"n_ies": len(ies_do_pais)}}
    print(f"[SERIE] {ano}: {lidas} linhas, {casadas} do rótulo exato, "
          f"{len(saida)} UFs, {len(ies_do_pais)} IES distintas no país")
    DADOS.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(agregado, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    return agregado


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--anos", nargs="+", type=int, default=[2023, 2024])
    p.add_argument("--rotulo", default="Odontologia")
    p.add_argument("--saida", default=str(DATA / "serie.json"))
    args = p.parse_args()

    por_ano, nacional_por_ano = {}, {}
    for ano in sorted(args.anos):
        agregado = agregar_ano(ano, args.rotulo)
        if agregado and agregado["ufs"]:
            por_ano[str(ano)] = agregado["ufs"]
            nacional_por_ano[str(ano)] = agregado["brasil"]

    if len(por_ano) < 2:
        raise SystemExit(
            "[SERIE] menos de duas edições disponíveis; a série não é gerada. "
            "Uma série de um ponto não é série, e publicar uma seria sugerir "
            "comparação onde não há.")

    anos = sorted(por_ano)
    siglas = sorted({s for ano in anos for s in por_ano[ano]})

    ufs = {}
    for sigla in siglas:
        ufs[sigla] = {ano: por_ano[ano].get(sigla) for ano in anos}

    brasil = {}
    sem_total_distinto = []
    for ano in anos:
        # Soma que PRESERVA a ausencia.
        #
        # `sum(... or 0)` transforma None em zero, e zero afirma. Aqui isso
        # apagaria duas coisas de uma vez: a EaD de 2025, que e nula porque a
        # categoria acabou (e nao porque a oferta desabou), e o
        # semipresencial dos anos anteriores a 2025, que e nulo porque a
        # categoria nao existia. Um campo nulo em TODAS as UFs continua nulo
        # no Brasil.
        brasil[ano] = {}
        for campo in CAMPOS:
            valores = [(por_ano[ano].get(s) or {}).get(campo) for s in siglas]
            presentes = [v for v in valores if v is not None]
            brasil[ano][campo] = sum(presentes) if presentes else None
        # `n_ies` é o único campo em que a soma das UFs não é o total do país:
        # uma instituição que oferta em três estados entra em três contagens.
        # Vem do conjunto nacional, ou fica nulo — nunca a soma.
        distinto = (nacional_por_ano.get(ano) or {}).get("n_ies")
        brasil[ano]["n_ies"] = distinto
        if distinto is None:
            sem_total_distinto.append(ano)
    if sem_total_distinto:
        print(f"[SERIE] atenção: {', '.join(sem_total_distinto)} vieram de "
              "cache antigo, sem o conjunto nacional de IES. O total do país "
              "fica NULO nesses anos — a soma das UFs contaria em dobro quem "
              "oferta em mais de um estado. Apague etl/dados/serie_<ano>.json "
              "e rode de novo para preencher.")

    saida = {
        "metadados": {
            "fonte": "Censo da Educação Superior (INEP)",
            "curso": args.rotulo,
            "match": "igualdade sobre o rótulo CINE normalizado",
            "observacao": (
                "Somente campos com significado estável entre edições. Campos "
                "de perfil e qualidade mudam de definição entre anos e não "
                "entram na série."
            ),
            "n_ies_no_brasil": (
                "Instituições DISTINTAS no país, contadas sobre o conjunto "
                "nacional de códigos — não a soma das contagens estaduais, que "
                "conta em dobro quem oferta em mais de um estado."
            ),
            "gerado_em": date.today().isoformat(),
        },
        "anos": anos,
        "campos": CAMPOS,
        "brasil": brasil,
        "ufs": ufs,
    }
    Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.saida).write_text(json.dumps(saida, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    print(f"[SERIE] {len(anos)} edições ({', '.join(anos)}) -> {args.saida}")
    for campo in ("vagas_total", "matriculas", "concluintes"):
        antes, depois = brasil[anos[0]][campo], brasil[anos[-1]][campo]
        variacao = (depois - antes) / antes * 100 if antes else None
        marca = f"{variacao:+.1f}%" if variacao is not None else "—"
        print(f"[SERIE]   {campo}: {antes} -> {depois} ({marca})")


if __name__ == "__main__":
    main()
