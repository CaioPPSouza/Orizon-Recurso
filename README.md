# Automacao de Recursos de Glosa - ORIZON

MVP em Python para automatizar o cadastro de recursos de glosa no portal ORIZON a partir de uma planilha Excel.

## Estrutura

- `src/ui/`: tela de acompanhamento (Tkinter).
- `src/core/`: leitura, validacao e agrupamento da planilha.
- `src/bot/`: automacao no portal via Playwright.
- `config/`: configuracoes do fluxo e seletores.
- `tests/`: testes automatizados.
- `logs/`: logs de execucao e screenshots.

## Requisitos

- Python 3.11+
- Chromium do Playwright instalado

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Execucao

```bash
python -m src.main
```

## Testes

```bash
pytest
```

## Configuracao

Edite `config/config.json`:

- `bot.object_resource_value`: valor do combo "Objeto Recurso".
- `bot.grau_participacao_value`: valor do campo "Grau de participacao".
- `bot.navigation_steps`: lista de seletores para navegar no menu ate a tela de recurso.
- `bot.selectors`: seletores dos campos e botoes do portal.
- `bot.error_mode`: `tolerant` (continua em falhas) ou `strict` (para na primeira falha).

`config/config.json` ja vem com uma sugestao inicial de mapeamento via `role=`/`label=`.
Valide no portal e ajuste conforme o HTML da sua conta/tenant.
