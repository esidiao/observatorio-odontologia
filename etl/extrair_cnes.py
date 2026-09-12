"""
etl/extrair_cnes.py
Extrai do CNES as três metades da cobertura assistencial em Odontologia no SUS,
lendo a base mensal do DATASUS por FTP sem baixar os 700 MB do ZIP.

Uso:
    python etl/extrair_cnes.py                      # competência mais recente
    python etl/extrair_cnes.py --competencia 202607
    python etl/extrair_cnes.py --listar             # competências disponíveis
    python etl/extrair_cnes.py --rebaixar           # ignora o cache e rebaixa

Saída: data/cobertura_cnes.json — por município.

AS TRÊS METADES
---------------
  · força de trabalho — municípios com ao menos um vínculo de cirurgião-dentista
    (família CBO 2232) em `tbCargaHorariaSus`;
  · rede especializada — municípios com estabelecimento que declara o serviço
    114, ATENÇÃO ESPECIALIZADA À SAÚDE BUCAL, em `rlEstabServClass`;
  · atenção primária — municípios com equipe de saúde bucal (eSB) em `tbEquipe`.

São três porque respondem a perguntas diferentes: existe profissional, existe
serviço especializado, existe porta de entrada. Fundi-las exigiria arbitrar um
peso entre as três, e peso arbitrário é estimativa disfarçada.

O CBO É POR PREFIXO EXATO DE FAMÍLIA, NÃO POR NOME
---------------------------------------------------
A família 2232 reúne as ocupações de cirurgião-dentista — clínico geral e as
especialidades. Casar por texto capturaria o professor e o pesquisador de
Odontologia (docência e pesquisa não são assistência) e também o auxiliar e o
técnico em saúde bucal, que são ocupações próprias, de outra família, e que
este indicador não mede. Quais ocupações a família reúne de fato é lido da
tabela de domínio e gravado na proveniência, não digitado aqui.

A HABILITAÇÃO DE CEO NÃO ESTÁ EM TABELA-FATO NENHUMA
------------------------------------------------------
O Centro de Especialidades Odontológicas é o nome da política; o que a base
publica é o SERVIÇO declarado. Os códigos de habilitação existem em
`tbSubGruposHabilitacao`, e nenhuma tabela desta base os relaciona a
estabelecimento — a mesma limitação que o observatório de Fonoaudiologia mediu
para o CER. Por isso o indicador se chama rede especializada, e não CEO:
nomeá-lo CEO afirmaria uma habilitação que não foi lida.

O SERVIÇO 114 TEM VINTE CLASSIFICAÇÕES, E ELAS NÃO VIRAM SUBGRUPOS
--------------------------------------------------------------------
Medido na competência 202607: dentística, endodontia, periodontia, cirurgia
oral, cirurgia bucomaxilofacial, atendimento a pessoa com deficiência,
odontopediatria, estomatologia, implantodontia, ortodontia, prótese, radiologia
e mais. São ESPECIALIDADES do mesmo tipo de serviço, não políticas opostas como
eram os três subgrupos da rede psicossocial no observatório de Psicologia —
lá o agrupamento existia para não somar comunidade terapêutica com residência
terapêutica. Aqui não há o que separar: o indicador conta o estabelecimento uma
vez, e a lista de classificações declaradas em cada município sai no detalhe,
para quem quiser olhar.

O LABORATÓRIO DE PRÓTESE DENTÁRIA É EXTRAÍDO, NÃO FUNDIDO
-----------------------------------------------------------
O serviço 157 (LABORATÓRIO DE PRÓTESE DENTÁRIA) aparece na mesma leitura e sai
em campo próprio. Não entra em índice nem se soma à rede especializada: é outra
política, com outra unidade de conta, e somá-lo mediria duas coisas num número
só.

AS EQUIPES SÃO DESCOBERTAS PELO DOMÍNIO, NÃO POR CÓDIGO FIXO
--------------------------------------------------------------
`tbEquipe` diz o tipo de cada equipe por código; o nome por extenso está em
`tbTipoEquipe` e `tbSubTipoEquipe`. Os códigos de saúde bucal são DESCOBERTOS
lendo esses nomes, e os que casaram vão para a proveniência com o rótulo que os
identificou. Fixá-los aqui envelheceria em silêncio: o código continuaria
válido e passaria a nomear outra coisa. Se o domínio não for lido, a fatia de
equipes fica sem filtro possível e o indicador sai NULO — declarado ausente,
nunca zerado.

E os dois conjuntos são casados SEPARADAMENTE, cada um contra a sua coluna.
Tipo e subtipo têm numeração própria e sobreposta: na competência 202607 o tipo
`01` é "ESF transitória com saúde bucal", e nada garante que o subtipo `01` de
uma competência futura seja de saúde bucal. Casar um conjunto único contra
"tipo ou subtipo" funcionaria hoje e passaria a contar equipe errada no dia em
que a numeração do subtipo mudasse — sem erro, só com um número maior.

O QUE ENTRA COMO "EQUIPE DE SAÚDE BUCAL"
------------------------------------------
Na competência 202607 casam sete tipos, e eles não são todos a mesma coisa:
duas são equipes de saúde bucal propriamente ditas (ESB e ESB modalidade II) e
cinco são equipes de atenção básica que INCLUEM saúde bucal (ESF transitória,
ESF ribeirinha, ESF fluvial, equipe de agentes comunitários e equipe de atenção
básica tipo III, todas "com saúde bucal"). O indicador conta as sete, porque a
pergunta é "há equipe com saúde bucal na atenção primária deste município?" —
e não "há uma ESB isolada?". Os rótulos ficam na proveniência para quem quiser
refazer a conta com outro recorte.

DUAS FASES, COM CACHE ENTRE ELAS
---------------------------------
Baixar e filtrar leva perto de uma hora: o FTP do DATASUS derruba a maior parte
das conexões que usam REST. Agregar leva segundos. As fatias filtradas ficam em
`etl/dados/cnes_<competência>/`, fora do versionamento.
"""
import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from rede import ZipRemotoFTP

REPO = Path(__file__).parent.parent
DADOS = REPO / "etl" / "dados"
DATA = REPO / "data"

HOST = "ftp.datasus.gov.br"
DIRETORIO = "/cnes"
PADRAO = "BASE_DE_DADOS_CNES_{competencia}.ZIP"

# Blocos grandes reduzem o número de conexões FTP, e cada conexão é uma aposta:
# o DATASUS derruba boa parte das conexões que usam REST.
BLOCO = 16 << 20

CBO_PREFIXO = "2232"            # família Cirurgião-dentista

SERVICO_ESPECIALIZADO = "114"   # ATENÇÃO ESPECIALIZADA À SAÚDE BUCAL
SERVICO_PROTESE = "157"         # LABORATÓRIO DE PRÓTESE DENTÁRIA

# Termos que identificam equipe de saúde bucal no NOME do tipo/subtipo. A
# seleção é por nome porque o código não é estável entre competências; os
# códigos efetivamente casados são gravados na proveniência, para conferência.
TERMOS_EQUIPE_BUCAL = ("SAUDE BUCAL", "SAÚDE BUCAL", "ESB")

UF_POR_CODIGO = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP",
    "17": "TO", "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB",
    "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
    "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
    "51": "MT", "52": "GO", "53": "DF",
}

# Fração de registros cujo CO_UNIDADE não existe em NENHUM estabelecimento do
# cadastro. Diferente de "estabelecimento desabilitado", que é exclusão
# deliberada e pode ser alta sem indicar defeito nenhum.
LIMITE_SEM_CADASTRO = 0.02

csv.field_size_limit(1 << 24)


def _limpo(valor):
    return (valor or "").strip().strip('"')


def _norm(texto):
    return re.sub(r"\s+", " ", _limpo(texto).upper())


def competencias_disponiveis():
    z = ZipRemotoFTP(HOST, DIRETORIO, PADRAO.format(competencia="000000"))
    nomes = z.listar_diretorio()
    return sorted(
        m.group(1) for m in
        (re.match(r"BASE_DE_DADOS_CNES_(\d{6})\.ZIP$", n, re.I) for n in nomes)
        if m
    )


def _membro(z, sufixo, competencia, obrigatorio=True):
    alvo = z.localizar(sufixo.lower(), competencia)
    if not alvo and obrigatorio:
        raise SystemExit(f"[ERRO] membro {sufixo}{competencia} não encontrado no ZIP")
    return alvo


def _coluna(campos, *trechos):
    """Acha a coluna cujo nome contém todos os trechos. Descobre, não supõe."""
    for nome in campos or ():
        alvo = _norm(nome)
        if all(t in alvo for t in trechos):
            return nome
    return None


# --------------------------------------------------------------------------- #
# FASE 1 — baixar e filtrar (cara, cacheada)
# --------------------------------------------------------------------------- #

def _escrever(caminho, cabecalho, linhas):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(cabecalho)
        escritor.writerows(linhas)


def _ler_dominio(z, competencia, anterior=None):
    """
    Nomes oficiais dos serviços, das ocupações da família 2232 e dos tipos de
    equipe — lidos da própria base, não digitados aqui.

    Para serviços e ocupações o domínio é luxo: o indicador é o código, e o
    código sai da tabela-fato. Para as EQUIPES ele é requisito, porque é o nome
    que identifica quais tipos são de saúde bucal. Falhando a leitura, o
    indicador de equipes sai nulo e a falha fica registrada.
    """
    dominio = {"servicos": {}, "classificacoes": {}, "ocupacoes": {},
               "tipos_equipe": {}, "subtipos_equipe": {}, "falhas": []}
    anterior = anterior or {}

    # Orçamento curto e próprio: no FTP do DATASUS, insistir doze vezes com
    # timeout de 300 s numa tabela de poucos KB pode custar uma hora.
    z_curto = ZipRemotoFTP(z.host, z.diretorio, z.nome, timeout=60, tentativas=4)
    z_curto._indice = z.indice()
    z_curto._tamanho = z.tamanho()

    def _tentar(sufixo, consumir, rotulo, chaves=()):
        # Se a execução anterior já leu esta tabela, reaproveita e não gasta
        # conexão: o FTP do DATASUS é o recurso escasso aqui, não o disco.
        if chaves and all(anterior.get(c) for c in chaves):
            for c in chaves:
                dominio[c] = anterior[c]
            print(f"[CNES] {rotulo}: aproveitado da leitura anterior.")
            return
        alvo = _membro(z_curto, sufixo, competencia, obrigatorio=False)
        if not alvo:
            dominio["falhas"].append(f"{sufixo}: membro não localizado no ZIP")
            print(f"[CNES] {rotulo}: membro não localizado.")
            return
        try:
            with z_curto.membro_arquivo(alvo) as f:
                consumir(csv.DictReader(f, delimiter=";"))
            print(f"[CNES] {rotulo}: lido.")
        except Exception as e:                                 # noqa: BLE001
            dominio["falhas"].append(f"{sufixo}: {type(e).__name__}")
            print(f"[CNES] {rotulo}: leitura falhou ({type(e).__name__}).")

    def _servicos(leitor):
        for linha in leitor:
            codigo = _limpo(linha.get("CO_SERVICO_ESPECIALIZADO"))
            if codigo in (SERVICO_ESPECIALIZADO, SERVICO_PROTESE):
                dominio["servicos"][codigo] = _limpo(
                    linha.get("DS_SERVICO_ESPECIALIZADO"))

    def _classificacoes(leitor):
        for linha in leitor:
            codigo = _limpo(linha.get("CO_SERVICO_ESPECIALIZADO"))
            if codigo in (SERVICO_ESPECIALIZADO, SERVICO_PROTESE):
                chave = f"{codigo}/{_limpo(linha.get('CO_CLASSIFICACAO_SERVICO'))}"
                dominio["classificacoes"][chave] = _limpo(
                    linha.get("DS_CLASSIFICACAO_SERVICO"))

    def _ocupacoes(leitor):
        for linha in leitor:
            cbo = _limpo(linha.get("CO_CBO"))
            if cbo.startswith(CBO_PREFIXO):
                dominio["ocupacoes"][cbo] = _limpo(
                    linha.get("DS_ATIVIDADE_PROFISSIONAL"))

    def _coletar_equipe(destino):
        def consumir(leitor):
            campos = leitor.fieldnames or []
            col_codigo = _coluna(campos, "CO_") or (campos[0] if campos else None)
            col_nome = _coluna(campos, "DS_") or _coluna(campos, "NO_")
            if not col_codigo or not col_nome:
                return
            for linha in leitor:
                destino[_limpo(linha.get(col_codigo))] = _limpo(linha.get(col_nome))
        return consumir

    _tentar("tbServicoEspecializado", _servicos, "nomes dos serviços 114 e 157",
            chaves=("servicos",))
    _tentar("tbClassificacaoServico", _classificacoes,
            "classificações dos serviços", chaves=("classificacoes",))
    _tentar("tbAtividadeProfissional", _ocupacoes,
            f"ocupações da família {CBO_PREFIXO}", chaves=("ocupacoes",))
    _tentar("tbTipoEquipe", _coletar_equipe(dominio["tipos_equipe"]),
            "tipos de equipe", chaves=("tipos_equipe",))
    _tentar("tbSubTipoEquipe", _coletar_equipe(dominio["subtipos_equipe"]),
            "subtipos de equipe", chaves=("subtipos_equipe",))

    def _bucais(mapa):
        return {codigo: nome for codigo, nome in mapa.items()
                if any(t in _norm(nome) for t in TERMOS_EQUIPE_BUCAL)}

    dominio["tipos_bucais"] = _bucais(dominio["tipos_equipe"])
    dominio["subtipos_bucais"] = _bucais(dominio["subtipos_equipe"])
    print(f"[CNES] {len(dominio['ocupacoes'])} ocupações da família "
          f"{CBO_PREFIXO}; tipos de equipe de saúde bucal: "
          f"{sorted(dominio['tipos_bucais'])}; subtipos: "
          f"{sorted(dominio['subtipos_bucais'])}")
    if dominio["falhas"]:
        print(f"[CNES] domínio incompleto: {dominio['falhas']}. A próxima "
              "execução tenta de novo só o que faltou.")
    return dominio


def baixar_fatias(competencia, cache, rebaixar=False):
    """Grava as fatias filtradas em `cache`. Só baixa o que faltar."""
    alvos = {
        "estabelecimentos": cache / "estabelecimentos.csv",
        "vinculos": cache / "vinculos_odontologia.csv",
        "servicos": cache / "servicos_bucais.csv",
        "equipes": cache / "equipes.csv",
        "dominio": cache / "dominio.json",
    }
    if not rebaixar and all(p.exists() for p in alvos.values()):
        print(f"[CNES] usando fatias já filtradas em {cache}")
        return alvos

    nome = PADRAO.format(competencia=competencia)
    z = ZipRemotoFTP(HOST, DIRETORIO, nome)
    print(f"[CNES] {nome} — {z.tamanho() / 1048576:.0f} MB no servidor; "
          "lendo só os membros necessários")

    # O domínio vem PRIMEIRO aqui, ao contrário do projeto irmão: é ele que diz
    # quais códigos de equipe são de saúde bucal, e sem isso a fatia de equipes
    # não teria como ser interpretada.
    # Domínio incompleto não é domínio pronto: se a leitura anterior deixou
    # falhas, tenta de novo o que faltou — aproveitando o que já veio. Sem isto,
    # um `dominio.json` com duas tabelas faltando sobreviveria para sempre,
    # porque o arquivo existe.
    anterior = None
    if alvos["dominio"].exists() and not rebaixar:
        anterior = json.loads(alvos["dominio"].read_text(encoding="utf-8"))
        if not anterior.get("falhas"):
            anterior = None
    if rebaixar or not alvos["dominio"].exists() or anterior:
        alvos["dominio"].parent.mkdir(parents=True, exist_ok=True)
        alvos["dominio"].write_text(
            json.dumps(_ler_dominio(z, competencia, anterior),
                       ensure_ascii=False, indent=1),
            encoding="utf-8")

    if rebaixar or not alvos["estabelecimentos"].exists():
        alvo = _membro(z, "tbEstabelecimento", competencia)
        print(f"[CNES] lendo {alvo} ...")
        linhas, ativos, desabilitados = [], 0, 0
        with z.membro_arquivo(alvo, bloco=BLOCO) as f:
            for linha in csv.DictReader(f, delimiter=";"):
                unidade = _limpo(linha.get("CO_UNIDADE"))
                if not unidade:
                    continue
                # Guardamos TODOS, com uma coluna dizendo se está ativo. O
                # desabilitado precisa continuar conhecido: é ele que permite
                # distinguir "excluído de propósito" de "não encontrado", e essa
                # distinção é o que dá sentido à guarda de junção.
                ativo = "0" if _limpo(linha.get("CO_MOTIVO_DESAB")) else "1"
                if ativo == "1":
                    ativos += 1
                else:
                    desabilitados += 1
                linhas.append([unidade,
                               _limpo(linha.get("CO_MUNICIPIO_GESTOR")),
                               _limpo(linha.get("CO_ESTADO_GESTOR")),
                               ativo])
        _escrever(alvos["estabelecimentos"],
                  ["CO_UNIDADE", "CO_MUNICIPIO_GESTOR", "CO_ESTADO_GESTOR", "ATIVO"],
                  linhas)
        print(f"[CNES] {ativos} estabelecimentos ativos, "
              f"{desabilitados} desabilitados -> {alvos['estabelecimentos'].name}")

    if rebaixar or not alvos["vinculos"].exists():
        alvo = _membro(z, "tbCargaHorariaSus", competencia)
        print(f"[CNES] lendo {alvo} ... (leitura em fluxo)")
        linhas, total = [], 0
        with z.membro_arquivo(alvo, bloco=BLOCO) as f:
            for linha in csv.DictReader(f, delimiter=";"):
                total += 1
                cbo = _limpo(linha.get("CO_CBO"))
                if not cbo.startswith(CBO_PREFIXO):
                    continue
                linhas.append([_limpo(linha.get("CO_UNIDADE")),
                               _limpo(linha.get("CO_PROFISSIONAL_SUS")),
                               cbo,
                               _limpo(linha.get("TP_SUS_NAO_SUS"))])
        _escrever(alvos["vinculos"],
                  ["CO_UNIDADE", "CO_PROFISSIONAL_SUS", "CO_CBO", "TP_SUS_NAO_SUS"],
                  linhas)
        print(f"[CNES] {total} vínculos lidos; {len(linhas)} de CBO "
              f"{CBO_PREFIXO}xx -> {alvos['vinculos'].name}")

    if rebaixar or not alvos["servicos"].exists():
        alvo = _membro(z, "rlEstabServClass", competencia)
        print(f"[CNES] lendo {alvo} ...")
        linhas, total = [], 0
        with z.membro_arquivo(alvo, bloco=BLOCO) as f:
            for linha in csv.DictReader(f, delimiter=";"):
                total += 1
                if _limpo(linha.get("CO_SERVICO")) not in (SERVICO_ESPECIALIZADO,
                                                           SERVICO_PROTESE):
                    continue
                linhas.append([_limpo(linha.get("CO_UNIDADE")),
                               _limpo(linha.get("CO_SERVICO")),
                               _limpo(linha.get("CO_CLASSIFICACAO")),
                               _limpo(linha.get("ST_ATIVO_SN"))])
        _escrever(alvos["servicos"],
                  ["CO_UNIDADE", "CO_SERVICO", "CO_CLASSIFICACAO", "ST_ATIVO_SN"],
                  linhas)
        print(f"[CNES] {total} registros de serviço; {len(linhas)} dos serviços "
              f"{SERVICO_ESPECIALIZADO}/{SERVICO_PROTESE} "
              f"-> {alvos['servicos'].name}")

    if rebaixar or not alvos["equipes"].exists():
        alvo = _membro(z, "tbEquipe", competencia)
        print(f"[CNES] lendo {alvo} ...")
        linhas, total, colunas = [], 0, {}
        with z.membro_arquivo(alvo, bloco=BLOCO) as f:
            leitor = csv.DictReader(f, delimiter=";")
            campos = leitor.fieldnames or []
            colunas = {
                "unidade": _coluna(campos, "CO_UNIDADE"),
                "tipo": (_coluna(campos, "TP_EQUIPE")
                         or _coluna(campos, "CO_TIPO_EQUIPE")),
                "subtipo": _coluna(campos, "SUBTIPO") or _coluna(campos, "SUB_TIPO"),
                "codigo": _coluna(campos, "CO_EQUIPE"),
                "desativacao": (_coluna(campos, "DESATIVACAO")
                                or _coluna(campos, "DT_DESAT")),
            }
            if not colunas["unidade"] or not colunas["tipo"]:
                raise SystemExit(
                    "[ERRO] tbEquipe sem coluna de unidade ou de tipo. "
                    f"Colunas: {campos}")
            # A fatia guarda TODAS as equipes, não só as de saúde bucal: o
            # filtro depende do domínio, e um domínio que falhou não pode
            # obrigar a repetir uma leitura de uma hora. Filtrar é barato;
            # reler, não.
            for linha in leitor:
                total += 1
                linhas.append([
                    _limpo(linha.get(colunas["unidade"])),
                    _limpo(linha.get(colunas["tipo"])),
                    _limpo(linha.get(colunas["subtipo"])) if colunas["subtipo"] else "",
                    _limpo(linha.get(colunas["codigo"])) if colunas["codigo"] else "",
                    _limpo(linha.get(colunas["desativacao"])) if colunas["desativacao"] else "",
                ])
        _escrever(alvos["equipes"],
                  ["CO_UNIDADE", "TIPO", "SUBTIPO", "CO_EQUIPE", "DESATIVACAO"],
                  linhas)
        print(f"[CNES] {total} equipes lidas -> {alvos['equipes'].name} "
              f"(colunas: {colunas})")

    return alvos


# --------------------------------------------------------------------------- #
# FASE 2 — agregar (barata, refaz à vontade)
# --------------------------------------------------------------------------- #

def carregar_estabelecimentos(caminho):
    """Devolve (ativos, conhecidos): dict CO_UNIDADE->(mun, uf) e set de todos."""
    ativos, conhecidos = {}, set()
    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            unidade = linha["CO_UNIDADE"]
            conhecidos.add(unidade)
            if linha["ATIVO"] == "1" and linha["CO_MUNICIPIO_GESTOR"]:
                ativos[unidade] = (linha["CO_MUNICIPIO_GESTOR"],
                                   UF_POR_CODIGO.get(linha["CO_ESTADO_GESTOR"]))
    return ativos, conhecidos


def _classificar(unidade, ativos, conhecidos, contadores):
    """
    Resolve o município de um CO_UNIDADE e classifica o que não resolve.

    Três desfechos, e a diferença entre eles é o ponto:
      · resolveu                   -> devolve o município
      · existe, mas desabilitado   -> exclusão DELIBERADA, esperada, não é erro
      · não existe no cadastro     -> falha de junção; é isso que a guarda vigia
    """
    local = ativos.get(unidade)
    if local:
        return local[0]
    if unidade in conhecidos:
        contadores["desabilitado"] += 1
    else:
        contadores["sem_cadastro"] += 1
    return None


def forca_de_trabalho(caminho, ativos, conhecidos):
    """
    Conta PROFISSIONAIS DISTINTOS por município, não vínculos: o mesmo dentista
    pode ter três vínculos no mesmo município, e contá-lo três vezes
    transformaria precariedade de vínculo em abundância de força de trabalho.
    """
    sus, todos = defaultdict(set), defaultdict(set)
    contadores = defaultdict(int)
    por_cbo = defaultdict(int)
    total = 0
    with open(caminho, encoding="utf-8") as f:
        for i, linha in enumerate(csv.DictReader(f, delimiter=";")):
            total += 1
            por_cbo[linha["CO_CBO"]] += 1
            municipio = _classificar(linha["CO_UNIDADE"], ativos, conhecidos,
                                     contadores)
            if not municipio:
                continue
            chave = linha["CO_PROFISSIONAL_SUS"] or f"{linha['CO_UNIDADE']}:{i}"
            todos[municipio].add(chave)
            if linha["TP_SUS_NAO_SUS"].upper() == "S":
                sus[municipio].add(chave)
    return sus, todos, {
        "vinculos_odontologia": total,
        "vinculos_por_cbo": dict(sorted(por_cbo.items())),
        "vinculos_em_estabelecimento_desabilitado": contadores["desabilitado"],
        "vinculos_sem_cadastro": contadores["sem_cadastro"],
    }


def rede_especializada(caminho, ativos, conhecidos):
    """
    Estabelecimentos com serviço 114 (e 157) por município.

    Conta estabelecimentos DISTINTOS: um mesmo estabelecimento declara várias
    classificações do 114 — endodontia, periodontia, cirurgia —, e contar
    classificações no lugar de estabelecimentos inflaria a rede pelo número de
    especialidades de cada unidade.
    """
    especializada = defaultdict(set)
    protese = defaultdict(set)
    detalhe = defaultdict(lambda: defaultdict(set))
    contadores = defaultdict(int)
    total = inativos = 0

    with open(caminho, encoding="utf-8") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            total += 1
            if linha["ST_ATIVO_SN"].upper() == "N":
                inativos += 1
                continue
            municipio = _classificar(linha["CO_UNIDADE"], ativos, conhecidos,
                                     contadores)
            if not municipio:
                continue
            unidade = linha["CO_UNIDADE"]
            detalhe[municipio][
                f"{linha['CO_SERVICO']}/{linha['CO_CLASSIFICACAO']}"].add(unidade)
            if linha["CO_SERVICO"] == SERVICO_ESPECIALIZADO:
                especializada[municipio].add(unidade)
            elif linha["CO_SERVICO"] == SERVICO_PROTESE:
                protese[municipio].add(unidade)

    return especializada, protese, detalhe, {
        "servicos_bucais": total,
        "servicos_marcados_inativos": inativos,
        "servicos_em_estabelecimento_desabilitado": contadores["desabilitado"],
        "servicos_sem_cadastro": contadores["sem_cadastro"],
    }


def equipes_saude_bucal(caminho, ativos, conhecidos, tipos, subtipos):
    """
    Equipes de saúde bucal por município.

    `tipos` e `subtipos` vêm do domínio, e cada um é casado contra a SUA coluna:
    as duas numerações são próprias e se sobrepõem, então um conjunto único
    contra "tipo ou subtipo" contaria equipe errada assim que a numeração do
    subtipo mudasse.

    Os dois vazios significam que o domínio não foi lido: o indicador sai NULO,
    não zero. Zero afirmaria que não existe equipe de saúde bucal no país
    inteiro — afirmação forte sustentada por uma tabela de nomes que não pôde
    ser aberta.
    """
    if not tipos and not subtipos:
        return None, {"equipes_sem_dominio": True}

    tipos, subtipos = set(tipos), set(subtipos)
    por_municipio = defaultdict(set)
    contadores = defaultdict(int)
    total = casadas = desativadas = 0

    with open(caminho, encoding="utf-8") as f:
        for i, linha in enumerate(csv.DictReader(f, delimiter=";")):
            total += 1
            if linha["TIPO"] not in tipos and linha["SUBTIPO"] not in subtipos:
                continue
            casadas += 1
            # Equipe com data de desativação preenchida não existe mais. Mantê-la
            # contaria estrutura extinta como cobertura atual.
            if linha["DESATIVACAO"]:
                desativadas += 1
                continue
            municipio = _classificar(linha["CO_UNIDADE"], ativos, conhecidos,
                                     contadores)
            if not municipio:
                continue
            por_municipio[municipio].add(
                linha["CO_EQUIPE"] or f"{linha['CO_UNIDADE']}:{i}")

    return por_municipio, {
        "equipes_lidas": total,
        "equipes_de_saude_bucal": casadas,
        "equipes_desativadas": desativadas,
        "equipes_em_estabelecimento_desabilitado": contadores["desabilitado"],
        "equipes_sem_cadastro": contadores["sem_cadastro"],
    }


def conferir_juncao(casos, limite=LIMITE_SEM_CADASTRO):
    """
    Aborta se registros demais apontarem para CO_UNIDADE que não existe.

    Vigia SÓ o desconhecido. Somar o desabilitado junto reprovaria execuções
    corretas: estabelecimento fechado é exclusão que o indicador faz de
    propósito, e sua fração pode ser alta sem indicar defeito nenhum. Uma guarda
    que dispara no comportamento certo é pior que nenhuma, porque ensina a
    ignorá-la.
    """
    problemas = []
    for rotulo, total, desconhecidos, desabilitados in casos:
        if total <= 0:
            problemas.append(f"{rotulo}: nenhum registro lido")
            continue
        fracao = desconhecidos / total
        marca = "OK" if fracao <= limite else "FALHOU"
        print(f"[JUNCAO] {marca}  {rotulo}: {desconhecidos}/{total} "
              f"({fracao:.2%}) sem cadastro; "
              f"{desabilitados} ({desabilitados / total:.1%}) em "
              "estabelecimento desabilitado (exclusão esperada)")
        if fracao > limite:
            problemas.append(
                f"{rotulo}: {fracao:.2%} sem cadastro (limite {limite:.0%})")
    if problemas:
        raise SystemExit("[ERRO] junção CO_UNIDADE degradada:\n  - "
                         + "\n  - ".join(problemas))


def montar(competencia, sus, todos, especializada, protese, esb, detalhe,
           dominio, diagnostico):
    municipios = {}
    chaves = set(sus) | set(todos) | set(especializada) | set(protese)
    if esb:
        chaves |= set(esb)
    for codigo in chaves:
        municipios[codigo] = {
            "dentistas_sus": len(sus.get(codigo, ())) or None,
            "dentistas_total": len(todos.get(codigo, ())) or None,
            "estabelecimentos_esp_bucal": len(especializada.get(codigo, ())) or None,
            "estabelecimentos_lrpd": len(protese.get(codigo, ())) or None,
            # None quando o domínio não foi lido — e aí é ausência, não zero.
            "equipes_esb": ((len(esb.get(codigo, ())) or None)
                            if esb is not None else None),
            "servicos": sorted(detalhe.get(codigo, {})) or None,
        }

    arquivo = PADRAO.format(competencia=competencia)
    return {
        "metadados": {
            "fonte": "Cadastro Nacional de Estabelecimentos de Saúde (CNES/DATASUS)",
            "arquivo": arquivo,
            "url": f"ftp://{HOST}{DIRETORIO}/{arquivo}",
            "competencia": competencia,          # do ARQUIVO, não do calendário
            "cbo_familia": CBO_PREFIXO,
            "cbo_ocupacoes": (dominio or {}).get("ocupacoes") or {},
            "cbo_criterio": (
                "prefixo exato da família 2232 (cirurgião-dentista). Casar por "
                "texto capturaria docência e pesquisa em Odontologia, e também "
                "auxiliar e técnico em saúde bucal — ocupações próprias, de "
                "outra família, que este indicador não mede."
            ),
            "servicos": (dominio or {}).get("servicos") or {},
            "classificacoes": (dominio or {}).get("classificacoes") or {},
            "classificacoes_nao_viram_subgrupos": (
                "As classificações do serviço 114 são especialidades do mesmo "
                "serviço — dentística, endodontia, periodontia, cirurgia, "
                "atendimento a pessoa com deficiência e outras. Não são "
                "políticas opostas, como eram os subgrupos da rede "
                "psicossocial no observatório de Psicologia, e por isso não "
                "viram subgrupos: o estabelecimento é contado uma vez, e as "
                "classificações que ele declara saem no detalhe do município."
            ),
            "equipes_tipos_bucais": (dominio or {}).get("tipos_bucais") or {},
            "equipes_subtipos_bucais": (dominio or {}).get("subtipos_bucais") or {},
            "equipes_criterio": (
                "tipos e subtipos de equipe cujo nome no domínio contém 'saúde "
                "bucal' ou 'eSB', descobertos na competência lida e casados "
                "cada um contra a sua coluna. Entram tanto as equipes de saúde "
                "bucal propriamente ditas quanto as equipes de atenção básica "
                "que incluem saúde bucal — a pergunta é se há equipe COM saúde "
                "bucal na atenção primária do município. Os rótulos acima "
                "permitem refazer a conta com outro recorte."
            ),
            "habilitacao_ceo_nao_publicada": (
                "A habilitação de Centro de Especialidades Odontológicas não é "
                "relacionada a estabelecimento em nenhuma tabela-fato desta "
                "base: os códigos existem em tbSubGruposHabilitacao e não há "
                "tabela que os ligue a uma unidade. Por isso o indicador mede "
                "SERVIÇO DECLARADO (114) e se chama rede especializada — "
                "chamá-lo CEO afirmaria uma habilitação não lida."
            ),
            "lrpd_nao_entra_em_indice": (
                "O serviço 157 (laboratório de prótese dentária) é extraído e "
                "publicado como contagem própria. Não entra na rede "
                "especializada: é outra política, com outra unidade de conta."
            ),
            "dominio_nao_lido": (dominio or {}).get("falhas") or None,
            "extraido_em": date.today().isoformat(),
            "diagnostico": diagnostico,
        },
        "municipios": municipios,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--competencia", help="AAAAMM; default: a mais recente publicada")
    p.add_argument("--listar", action="store_true",
                   help="lista as competências disponíveis e sai")
    p.add_argument("--rebaixar", action="store_true",
                   help="ignora o cache de fatias e baixa tudo de novo")
    p.add_argument("--saida", default=str(DATA / "cobertura_cnes.json"))
    args = p.parse_args()

    if args.listar:
        comps = competencias_disponiveis()
        print(f"{len(comps)} competências: {comps[0]} .. {comps[-1]}")
        print("últimas 12:", ", ".join(comps[-12:]))
        return

    competencia = args.competencia
    if not competencia:
        comps = competencias_disponiveis()
        if not comps:
            raise SystemExit("[ERRO] nenhuma competência encontrada no FTP")
        competencia = comps[-1]
        print(f"[CNES] competência mais recente: {competencia}")

    cache = DADOS / f"cnes_{competencia}"
    fatias = baixar_fatias(competencia, cache, args.rebaixar)

    ativos, conhecidos = carregar_estabelecimentos(fatias["estabelecimentos"])
    print(f"[CNES] {len(ativos)} estabelecimentos ativos de "
          f"{len(conhecidos)} cadastrados")

    dominio = json.loads(fatias["dominio"].read_text(encoding="utf-8"))
    sus, todos, diag_ch = forca_de_trabalho(fatias["vinculos"], ativos, conhecidos)
    especializada, protese, detalhe, diag_sc = rede_especializada(
        fatias["servicos"], ativos, conhecidos)
    esb, diag_eq = equipes_saude_bucal(
        fatias["equipes"], ativos, conhecidos,
        dominio.get("tipos_bucais") or {}, dominio.get("subtipos_bucais") or {})

    casos = [
        ("vínculos de cirurgião-dentista",
         diag_ch["vinculos_odontologia"],
         diag_ch["vinculos_sem_cadastro"],
         diag_ch["vinculos_em_estabelecimento_desabilitado"]),
        ("serviços de saúde bucal",
         diag_sc["servicos_bucais"],
         diag_sc["servicos_sem_cadastro"],
         diag_sc["servicos_em_estabelecimento_desabilitado"]),
    ]
    if esb is not None:
        casos.append(("equipes de saúde bucal",
                      diag_eq["equipes_de_saude_bucal"],
                      diag_eq["equipes_sem_cadastro"],
                      diag_eq["equipes_em_estabelecimento_desabilitado"]))
    conferir_juncao(casos)

    saida = montar(competencia, sus, todos, especializada, protese, esb, detalhe,
                   dominio, {**diag_ch, **diag_sc, **diag_eq})
    Path(args.saida).parent.mkdir(parents=True, exist_ok=True)
    Path(args.saida).write_text(
        json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")

    n_forca = sum(1 for m in saida["municipios"].values() if m["dentistas_sus"])
    n_esp = sum(1 for m in saida["municipios"].values()
                if m["estabelecimentos_esp_bucal"])
    n_lrpd = sum(1 for m in saida["municipios"].values()
                 if m["estabelecimentos_lrpd"])
    print(f"\n[CNES] municípios com cirurgião-dentista no SUS: {n_forca}")
    print(f"[CNES] municípios com serviço 114 (rede especializada): {n_esp}")
    print(f"[CNES] municípios com laboratório de prótese (157): {n_lrpd}")
    if esb is None:
        print("[CNES] equipes de saúde bucal: NULO — o domínio não foi lido, "
              "e sem ele não há como saber quais códigos são de saúde bucal.")
    else:
        n_esb = sum(1 for m in saida["municipios"].values() if m["equipes_esb"])
        print(f"[CNES] municípios com equipe de saúde bucal: {n_esb}")
    print(f"[CNES] -> {args.saida}")


if __name__ == "__main__":
    main()
