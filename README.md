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

| | Odontologia (Censo 2025) | Psicologia, para escala |
|---|---:|---:|
| Cursos no Censo, rótulo CINE exato | **676** | 1.319 |
| UFs com oferta presencial | **27** de 27 | 27 |
| Municípios com oferta presencial | **308** | 514 |
| Vagas presenciais | **131.644** | 296.638 |
| Vagas **a distância** | **0** | 591 |
| Vagas **semipresenciais** | **0** | 0 |
| Polos | **0** | 2.510 |
| Vagas em cursos encerrados ou em extinção | **120** (3 cursos) | 200 (1 curso) |
| Matrículas | **164.680** | 410.621 |
| Concluintes | **26.681** | 44.301 |
| IES distintas no país | **595** | 1.080 |
| Cursos avaliados | **452** (CPC 2023) | 746 (CPC 2022) |
| Municípios com cirurgião-dentista no SUS | **5.565** de 5.571 | 5.528 |
| Cirurgiões-dentistas no SUS | **96.748** | 72.751 |
| Municípios com serviço especializado ao SUS | **2.495** | — |
| Municípios com laboratório de prótese ao SUS | **4.356** | — |

### A manchete é uma ausência

**Não existe Odontologia a distância no Brasil.** Os 676 cursos registrados no
Censo 2025 são presenciais — nenhuma vaga a distância, nenhuma semipresencial,
nenhum polo, em nenhuma unidade da federação —, e os 452 cursos avaliados no
CPC 2023 também são todos presenciais.

Esse zero é **medido, não presumido**. Até 2024 a verificação era feita em
`TP_DIMENSAO`, a coluna que separava presencial, polo de EaD e sede de EaD: as
três camadas eram lidas e só a primeira tinha linha. Em 2025 essa coluna
**deixou de existir** — o INEP normalizou os microdados —, e a mesma pergunta
passou a ser feita a `TP_MODALIDADE_ENSINO`, que agora registra o formato de
oferta (1 presencial, 2 a distância, 3 semipresencial). O teste que faz essa
conferência foi reescrito para a coluna nova em vez de ser apagado: ele é o
único jeito de distinguir "não existe oferta a distância" de "o pipeline somou
a tabela errada".

### O decreto que mudou a família não mudou nada aqui

O Decreto 12.456/2025 encerrou a graduação em saúde inteiramente a distância, e
isso virou a manchete dos observatórios irmãos: em Fisioterapia são 302.594
vagas em extinção, em Fonoaudiologia 39.580. Em Odontologia o decreto é um
**não-evento**, e a afirmação é medida, não suposta: não havia oferta a
distância para encerrar.

O que a edição nova trouxe foi a coluna `TP_SITUACAO`, que declara curso
extinto ou em extinção. Em Odontologia são **três cursos presenciais, com 120
vagas e uma matrícula** — pequeno, e publicado do mesmo jeito, porque a
extinção é um processo e quem está matriculado tem direito a concluir.

Nos observatórios irmãos a oferta não presencial vai de 0,2% da capacidade em
Psicologia a dois terços em Administração — aqui ela é exatamente nada.

A consequência é de produto, não só de dado: como `pct_ead` é constante,
**ele fica fora da matriz de correlação** (variável sem variância não tem
correlação fraca, tem correlação indefinida) e a EaD não aparece em mapa nem em
coluna comparativa. Os campos continuam publicados no conjunto de dados,
valendo zero, e a ausência é declarada numa seção própria — que é onde ela
informa alguma coisa.

### O rótulo CINE não tem vizinho, e o critério exato continua

Medido no Censo 2025: `Odontologia` é o **único** rótulo que contém "odont".
Diferente de `Psicologia`, que convive com `Psicopedagogia` (6.083 registros),
ou de `Administração`, ao lado de `Administração pública` (3.530).

Ainda assim o recorte usa **igualdade exata** sobre o rótulo normalizado. O
vizinho que hoje não existe pode existir na próxima edição, e um extrator que
passou a usar substring porque "dava no mesmo" somaria o curso novo sem avisar.
O diagnóstico refaz a verificação em qualquer edição:

```bash
python etl/extrair_censo.py --ano 2025 --listar-rotulos odont
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
| Oferta, vagas, matrículas | Censo da Educação Superior 2025 (INEP) | HTTPS, ZIP lido por `Range` (ou espelho local conferido) |
| CPC, IDD, ENADE, perfil docente | **CPC 2023** (INEP), área ODONTOLOGIA | HTTPS |
| Cirurgiões-dentistas no SUS | CNES — vínculos com CBO `2232xx` | FTP DATASUS |
| Rede especializada | CNES — serviço **114**, atenção especializada à saúde bucal | FTP DATASUS |
| Equipes de saúde bucal | CNES — `tbEquipe`; **sem catálogo para `TP_EQUIPE`**, indicador nulo | FTP DATASUS |
| Laboratório de prótese | CNES — serviço **157** | FTP DATASUS |
| População e municípios | IBGE (agregado 6579; API de localidades) | HTTPS |

### Por que três indicadores de cobertura, e não um

O ICON de Farmácia mede municípios com Farmácia Popular; o ICAP de Psicologia,
municípios com psicólogo no SUS. Nenhum transfere. Para Odontologia a pergunta
"onde existe rede pública que absorve quem se forma?" tem três respostas, e
nenhuma fonte única as dá:

* **ICAB** — municípios com ao menos um cirurgião-dentista vinculado ao SUS
  (família CBO 2232, por prefixo exato). Medido: **5.565 dos 5.571**;
* **ICRE** — municípios com estabelecimento que oferta **ao SUS** atenção
  especializada à saúde bucal (serviço 114). Medido: **2.495**;
* **ICSB** — municípios com equipe de saúde bucal na atenção primária.
  **Não é mensurável nesta base** — ver abaixo — e sai nulo, nunca zero.

São publicados separadamente. Fundi-los exigiria arbitrar um peso entre "tem
dentista", "tem centro de especialidades" e "tem equipe na atenção básica" — e
peso arbitrário é estimativa disfarçada. Foi a mesma decisão tomada em
Fonoaudiologia e em Psicologia.

O contraste entre o primeiro e o segundo é o assunto: **quase todo município do
país tem dentista no SUS, e menos da metade tem serviço especializado**. O ICAB
satura — vai de 0,942 a 1,000 —, então quem quiser comparar estados deve olhar
a densidade, que vai de 26,9 a 80,5 dentistas por 100 mil habitantes. Já o ICRE
separa de verdade: de 0,211 a 1,000.

### Serviço declarado não é serviço público

`rlEstabServClass` traz `CO_AMBULATORIAL_SUS` e `CO_HOSPITALAR_SUS`, e sem
olhá-las o indicador conta consultório privado como rede pública. Medida a
diferença: o serviço 114 é **declarado** em 3.130 municípios por 29.188
estabelecimentos, e **ofertado ao SUS** em 2.495 municípios por 6.075. A
cobertura pública é publicada como índice; o total declarado sai ao lado, nos
campos terminados em `_total`, porque a distância entre os dois diz quanto da
rede especializada de saúde bucal do município é acessível pelo SUS.

Na mesma tabela, a coluna `ST_ATIVO_SN` vem **vazia em todas as linhas** deste
export — medido aqui e também na fatia do observatório de Fonoaudiologia. O
filtro de serviço inativo herdado dos projetos irmãos nunca excluiu nada; aqui
isso é contado e declarado em vez de passar por filtro que funciona.

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

### As equipes de saúde bucal não são mensuráveis nesta base

Este é o achado mais desconfortável da apuração, e o motivo de o ICSB sair em
branco. A coluna `TP_EQUIPE` de `tbEquipe` usa os códigos **70, 71, 72, 76…**,
e **nenhuma tabela de domínio deste export os nomeia**: as três candidatas —
`tbTipoEquipe`, `tbGrupoEquipe` e `tbTipoEqSubTipo` — numeram de 01 a 30, um
espaço de códigos inteiramente diferente. Sem catálogo não há como saber qual
tipo é equipe de saúde bucal.

O perigo aqui não é o dado faltar, é ele **parecer existir**. Casar os códigos
de `tbTipoEquipe` contra `TP_EQUIPE` "funciona": devolve 846 equipes de 125.202
e 157 municípios — números plausíveis à primeira vista e completamente sem
sentido, porque são as equipes cujo tipo por acaso tem o mesmo número em dois
catálogos que não se falam. Foi assim que a primeira execução deste projeto
mediu, e o número só se denunciou por ser pequeno demais para um país com
dezenas de milhares de equipes de saúde bucal.

Por isso `_compativel` **mede** a cobertura do catálogo antes de usá-lo: se os
códigos descobertos não alcançam ao menos 5% das equipes, o catálogo é
declarado incompatível e o indicador sai nulo, com o motivo na proveniência.
Na 202609 a interseção cobre 0,67%. A fatia de equipes continua sendo baixada e
guardada: se uma competência futura exportar o catálogo correspondente, a
medição volta sozinha, sem nova leitura de uma hora.

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
python etl/pipeline.py --ano 2025       # extrai, calcula, valida, publica
python etl/pipeline.py --so-riqueza     # só a guarda de riqueza
python etl/serie.py --anos 2021 2022 2023 2024 2025
```

A extração do CNES leva perto de uma hora, porque o FTP do DATASUS derruba a
maior parte das conexões que usam `REST`. Ela guarda as fatias já filtradas em
`etl/dados/cnes_<competência>/`, então reprocessar a agregação depois é
instantâneo. Use `--pular-cnes` quando estiver mexendo em outra parte.

Se for atualizar também os observatórios de **Fonoaudiologia** e **Psicologia**,
que leem as mesmas tabelas-fato do CNES, existe uma ferramenta que produz as
três fatias numa leitura só — cerca de uma hora em vez de três:
[observatorio-ferramentas](https://github.com/esidiao/observatorio-ferramentas).
Ela também traz a fatia de `tbEquipe`, que só este observatório usa.

**Este repositório não depende dela.** O `etl/` aqui é completo e autônomo, e o
pipeline acima roda sozinho, do zero, sem nada instalado ao lado. A ferramenta
apenas preenche antes o cache de `etl/dados/cnes_<competência>/`; encontrando as
fatias prontas, o extrator daqui as aproveita e segue. Quem mantém só este
observatório pode ignorar o parágrafo inteiro.

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
