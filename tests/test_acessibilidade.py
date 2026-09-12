"""
tests/test_acessibilidade.py
Acessibilidade MEDIDA, não presumida.

Existe por causa de uma troca concreta: este observatório herdou o design
system dos anteriores e trocou a paleta inteira — índigo/ardósia/âmbar por
grafite/verde-menta/ouro. Trocar paleta é a maneira mais fácil de reprovar em
contraste sem que nada quebre na tela: o site continua bonito e continua
ilegível para parte dos leitores.

O que se confere aqui:

  · contraste dos pares que existem de fato no CSS, contra os mínimos da WCAG
    (4,5 para texto normal; 3,0 para texto grande e elementos de interface);
  · monotonicidade da escala sequencial — uma rampa cuja luminância sobe no
    meio faz o mapa mentir sobre a ordem dos valores;
  · que a escala do CSS e a lista de reserva do `app.js` não divirjam: a cópia
    em JS é a que sobrevive a um `sed` na paleta e passa a desenhar com a cor
    de outro observatório, sem erro no console;
  · o que só o HTML gerado prova: `caption` e `scope` nas tabelas, `lang`,
    link de pular para o conteúdo, `alt` nas imagens, e a ORDEM das folhas de
    estilo (o leaflet.css tem de vir antes da do projeto, senão vence por
    cascata e devolve os controles de zoom a 30px).

Os testes de HTML são pulados quando `site/dist` não existe: rodar o build é
pré-requisito deles, e um teste que falha por falta de insumo vira ruído.

Roda como script ou sob pytest.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CSS = REPO / "site" / "static" / "css" / "style.css"
JS = REPO / "site" / "static" / "js" / "app.js"
DIST = REPO / "site" / "dist"

MINIMO_TEXTO = 4.5
MINIMO_GRANDE = 3.0


# --------------------------------------------------------------------------- #
# Contraste
# --------------------------------------------------------------------------- #

def _rgb(cor):
    cor = cor.strip().lstrip("#")
    if len(cor) == 3:
        cor = "".join(c * 2 for c in cor)
    return tuple(int(cor[i:i + 2], 16) for i in (0, 2, 4))


def _luminancia(cor):
    def canal(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(cor)
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def _contraste(a, b):
    la, lb = _luminancia(a), _luminancia(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _tokens():
    """Lê as variáveis do bloco `:root` do CSS do projeto."""
    texto = CSS.read_text(encoding="utf-8")
    bloco = texto[texto.index(":root {"):texto.index("}", texto.index(":root {"))]
    return {nome: valor for nome, valor in
            re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{3,6})\s*;", bloco)}


def test_pares_de_contraste_do_texto():
    """Cada par abaixo existe de fato no CSS — não é catálogo de possibilidades."""
    t = _tokens()
    pares = [
        ("corpo sobre o papel", t["text"], t["bg"], MINIMO_TEXTO),
        ("corpo sobre a superfície", t["text"], t["surface"], MINIMO_TEXTO),
        ("texto apagado sobre o papel", t["text-muted"], t["bg"], MINIMO_TEXTO),
        ("link sobre o papel", t["mid"], t["bg"], MINIMO_TEXTO),
        ("branco sobre a cor institucional", "#FFFFFF", t["deep"], MINIMO_TEXTO),
        ("branco sobre a secundária", "#FFFFFF", t["mid"], MINIMO_TEXTO),
        ("ouro sobre o papel", t["warm"], t["bg"], MINIMO_TEXTO),
        # Contorno de foco é elemento de interface: mínimo 3,0. Vale 4,5 aqui
        # porque a mesma variável também pinta texto.
        ("contorno de foco sobre o papel", t["warm"], t["bg"], MINIMO_GRANDE),
        # O destaque da manchete é texto grande sobre o fundo escuro.
        ("destaque da manchete sobre a institucional",
         t["warm-claro"], t["deep"], MINIMO_GRANDE),
    ]
    problemas = []
    for rotulo, frente, fundo, minimo in pares:
        valor = _contraste(frente, fundo)
        if valor < minimo:
            problemas.append(
                f"{rotulo}: {frente} sobre {fundo} = {valor:.2f} "
                f"(mínimo {minimo})")
    assert not problemas, "contraste insuficiente:\n  - " + "\n  - ".join(problemas)


def test_ouro_institucional_nao_e_usado_sobre_o_fundo_escuro():
    """
    O ouro mede 2,70 sobre o grafite. É por isso que existe --warm-claro.

    A regra que este teste protege é a que se perde primeiro numa refatoração:
    alguém troca `var(--warm-claro)` por `var(--warm)` para "usar a cor
    institucional", o número da manchete continua aparecendo, e passa a ter
    contraste de 2,70 — reprovado, e invisível para quem fez a troca.
    """
    t = _tokens()
    valor = _contraste(t["warm"], t["deep"])
    assert valor < MINIMO_GRANDE, (
        "o ouro institucional passou a ter contraste suficiente sobre o fundo "
        "escuro — se a paleta mudou, --warm-claro pode ter deixado de ser "
        "necessário, e este teste precisa ser revisto em vez de silenciado")


def test_neutros_sobre_o_fundo_escuro():
    """
    Os cinzas tingidos usados sobre a cor institucional.

    Vêm escritos direto nas regras, não em variáveis — herança do design system
    original. Ficam listados aqui porque é o único lugar onde uma troca de
    paleta os alcançaria: um `sed` na paleta que esquecesse um deles deixaria
    um texto quase invisível, e nada na tela avisaria.
    """
    t = _tokens()
    texto = CSS.read_text(encoding="utf-8")
    usados_sobre_escuro = ["#E7EFEC", "#F2F6F4", "#A9C3B9", "#D8E2DE",
                           "#BACEC7", "#C6D3CE", "#9DB5AC", "#AFC5BC",
                           "#E4EDE9"]
    problemas = []
    for cor in usados_sobre_escuro:
        if cor not in texto:
            problemas.append(f"{cor} não está mais no CSS — a lista envelheceu")
            continue
        valor = _contraste(cor, t["deep"])
        if valor < MINIMO_TEXTO:
            problemas.append(
                f"{cor} sobre {t['deep']} = {valor:.2f} (mínimo {MINIMO_TEXTO})")
    assert not problemas, "\n  - ".join(problemas)


def test_escala_sequencial_e_monotonica():
    """
    A luminância tem de cair a cada passo.

    Uma rampa que sobe no meio inverte a leitura do mapa num trecho da escala,
    e o leitor não tem como perceber: as cores continuam bonitas e a ordem
    continua errada.
    """
    t = _tokens()
    passos = [t[f"seq-{i}"] for i in range(1, 9)]
    anterior = None
    problemas = []
    for i, cor in enumerate(passos, 1):
        atual = _luminancia(cor)
        if anterior is not None and atual >= anterior:
            problemas.append(
                f"seq-{i} ({cor}) não é mais escuro que seq-{i - 1}")
        anterior = atual
    assert not problemas, "\n  - ".join(problemas)


def test_reserva_do_js_nao_diverge_do_css():
    """
    A lista de reserva do `app.js` tem de ser a mesma escala do CSS.

    O JS lê as variáveis do CSS em tempo de execução e só usa a lista embutida
    quando a leitura falha. Essa lista é justamente a cópia que sobrevive à
    troca de paleta: no observatório de Psicologia, uma cópia esquecida fez a
    tabela do índice e todo mapa sequencial desenharem no teal do observatório
    de Fonoaudiologia, sem erro no console e sem nada quebrar na tela.
    """
    t = _tokens()
    js = JS.read_text(encoding="utf-8")
    bloco = re.search(r"_escalaDoCss\('seq',\s*8,\s*\[(.*?)\]", js, re.S)
    assert bloco, "não achei a lista de reserva da escala sequencial no app.js"
    reserva = [c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}", bloco.group(1))]
    do_css = [t[f"seq-{i}"].upper() for i in range(1, 9)]
    assert reserva == do_css, (
        "a reserva do app.js divergiu do CSS:\n"
        f"  css: {do_css}\n  js : {reserva}")


def test_todo_passo_da_escala_aceita_algum_rotulo():
    """Cada faixa do mapa precisa comportar texto legível — branco ou escuro."""
    t = _tokens()
    problemas = []
    for i in range(1, 9):
        cor = t[f"seq-{i}"]
        claro = _contraste("#FFFFFF", cor)
        escuro = _contraste(t["text"], cor)
        if max(claro, escuro) < MINIMO_TEXTO:
            problemas.append(
                f"seq-{i} ({cor}): branco {claro:.2f}, escuro {escuro:.2f}")
    assert not problemas, ("faixas sem rótulo legível:\n  - "
                           + "\n  - ".join(problemas))


def test_cor_de_ausencia_esta_fora_da_paleta():
    """
    "Sem dados" não pode ser confundido com "valor baixo".

    O cinza de ausência precisa ficar distante da ponta clara da escala
    sequencial — se ele parecer o primeiro passo da rampa, a lacuna vira o
    menor valor do mapa aos olhos de quem lê.
    """
    t = _tokens()
    distancia = abs(_luminancia(t["nodata"]) - _luminancia(t["seq-1"]))
    assert distancia > 0.15, (
        f"o cinza de ausência ({t['nodata']}) tem luminância parecida com a do "
        f"primeiro passo da escala ({t['seq-1']}): diferença de {distancia:.3f}")


# --------------------------------------------------------------------------- #
# Regras que só o CSS prova
# --------------------------------------------------------------------------- #

def test_movimento_reduzido_e_respeitado():
    texto = CSS.read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in texto, (
        "sem bloco de prefers-reduced-motion")


def test_alvos_de_toque():
    """
    Os controles do Leaflet vêm com 30px e contraste 1,75 no padrão da
    biblioteca. As duas correções precisam continuar aqui.
    """
    texto = CSS.read_text(encoding="utf-8")
    assert re.search(r"\.leaflet-bar a\s*\{[^}]*44px", texto, re.S), (
        "os controles do mapa perderam o alvo de toque de 44px")
    assert re.search(r"\.botao\s*\{[^}]*min-height:\s*44px", texto), (
        "os botões perderam a altura mínima de 44px")


# --------------------------------------------------------------------------- #
# Regras que só o HTML gerado prova
# --------------------------------------------------------------------------- #

def _paginas():
    if not DIST.exists():
        return []
    # Uma de cada tipo basta: as páginas saem todas dos mesmos templates, e
    # varrer o site inteiro a cada execução do CI custa minutos para provar o
    # mesmo que sete provam.
    alvos = ["index.html", "comparar.html", "correlacoes.html", "indice.html",
             "glossario.html", "autor.html", "aviso-legal.html"]
    paginas = [DIST / a for a in alvos if (DIST / a).exists()]
    for padrao in ("uf/*.html", "municipio/*/*.html"):
        encontrados = sorted(DIST.glob(padrao))
        if encontrados:
            paginas.append(encontrados[0])
    return paginas


def test_tabelas_tem_legenda_e_escopo():
    paginas = _paginas()
    if not paginas:
        print("          (pulado: site/dist ausente — rode python site/build.py)")
        return
    problemas = []
    for pagina in paginas:
        html = pagina.read_text(encoding="utf-8")
        for tabela in re.findall(r"<table\b.*?</table>", html, re.S):
            if "<caption" not in tabela:
                problemas.append(f"{pagina.name}: tabela sem <caption>")
            for th in re.findall(r"<th\b[^>]*>", tabela):
                if "scope=" not in th:
                    problemas.append(f"{pagina.name}: <th> sem scope: {th[:60]}")
    assert not problemas, "\n  - ".join(sorted(set(problemas))[:12])


def test_paginas_declaram_idioma_e_link_de_pular():
    paginas = _paginas()
    if not paginas:
        print("          (pulado: site/dist ausente)")
        return
    problemas = []
    for pagina in paginas:
        html = pagina.read_text(encoding="utf-8")
        if '<html lang="pt-BR">' not in html:
            problemas.append(f"{pagina.name}: sem lang=pt-BR")
        if 'class="pular"' not in html:
            problemas.append(f"{pagina.name}: sem link de pular para o conteúdo")
        if 'id="conteudo"' not in html:
            problemas.append(f"{pagina.name}: sem âncora #conteudo")
    assert not problemas, "\n  - ".join(problemas)


def test_imagens_tem_alternativa_textual():
    paginas = _paginas()
    if not paginas:
        print("          (pulado: site/dist ausente)")
        return
    problemas = []
    for pagina in paginas:
        for img in re.findall(r"<img\b[^>]*>", pagina.read_text(encoding="utf-8")):
            if "alt=" not in img:
                problemas.append(f"{pagina.name}: {img[:70]}")
    assert not problemas, "imagens sem alt:\n  - " + "\n  - ".join(problemas)


def test_leaflet_css_vem_antes_do_css_do_projeto():
    """
    Ordem de cascata, não preferência de estilo.

    `leaflet.css` declara `.leaflet-touch .leaflet-bar a { width: 30px }` com a
    mesma especificidade das regras do projeto. Carregado DEPOIS, ele vence por
    ordem, e os controles de zoom voltam a 30px — abaixo do alvo de toque —
    sem que nada mais mude de aparência.
    """
    paginas = [p for p in _paginas() if "leaflet.css" in p.read_text(encoding="utf-8")]
    if not paginas:
        print("          (pulado: nenhuma página com mapa em site/dist)")
        return
    problemas = []
    for pagina in paginas:
        html = pagina.read_text(encoding="utf-8")
        if html.index("leaflet.css") > html.index("css/style.css"):
            problemas.append(f"{pagina.name}: leaflet.css depois de style.css")
    assert not problemas, "\n  - ".join(problemas)


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
    print(f"\n{len(testes) - falhas}/{len(testes)} testes de acessibilidade passaram.")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
