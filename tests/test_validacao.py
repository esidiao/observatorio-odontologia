"""
tests/test_validacao.py
Integridade do conjunto publicado, contra âncoras conhecidas.

As âncoras foram apuradas no Censo 2024, no CPC 2023 e no CNES 202607, lendo
os arquivos originais. Servem como teste de REGRESSÃO: se o pipeline passar a
produzir outra coisa, o mais provável é defeito no pipeline, não notícia nos
dados. Uma edição nova das fontes muda os números de propósito — e aí estas
constantes mudam junto, num commit que diz isso.

Roda como script ou sob pytest.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data"

CENSO = 2024
CICLO_CPC = "2023"
COMPETENCIA_CNES = "202607"

# ------------------------------------------------------- âncoras: Censo 2024
REGISTROS_CENSO = 650
UFS_COM_PRESENCIAL = 27
MUNICIPIOS_COM_OFERTA = 303
CURSOS_PRESENCIAIS = 650
VAGAS_PRESENCIAL = 118747
VAGAS_EAD = 0
POLOS_REGISTROS = 0
POLOS_MUNICIPIOS = 0
MATRICULAS = 162750
CONCLUINTES = 26883
IES_DISTINTAS = 576

# A ausência de EaD como fato estrutural: não há uma linha sequer de ensino a
# distância no recorte — nem sede, nem polo. As 27 UFs valem zero.
UFS_SEM_VAGA_EAD = 27
UFS_SEM_POLO = 27

# --------------------------------------------------------- âncoras: CPC 2023
CURSOS_AVALIADOS = 452
UFS_AVALIADAS = 27
CURSOS_COM_CPC = 448
CURSOS_COM_IDD = 445
CONCLUINTES_PARTICIPANTES = 23278

# --------------------------------------------------------- âncoras: CNES 202607
MUNICIPIOS_COM_DENTISTA = 5567
DENTISTAS_SUS = 95952
# Rede especializada: só o que é ofertado AO SUS entra no índice. O total
# declarado — que inclui clínica e consultório privados — fica ao lado, e a
# diferença entre os dois é grande o bastante para merecer âncora própria.
MUNICIPIOS_COM_ESP_BUCAL = 2470
ESTABELECIMENTOS_ESP_BUCAL = 6014
MUNICIPIOS_COM_ESP_BUCAL_TOTAL = 3105
ESTABELECIMENTOS_ESP_BUCAL_TOTAL = 28819
MUNICIPIOS_COM_LRPD = 4345

MUNICIPIOS_BRASIL = 5571          # IBGE, desde a instalação de Boa Esperança
MUNICIPIOS_MT = 142               # do Norte (MT) em 01/01/2025


def _ler(nome):
    caminho = DATA / nome
    if not caminho.exists():
        raise SystemExit(
            f"[ERRO] {caminho} ausente. Rode o pipeline antes dos testes.")
    return json.loads(caminho.read_text(encoding="utf-8"))


def _soma(ufs, campo):
    return sum(d.get(campo) or 0 for d in ufs.values())


# ------------------------------------------------------------------ território

def test_27_ufs_presentes():
    ufs = _ler("nacional.json")["ufs"]
    assert len(ufs) == 27, f"esperado 27 UFs, veio {len(ufs)}"


def test_todas_as_ufs_tem_oferta_presencial():
    """
    Odontologia é ofertada nas 27 unidades da federação.

    É um fato do curso, não um acaso da edição: nos observatórios irmãos há
    estados sem oferta, e a página tem texto próprio para eles. Se este teste
    falhar, ou o Censo mudou ou o filtro de rótulo capturou/perdeu linhas.
    """
    ufs = _ler("nacional.json")["ufs"]
    sem = sorted(u for u, d in ufs.items() if not d.get("tem_oferta_presencial"))
    assert not sem, f"UFs sem oferta presencial: {sem} — esperado nenhuma"
    com = [u for u, d in ufs.items() if d.get("tem_oferta_presencial")]
    assert len(com) == UFS_COM_PRESENCIAL


def test_contagem_de_municipios_do_ibge():
    ufs = _ler("nacional.json")["ufs"]
    total = _soma(ufs, "municipios_total")
    assert total == MUNICIPIOS_BRASIL, (
        f"esperado {MUNICIPIOS_BRASIL} municípios, veio {total}. "
        "Se o IBGE instalou ou extinguiu município, a mudança é real — "
        "atualize a âncora num commit que explique.")
    assert ufs["MT"]["municipios_total"] == MUNICIPIOS_MT, (
        f"MT deveria ter {MUNICIPIOS_MT} municípios, "
        f"veio {ufs['MT']['municipios_total']}")


def test_municipios_com_oferta():
    ufs = _ler("nacional.json")["ufs"]
    total = _soma(ufs, "municipios_oferta")
    assert total == MUNICIPIOS_COM_OFERTA, (
        f"esperado {MUNICIPIOS_COM_OFERTA}, veio {total}")


# ------------------------------------------------------------------ capacidade

def test_vagas_presenciais_e_ead():
    """
    O zero da EaD aqui é DADO, e por isso não serve como teste de leitura.

    Nos observatórios irmãos, `vagas_ead == 0` era o sintoma clássico de somar
    as linhas de polo em vez das de sede — e o teste cobrava um valor positivo
    para pegar esse defeito. Em Odontologia o zero é o resultado correto, então
    essa guarda não pode existir: ela reprovaria a execução certa.

    A leitura das camadas continua sendo verificada, só que na fonte: veja
    `test_nao_existe_camada_de_ead_no_recorte`, que confere direto no recorte
    do Censo que não há linha de sede nem de polo. É a única forma de
    distinguir "não somou" de "não existe".
    """
    ufs = _ler("nacional.json")["ufs"]
    presencial = _soma(ufs, "vagas_presencial")
    ead = _soma(ufs, "vagas_ead")
    assert presencial == VAGAS_PRESENCIAL, (
        f"vagas presenciais: esperado {VAGAS_PRESENCIAL}, veio {presencial}")
    assert ead == VAGAS_EAD, f"vagas EaD: esperado {VAGAS_EAD}, veio {ead}"


def test_cursos_e_ies():
    ufs = _ler("nacional.json")["ufs"]
    assert _soma(ufs, "n_cursos_presencial") == CURSOS_PRESENCIAIS


def test_polos_ead():
    ufs = _ler("nacional.json")["ufs"]
    assert _soma(ufs, "ead_polos_registros") == POLOS_REGISTROS
    assert _soma(ufs, "ead_polos_municipios") == POLOS_MUNICIPIOS


def test_fluxo():
    ufs = _ler("nacional.json")["ufs"]
    assert _soma(ufs, "matriculas") == MATRICULAS, (
        f"matrículas: esperado {MATRICULAS}, veio {_soma(ufs, 'matriculas')}")
    assert _soma(ufs, "concluintes") == CONCLUINTES


def test_concentracao_calculada_sobre_a_capacidade_total():
    """
    HHI existe para toda UF com vagas, e fica em 0..1.

    Nos observatórios com EaD grande, este teste também exigia que o HHI de
    mantenedora fosse bem maior que o de IES — era assim que se detectava um
    cálculo feito só sobre o presencial. Aqui essa checagem não vale: com a EaD
    em 0,2% da capacidade, os dois índices coincidem por motivo legítimo, e
    exigir distância entre eles reprovaria o resultado correto.
    """
    ufs = _ler("nacional.json")["ufs"]
    for sigla, d in ufs.items():
        if d["vagas_total"]:
            assert d["HHI"] is not None, f"{sigla} com vagas mas sem HHI"
            assert 0 <= d["HHI"] <= 1, f"{sigla}: HHI fora de 0..1"
            assert d["HHI_mantenedora"] is not None, (
                f"{sigla} com vagas mas sem HHI de mantenedora")


# ----------------------------------------------- o fato estrutural: é presencial

def test_nao_existe_camada_de_ead_no_recorte():
    """
    Confere na FONTE que a ausência de EaD é do dado, não da leitura.

    O conjunto publicado não consegue provar isso sozinho: zero em
    `vagas_ead` tanto pode significar "o Censo não tem linha de EaD" quanto
    "o pipeline somou a camada errada e perdeu as vagas". A diferença só
    aparece no recorte bruto, onde `TP_DIMENSAO` separa presencial (1), polo
    (2) e sede EaD (3). Se um dia o INEP registrar oferta a distância em
    Odontologia, é aqui que o observatório fica sabendo.
    """
    import csv
    recorte = REPO / "etl" / "dados" / f"curso_odontologia_{CENSO}.csv"
    if not recorte.exists():
        print("          (pulado: recorte do Censo ausente — "
              "rode etl/extrair_censo.py)")
        return
    csv.field_size_limit(1 << 24)
    dimensoes = set()
    with open(recorte, encoding="utf-8") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            dimensoes.add((linha.get("TP_DIMENSAO") or "").strip())
    assert dimensoes == {"1"}, (
        f"o recorte tem as camadas {sorted(dimensoes)}. Se apareceu 2 (polo) "
        "ou 3 (sede EaD), passou a existir oferta a distância em Odontologia: "
        "é notícia, e todas as âncoras de EaD mudam junto — inclusive as "
        "seções do site que hoje declaram a ausência.")


def test_zero_de_ead_e_medida_nao_ausencia():
    """
    O teste central deste observatório.

    Nenhuma das 27 UFs tem vaga a distância. Esse valor precisa ser `0` —
    medida —, jamais `None`. Trocar por None faria o site dizer "sem dados"
    onde há dado apurado, e o achado que distingue Odontologia dos cursos
    irmãos desapareceria disfarçado de lacuna.

    O contrário — 0 no lugar de ausência — é vigiado por
    `test_ausencia_nunca_e_zero`. As duas direções importam.
    """
    ufs = _ler("nacional.json")["ufs"]
    zeros = [u for u, d in ufs.items() if d.get("vagas_ead") == 0]
    nulos = [u for u, d in ufs.items() if d.get("vagas_ead") is None]
    assert not nulos, (
        f"UFs com vagas_ead nulo: {nulos}. O Censo foi lido para as 27 UFs, "
        "então a ausência de vaga EaD é zero medido, não dado faltante.")
    assert len(zeros) == UFS_SEM_VAGA_EAD, (
        f"esperado {UFS_SEM_VAGA_EAD} UFs com zero vagas EaD, veio {len(zeros)}")

    sem_polo = [u for u, d in ufs.items() if d.get("ead_polos_registros") == 0]
    nulos_polo = [u for u, d in ufs.items()
                  if d.get("ead_polos_registros") is None]
    assert not nulos_polo, f"UFs com polos nulos: {nulos_polo}"
    assert len(sem_polo) == UFS_SEM_POLO, (
        f"esperado {UFS_SEM_POLO} UFs sem nenhum polo, veio {len(sem_polo)}")


# ------------------------------------------------------------------- qualidade

def test_qualidade_cpc():
    q = _ler("qualidade.json")
    assert q["metadados"]["ciclo"] == CICLO_CPC, (
        f"ciclo {q['metadados']['ciclo']}, esperado {CICLO_CPC}. Odontologia "
        "não está no CPC 2023 — aquele é o ciclo da saúde e das engenharias.")
    assert len(q["cursos"]) == CURSOS_AVALIADOS, (
        f"esperado {CURSOS_AVALIADOS} cursos avaliados, veio {len(q['cursos'])}")
    assert len(q["ufs"]) == UFS_AVALIADAS
    com_cpc = sum(1 for c in q["cursos"] if c["CPC_cont"] is not None)
    com_idd = sum(1 for c in q["cursos"] if c["IDD"] is not None)
    assert com_cpc == CURSOS_COM_CPC, f"com CPC: esperado {CURSOS_COM_CPC}, veio {com_cpc}"
    assert com_idd == CURSOS_COM_IDD, f"com IDD: esperado {CURSOS_COM_IDD}, veio {com_idd}"
    participantes = sum(c["concluintes_participantes"] or 0 for c in q["cursos"])
    assert participantes == CONCLUINTES_PARTICIPANTES


def test_todos_os_cursos_avaliados_sao_presenciais():
    """
    Nenhum curso EaD de Psicologia foi avaliado no ciclo 2022 — nenhum mesmo.

    Não é filtro deste projeto: é o que a planilha do INEP traz. Se algum dia
    aparecer curso a distância aqui, é mudança real do ensino da Psicologia no
    país, e merece um commit que diga isso em vez de passar batido.
    """
    q = _ler("qualidade.json")
    modalidades = {(c.get("modalidade") or "").strip() for c in q["cursos"]}
    assert len(modalidades) == 1, (
        f"esperada uma única modalidade entre os cursos avaliados, "
        f"vieram {sorted(modalidades)}")
    assert "distância" not in next(iter(modalidades)).lower()


def test_toda_uf_tem_curso_avaliado():
    ufs = _ler("nacional.json")["ufs"]
    sem = sorted(u for u, d in ufs.items() if not d.get("tem_avaliacao"))
    assert not sem, (
        f"UFs sem curso avaliado: {sem}. No ciclo 2023 todas as 27 UFs têm "
        "curso de Odontologia avaliado; se deixou de ser assim, os indicadores "
        "de qualidade delas ficam nulos e esta âncora muda junto.")


# ------------------------------------------------------------------- cobertura

def test_tres_coberturas_existem_e_sao_separadas():
    """
    ICAB, ICRE e ICSB precisam existir para as 27 UFs e ficar em 0..1.

    O ICSB tem um terceiro estado legítimo: sai NULO no país inteiro quando a
    tabela de domínio de tipos de equipe do CNES não pôde ser lida. Nulo em
    ALGUMAS UFs, não — isso seria agregação parcial, que é defeito.
    """
    ufs = _ler("nacional.json")["ufs"]
    for indice in ("ICAB", "ICRE"):
        faltando = [u for u, d in ufs.items() if d.get(indice) is None]
        assert not faltando, (
            f"{indice} ausente em {faltando}. O CNES foi lido para o país "
            "inteiro; ausência aqui significa que um passo não rodou.")
        fora = [(u, d[indice]) for u, d in ufs.items()
                if not 0 <= d[indice] <= 1]
        assert not fora, f"{indice} fora de 0..1: {fora}"

    icsb = [d.get("ICSB") for d in ufs.values()]
    if all(v is None for v in icsb):
        print("          (ICSB nulo nas 27 UFs — ausência declarada; o "
              "catálogo de TP_EQUIPE não existe neste export do CNES)")
    else:
        faltando = [u for u, d in ufs.items() if d.get("ICSB") is None]
        assert not faltando, (
            f"ICSB nulo só em {faltando}. Ou o domínio foi lido e vale para "
            "todas, ou não foi lido e não vale para nenhuma — nulo parcial é "
            "agregação quebrada, não ausência de fonte.")
        fora = [(u, d["ICSB"]) for u, d in ufs.items()
                if not 0 <= d["ICSB"] <= 1]
        assert not fora, f"ICSB fora de 0..1: {fora}"


def test_ancoras_da_cobertura_do_cnes():
    """Totais conhecidos do CNES 202607, como teste de regressão."""
    ufs = _ler("nacional.json")["ufs"]
    for campo, esperado in [
        ("municipios_com_dentista", MUNICIPIOS_COM_DENTISTA),
        ("dentistas_sus", DENTISTAS_SUS),
        ("municipios_com_esp_bucal", MUNICIPIOS_COM_ESP_BUCAL),
        ("estabelecimentos_esp_bucal", ESTABELECIMENTOS_ESP_BUCAL),
        ("municipios_com_esp_bucal_total", MUNICIPIOS_COM_ESP_BUCAL_TOTAL),
        ("estabelecimentos_esp_bucal_total", ESTABELECIMENTOS_ESP_BUCAL_TOTAL),
        ("municipios_com_lrpd", MUNICIPIOS_COM_LRPD),
    ]:
        obtido = _soma(ufs, campo)
        assert obtido == esperado, (
            f"{campo}: esperado {esperado}, veio {obtido}")


def test_rede_especializada_publica_e_menor_que_a_declarada():
    """
    O filtro de SUS precisa continuar existindo.

    Sem ele, consultório privado que declara o serviço 114 entra como rede
    pública: medido, isso levaria a cobertura de 2.470 para 3.105 municípios e
    de 6.014 para 28.819 estabelecimentos. Se os dois números empatarem, o
    filtro caiu — e o site passaria a chamar de pública uma rede que não é.
    """
    ufs = _ler("nacional.json")["ufs"]
    sus = _soma(ufs, "municipios_com_esp_bucal")
    total = _soma(ufs, "municipios_com_esp_bucal_total")
    assert sus < total, (
        f"municípios com serviço ao SUS ({sus}) igualou o total declarado "
        f"({total}) — o filtro CO_AMBULATORIAL_SUS/CO_HOSPITALAR_SUS caiu")
    estab_sus = _soma(ufs, "estabelecimentos_esp_bucal")
    estab_total = _soma(ufs, "estabelecimentos_esp_bucal_total")
    assert estab_sus < estab_total


def test_icsb_e_ausencia_declarada_nao_zero():
    """
    O ICSB tem de estar NULO nas 27 UFs, e o motivo tem de estar escrito.

    Zero aqui afirmaria que nenhum município do país tem equipe de saúde bucal,
    o que é falso e absurdo. A causa é conhecida e medida: a coluna TP_EQUIPE
    do CNES usa 70, 71, 72… e nenhuma tabela de domínio do export nomeia esses
    códigos. Casar o catálogo errado devolve 846 de 125.202 equipes — plausível
    à primeira vista, sem sentido nenhum.

    Se um dia o CNES exportar o catálogo certo, este teste falha: é o sinal de
    que o indicador pode voltar, e de que as âncoras precisam ser escritas.
    """
    ufs = _ler("nacional.json")["ufs"]
    com_valor = {u: d["ICSB"] for u, d in ufs.items() if d.get("ICSB") is not None}
    assert not com_valor, (
        f"ICSB passou a ter valor em {sorted(com_valor)}. Se o CNES publicou o "
        "catálogo de TP_EQUIPE, ótimo — confira a medição e escreva as âncoras. "
        "Se não, alguém está contando equipe por coincidência de numeração.")
    for campo in ("municipios_com_esb", "equipes_esb_total"):
        valores = [d.get(campo) for d in ufs.values()]
        assert all(v is None for v in valores), (
            f"{campo} saiu com valor onde não há medição — ausência virou zero")

    texto = " ".join(_ler("_proveniencia.json")["limitacoes_conhecidas"]).lower()
    assert "tp_equipe" in texto, (
        "a razão da ausência do ICSB não está declarada nas limitações")


def test_as_tres_coberturas_nao_sao_o_mesmo_numero():
    """
    Três índices que coincidem são um índice publicado três vezes.

    Eles medem alcances diferentes por construção — profissional, serviço
    especializado e equipe da atenção primária —, e a rede especializada é a
    mais rara das três por desenho de política: ela é regionalizada. Se os
    valores passarem a coincidir, o mais provável é que dois deles tenham
    começado a ler o mesmo campo.
    """
    ufs = _ler("nacional.json")["ufs"]
    # Sem cobertura lida não há o que comparar, e comparar mesmo assim estoura
    # com TypeError — que é pior de diagnosticar que uma falha declarada.
    sem_cobertura = [u for u, d in ufs.items()
                     if d.get("ICAB") is None or d.get("ICRE") is None]
    assert not sem_cobertura, (
        f"ICAB/ICRE ausentes em {sem_cobertura}: rode etl/extrair_cnes.py e "
        "etl/consolidar.py antes deste teste")
    iguais = [u for u, d in ufs.items() if d["ICAB"] == d["ICRE"]]
    assert len(iguais) < len(ufs), (
        "ICAB e ICRE são idênticos em todas as UFs — sinal de que os dois "
        "estão contando o mesmo campo")
    media_icab = sum(d["ICAB"] for d in ufs.values()) / len(ufs)
    media_icre = sum(d["ICRE"] for d in ufs.values()) / len(ufs)
    assert media_icre < media_icab, (
        f"a rede especializada (média {media_icre:.3f}) deveria alcançar menos "
        f"municípios que a força de trabalho (média {media_icab:.3f}); se "
        "inverteu, confira o que cada um está contando")


def test_icab_esta_saturado_e_a_densidade_e_que_separa():
    """
    Registra a saturação do ICAB, que muda como ele deve ser lido.

    5.567 dos 5.571 municípios têm cirurgião-dentista vinculado ao SUS — uma
    saturação ainda maior que a do observatório de Psicologia. O índice continua
    legítimo, mas separa pouquíssimo: quem o ler como retrato da rede vai
    concluir que o país inteiro está igualmente atendido. A medida que separa é
    a densidade, e este teste existe para que a ressalva não sobreviva ao dia em
    que ela deixar de ser verdade.
    """
    ufs = _ler("nacional.json")["ufs"]
    valores = [d["ICAB"] for d in ufs.values() if d.get("ICAB") is not None]
    assert len(valores) == 27
    assert min(valores) > 0.9, (
        f"ICAB mínimo em {min(valores):.3f}. Abaixo de 0,9 a amplitude deixa de "
        "ser estreita e as ressalvas de saturação precisam ser revistas.")

    densidades = [d.get("dentistas_por_100k") for d in ufs.values()]
    assert all(v is not None for v in densidades), (
        "dentistas_por_100k ausente em alguma UF — é ela que separa os estados "
        "onde o ICAB satura")
    assert max(densidades) / min(densidades) > 1.5, (
        "a densidade deveria variar bem mais que o ICAB; se não varia, o "
        "argumento de que ela é a medida discriminante caiu")


def test_laboratorio_de_protese_e_contagem_nunca_indice():
    """
    O serviço 157 sai como contagem e não vira fração de cobertura.

    Publicá-lo como índice o faria ser lido como acesso à reabilitação
    protética, que não é o que o cadastro diz: o laboratório atende uma região,
    não o município onde está.
    """
    ufs = _ler("nacional.json")["ufs"]
    for campo in ("municipios_com_lrpd", "estabelecimentos_lrpd"):
        valores = [d.get(campo) for d in ufs.values()]
        assert all(v is None or isinstance(v, int) for v in valores), (
            f"{campo} deveria ser contagem inteira")
    assert not any("LRPD" in k or "ICPR" in k for d in ufs.values() for k in d), (
        "apareceu um índice de prótese no conjunto — ele foi publicado como "
        "contagem de propósito")


# ------------------------------------------------------- princípio inegociável

def test_ausencia_nunca_e_zero():
    """
    O par de `test_zero_de_ead_e_medida_nao_ausencia`, na direção oposta.

    Regra: um indicador que depende de insumo faltante não pode ter valor
    numérico. `None` chega à tela como "sem dados"; `0` chega como afirmação.
    """
    ufs = _ler("nacional.json")["ufs"]
    problemas = []
    for sigla, d in ufs.items():
        if not d.get("tem_avaliacao"):
            for campo in ("CPC", "CPC_cont", "IDD", "IAF"):
                if d.get(campo) is not None:
                    problemas.append(
                        f"{sigla}.{campo} = {d[campo]} sem curso avaliado")
        if not d.get("tem_oferta_presencial"):
            for campo in ("ICT", "E", "IAF", "HHI", "HHI_mantenedora"):
                if d.get(campo) is not None:
                    problemas.append(
                        f"{sigla}.{campo} = {d[campo]} sem oferta presencial")
    assert not problemas, ("valores numéricos onde deveria haver ausência:\n  - "
                          + "\n  - ".join(problemas))


def test_iaf_so_existe_com_os_tres_componentes():
    ufs = _ler("nacional.json")["ufs"]
    problemas = []
    for sigla, d in ufs.items():
        tem_tudo = (d.get("CPC") is not None
                    and d.get("vagas_avaliadas") is not None
                    and d.get("ICT") is not None)
        if d.get("IAF") is not None and not tem_tudo:
            problemas.append(f"{sigla}: IAF={d['IAF']} sem os três componentes")
        if d.get("IAF") is None and tem_tudo:
            problemas.append(f"{sigla}: componentes completos mas IAF nulo")
    assert not problemas, "\n  - ".join(problemas)


def test_percentuais_dentro_da_faixa():
    ufs = _ler("nacional.json")["ufs"]
    problemas = []
    for sigla, d in ufs.items():
        for campo, valor in d.items():
            if campo.startswith("pct_") and valor is not None:
                if not 0 <= valor <= 100:
                    problemas.append(f"{sigla}.{campo} = {valor}")
    assert not problemas, "percentuais fora de 0..100:\n  - " + "\n  - ".join(problemas)


# ---------------------------------------------------------------- proveniência

def test_proveniencia_vem_do_arquivo():
    """
    O ano do Censo tem de vir do arquivo lido, não do calendário.

    Derivar `ano - 1` da data de hoje rotula o Censo 2024 como 2025 durante todo
    o ano seguinte, e o erro só aparece muito depois, num gráfico de série.
    """
    meta = _ler("nacional.json")["metadados"]
    assert meta["ano_censo"] == CENSO
    prov = meta["proveniencia"]["fontes"]["censo"]
    assert prov.get("ano_censo") == CENSO
    assert prov.get("md5_publicado"), "sem md5 publicado pelo INEP na proveniência"
    assert str(CENSO) in prov.get("membro_cursos", ""), (
        "o membro lido não confere com o ano declarado")
    assert prov.get("rotulo_cine") == "Odontologia", (
        f"rótulo CINE {prov.get('rotulo_cine')!r} — o match é EXATO. No Censo "
        "2024 'Odontologia' é o único rótulo que contém 'odont', e é "
        "justamente por isso que o critério exato precisa continuar: o dia em "
        "que surgir um vizinho, a substring o somaria sem avisar")
    assert prov.get("linhas_curso") == REGISTROS_CENSO


def test_todas_as_fontes_declaram_presenca():
    fontes = _ler("_proveniencia.json")["fontes"]
    for nome in ("censo", "cpc", "cnes",
                 "ibge_municipios", "ibge_populacao"):
        assert nome in fontes, f"fonte {nome} não declarada na proveniência"
        assert "presente" in fontes[nome], (
            f"fonte {nome} sem o campo `presente` — ficaria de fora de "
            "qualquer varredura que pergunte quais fontes faltaram")
    assert fontes["cnes"].get("competencia") == COMPETENCIA_CNES


def test_limitacoes_declaradas():
    """O que não foi medido precisa estar escrito, não subentendido."""
    prov = _ler("_proveniencia.json")
    texto = " ".join(prov["limitacoes_conhecidas"]).lower()
    for termo, motivo in [
        ("ead", "a ausência completa de oferta a distância"),
        ("114", "o serviço do CNES que define a rede especializada"),
        ("ceo", "por que o indicador não se chama CEO"),
        ("157", "o laboratório de prótese, publicado fora de índice"),
        ("correlação", "por que pct_ead fica fora da matriz"),
    ]:
        assert termo in texto, f"{motivo} não está declarada nas limitações"


def main():
    testes = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    falhas = 0
    for teste in testes:
        try:
            teste()
            print(f"  OK      {teste.__name__}")
        except AssertionError as e:
            falhas += 1
            print(f"  FALHOU  {teste.__name__}: {e}")
    print(f"\n{len(testes) - falhas}/{len(testes)} testes de integridade passaram.")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
