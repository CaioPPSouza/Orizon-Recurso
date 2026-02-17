# Arquitetura MVP

## Fluxo principal

1. Usuario seleciona planilha (`.xlsx`) na UI.
2. `src/core/spreadsheet.py` valida colunas obrigatorias, ordena e agrupa por `Senha`.
3. UI exibe totais previstos (linhas e guias).
4. Usuario clica em `Iniciar`.
5. `src/bot/automator.py` executa automacao no ORIZON:
   - abre portal,
   - aguarda login manual (quando configurado),
   - navega para tela de recurso,
   - cria guia por senha,
   - inclui procedimentos da senha,
   - finaliza guia.
6. UI atualiza status, contadores e log em tempo real.
7. `Parar` solicita parada segura apos a acao atual.

## Regras de negocio implementadas

- `1 senha = 1 guia`.
- multiplas linhas da mesma senha geram multiplos procedimentos na mesma guia.
- preenchimento de cabecalho usa a primeira linha da senha.
- `error_mode`:
  - `tolerant`: continua para o proximo procedimento/guia.
  - `strict`: para na primeira falha.

## Configuracoes

Arquivo: `config/config.json`

- `spreadsheet.sheet_name`
- `bot.object_resource_value`
- `bot.grau_participacao_value`
- `bot.navigation_steps`
- `bot.selectors`
- `bot.error_mode`
