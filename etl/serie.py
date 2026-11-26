"""
etl/serie.py
Série histórica: compara edições do Censo da Educação Superior.

Uso:
    python etl/serie.py --anos 2023 2024
    python etl/serie.py --anos 2021 2022 2023 2024
    python etl/serie.py --anos 2015 ... 2025 --origem-local <dir 2015-2024> <dir 2025>

EDIÇÕES LOCAIS E O CENSO 2025
-----------------------------
`--origem-local` aceita uma ou mais pastas com pares
MICRODADOS_CADASTRO_CURSOS_<ano>.CSV + MICRODADOS_ED_SUP_IES_<ano>.CSV. De 2015
a 2024 são os CSV originais do INEP (mesmo layout, com TP_DIMENSAO); 2025 só
existe traduzido por `etl/censo2025_adaptador.py`. Se o mesmo ano aparecer em
mais de uma pasta o script ABORTA: um par de outro curso com o mesmo nome (a
pasta compartilhada da família tem um 2025 filtrado para Farmácia) seria lido
como "Odontologia sem nenhuma linha" e o ano sumiria da série em silêncio.

A definição de cada campo é a mesma de `ingestao.py`, e o ponto mais recente
da série tem de coincidir com `data/nacional.json` — tests/test_validacao.py
confere isso UF a UF. A semipresencial (TP_MODALIDADE_ENSINO = 3) só existe a
partir de 2025: antes disso `vagas_semipresencial` é NULO (categoria
inexistente), não zero. E como a definição de modalidade mudou em 2025, a
fronteira 2024→2025 não é estritamente comparável na partição entre
modalidades — em Odontologia, porém, as camadas não presenciais valem zero dos
dois lados.

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

from extrair_censo import (ZipLocal, normalizar, url_do_censo,  # noqa: E402
                           urls_do_censo)
from ingestao import (DIM_PRESENCIAL, DIM_POLO, DIM_SEDE,  # noqa: E402
                      MOD_SEMIPRESENCIAL, MODALIDADES_POR_DIMENSAO, inteiro)
from rede import ZipRemotoHTTP, sondar  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DADOS = REPO / "etl" / "dados"
DATA = REPO / "data"

# Primeiro Censo com a modalidade semipresencial (Decreto 12.456/2025).
ANO_PRIMEIRO_SEMIPRESENCIAL = 2025
# Versão do agregado em cache: caches de uma versão anterior não têm os campos
# novos e são recalculados em vez de lidos.
VERSAO_CACHE = 2

# Campos que significam a mesma coisa em todas as edições.
CAMPOS = [
    "vagas_presencial", "vagas_ead", "vagas_semipresencial", "vagas_total",
    "n_cursos_presencial", "n_ies",
    "municipios_oferta", "ead_polos_registros", "ead_polos_municipios",
    "matriculas", "concluintes",
]

csv.field_size_limit(1 << 24)


def _pasta_do_ano(ano, pastas):
    """A única pasta que tem o par daquele ano, ou None. Duas é erro."""
    achadas = [p for p in pastas
               if (Path(p) / f"MICRODADOS_CADASTRO_CURSOS_{ano}.CSV").exists()]
    if len(achadas) > 1:
        raise SystemExit(
            f"[SERIE] {ano} aparece em mais de uma pasta: {achadas}. Escolha "
            "uma — um par de outro curso com o mesmo nome esvaziaria o ano "
            "em silêncio.")
    return achadas[0] if achadas else None


def _abrir_local(ano, pasta):
    local = ZipLocal(pasta, ano)
    cursos = local.membro("MICRODADOS_CADASTRO_CURSOS")
    ies = local.membro("MICRODADOS_ED_SUP_IES")
    print(f"[SERIE] {ano}: lendo {cursos} ...")
    return lambda: local.abrir(cursos), lambda: local.abrir(ies)


def _abrir_remoto(ano):
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

    print(f"[SERIE] {ano}: lendo {alvo.rsplit('/', 1)[-1]} ...")
    return lambda: z.membro_arquivo(alvo), lambda: z.membro_arquivo(alvo_ies)


def agregar_ano(ano, rotulo="Odontologia", pastas=()):
    """Lê a edição `ano` (local ou remota) e devolve o agregado por UF."""
    cache = DADOS / f"serie_{ano}.json"
    if cache.exists():
        guardado = json.loads(cache.read_text(encoding="utf-8"))
        if (guardado.get("versao") == VERSAO_CACHE
                and guardado.get("rotulo") == rotulo):
            print(f"[SERIE] {ano}: usando agregado em cache")
            return guardado
        print(f"[SERIE] {ano}: cache de versão anterior — recalculando")

    pasta = _pasta_do_ano(ano, pastas)
    abertos = _abrir_local(ano, pasta) if pasta else _abrir_remoto(ano)
    if abertos is None:
        return None
    abrir_cursos, abrir_ies = abertos

    uf_sede = {}
    with abrir_ies() as f:
        for linha in csv.DictReader(f, delimiter=";"):
            codigo = (linha.get("CO_IES") or "").strip()
            sigla = (linha.get("SG_UF_IES") or "").strip().upper()
            if codigo and sigla:
                uf_sede[codigo] = sigla

    alvo_norm = normalizar(rotulo)
    ies_do_pais = set()
    ufs = defaultdict(lambda: {
        "vagas_presencial": 0, "vagas_ead": 0, "vagas_semipresencial": 0,
        "n_cursos_presencial": 0,
        "ead_polos_registros": 0, "matriculas": 0, "concluintes": 0,
        "_ies": set(), "_mun_oferta": set(), "_mun_polo": set(),
    })
    lidas = casadas = 0

    modalidades_vistas = set()
    with abrir_cursos() as f:
        for linha in csv.DictReader(f, delimiter=";"):
            lidas += 1
            if normalizar(linha.get("NO_CINE_ROTULO")) != alvo_norm:
                continue
            casadas += 1
            dimensao = (linha.get("TP_DIMENSAO") or "").strip()
            modalidade = (linha.get("TP_MODALIDADE_ENSINO") or "").strip()
            esperadas = MODALIDADES_POR_DIMENSAO.get(dimensao)
            if esperadas is not None and modalidade not in esperadas:
                raise SystemExit(
                    f"[SERIE] {ano}: TP_MODALIDADE_ENSINO={modalidade!r} em "
                    f"TP_DIMENSAO={dimensao}. Modalidade nova ou incoerente "
                    "não pode cair calada num ramo.")
            modalidades_vistas.add(modalidade)
            semi = modalidade == MOD_SEMIPRESENCIAL
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
            elif dimensao == DIM_SEDE and semi:
                d["vagas_semipresencial"] += vagas
            elif dimensao == DIM_SEDE:
                d["vagas_ead"] += vagas
            elif dimensao == DIM_POLO and not semi:
                d["ead_polos_registros"] += 1
                d["_mun_polo"].add(cod_mun)

    if (ano < ANO_PRIMEIRO_SEMIPRESENCIAL
            and MOD_SEMIPRESENCIAL in modalidades_vistas):
        raise SystemExit(
            f"[SERIE] {ano}: modalidade 3 antes de {ANO_PRIMEIRO_SEMIPRESENCIAL}. "
            "Em 2015–2024 só existiram os códigos 1 e 2 — confira a fonte.")
    tem_semi = ano >= ANO_PRIMEIRO_SEMIPRESENCIAL

    saida = {}
    for sigla, d in ufs.items():
        saida[sigla] = {
            "vagas_presencial": d["vagas_presencial"],
            "vagas_ead": d["vagas_ead"],
            # Antes de 2025 a categoria não existia: nulo, não zero.
            "vagas_semipresencial": (d["vagas_semipresencial"]
                                     if tem_semi else None),
            "vagas_total": (d["vagas_presencial"] + d["vagas_ead"]
                            + d["vagas_semipresencial"]),
            "n_cursos_presencial": d["n_cursos_presencial"],
            "n_ies": len(d["_ies"]),
            "municipios_oferta": len(d["_mun_oferta"]),
            "ead_polos_registros": d["ead_polos_registros"],
            "ead_polos_municipios": len(d["_mun_polo"]),
            "matriculas": d["matriculas"],
            "concluintes": d["concluintes"],
        }

    agregado = {"versao": VERSAO_CACHE, "rotulo": rotulo, "ufs": saida,
                "brasil": {"n_ies": len(ies_do_pais)}}
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
    p.add_argument("--origem-local", nargs="+", default=[], metavar="DIR",
                   help="pastas com pares MICRODADOS_CADASTRO_CURSOS_<ano>.CSV "
                        "+ MICRODADOS_ED_SUP_IES_<ano>.CSV; anos ausentes "
                        "nelas são lidos do ZIP remoto")
    p.add_argument("--saida", default=str(DATA / "serie.json"))
    args = p.parse_args()

    por_ano, nacional_por_ano = {}, {}
    for ano in sorted(args.anos):
        agregado = agregar_ano(ano, args.rotulo, args.origem_local)
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
        brasil[ano] = {}
        for campo in CAMPOS:
            valores = [(por_ano[ano].get(s) or {}).get(campo) for s in siglas]
            # Campo nulo em todas as UFs (a semipresencial antes de 2025) é
            # categoria inexistente no ano: o total do país também é nulo.
            brasil[ano][campo] = (None if all(v is None for v in valores)
                                  else sum(v or 0 for v in valores))
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
            # UF sem nenhuma linha do rótulo numa edição que FOI lida. Fica
            # nula, não zero: o caso medido (RR em 2020) é o curso 96461, de
            # Boa Vista, presente em 2019 e 2021 com o mesmo código e ausente
            # do arquivo de 2020 — lacuna de registro na fonte, não um ano sem
            # curso. Zero afirmaria o fechamento de um curso que continuou.
            "ufs_sem_linha_no_ano": {
                ano: sorted(s for s in siglas if por_ano[ano].get(s) is None)
                for ano in anos
                if any(por_ano[ano].get(s) is None for s in siglas)},
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
