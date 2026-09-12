"""
tests/test_links.py
Todo link e todo recurso do site gerado tem de existir no que é publicado.

Existe por um defeito concreto, encontrado só DEPOIS de publicar: o gerador
escrevia a planilha como `observatorio-psicologia.xlsx` — nome herdado do
observatório irmão — enquanto o template apontava para
`observatorio-odontologia.xlsx`. O link de download estava quebrado em todas as
páginas do site, e nada acusou: o build passou, os 49 testes passaram, e a
página continuou bonita. Um 404 não quebra build nenhum.

A verificação é feita contra o SISTEMA DE ARQUIVOS gerado, não contra a rede:
roda offline, no CI, antes de publicar. Links externos (http, mailto) ficam de
fora de propósito — quebram por motivo alheio ao repositório, e um teste que
falha por causa de terceiro é um teste que se aprende a ignorar.

Case-sensitive por opção: o GitHub Pages serve de um sistema de arquivos que
distingue maiúsculas, e o Windows, onde este projeto é desenvolvido, não. Sem
comparar caixa, `municipio/GO/goiania.html` passaria na máquina do autor e daria
404 no ar.

Pulado quando `site/dist` não existe — rode `python site/build.py` antes.
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "site" / "dist"

# Atributos que apontam para um recurso que precisa existir.
PADRAO = re.compile(r'(?:href|src)\s*=\s*"([^"]+)"', re.I)
EXTERNOS = ("http://", "https://", "mailto:", "tel:", "data:", "javascript:",
            "#")


def _paginas():
    return sorted(DIST.rglob("*.html")) if DIST.exists() else []


def _existe(caminho: Path) -> bool:
    """
    Existência sensível à caixa, mesmo em sistema de arquivos que não é.

    `Path.exists()` no Windows diz que `GOIANIA.html` e `goiania.html` são o
    mesmo arquivo. No Linux do GitHub Pages, não são — e é lá que o leitor
    recebe o 404.
    """
    if not caminho.exists():
        return False
    try:
        atual = caminho.resolve()
        return atual.name in {p.name for p in atual.parent.iterdir()}
    except OSError:
        return False


def test_todo_link_interno_aponta_para_arquivo_existente():
    paginas = _paginas()
    if not paginas:
        print("          (pulado: site/dist ausente — rode python site/build.py)")
        return

    quebrados = []
    for pagina in paginas:
        html = pagina.read_text(encoding="utf-8")
        for alvo in PADRAO.findall(html):
            alvo = alvo.strip()
            if not alvo or alvo.startswith(EXTERNOS):
                continue
            caminho = unquote(urlparse(alvo).path)
            if not caminho:
                continue
            destino = (DIST / caminho.lstrip("/") if caminho.startswith("/")
                       else (pagina.parent / caminho))
            if not _existe(destino):
                relativo = pagina.relative_to(DIST).as_posix()
                quebrados.append(f"{relativo} -> {alvo}")

    # Uma amostra basta para o diagnóstico: um nome de arquivo errado aparece em
    # todas as páginas de uma vez, e listar 339 ocorrências do mesmo defeito
    # esconde os outros.
    unicos = sorted({q.split(" -> ")[1] for q in quebrados})
    assert not quebrados, (
        f"{len(quebrados)} referências quebradas, {len(unicos)} alvos "
        f"distintos:\n  - " + "\n  - ".join(unicos[:15])
        + f"\n\nExemplos: " + "; ".join(quebrados[:5]))


def test_a_planilha_publicada_tem_o_nome_deste_projeto():
    """
    Guarda específica contra o defeito que originou este arquivo.

    O nome da planilha é gerado num lugar e referenciado em outro; quando os
    dois divergem, o resultado é um 404 silencioso. Aqui se confere que existe
    exatamente uma planilha em `dados/` e que o nome dela é o deste
    observatório — não o de um irmão.
    """
    if not DIST.exists():
        print("          (pulado: site/dist ausente)")
        return
    planilhas = sorted((DIST / "dados").glob("*.xlsx"))
    assert len(planilhas) == 1, (
        f"esperava uma planilha em dados/, achei {[p.name for p in planilhas]}")
    assert planilhas[0].name == "observatorio-odontologia.xlsx", (
        f"a planilha publicada chama-se {planilhas[0].name!r} — nome de outro "
        "projeto deixa o link de download quebrado em todas as páginas")


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
    print(f"\n{len(testes) - falhas}/{len(testes)} testes de links passaram.")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
