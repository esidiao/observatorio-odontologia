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

from rede import ZipRemotoHTTP, sondar

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


def abrir_censo(ano):
    url, situacao = url_do_censo(ano)
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
            "em PADROES_CENSO — não troque o que já está lá, porque os anos "
            "anteriores continuam com o nome antigo.")
    z = ZipRemotoHTTP(url)
    if esquema_do_zip(z) == "normalizado":
        raise SystemExit(
            f"[ERRO] a edição {ano} do Censo vem no esquema NORMALIZADO (tabelas "
            "separadas, sem TP_DIMENSAO), e este repositório ainda só sabe ler o "
            "cadastro único.\n"
            "Parar aqui é deliberado: a tabela de cursos nova tem "
            "NO_CINE_ROTULO, então o recorte funcionaria e o resto do pipeline "
            "produziria um país VAZIO em silêncio — SG_UF e TP_DIMENSAO não "
            "existem mais, e toda linha cairia em 'sem localização'.\n"
            "O leitor do esquema novo está em observatorio-fisioterapia.")
    alvo_curso = z.localizar("cadastro_cursos", ".csv") or z.localizar("curso", ".csv")
    alvo_ies = z.localizar("_ies_", ".csv") or z.localizar("ies", ".csv")
    if not alvo_curso:
        raise SystemExit(f"[ERRO] cadastro de cursos não encontrado em {url}")
    if not alvo_ies:
        raise SystemExit(f"[ERRO] cadastro de IES não encontrado em {url}")
    return z, url, alvo_curso, alvo_ies


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


def extrair(ano, rotulo):
    z, url, alvo_curso, alvo_ies = abrir_censo(ano)
    ano_do_membro(alvo_curso, ano)
    DADOS.mkdir(parents=True, exist_ok=True)

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
    }
    (DADOS / f"_censo_{ano}.json").write_text(
        json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[CENSO] proveniência -> _censo_{ano}.json")
    return prov


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ano", type=int, default=2024)
    p.add_argument("--rotulo", default="Odontologia",
                   help="rótulo CINE EXATO (default: Odontologia)")
    p.add_argument("--listar-rotulos", metavar="TRECHO",
                   help="diagnóstico: lista rótulos CINE contendo TRECHO e sai")
    args = p.parse_args()

    if args.listar_rotulos:
        z, _url, alvo_curso, _ies = abrir_censo(args.ano)
        listar_rotulos(z, alvo_curso, args.listar_rotulos)
        return

    extrair(args.ano, args.rotulo)


if __name__ == "__main__":
    main()
