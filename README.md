# Observatório Nacional da Formação em Odontologia

Site estático data-driven com indicadores de acesso territorial, qualidade e
cobertura assistencial dos cursos de Odontologia no Brasil.

Quinto de uma família de projetos irmãos — e independentes:
[Farmácia](https://github.com/esidiao/observatorio-formacao-farmaceutica),
[Fonoaudiologia](https://github.com/esidiao/observatorio-fonoaudiologia),
[Psicologia](https://github.com/esidiao/observatorio-psicologia) e
[Administração](https://github.com/esidiao/observatorio-administracao).
Compartilham o método e o design system; não compartilham código, dados nem
dependências.

## O que este observatório mede

| | Odontologia (Censo 2024) | Psicologia, para escala |
|---|---:|---:|
| Registros no Censo, rótulo CINE exato | **650** | 1.372 |
| UFs com oferta presencial | **27** de 27 | 27 |
| Municípios com oferta presencial | **303** | 498 |
| Vagas presenciais | **118.747** | 276.250 |
| Vagas EaD | **0** | 549 |
| Polos de EaD | **0** | 114 |
| Matrículas | **162.750** | 375.574 |
| Concluintes | **26.883** | 49.065 |
| IES distintas | **576** | 1.032 |
| Cursos avaliados | **452** (CPC 2023) | 746 (CPC 2022) |

### A manchete é uma ausência

**Não existe Odontologia a distância no Brasil.** As 650 ofertas registradas no
Censo 2024 são presenciais — nenhuma vaga de EaD, nenhum polo, em nenhuma
unidade da federação —, e os 452 cursos avaliados no CPC 2023 também são todos
presenciais.

Esse zero é **medido, não presumido**. O cadastro do Censo separa a oferta em
três camadas (`TP_DIMENSAO`: presencial, polo de EaD, sede de EaD) e as três
foram lidas: só a primeira tem linha. Nos observatórios irmãos a modalidade vai
de 0,2% da capacidade em Psicologia a 66,9% em Administração — aqui ela é
exatamente nada.

A consequência é de produto, não só de dado: como `pct_ead` é constante,
**ele fica fora da matriz de correlação** (variável sem variância não tem
correlação fraca, tem correlação indefinida) e a EaD não aparece em mapa nem em
coluna comparativa. Os campos continuam publicados no conjunto de dados,
valendo zero, e a ausência é declarada numa seção própria — que é onde ela
informa alguma coisa.

### O rótulo CINE não tem vizinho, e o critério exato continua

Medido no Censo 2024: `Odontologia` é o **único** rótulo que contém "odont".
Diferente de `Psicologia`, que convive com `Psicopedagogia` (6.083 registros),
ou de `Administração`, ao lado de `Administração pública` (3.530).

Ainda assim o recorte usa **igualdade exata** sobre o rótulo normalizado. O
vizinho que hoje não existe pode existir na próxima edição, e um extrator que
passou a usar substring porque "dava no mesmo" somaria o curso novo sem avisar.
O diagnóstico refaz a verificação em qualquer edição:

```bash
python etl/extrair_censo.py --ano 2024 --listar-rotulos odont
```

## Estrutura

```
/etl                        Scripts ETL (Python): extração, índices, pipeline
/data                       Dados versionados: nacional.json, _proveniencia.json
/etl/dados                  Recortes brutos — NÃO versionados (ver .gitignore)
/site                       Gerador estático (Python/Jinja2) + templates + assets
/site/marca.py              Gera o SVG e os seis PNGs da marca
/site/dist                  Site gerado — NÃO versionar
/tests                      Portão de qualidade (GO) + integridade + acessibilidade
/.github/workflows/ci.yml   CI: valida -> constrói -> publica
```

## Fontes

| Indicador | Fonte | Acesso |
|---|---|---|
| Oferta, vagas, matrículas | Censo da Educação Superior 2024 (INEP) | HTTPS, ZIP lido por `Range` |
| CPC, IDD, ENADE, perfil docente | **CPC 2023** (INEP), área ODONTOLOGIA | HTTPS |
| Cirurgiões-dentistas no SUS | CNES — vínculos com CBO `2232xx` | FTP DATASUS |
| Rede especializada | CNES — serviço **114**, atenção especializada à saúde bucal | FTP DATASUS |
| Equipes de saúde bucal | CNES — `tbEquipe`, tipos com saúde bucal | FTP DATASUS |
| Laboratório de prótese | CNES — serviço **157** | FTP DATASUS |
| População e municípios | IBGE (agregado 6579; API de localidades) | HTTPS |

### Por que três indicadores de cobertura, e não um

O ICON de Farmácia mede municípios com Farmácia Popular; o ICAP de Psicologia,
municípios com psicólogo no SUS. Nenhum transfere. Para Odontologia a pergunta
"onde existe rede pública que absorve quem se forma?" tem três respostas, e
nenhuma fonte única as dá:

* **ICAB** — municípios com ao menos um cirurgião-dentista vinculado ao SUS
  (família CBO 2232, por prefixo exato);
* **ICRE** — municípios com estabelecimento que declara o serviço 114 do CNES,
  a atenção especializada à saúde bucal;
* **ICSB** — municípios com equipe de saúde bucal na atenção primária.

São publicados separadamente. Fundi-los exigiria arbitrar um peso entre "tem
dentista", "tem centro de especialidades" e "tem equipe na atenção básica" — e
peso arbitrário é estimativa disfarçada. Foi a mesma decisão tomada em
Fonoaudiologia e em Psicologia.

### O indicador não se chama CEO, e isso é deliberado

O Centro de Especialidades Odontológicas é o nome da política; o que a base
publica é o **serviço declarado**. Os códigos de habilitação de CEO existem em
`tbSubGruposHabilitacao` e **nenhuma tabela-fato do CNES os relaciona a
estabelecimento** — a mesma limitação que o observatório de Fonoaudiologia
mediu para o CER. Chamar o indicador de CEO afirmaria uma habilitação que não
foi lida.

Pela mesma razão o **laboratório de prótese dentária** (serviço 157) sai como
contagem própria, fora de qualquer índice: ele atende uma região, não o
município onde está instalado.

### O que o CNES entende por "equipe de saúde bucal"

Os códigos não estão fixados no extrator: ele lê `tbTipoEquipe` e
`tbSubTipoEquipe` e seleciona os tipos cujo nome contém "saúde bucal" ou "eSB",
gravando na proveniência os que casaram. Na competência 202607 são sete, e não
são a mesma coisa: duas são equipes de saúde bucal propriamente ditas (ESB e
ESB modalidade II) e cinco são equipes de atenção básica que **incluem** saúde
bucal — ESF transitória, ESF ribeirinha, ESF fluvial, equipe de agentes
comunitários e equipe de atenção básica tipo III. O indicador conta as sete,
porque a pergunta é se há equipe com saúde bucal na atenção primária do
município; os rótulos ficam registrados para quem quiser refazer a conta com
outro recorte.

Se essas tabelas de nomes não puderem ser lidas — o FTP do DATASUS derruba a
maior parte das conexões —, o ICSB sai **nulo**, nunca zero, e a execução
seguinte tenta de novo apenas o que faltou.

## Rodar localmente

```bash
pip install -r requirements.txt
```

### Gerar o site

```bash
python site/build.py
```

O site sai em `site/dist/`: 27 páginas de estado e 303 de município, uma para
cada município com curso presencial.

### Portões de qualidade (GO)

```bash
python etl/indices.py --autoteste
python site/estatistica.py
python site/catalogo.py
```

Conferem, respectivamente: as fórmulas dos índices contra um estado sintético
calculado à mão; Spearman, valor de p e regressão múltipla contra casos de
resultado conhecido; e a coerência interna do catálogo de indicadores.

### Testes

```bash
python tests/test_catalogo.py
python tests/test_validacao.py
python tests/test_check_fontes.py
python tests/test_acessibilidade.py   # depois de `python site/build.py`
```

A auditoria de acessibilidade não é decorativa: este projeto herdou o design
system dos observatórios anteriores e **trocou a paleta inteira**. Trocar
paleta é a maneira mais fácil de reprovar em contraste sem que nada quebre na
tela. Ela mede os pares que existem de fato no CSS, confere que a escala
sequencial é monotônica, verifica que a lista de reserva do JavaScript não
divergiu da paleta do CSS — o defeito que no observatório de Psicologia fez os
mapas desenharem na cor de outro projeto — e checa no HTML gerado o que só ele
prova.

## Atualizar os dados

```bash
python etl/pipeline.py --check-only     # só verifica se as fontes mudaram
python etl/pipeline.py --ano 2024       # extrai, calcula, valida, publica
python etl/pipeline.py --so-riqueza     # só a guarda de riqueza
python etl/serie.py --anos 2021 2022 2023 2024
```

A extração do CNES leva perto de uma hora, porque o FTP do DATASUS derruba a
maior parte das conexões que usam `REST`. Ela guarda as fatias já filtradas em
`etl/dados/cnes_<competência>/`, então reprocessar a agregação depois é
instantâneo. Use `--pular-cnes` quando estiver mexendo em outra parte.

O pipeline encadeia extração -> índices -> enriquecimento -> **conferência de
riqueza** -> validação, e só grava se tudo passar. A conferência de riqueza
compara o número de campos por UF com o que está em `git show HEAD` e aborta se
o novo resultado for mais pobre — guarda que existe porque, no projeto de
Farmácia, republicar por um caminho parcial derrubou 33 dos 51 campos com todos
os testes verdes: nenhum teste checava *presença* de campo.

## Publicação

O deploy é **automático**: todo push na `main` que passar pelos portões vai ao
ar no GitHub Pages.

> **Uma vez por repositório, à mão:** *Settings → Pages → Build and deployment →
> Source: **GitHub Actions***. Não dá para automatizar. Criar um site do Pages
> exige permissão de administração do repositório, que o `GITHUB_TOKEN` do
> workflow não tem e que `permissions:` não sabe conceder — `pages: write`
> autoriza publicar num site existente, não criá-lo.

Para republicar sem commit novo, ou para rodar só a validação, use
**Actions → CI → Run workflow**:

| Ação | O que faz |
|---|---|
| `publicar` | valida e republica no GitHub Pages |
| `so-validar` | roda portões, testes e build; não publica |
| `verificar-fontes` | só checa se INEP ou DATASUS publicaram edição nova |

A verificação de fontes também roda sozinha toda segunda-feira e abre issue
quando encontra edição nova — ou quando a verificação fica **indeterminada**,
que é diferente de não ter novidade.

## Princípio inegociável

Nenhum indicador é estimado, interpolado ou preenchido por analogia. Sem fonte
oficial para um recorte, o valor é `null` e aparece como **"sem dados"** —
nunca zero, nunca média plausível. Todo número carrega proveniência: fonte, ano
e data de extração.

E o inverso vale com a mesma força, o que neste curso é decisivo: **zero medido
continua zero**. As 27 UFs sem nenhuma vaga a distância foram apuradas, e
trocar esse zero por "sem dados" transformaria o achado central do observatório
numa falha de apuração.

## Autoria e direitos

**Edson Sidião de Souza Júnior** — sidiao@i9educar.com ·
[Lattes](http://lattes.cnpq.br/9464330669014306)
Farmacêutico, Mestre e Doutor em Medicina Tropical (UFG), avaliador *ad hoc*
INEP/MEC há mais de quinze anos.

O autor **não é cirurgião-dentista**. O que ele traz é competência em avaliação
e regulação do ensino superior, que independe do curso; o que ele não traz é a
leitura de quem exerce a profissão. Correções de método, de interpretação e de
recorte vindas de cirurgiões-dentistas, docentes e entidades da área são
bem-vindas e serão creditadas.

Vale uma ressalva de recorte: a Odontologia é exercida em larga medida no
consultório privado, e não há fonte municipal pública que o registre. Os três
índices de cobertura descrevem a rede pública — onde o ICAB é baixo, o que se
pode afirmar é que falta dentista *no SUS* daquele município.

© 2026, todos os direitos reservados sobre a obra autoral (Leis 9.610/1998 e
9.609/1998). Os **dados primários** são públicos e pertencem ao INEP, ao
Ministério da Saúde e ao IBGE; os **indicadores calculados** são liberados para
reúso com citação.

Termos completos, forma de citação e registro de anterioridade em
[`DIREITOS.md`](DIREITOS.md). Ver também [`SECURITY.md`](SECURITY.md) e a
página de aviso legal do site.
