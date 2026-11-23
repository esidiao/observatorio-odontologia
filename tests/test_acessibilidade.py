"""
tests/test_acessibilidade.py
Acessibilidade MEDIDA, não presumida.

Estes observatórios compartilham um design system e trocam a paleta a cada
projeto. Trocar paleta é a maneira mais fácil de reprovar em contraste sem que
nada quebre na tela: o sítio continua bonito e continua ilegível para parte dos
leitores. Nenhum erro aparece no console, nenhum teste de dado falha, e a
página vai ao ar.

Foi assim que dois defeitos reais chegaram à produção nesta família:

  · no observatório de Psicologia, uma lista de cores esquecida no `app.js`
    fazia a tabela do índice e todo mapa sequencial desenharem no teal do
    observatório de Fonoaudiologia;

  · aqui o prefixo do cache do service worker ficou `psi-`, herdado
    da Psicologia: como os sítios da família moram na mesma origem
    do GitHub Pages, os dois disputavam o mesmo namespace.

O que se confere aqui:

  · contraste dos pares que existem de fato no CSS, contra os mínimos da WCAG
    (4,5 para texto normal; 3,0 para texto grande e elementos de interface);
  · monotonicidade da escala sequencial — uma rampa cuja luminância sobe no
    meio faz o mapa mentir sobre a ordem dos valores;
  · que a escala do CSS e a cópia do `app.js` não divirjam;
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


# --------------------------------------------------------------------------- #
# Estrutura do CSS: quem pinta escuro, e o que é escrito em cima
# --------------------------------------------------------------------------- #

def _regras(css):
    """(seletor, corpo) de cada regra de primeiro nível."""
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel = " ".join(m.group(1).split())
        if sel and not sel.startswith("@"):
            yield sel, m.group(2)


def _raizes_escuras(css):
    """Classes cujo bloco pinta o fundo com a cor institucional."""
    raizes = set()
    for sel, corpo in _regras(css):
        m = re.search(r"background(?:-color|-image)?\s*:\s*([^;]+);", corpo)
        if m and "var(--deep)" in m.group(1):
            for parte in sel.split(","):
                raiz = re.match(r"\.[\w-]+", parte.strip())
                if raiz:
                    raizes.add(raiz.group())
    return raizes


def _cores_sobre_escuro(css, raizes):
    """{cor: [seletores]} das cores de texto declaradas sob uma raiz escura."""
    achadas = {}
    for sel, corpo in _regras(css):
        for parte in sel.split(","):
            parte = parte.strip()
            if not any(parte == r or parte.startswith((r + "-", r + " ", r + ":"))
                       for r in raizes):
                continue
            for m in re.finditer(r"(?<!-)color\s*:\s*(#[0-9A-Fa-f]{3,6})\s*;",
                                 corpo):
                achadas.setdefault(m.group(1).upper(), []).append(parte)
    return achadas


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
        # Contorno de foco é elemento de interface: mínimo 3,0.
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


def test_a_institucional_nao_e_usada_como_texto_sobre_o_fundo_escuro():
    """
    A ouro institucional mede 2,70 sobre o fundo escuro. É por isso
    que `--warm-claro` existe.

    A regra que este teste protege é a que se perde primeiro numa refatoração:
    alguém troca `var(--warm-claro)` por `var(--warm)` para "usar a cor
    institucional", o número da manchete continua aparecendo, e passa a ter
    contraste reprovado — invisível para quem fez a troca.
    """
    t = _tokens()
    valor = _contraste(t["warm"], t["deep"])
    assert valor < MINIMO_GRANDE, (
        f"a cor institucional passou a ter contraste suficiente ({valor:.2f}) "
        "sobre o fundo escuro — se a paleta mudou, --warm-claro pode ter "
        "deixado de ser necessário, e este teste precisa ser revisto em vez "
        "de silenciado")


def test_cores_escritas_sobre_o_fundo_escuro():
    """
    Toda cor declarada numa área de fundo institucional, conferida.

    Estas cores vêm escritas direto nas regras, não em variáveis — herança do
    design system original. A lista NÃO está escrita aqui: é derivada do CSS a
    cada execução, procurando quem pinta o fundo com `var(--deep)` e descendo
    para os seletores que começam por essa classe.

    O motivo de derivar em vez de listar: uma lista à mão não enxerga regra
    nova. Acrescentar `.heroi-aviso { color: #9FB3C0; }` passaria batido para
    sempre, e o teste continuaria verde afirmando ter conferido a área.
    """
    t = _tokens()
    css = CSS.read_text(encoding="utf-8")
    raizes = _raizes_escuras(css)
    assert raizes, (
        "não achei nenhuma regra que pinte o fundo com var(--deep). Se a "
        "estrutura do CSS mudou, conserte este teste: sem raiz, ele não está "
        "aprovando nada, está calado.")

    cores = _cores_sobre_escuro(css, raizes)
    assert cores, (
        f"achei as áreas escuras {sorted(raizes)} mas nenhuma cor declarada "
        "dentro delas — o padrão perdeu o alvo")

    problemas = []
    for cor, seletores in sorted(cores.items()):
        valor = _contraste(cor, t["deep"])
        if valor < MINIMO_TEXTO:
            problemas.append(
                f"{cor} sobre {t['deep']} = {valor:.2f} "
                f"(mínimo {MINIMO_TEXTO}) em {seletores[:3]}")
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


def test_copia_da_escala_no_js_nao_diverge_do_css():
    """
    A escala que o JS usa tem de ser a mesma do CSS.

    A família tem duas formas, e as duas são procuradas aqui:

      · `escalaDoCss('seq', 8, [...])` lê as variáveis em tempo de execução e
        só usa a lista embutida se a leitura falhar;
      · `const SEQ = [...]` é lista fixa, que nunca lê o CSS — mais perigosa,
        porque diverge sem nenhuma condição de falha.

    Nos dois casos a cópia é o que sobrevive a um `sed` na paleta e passa a
    desenhar com a cor de outro observatório, sem erro no console e sem nada
    quebrar na tela. Foi o que aconteceu no observatório de Psicologia.
    """
    t = _tokens()
    js = JS.read_text(encoding="utf-8")
    bloco = None
    for padrao in (r"_?escalaDoCss\(\s*'seq',\s*8,\s*\[(.*?)\]",
                   r"const\s+SEQ\s*=\s*\[(.*?)\]"):
        bloco = re.search(padrao, js, re.S)
        if bloco:
            break
    assert bloco, (
        "não achei a escala sequencial no app.js, em nenhuma das formas "
        "conhecidas. Se a chamada foi renomeada, conserte o padrão aqui: um "
        "teste que não encontra o alvo não está aprovando nada, está calado.")
    copia = [c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}", bloco.group(1))]
    do_css = [t[f"seq-{i}"].upper() for i in range(1, 9)]
    assert copia == do_css, (
        "a cópia do app.js divergiu do CSS:\n"
        f"  css: {do_css}\n  js : {copia}")


def test_todo_passo_da_escala_carrega_rotulo_legivel():
    """
    Toda faixa do mapa precisa comportar número legível — por uma das duas vias.

    Há duas maneiras de garantir isso nesta família, e as duas valem:

      1. a faixa aceita texto branco OU escuro a 4,5 direto;
      2. o pintor da célula MISTURA a cor em direção ao papel até o texto
         escuro alcançar 4,5 (`_clarearAte`), o que conserta a faixa do meio
         sem interromper o degradê.

    A segunda existe porque tom intermediário não carrega nem branco nem
    escuro: é geometria da luminância, não descuido de paleta. Exigir só a
    primeira obrigaria a torcer a rampa para fugir do meio — e foi o que este
    teste fez quando chegou aqui, acusando `seq-5` num projeto que já resolve
    o caso em tempo de execução, com alvo medido de 4,5.
    """
    t = _tokens()
    js = JS.read_text(encoding="utf-8") if JS.exists() else ""
    mistura = re.search(r"_clarearAte\(\s*cor\s*,\s*([\d.]+)\s*\)", js)
    if mistura:
        alvo = float(mistura.group(1))
        assert alvo >= MINIMO_TEXTO, (
            f"o pintor da célula mistura até {alvo}, abaixo do mínimo de "
            f"{MINIMO_TEXTO} — o degradê fica contínuo e o número, ilegível")
        return

    problemas = []
    for i in range(1, 9):
        cor = t[f"seq-{i}"]
        claro = _contraste("#FFFFFF", cor)
        escuro = _contraste(t["text"], cor)
        if max(claro, escuro) < MINIMO_TEXTO:
            problemas.append(
                f"seq-{i} ({cor}): branco {claro:.2f}, escuro {escuro:.2f}")
    assert not problemas, (
        "faixas sem rótulo legível, e sem o clareamento que o resolveria "
        "(ver `_clarearAte` nos observatórios de Psicologia e Odontologia):"
        "\n  - " + "\n  - ".join(problemas))


def test_cor_de_ausencia_nao_parece_o_menor_valor():
    """
    "Sem dados" não pode ser lido como "o menor valor do mapa".

    A confusão que importa é com a PONTA CLARA da rampa: é lá que mora o menor
    valor, e é com ele que um cinza claro se confunde. O cinza da ausência tem
    luminância 0,607 em todos os projetos desta família, e as rampas vão de
    ~0,88 a ~0,03 — ou seja, o cinza cai DENTRO da faixa da rampa, por
    construção.

    Por isso a regra não pode ser "longe de todo passo". Uma primeira versão
    deste teste exigiu distância de 0,02 de cada um dos oito passos e reprovou
    três dos cinco observatórios — não porque três paletas estejam erradas,
    mas porque a exigência é impossível: um cinza dentro da faixa está sempre
    perto de algum passo. Teste que pede o impossível não protege nada; é
    desligado na primeira vez que atrapalha.

    O que de fato separa ausência de valor não é luminância, é SATURAÇÃO: o
    cinza não tem matiz e todo passo da rampa tem. A luminância só precisa
    garantir que ele não seja lido como o primeiro degrau.
    """
    t = _tokens()
    distancia = abs(_luminancia(t["nodata"]) - _luminancia(t["seq-1"]))
    assert distancia > 0.15, (
        f"o cinza de ausência ({t['nodata']}) tem luminância parecida com a do "
        f"primeiro passo da escala ({t['seq-1']}): diferença de {distancia:.3f}")


def test_movimento_reduzido_e_respeitado():
    """Quem pede menos movimento no sistema tem de receber menos movimento."""
    css = CSS.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css, (
        "o CSS não respeita `prefers-reduced-motion`")


def test_alvos_de_toque():
    """
    Controles precisam de área clicável suficiente.

    Não é regra estética: alvo pequeno é a barreira mais comum para quem tem
    tremor, usa o sítio em movimento ou tem dedo grande — que é todo mundo,
    alguma hora.
    """
    css = CSS.read_text(encoding="utf-8")
    problemas = []
    for sel, corpo in _regras(css):
        if not re.match(r"\.(chip|botao|aba|paginacao)", sel.strip()):
            continue
        alturas = re.findall(r"min-height\s*:\s*(\d+)px", corpo)
        for a in alturas:
            if int(a) < 32:
                problemas.append(f"{sel}: min-height {a}px")
    assert not problemas, ("alvo de toque pequeno demais:\n  - "
                           + "\n  - ".join(problemas))


# --------------------------------------------------------------------------- #
# O que só o HTML gerado prova
# --------------------------------------------------------------------------- #

def _paginas():
    if not DIST.exists():
        return []
    return sorted(DIST.rglob("*.html"))


def test_tabelas_tem_legenda_e_escopo():
    """
    Toda tabela precisa de `<caption>` e de `scope` nos cabeçalhos.

    Sem `caption`, quem navega por leitor de tela recebe "tabela, 12 colunas" e
    nenhuma pista do que ela contém. Sem `scope`, cada célula é lida solta, sem
    o cabeçalho que lhe dá sentido.
    """
    problemas = []
    for p in _paginas():
        html = p.read_text(encoding="utf-8")
        for m in re.finditer(r"<table\b.*?</table>", html, re.S):
            tabela = m.group()
            if "<caption" not in tabela:
                problemas.append(f"{p.name}: tabela sem <caption>")
            ths = re.findall(r"<th\b([^>]*)>", tabela)
            sem_escopo = [a for a in ths if "scope=" not in a]
            if ths and sem_escopo:
                problemas.append(
                    f"{p.name}: {len(sem_escopo)} de {len(ths)} <th> sem scope")
    assert not problemas, "\n  - ".join(sorted(set(problemas)))


def test_paginas_declaram_idioma_e_link_de_pular():
    problemas = []
    for p in _paginas():
        html = p.read_text(encoding="utf-8")
        if not re.search(r"<html[^>]*\blang=", html):
            problemas.append(f"{p.name}: <html> sem lang")
        if "pular" not in html.lower():
            problemas.append(f"{p.name}: sem link de pular para o conteúdo")
    assert not problemas, "\n  - ".join(sorted(set(problemas))[:20])


def test_imagens_tem_alternativa_textual():
    problemas = []
    for p in _paginas():
        html = p.read_text(encoding="utf-8")
        for m in re.finditer(r"<img\b([^>]*)>", html):
            if "alt=" not in m.group(1):
                problemas.append(f"{p.name}: <img> sem alt")
    assert not problemas, "\n  - ".join(sorted(set(problemas))[:20])


def test_leaflet_css_vem_antes_do_css_do_projeto():
    """
    A ordem das folhas decide quem vence por cascata.

    Com a do projeto antes, o leaflet.css vence e devolve os controles de zoom
    ao tamanho padrão de 30px — abaixo do alvo de toque que o projeto define.
    """
    problemas = []
    for p in _paginas():
        html = p.read_text(encoding="utf-8")
        i_leaflet = html.find("leaflet.css")
        i_projeto = html.find("css/style.css")
        if i_leaflet >= 0 and i_projeto >= 0 and i_leaflet > i_projeto:
            problemas.append(f"{p.name}: leaflet.css depois de style.css")
    assert not problemas, "\n  - ".join(sorted(set(problemas))[:20])


if __name__ == "__main__":
    falhas = 0
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  OK      {nome}")
            except AssertionError as e:
                falhas += 1
                print(f"  FALHOU  {nome}: {e}")
    total = sum(1 for n, f in globals().items()
                if n.startswith("test_") and callable(f))
    print(f"\n{total - falhas}/{total} testes de acessibilidade passaram.")
    sys.exit(1 if falhas else 0)
