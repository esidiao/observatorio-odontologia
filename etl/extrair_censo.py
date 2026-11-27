"""
etl/extrair_censo.py
Extrai do Censo da Educação Superior o recorte de UM curso, lendo o ZIP do INEP
remotamente — sem baixar os 436 MB nem gravar o cadastro inteiro em disco.

Uso:
    python etl/extrair_censo.py --ano 2024
    python etl/extrair_censo.py --ano 2024 --rotulo "Odontologia"
    python etl/extrair_censo.py --ano 2024 --listar-rotulos odont

Saída (em etl/dados/, fora do versionamento):
    curso_<slug>_<ano>.csv   linhas do cadastro de cursos com o rótulo exato
    ies_<ano>.csv            cadastro de IES (CO_IES -> SG_UF_IES) do mesmo ano
    _censo_<ano>.json        proveniência: URL, md5 publicado, data de extração

MATCH EXATO NO RÓTULO CINE, NUNCA SUBSTRING
-------------------------------------------
`NO_CINE_ROTULO` é a classificação padronizada do INEP e a única chave estável
entre edições. A comparação é de igualdade sobre o rótulo normalizado (sem
acento, sem caixa, sem espaço nas bordas) — jamais `in`.

Aqui, e só aqui entre os observatórios da família, a substring daria o mesmo
resultado: medido no Censo 2024, existe **um único** rótulo contendo "odont" —
`Odontologia`, com 650 registros. Não há `Odontologia formação de professor`
nem vizinho de nome parecido, como havia `Psicopedagogia` (6.083 registros) ao
lado de `Psicologia`, ou `Administração pública` (3.530) ao lado de
`Administração`.

O critério continua sendo igualdade exata, e isso não é zelo decorativo: o
rótulo que hoje não existe pode existir na próxima edição, e um extrator que
passou a usar substring porque "dava no mesmo" não avisaria — ele simplesmente
começaria a somar o curso novo ao recorte, com todas as âncoras se deslocando
de uma vez. O diagnóstico `--listar-rotulos odont` refaz esta verificação em
qualquer edição futura; rode-o antes de confiar no número.

PROVENIÊNCIA VEM DO ARQUIVO, NUNCA DO CALENDÁRIO
------------------------------------------------
O ano gravado é o ano do MEMBRO lido dentro do ZIP, não `date.today().year - 1`.
Essa derivação por calendário rotula o Censo 2024 como 2025 durante todo o ano
seguinte, e o erro só aparece muito depois, num gráfico de série histórica.
"""
import argparse
import csv
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from rede import ZipLocal, ZipRemotoHTTP, sondar, tamanho_remoto

REPO = Path(__file__).parent.parent
DADOS = REPO / "etl" / "dados"
BASE_CENSO = "https://download.inep.gov.br/microdados/"

# O NOME DO ARQUIVO MUDA ENTRE EDIÇÕES, E ISSO JÁ CUSTOU UM ALERTA MUDO.
#
# Até 2024 o arquivo se chamava `microdados_censo_da_educacao_superior_AAAA`.
# Em 2025 o INEP tirou o "da": `microdados_censo_educacao_superior_2025.zip`.
# Enquanto houve um molde único com `{ano}`, o verificador semanal sondava um
# endereço que nunca existiu, recebia 404 e relatava "confirmadamente não
# publicado" — afirmação confiante e falsa sobre uma edição no ar.
#
# A lição não é "corrigir o molde", é que 404 em UM nome não prova ausência.
# Por isso aqui há uma LISTA, do mais novo para o mais antigo, e quem procura
# uma edição tenta todos antes de dizer que não existe.
PADROES_CENSO = (
    "microdados_censo_educacao_superior_{ano}.zip",      # 2025 em diante
    "microdados_censo_da_educacao_superior_{ano}.zip",   # até 2024
)


def urls_do_censo(ano):
    """Os endereços candidatos daquela edição, na ordem de tentativa."""
    return tuple(BASE_CENSO + p.format(ano=ano) for p in PADROES_CENSO)


def url_do_censo(ano):
    """
    Devolve (url, situacao): `True` se algum candidato respondeu, `False` se
    TODOS deram 404, `None` se houve falha de rede — porque não saber é
    diferente de não existir.
    """
    indeterminado = False
    for url in urls_do_censo(ano):
        existe, _ = sondar(url)
        if existe:
            return url, True
        if existe is None:
            indeterminado = True
    return None, (None if indeterminado else False)


def esquema_do_zip(z):
    """
    Diz se a edição usa o cadastro único ou as tabelas normalizadas.

    De 1995 a 2024 o Censo veio num cadastro só, com uma linha por curso E POR
    CAMADA (`TP_DIMENSAO`). Em 2025 o INEP normalizou: curso, local de oferta,
    aluno, docente, biblioteca e laboratório viraram tabelas separadas, e a
    camada deixou de existir como coluna.

    A detecção é pela PRESENÇA da tabela de local de oferta, não pelo ano: ano
    é calendário, estrutura é fato do arquivo.
    """
    return "normalizado" if z.localizar("tabela_local_oferta", ".csv") else "unico"


def codificacao_do_membro(z, alvo):
    """
    Descobre a codificação pelo BOM, em vez de supor.

    Até 2024 os CSV do Censo vinham em latin-1; o de 2025 veio em UTF-8 com
    BOM. Ler UTF-8 como latin-1 não levanta erro: devolve texto estragado, e
    `Administração` vira `AdministraÃ§Ã£o`. Como o recorte deste projeto é por
    IGUALDADE EXATA de rótulo, o efeito seria ZERO linha casada — um recorte
    vazio, que o resto do pipeline trataria como "curso que não existe nesta
    edição" em vez de erro de leitura.
    """
    for pedaco in z.membro_blocos(alvo, bloco=4096):
        return "utf-8-sig" if pedaco.startswith(b"\xef\xbb\xbf") else "latin-1"
    return "latin-1"

# csv do INEP tem campos longos (ementas, listas); o padrão de 128 KB estoura.
csv.field_size_limit(1 << 24)


def normalizar(texto):
    """Maiúsculas sem acento, espaços colapsados. Só para COMPARAR rótulos."""
    s = unicodedata.normalize("NFD", str(texto or "").strip().upper())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def slug(texto):
    s = normalizar(texto).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def espelho_do_ano(caminho, ano):
    """
    O arquivo do espelho que corresponde à edição pedida.

    Aceita o ZIP direto ou um diretório que o contenha. O nome TEM de casar com
    um padrão conhecido daquele ano: um espelho de outra edição publicaria o
    ano errado com toda a proveniência parecendo certa.
    """
    p = Path(caminho)
    nomes = [padrao.format(ano=ano) for padrao in PADROES_CENSO]
    if p.is_dir():
        for nome in nomes:
            if (p / nome).is_file():
                return p / nome
        raise SystemExit(
            f"[ERRO] nenhum ZIP do Censo {ano} em {p}. Procurei:\n  "
            + "\n  ".join(nomes))
    if p.name not in nomes:
        raise SystemExit(
            f"[ERRO] o espelho {p.name} não é um nome conhecido do Censo {ano}.\n"
            "Nomes aceitos:\n  " + "\n  ".join(nomes) + "\n"
            "Renomear o arquivo para passar daqui publicaria a edição errada "
            "com a proveniência parecendo correta.")
    return p


def abrir_censo(ano, espelho=None):
    """
    Abre o ZIP da edição pedida e devolve (z, url, alvo_curso, alvo_ies, origem).

    `origem` é a parte da proveniência que diz DE ONDE os bytes vieram. Ela
    nunca é omitida: um conjunto gerado de espelho não conferido tem de dizer
    isso de si mesmo.
    """
    url, situacao = url_do_censo(ano)
    if espelho:
        local = espelho_do_ano(espelho, ano)
        bytes_locais = local.stat().st_size
        if url is None:
            # Sem rede não há com o que comparar. O espelho é lido, e a
            # proveniência diz que a conferência não aconteceu -- silenciar a
            # diferença entre "conferido" e "não deu para conferir" seria
            # exatamente o erro que o verificador de fontes já cometeu.
            url = BASE_CENSO + local.name
            conferido, publicado = None, None
        else:
            publicado = tamanho_remoto(url)
            if publicado and publicado != bytes_locais:
                raise SystemExit(
                    "[ERRO] o espelho não é o arquivo publicado.\n"
                    f"  espelho:   {local} ({bytes_locais} bytes)\n"
                    f"  publicado: {url} ({publicado} bytes)\n"
                    "Baixe o arquivo outra vez. Publicar a partir de um "
                    "espelho que não confere é publicar sem proveniência.")
            conferido = bool(publicado)
        print(f"[CENSO] lendo do espelho local: {local}")
        print("[CENSO] conferência de tamanho contra o publicado: "
              + ("ok" if conferido else "NÃO FOI POSSÍVEL (sem rede)"))
        z = ZipLocal(local)
        origem = {"lido_de": "espelho local",
                  "espelho": str(local),
                  "espelho_bytes": bytes_locais,
                  "espelho_confere_com_publicado": conferido,
                  "bytes_publicados": publicado}
    else:
        if url is None:
            tentados = "\n  ".join(urls_do_censo(ano))
            if situacao is None:
                raise SystemExit(
                    f"[ERRO] não consegui verificar a edição {ano} do Censo (falha "
                    f"de rede). Tentei:\n  {tentados}\n"
                    "Falha de rede não é ausência: tente de novo em vez de "
                    "concluir que a edição não existe.")
            raise SystemExit(
                f"[ERRO] edição {ano} do Censo não encontrada. Tentei:\n  {tentados}\n"
                "Se o INEP renomeou o arquivo outra vez, acrescente o padrão novo "
                "em PADROES_CENSO -- não troque o que já está lá, porque os anos "
                "anteriores continuam com o nome antigo.")
        z = ZipRemotoHTTP(url)
        origem = {"lido_de": "INEP por Range"}
    alvo_curso = z.localizar("cadastro_cursos", ".csv") or z.localizar("curso", ".csv")
    alvo_ies = z.localizar("_ies_", ".csv") or z.localizar("ies", ".csv")
    if not alvo_curso:
        raise SystemExit(f"[ERRO] cadastro de cursos não encontrado em {url}")
    if not alvo_ies:
        raise SystemExit(f"[ERRO] cadastro de IES não encontrado em {url}")
    return z, url, alvo_curso, alvo_ies, origem


def ano_do_membro(nome, ano_pedido):
    """
    Confere que o membro lido é mesmo da edição pedida.

    Barato e vale a pena: se o INEP republicar o zip de 2024 contendo o CSV de
    2023, todo o resto do pipeline produziria números plausíveis e errados.
    """
    anos = {int(m.group()) for m in re.finditer(r"(?:19|20)\d{2}", nome)}
    if anos and ano_pedido not in anos:
        raise SystemExit(
            f"[ERRO] membro '{nome}' não corresponde ao ano pedido {ano_pedido}. "
            "Proveniência tem de vir do arquivo — abortando em vez de rotular errado.")
    return ano_pedido


def listar_rotulos(z, alvo_curso, filtro):
    """Diagnóstico: mostra rótulos CINE que contêm o filtro, com contagem."""
    alvo = normalizar(filtro)
    contagem = {}
    with z.membro_arquivo(
            alvo_curso,
            encoding=codificacao_do_membro(z, alvo_curso)) as f:
        leitor = csv.DictReader(f, delimiter=";")
        if "NO_CINE_ROTULO" not in (leitor.fieldnames or []):
            raise SystemExit("[ERRO] coluna NO_CINE_ROTULO ausente no cadastro")
        for linha in leitor:
            rotulo = linha.get("NO_CINE_ROTULO") or ""
            if alvo in normalizar(rotulo):
                contagem[rotulo] = contagem.get(rotulo, 0) + 1
    print(f"\nRótulos CINE contendo '{filtro}':")
    for rotulo, n in sorted(contagem.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6d}  {rotulo!r}")
    if not contagem:
        print("  (nenhum)")
    print("\nUse o rótulo EXATO em --rotulo. Substring capturaria os demais.")
    return contagem


def extrair(ano, rotulo, rebaixar=False, espelho=None):
    """
    Extrai o recorte da edição pedida, no esquema que ela usar.

    Os dois caminhos convivem de propósito. O de cadastro único não é legado a
    ser removido: a série histórica compara 2021 a 2024, e essas edições só
    existem naquele formato. Apagar o leitor antigo apagaria a série.
    """
    z, url, alvo_curso, alvo_ies, origem = abrir_censo(ano, espelho)
    ano_do_membro(alvo_curso, ano)
    DADOS.mkdir(parents=True, exist_ok=True)
    esquema = esquema_do_zip(z)
    print(f"[CENSO] esquema do arquivo: {esquema}")
    if esquema == "normalizado":
        return _extrair_normalizado(z, url, ano, rotulo, alvo_curso, alvo_ies,
                                    rebaixar=rebaixar, origem=origem)
    return _extrair_unico(z, url, ano, rotulo, alvo_curso, alvo_ies,
                          origem=origem)


def _cache_vale(caminho, prov_anterior, campo, rebaixar):
    """
    Quantas linhas a fatia tem, se ela puder ser reaproveitada — ou `None`.

    EXISTIR NÃO BASTA, E ISSO NÃO É ZELO EXCESSIVO.

    Uma extração deste projeto caiu no último passo depois de gravar as tabelas
    pesadas, e a fatia de aluno ficou em disco TRUNCADA, escrita por uma versão
    anterior à gravação atômica. A execução seguinte viu o arquivo, aceitou
    como cache e seguiu: 844 dos 1.076 cursos do recorte, um prefixo da tabela.
    Nada falhou, nenhum total ficou absurdo, e os percentuais de perfil foram
    publicados sobre 78% dos cursos.

    Então o que autoriza o reaproveitamento é a PROVENIÊNCIA da última execução
    completa — que é escrita por último, depois de todas as fatias, e portanto é
    a declaração de que a passada inteira terminou. O número declarado tem de
    bater com o que está no arquivo; divergência significa relê.
    """
    if rebaixar or not caminho.exists():
        return None
    declarado = prov_anterior.get(campo)
    if not isinstance(declarado, int):
        print(f"[CENSO] {caminho.name} existe, mas nenhuma execução completa "
              f"declarou `{campo}` — relendo em vez de confiar no arquivo.")
        return None
    em_disco = _linhas(caminho)
    if em_disco != declarado:
        print(f"[CENSO] {caminho.name} tem {em_disco} linhas e a proveniência "
              f"declara {declarado} — fatia inconsistente, relendo.")
        return None
    return em_disco


def _linhas(caminho):
    """Quantas linhas de dado a fatia tem (sem o cabeçalho)."""
    with open(caminho, encoding="utf-8", newline="") as f:
        return max(sum(1 for _ in f) - 1, 0)


def _niveis_da_fatia(caminho):
    """
    Os níveis de agregação presentes numa fatia de aluno já gravada.

    Reconferido ao reaproveitar o cache, e não só ao extrair: a proveniência
    declara qual nível foi somado, e declarar sem olhar seria pior que não
    declarar.
    """
    niveis = {}
    with open(caminho, encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            nivel = (linha.get("TP_NIVEL_AGREGACAO") or "").strip()
            niveis[nivel] = niveis.get(nivel, 0) + 1
    return niveis


def _fatiar(z, alvo, destino, rotulo, encoding, extra=None,
            rotulo_coluna="NO_CINE_ROTULO", extra_coluna=None):
    """
    Copia para `destino` só as linhas do rótulo pedido, lendo em fluxo.

    `extra` é um filtro adicional que recebe o VALOR de `extra_coluna` naquela
    linha (não a linha inteira). Devolve (lidas, escritas, descartadas_pelo_extra).

    POR QUE NÃO `DictReader`
    ------------------------
    A tabela de local de oferta tem 78 colunas e milhões de linhas; a de aluno
    tem 284. `DictReader` monta um dicionário por linha — uma chave por coluna
    — para que daqui se leia UMA célula, e `DictWriter` o desmonta de volta.
    Medido nesta máquina, com quatro extrações da família em paralelo, o custo
    projetava cinco horas por observatório, com o disco parado: o gargalo era
    parsing, não leitura.

    Com `csv.reader` a linha é uma tupla, o índice da coluna do rótulo é achado
    uma vez, e a normalização é MEMORIZADA por valor cru — o Censo tem algumas
    centenas de rótulos distintos, então ela roda umas centenas de vezes em vez
    de milhões. A saída é a mesma: mesmo dialeto, mesma ordem de colunas.
    """
    alvo_norm = normalizar(rotulo)
    lidas = escritas = barradas = 0
    # Normalização memorizada: NFD + filtro de acento + regex por linha era o
    # segundo custo por linha, para comparar um rótulo que se repete.
    normalizados = {}
    # Escreve em `.parcial` e renomeia no fim.
    #
    # Sem isso, uma queda no meio de uma tabela de 1,6 GB deixa em disco um
    # arquivo com cara de pronto, e a execucao seguinte o aceita como cache.
    # O recorte sairia truncado sem nada acusar — e truncado, em estatistica,
    # e pior que ausente: continua plausivel.
    parcial = destino.with_suffix(destino.suffix + ".parcial")
    with z.membro_arquivo(alvo, encoding=encoding) as f:
        leitor = csv.reader(f, delimiter=";")
        campos = next(leitor, None)
        if not campos:
            raise SystemExit(f"[ERRO] {alvo} veio vazio")
        if rotulo_coluna not in campos:
            raise SystemExit(
                f"[ERRO] coluna {rotulo_coluna} ausente em {alvo}")
        i_rotulo = campos.index(rotulo_coluna)
        i_extra = campos.index(extra_coluna) if extra_coluna else None
        if extra is not None and i_extra is None:
            raise SystemExit(
                "[ERRO] filtro extra sem coluna declarada: quem filtra tem de "
                "dizer por qual coluna, senão a linha inteira voltaria a ser "
                "montada só para isso")
        largura = len(campos)
        with open(parcial, "w", encoding="utf-8", newline="") as saida:
            escritor = csv.writer(saida, delimiter=";")
            escritor.writerow(campos)
            for linha in leitor:
                lidas += 1
                if lidas % 2_000_000 == 0:
                    print(f"      {lidas:,} linhas lidas...".replace(",", "."))
                if len(linha) != largura:
                    # Linha com número de colunas diferente do cabeçalho não é
                    # filtrada em silêncio: ou o arquivo está corrompido, ou o
                    # dialeto está errado, e as duas coisas invalidam a fatia.
                    raise SystemExit(
                        f"[ERRO] linha {lidas} de {alvo} tem {len(linha)} "
                        f"colunas, e o cabeçalho tem {largura}")
                cru = linha[i_rotulo]
                norm = normalizados.get(cru)
                if norm is None:
                    norm = normalizados[cru] = normalizar(cru)
                if norm != alvo_norm:
                    continue
                if extra is not None and not extra(linha[i_extra]):
                    barradas += 1
                    continue
                escritor.writerow(linha)
                escritas += 1
    parcial.replace(destino)
    return lidas, escritas, barradas


# Nível de agregação da tabela de aluno. A MESMA pessoa é contada em três
# níveis — IES, curso e local de oferta —, então somar a tabela inteira
# triplica o país. O projeto agrega por CURSO, que é a unidade de todo o
# resto do pipeline.
NIVEL_IES, NIVEL_CURSO, NIVEL_LOCAL = "1", "2", "3"


def _extrair_normalizado(z, url, ano, rotulo, alvo_curso, alvo_ies,
                         rebaixar=False, origem=None):
    """
    Tabelas separadas — edições de 2025 em diante.

    O que mudou, e por que cada peça é lida de onde é lida:

      · `tabela_curso` tem uma linha por curso, com a localização do LOCAL DE
        OFERTA (colunas `*_LO`), o formato de oferta e as quantidades totais.
        Não tem mais `TP_DIMENSAO`: a camada virou atributo de outra tabela.
      · `tabela_local_oferta` tem uma linha por (local x curso), com as
        bandeiras `IN_SEDE`, `IN_LOCAL_OFERTA_POLO` e afins. É de lá que sai
        o alcance territorial da EaD, que antes era a camada 2.
      · `tabela_aluno` tem o perfil (sexo, raça, turno, financiamento, apoio,
        reserva), que saiu da tabela de cursos. Aqui se lê só o nível de
        curso, pelo motivo explicado em NIVEL_CURSO.
    """
    enc_curso = codificacao_do_membro(z, alvo_curso)
    destino = DADOS / f"curso_{slug(rotulo)}_{ano}.csv"

    # A proveniencia da ultima execucao COMPLETA e o que autoriza reaproveitar
    # fatia. Ver `_cache_vale`: existir nao basta, porque arquivo truncado
    # tambem existe.
    prov_anterior = {}
    caminho_prov = DADOS / f"_censo_{ano}.json"
    if caminho_prov.exists():
        try:
            prov_anterior = json.loads(caminho_prov.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            prov_anterior = {}
    if prov_anterior.get("rotulo_cine") and \
            normalizar(prov_anterior["rotulo_cine"]) != normalizar(rotulo):
        # Proveniencia de outro recorte nao diz nada sobre estas fatias.
        prov_anterior = {}

    print(f"[CENSO] {url}")
    print(f"[CENSO] rótulo exato: {rotulo!r}  (codificação: {enc_curso})")
    print(f"[CENSO] curso: {alvo_curso.split('/')[-1]}")

    escritas = _cache_vale(destino, prov_anterior, "linhas_curso", rebaixar)
    lidas = None
    if escritas is not None:
        print(f"[CENSO] {destino.name} já em disco e declarado completo "
              f"({escritas} linhas) — pulando. Use --rebaixar para reler.")
    else:
        lidas, escritas, _ = _fatiar(z, alvo_curso, destino, rotulo, enc_curso)
        print(f"[CENSO] {lidas} cursos no país, {escritas} do rótulo exato "
              f"-> {destino.name}")
    if not escritas:
        raise SystemExit(
            f"[ERRO] nenhum curso com o rótulo exato {rotulo!r}. "
            "Antes de concluir que a edição não traz o curso, confira a "
            "codificação: UTF-8 lido como latin-1 estraga todo rótulo "
            "acentuado e produz exatamente este sintoma.")

    # Local de oferta: é a tabela maior do pacote (1,6 GB em 2025).
    alvo_local = z.localizar("tabela_local_oferta", ".csv")
    destino_local = DADOS / f"local_oferta_{slug(rotulo)}_{ano}.csv"
    escritas_lo = _cache_vale(destino_local, prov_anterior,
                              "linhas_local_oferta", rebaixar)
    if escritas_lo is not None:
        print(f"[CENSO] {destino_local.name} já em disco e declarado completo "
              f"({escritas_lo} linhas) — pulando a tabela de 1,6 GB.")
    else:
        print(f"[CENSO] local de oferta: {alvo_local.split('/')[-1]} "
              f"({z.indice()[alvo_local]['usz'] / 1048576:.0f} MB, leitura em fluxo)")
        _lidas_lo, escritas_lo, _ = _fatiar(
            z, alvo_local, destino_local, rotulo,
            codificacao_do_membro(z, alvo_local))
        print(f"[CENSO] {_lidas_lo} locais no país, {escritas_lo} do recorte "
              f"-> {destino_local.name}")

    # Aluno: só o nível de curso, para não contar a mesma pessoa três vezes.
    alvo_aluno = z.localizar("tabela_aluno", ".csv")
    destino_aluno = DADOS / f"aluno_{slug(rotulo)}_{ano}.csv"
    niveis = {}

    def so_nivel_curso(valor):
        nivel = (valor or "").strip()
        niveis[nivel] = niveis.get(nivel, 0) + 1
        return nivel == NIVEL_CURSO

    niveis_na_fonte = None
    escritas_al = _cache_vale(destino_aluno, prov_anterior,
                              "linhas_aluno_nivel_curso", rebaixar)
    if escritas_al is not None:
        niveis = _niveis_da_fatia(destino_aluno)
        print(f"[CENSO] {destino_aluno.name} já em disco e declarado completo "
              f"({escritas_al} linhas) — pulando a tabela de 796 MB.")
    else:
        print(f"[CENSO] aluno: {alvo_aluno.split('/')[-1]} "
              f"({z.indice()[alvo_aluno]['usz'] / 1048576:.0f} MB, leitura em fluxo)")
        lidas_al, escritas_al, barradas_al = _fatiar(
            z, alvo_aluno, destino_aluno, rotulo,
            codificacao_do_membro(z, alvo_aluno), extra=so_nivel_curso,
            extra_coluna="TP_NIVEL_AGREGACAO")
        niveis_na_fonte = dict(niveis)
        niveis = _niveis_da_fatia(destino_aluno)
        print(f"[CENSO] {lidas_al} linhas de aluno, {escritas_al} do recorte no "
              f"nível de curso ({barradas_al} descartadas por serem repetição "
              f"em outro nível) -> {destino_aluno.name}")
    print(f"[CENSO] níveis na fatia: {niveis} | na fonte: "
          f"{niveis_na_fonte if niveis_na_fonte else 'não remedido (cache)'}")

    # Cadastro de IES.
    destino_ies = DADOS / f"ies_{ano}.csv"
    n_ies = 0
    with z.membro_arquivo(alvo_ies, encoding=codificacao_do_membro(z, alvo_ies)) as f, \
            open(destino_ies, "w", encoding="utf-8", newline="") as saida:
        leitor = csv.DictReader(f, delimiter=";")
        escritor = csv.DictWriter(saida, fieldnames=leitor.fieldnames, delimiter=";")
        escritor.writeheader()
        for linha in leitor:
            escritor.writerow(linha)
            n_ies += 1
    print(f"[CENSO] {n_ies} IES -> {destino_ies.name}")

    md5_alvo = z.localizar("md5", ".txt")
    md5_texto = z.membro_texto(md5_alvo).strip() if md5_alvo else None

    prov = {
        "fonte": "Censo da Educação Superior (INEP)",
        "url": url,
        "ano_censo": ano,
        "esquema": "normalizado",
        "esquema_nota": (
            "A partir de 2025 o INEP publica os microdados em tabelas "
            "separadas e a coluna TP_DIMENSAO deixou de existir. As camadas "
            "presencial/polo/sede viraram bandeiras na tabela de local de "
            "oferta, e o perfil do aluno saiu para tabela própria."),
        "membro_cursos": alvo_curso,
        "membro_local_oferta": alvo_local,
        "membro_aluno": alvo_aluno,
        "membro_ies": alvo_ies,
        "codificacao": enc_curso,
        "rotulo_cine": rotulo,
        "md5_publicado": md5_texto,
        # `None` quando a fatia veio do cache: o cadastro nao foi relido, e
        # repetir o numero da execucao anterior seria afirmar uma leitura que
        # nao aconteceu.
        "linhas_cadastro": lidas,
        "fatias_reaproveitadas": lidas is None,
        "linhas_curso": escritas,
        "linhas_local_oferta": escritas_lo,
        "linhas_aluno_nivel_curso": escritas_al,
        # DOIS campos, porque sao duas perguntas diferentes:
        #   na_fatia  o que ficou gravado aqui — tem de ser so o nivel 2;
        #   na_fonte  o que a tabela do INEP trazia — so e conhecido quando a
        #             tabela foi realmente lida nesta execucao.
        # Um nome so para os dois mudava de sentido conforme o caminho, e um
        # campo que muda de sentido em silencio e pior que um campo ausente.
        "aluno_niveis_na_fatia": niveis,
        "aluno_niveis_na_fonte": niveis_na_fonte,
        "aluno_nivel_usado": NIVEL_CURSO,
        "aluno_nivel_nota": (
            "A tabela de aluno conta a MESMA pessoa em três níveis de "
            "agregação (1 IES, 2 curso, 3 local de oferta). Só o nível 2 é "
            "lido; somar a tabela inteira triplicaria o país. No nível 3 o "
            "INEP suprime contagens de até três por proteção de dado pessoal, "
            "então perfil por local de oferta teria buraco nos lugares "
            "pequenos."),
        "ies_cadastradas": n_ies,
        "extraido_em": date.today().isoformat(),
        **(origem or {"lido_de": "INEP por Range"}),
    }
    (DADOS / f"_censo_{ano}.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[CENSO] proveniência -> _censo_{ano}.json")
    return prov


def _extrair_unico(z, url, ano, rotulo, alvo_curso, alvo_ies, origem=None):
    """
    Cadastro único — edições de 1995 a 2024.

    Não é legado a ser removido: a série histórica compara edições anteriores,
    e elas só existem neste formato. Apagar o leitor antigo apagaria a série.
    """
    alvo_norm = normalizar(rotulo)
    destino = DADOS / f"curso_{slug(rotulo)}_{ano}.csv"

    print(f"[CENSO] {url}")
    print(f"[CENSO] membro: {alvo_curso}")
    print(f"[CENSO] rótulo exato: {rotulo!r}  (normalizado: {alvo_norm!r})")

    lidas = escritas = 0
    vizinhos = {}          # rótulos que substring capturaria — para o relatório
    # 4 letras: o fragmento de "Odontologia" vira "ODON". No Censo 2024 ele não
    # casa com nenhum outro rótulo — medido — e por isso o relatório abaixo sai
    # vazio nesta edição. Ele fica de pé mesmo assim: é exatamente o aviso que
    # precisa disparar na primeira edição em que o INEP criar um rótulo vizinho.
    fragmento = alvo_norm.split()[0][:4]

    with z.membro_arquivo(
            alvo_curso,
            encoding=codificacao_do_membro(z, alvo_curso)) as f:
        leitor = csv.DictReader(f, delimiter=";")
        campos = leitor.fieldnames or []
        if "NO_CINE_ROTULO" not in campos:
            raise SystemExit("[ERRO] coluna NO_CINE_ROTULO ausente no cadastro")
        with open(destino, "w", encoding="utf-8", newline="") as saida:
            escritor = csv.DictWriter(saida, fieldnames=campos, delimiter=";")
            escritor.writeheader()
            for linha in leitor:
                lidas += 1
                if lidas % 2_000_000 == 0:
                    print(f"      {lidas:,} linhas lidas...".replace(",", "."))
                rotulo_linha = linha.get("NO_CINE_ROTULO") or ""
                norm = normalizar(rotulo_linha)
                if norm == alvo_norm:
                    escritor.writerow(linha)
                    escritas += 1
                elif fragmento and fragmento in norm:
                    vizinhos[rotulo_linha] = vizinhos.get(rotulo_linha, 0) + 1

    print(f"[CENSO] {lidas} linhas lidas, {escritas} do rótulo exato -> {destino.name}")
    if vizinhos:
        print("[CENSO] rótulos que uma busca por substring teria capturado por engano:")
        for r, n in sorted(vizinhos.items(), key=lambda kv: -kv[1]):
            print(f"          {n:6d}  {r!r}")

    # cadastro de IES: pequeno, e indispensável para atribuir as vagas EaD à
    # UF-sede da mantenedora (CO_IES -> SG_UF_IES).
    destino_ies = DADOS / f"ies_{ano}.csv"
    with z.membro_arquivo(alvo_ies) as f, \
            open(destino_ies, "w", encoding="utf-8", newline="") as saida:
        leitor = csv.DictReader(f, delimiter=";")
        escritor = csv.DictWriter(saida, fieldnames=leitor.fieldnames, delimiter=";")
        escritor.writeheader()
        n_ies = 0
        for linha in leitor:
            escritor.writerow(linha)
            n_ies += 1
    print(f"[CENSO] {n_ies} IES -> {destino_ies.name}")

    # md5 publicado pelo próprio INEP: âncora de proveniência que não depende
    # de data nem de cabeçalho HTTP.
    md5_alvo = z.localizar("md5", ".txt")
    md5_texto = z.membro_texto(md5_alvo).strip() if md5_alvo else None

    prov = {
        "fonte": "Censo da Educação Superior (INEP)",
        "url": url,
        "ano_censo": ano,                    # do MEMBRO, não do calendário
        "membro_cursos": alvo_curso,
        "membro_ies": alvo_ies,
        "rotulo_cine": rotulo,
        "md5_publicado": md5_texto,
        "linhas_cadastro": lidas,
        "linhas_curso": escritas,
        "ies_cadastradas": n_ies,
        "extraido_em": date.today().isoformat(),
        **(origem or {"lido_de": "INEP por Range"}),
    }
    (DADOS / f"_censo_{ano}.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[CENSO] proveniência -> _censo_{ano}.json")
    return prov


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ano", type=int, default=2025)
    p.add_argument("--rotulo", default="Odontologia",
                   help="rótulo CINE EXATO (default: Odontologia)")
    p.add_argument("--rebaixar", action="store_true",
                   help="relê as fatias mesmo que já estejam em disco")
    p.add_argument("--espelho", metavar="CAMINHO",
                   help="lê o ZIP de um espelho local (arquivo ou "
                        "diretório) em vez de baixar do INEP; o "
                        "tamanho é conferido contra o publicado")
    p.add_argument("--listar-rotulos", metavar="TRECHO",
                   help="diagnóstico: lista rótulos CINE contendo TRECHO e sai")
    args = p.parse_args()

    if args.listar_rotulos:
        z, _url, alvo_curso, _ies, _origem = abrir_censo(
            args.ano, args.espelho)
        listar_rotulos(z, alvo_curso, args.listar_rotulos)
        return

    extrair(args.ano, args.rotulo, rebaixar=args.rebaixar,
            espelho=args.espelho)


if __name__ == "__main__":
    main()
