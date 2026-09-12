"""
tests/test_catalogo.py
Fecha o ciclo do catálogo: nada é publicado sem entrada, nada tem entrada sem
ser publicado.

`site/catalogo.py` já garante que glossário e metadados de formatação sejam a
mesma estrutura. O que ele não pode saber sozinho é se o conjunto de dados
publicado ganhou um campo novo que ninguém registrou — e é exatamente aí que
nasce o defeito que este teste existe para pegar: um campo sem entrada cai no
fallback de três casas decimais, e uma contagem de 19 municípios aparece na
página como "19,000".

Roda como script ou sob pytest.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "site"))

import catalogo  # noqa: E402

NACIONAL = REPO / "data" / "nacional.json"

# Importada do catálogo, não redigitada. Uma segunda lista de escalas de
# contagem divergiria da primeira em silêncio — e este arquivo existe
# justamente para pegar divergências silenciosas entre duas cópias da mesma
# informação. Seria irônico introduzir uma aqui.
CONTAGENS = catalogo.CONTAGENS


def test_catalogo_integro():
    problemas = catalogo.validar()
    assert not problemas, "catálogo inconsistente:\n  - " + "\n  - ".join(problemas)


def test_contagem_nunca_tem_casa_decimal():
    """
    Uma contagem formatada com casa decimal vira outro número em pt-BR.

    `19` com três casas é renderizado `19,000`, que um leitor brasileiro lê como
    dezenove mil. O erro não gera exceção, não quebra layout e não aparece em
    revisão de código — só na tela, para quem confiar nele.
    """
    erradas = [i["key"] for i in catalogo.INDICADORES
               if i["escala"] in CONTAGENS and i["dec"] != 0]
    assert not erradas, f"contagens com casa decimal: {erradas}"


def test_toda_direcao_e_declarada():
    sem = [i["key"] for i in catalogo.INDICADORES
           if i["dir"] not in catalogo.DIRECOES]
    assert not sem, f"indicadores sem direção normativa válida: {sem}"


def test_escala_de_cor_e_completa():
    """min e max andam juntos: um sozinho produz cor indefinida em silêncio."""
    quebrados = [i["key"] for i in catalogo.INDICADORES
                 if (i["min"] is None) != (i["max"] is None)]
    assert not quebrados, f"escala de cor incompleta: {quebrados}"


def _campos_publicados():
    """
    Todos os campos que chegam a alguma página: UF E município.

    Varrer só o nacional.json deixaria os campos exclusivos das páginas
    municipais — `polos_ead`, `cursos_presencial` — fora da conferência, e são
    justamente eles que o catálogo é mais propenso a esquecer, porque não
    aparecem em nenhuma tabela de estado.
    """
    if not NACIONAL.exists():
        return None
    dados = json.loads(NACIONAL.read_text(encoding="utf-8"))
    campos = set()
    for uf in dados["ufs"].values():
        campos |= set(uf)
    for arquivo in sorted((REPO / "data" / "municipios").glob("*.json")):
        conteudo = json.loads(arquivo.read_text(encoding="utf-8"))
        for municipio in conteudo["municipios"]:
            campos |= set(municipio)
    return campos


def test_todo_campo_publicado_esta_no_catalogo():
    campos = _campos_publicados()
    if campos is None:
        print("[AVISO] data/nacional.json ausente; teste de cobertura pulado.")
        return
    orfaos = sorted(campos - set(catalogo.POR_CHAVE)
                    - catalogo.CAMPOS_NAO_INDICADORES)
    assert not orfaos, (
        "campos publicados sem entrada no catálogo — cairiam no fallback de "
        f"formatação: {orfaos}\n"
        "Registre em site/catalogo.py, ou declare em CAMPOS_NAO_INDICADORES "
        "se for identificador e não medida.")


def test_todo_indicador_do_catalogo_e_publicado():
    """
    O inverso: entrada de glossário para campo que não existe mais.

    Não quebra a página, mas mente para o leitor — descreve um indicador que
    ele nunca vai encontrar em lugar nenhum do site.
    """
    campos = _campos_publicados()
    if campos is None:
        print("[AVISO] data/nacional.json ausente; teste de cobertura pulado.")
        return
    fantasmas = sorted(set(catalogo.POR_CHAVE) - campos)
    assert not fantasmas, (
        f"indicadores no catálogo que não são publicados: {fantasmas}")


def test_categoria_da_ead_existe_e_tem_conteudo():
    """
    A seção de EaD do site sai da CATEGORIA do catálogo, não de uma lista de
    chaves no template. Se a categoria sumir ou esvaziar, a seção some da
    página sem erro nenhum — e o achado que ela existe para mostrar vai junto.
    """
    assert catalogo.CATEGORIA_EAD in catalogo.CATEGORIAS
    da_ead = catalogo.da_categoria(catalogo.CATEGORIA_EAD)
    assert len(da_ead) >= 5, (
        f"só {len(da_ead)} indicadores na categoria {catalogo.CATEGORIA_EAD!r}")


def test_as_tres_coberturas_medem_campos_diferentes():
    """
    ICAB, ICRE e ICSB saem todos do CNES e ficam na mesma categoria — a
    separação que importa aqui não é de fonte, é de OBJETO: profissional,
    serviço especializado e equipe da atenção primária.

    O risco concreto que este teste cobre é o de dois índices passarem a
    contar o mesmo campo depois de uma refatoração de nomes. Eles continuariam
    na página, com siglas diferentes e valores idênticos, e nada acusaria: um
    índice duplicado não quebra build nenhum.
    """
    indices = ("ICAB", "ICRE", "ICSB")
    for key in indices:
        assert key in catalogo.POR_CHAVE, f"{key} sumiu do catálogo"
        assert catalogo.POR_CHAVE[key]["cat"] == "Cobertura — saúde", (
            f"{key} saiu da categoria de cobertura em saúde")

    contagens = ("municipios_com_dentista", "municipios_com_esp_bucal",
                 "municipios_com_esb")
    for key in contagens:
        assert key in catalogo.POR_CHAVE, (
            f"{key} não está no catálogo: cada índice de cobertura precisa da "
            "contagem que o origina, senão o leitor vê a fração sem o número")

    explicacoes = {catalogo.POR_CHAVE[k]["oque"] for k in indices}
    assert len(explicacoes) == 3, (
        "dois índices de cobertura estão com a mesma explicação — sinal de "
        "cópia entre entradas, que é como um índice duplicado passa")

    siglas = {catalogo.POR_CHAVE[k]["sigla"] for k in indices}
    assert len(siglas) == 3, "siglas repetidas entre os índices de cobertura"


def test_laboratorio_de_protese_nao_e_indice():
    """
    O serviço 157 é publicado como contagem, nunca como fração de cobertura.

    Ele mede outra política, com outra unidade de conta. Um índice chamado
    "cobertura de prótese" seria lido como acesso à reabilitação protética, que
    não é o que o cadastro diz — o laboratório atende uma região inteira.
    """
    for key in ("municipios_com_lrpd", "estabelecimentos_lrpd"):
        ind = catalogo.POR_CHAVE.get(key)
        assert ind, f"{key} não está no catálogo"
        assert ind["escala"] in catalogo.CONTAGENS, (
            f"{key} deixou de ser contagem: escala {ind['escala']!r}")


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
    print(f"\n{len(testes) - falhas}/{len(testes)} testes de catálogo passaram.")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
