# Painel de apuração - Presidente (2º turno 2026)

Painel não oficial que acompanha a apuração do 2º turno da eleição para Presidente, usando os arquivos públicos de divulgação de resultados do TSE.

Endereço: https://jeffnews.github.io/painel-eleicao/

## O que mostra

- Progresso da apuração (seções apuradas).
- Placar dos dois candidatos, com percentual sobre votos válidos, votos e diferença entre eles.
- Comparação com o 1º turno: percentual e votos dos finalistas, brancos, nulos e abstenção, com a variação em pontos percentuais.
- Mapa do Brasil por estado, colorido pelo candidato que lidera (cor mais forte = maior margem).
- Resumo por região (Norte, Nordeste, Centro-Oeste, Sudeste, Sul e Exterior).
- Tabela com os 27 estados e o Exterior, ordenável pelas colunas.

Funciona em computador e celular, no modo claro e no escuro.

## Como funciona

A página é um único arquivo (`index.html`), sem build e sem bibliotecas externas. Os dados vêm de duas fontes, nesta ordem:

1. **Direto do TSE:** o navegador busca os arquivos em `https://resultados.tse.jus.br/oficial/ele2026/...` a cada 60 segundos (arquivo nacional e estados).
2. **Cópia no repositório:** um robô do GitHub Actions baixa os mesmos arquivos e guarda na pasta `tse/`. A página usa essas cópias quando o acesso direto falha.

O rodapé informa de onde veio cada dado. Para ver o detalhe de cada requisição, abra a página com `?debug=1` no final do endereço:

```
https://jeffnews.github.io/painel-eleicao/?debug=1
```

## Arquivos

| Caminho | Para que serve |
| --- | --- |
| `index.html` | A página inteira (HTML, CSS, JavaScript e o mapa dos estados). |
| `.github/workflows/atualizar.yml` | Robô que baixa os arquivos do TSE e publica na pasta `tse/`. |
| `tse/` | Cópias dos arquivos do TSE. Criada e atualizada pelo robô, não precisa mexer. |

## Robô de atualização

O fluxo `Atualizar dados do TSE` tem dois modos:

- **Agendado:** roda sozinho a cada 5 minutos. O GitHub costuma atrasar esses disparos.
- **Contínuo (recomendado na noite da eleição):** aba *Actions*, *Atualizar dados do TSE*, *Run workflow*. Informe a duração em minutos (por exemplo `300`) e o intervalo em segundos (por exemplo `90`). O limite é de cerca de 5 horas e meia por execução.

O robô só faz commit quando algum arquivo mudou. Se algum download falhar, o log mostra linhas `FALHOU` com o endereço e o motivo.

Depois da eleição, desligue o agendamento em *Actions*, *Atualizar dados do TSE*, menu `...`, *Disable workflow*.

## Configuração

No começo do script do `index.html` há um bloco `CFG`:

```js
var CFG = {
  base: 'https://resultados.tse.jus.br/oficial/ele2026',
  t1: { cod:'6257', arq:'006257' },   // 1º turno
  t2: { cod:'6258', arq:'006258' },   // 2º turno
  cargo: 'c0001',                      // 1 = Presidente
  finalistas: [
    { n:'13', nome:'Lula',             cor:'#1f7a8c' },
    { n:'22', nome:'Flávio Bolsonaro', cor:'#d4772c' }
  ],
  direto: true,    // tenta o TSE direto antes da cópia do repositório
  msBR: 60000,     // intervalo do arquivo nacional (ms)
  msUF: 60000      // intervalo dos estados (ms)
};
```

- `finalistas`: número do candidato na urna, nome exibido e cor. Para trocar as cores, altere o campo `cor`.
- `direto: false`: a página passa a ler só as cópias do repositório. É útil se muita gente for acessar, para não sobrecarregar o TSE.
- Os códigos de eleição (`6257`, `6258`) vêm do arquivo de configuração do TSE: `https://resultados.tse.jus.br/oficial/comum/config/ele-c.json`.

O robô tem os mesmos códigos e a lista de estados dentro do arquivo `atualizar.yml`. Se mudar a eleição, ajuste os dois lugares.

## Limitações

- Os percentuais dos candidatos são calculados sobre votos válidos; brancos e nulos, sobre o total de votos. Podem diferir em uma casa decimal do que o TSE exibe, por arredondamento.
- Os campos de votos, brancos, nulos e abstenção foram mapeados pelo nome das chaves do JSON do TSE. Com apuração em andamento, confira os números com `resultados.tse.jus.br`.
- Enquanto a apuração não termina, as comparações com o 1º turno são parciais: a ordem em que as seções chegam pode distorcer os percentuais.
- Se a página for aberta direto do computador (`file://`), o navegador bloqueia a leitura dos arquivos. Use o endereço do GitHub Pages ou um servidor local.
- Com a página aberta, são feitos cerca de 29 pedidos ao TSE por minuto. O uso é pensado para poucas pessoas.

## Fonte e aviso

Dados: Tribunal Superior Eleitoral, divulgação de resultados das Eleições 2026 (`resultados.tse.jus.br`). Este painel é um projeto pessoal e **não é um canal oficial**: para o resultado oficial, consulte sempre o site do TSE.
