# -*- coding: utf-8 -*-
"""site/marca.py
Gera a marca do Observatório Nacional da Formação em Odontologia.

Uso:
    python site/marca.py

Fica no repositório porque a marca é obra do projeto e precisa ser
reproduzível: o SVG e os seis PNGs saem todos daqui, e regerá-los depois de
mexer na paleta é uma linha de comando, não um trabalho de desenho.

O desenho é um dente: coroa arredondada e duas raízes. É a forma mais
reconhecível da área em qualquer tamanho, e sobrevive ao favicon de 32 px, onde
um símbolo com traço fino vira borrão. As duas raízes carregam as cores
secundárias da paleta — verde e ouro —, do mesmo modo que o observatório de
Administração pinta os nós do organograma: a marca leva a paleta, sem
acrescentar matiz.

SVG e PNG saem da MESMA geometria, declarada uma vez abaixo. Desenhar duas
vezes é exatamente como o ícone e o favicon divergem sem ninguém notar.
"""
import pathlib

from PIL import Image, ImageDraw

DESTINO = pathlib.Path(__file__).resolve().parent / "static" / "img"

FUNDO = "#232B29"
CLARO = "#F2F6F4"
MID = "#8FB4A9"     # verde claro: mede 6,4 contra o grafite, legível a 32 px
WARM = "#D8A73A"    # ouro claro, a variante que a paleta reserva a fundo escuro

# Geometria em unidades de um viewBox 64x64.
RAIO_CAIXA = 12
# Coroa: elipse. cx, cy, rx, ry
COROA = (32, 27, 17, 15)
# Raízes: quadriláteros que saem de dentro da coroa e afinam para baixo.
RAIZ_ESQ = [(21, 33), (30, 33), (27.5, 53), (23.5, 53)]
RAIZ_DIR = [(34, 33), (43, 33), (40.5, 53), (36.5, 53)]


def _caminho(pontos):
    d = f"M{pontos[0][0]} {pontos[0][1]}"
    for x, y in pontos[1:]:
        d += f"L{x} {y}"
    return d + "Z"


def svg():
    cx, cy, rx, ry = COROA
    return "\n".join([
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img"'
        ' aria-label="Observatório Nacional da Formação em Odontologia">',
        f'  <rect width="64" height="64" rx="{RAIO_CAIXA}" fill="{FUNDO}"/>',
        '  <!-- Dente: coroa em papel, raízes nas duas cores secundárias.',
        '       As raízes começam DENTRO da coroa, para que a junção não',
        '       apareça como emenda em tamanho pequeno. -->',
        f'  <path d="{_caminho(RAIZ_ESQ)}" fill="{MID}"/>',
        f'  <path d="{_caminho(RAIZ_DIR)}" fill="{WARM}"/>',
        f'  <ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{CLARO}"/>',
        '</svg>',
    ]) + "\n"


def png(lado, maskable=False):
    """
    Desenha em 8x e reduz: o Pillow não tem antialias em `ellipse` nem em
    `polygon`, e sem a supersamostragem a coroa sai serrilhada justamente no
    tamanho em que o ícone é mais visto.
    """
    escala = 8
    n = lado * escala
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = n / 64  # uma unidade do viewBox em pixels

    # `maskable` tem zona de segurança: o Android recorta um círculo de 80% da
    # área. O conteúdo encolhe para caber nela e o fundo vai até a borda, sem
    # cantos arredondados — quem arredonda é o sistema.
    if maskable:
        d.rectangle([0, 0, n, n], fill=FUNDO)
        margem = 0.10
    else:
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=RAIO_CAIXA * u, fill=FUNDO)
        margem = 0.0

    def px(x, y):
        if not margem:
            return x * u, y * u
        centro = 32
        return ((centro + (x - centro) * (1 - 2 * margem)) * u,
                (centro + (y - centro) * (1 - 2 * margem)) * u)

    # As raízes vêm antes da coroa, na mesma ordem do SVG: a coroa cobre o topo
    # delas, e é isso que faz a junção desaparecer.
    d.polygon([px(x, y) for x, y in RAIZ_ESQ], fill=MID)
    d.polygon([px(x, y) for x, y in RAIZ_DIR], fill=WARM)

    cx, cy, rx, ry = COROA
    a, b = px(cx - rx, cy - ry)
    c, e = px(cx + rx, cy + ry)
    d.ellipse([a, b, c, e], fill=CLARO)

    return img.resize((lado, lado), Image.LANCZOS)


def main():
    (DESTINO / "favicon.svg").write_text(svg(), encoding="utf-8", newline="\n")
    png(32).save(DESTINO / "favicon-32.png")
    png(180).save(DESTINO / "favicon-180.png")
    png(192).save(DESTINO / "icon-192.png")
    png(512).save(DESTINO / "icon-512.png")
    png(192, maskable=True).save(DESTINO / "icon-maskable-192.png")
    png(512, maskable=True).save(DESTINO / "icon-maskable-512.png")
    print("marca gerada:")
    for f in sorted(DESTINO.glob("*")):
        if f.name != "autor.jpg":
            print(f"  {f.name:26s} {f.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
