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
- `bot.resource_option_value`: valor do combo "Opcao de Recurso" (ex.: `Itens Guia`).
- `bot.grau_participacao_value`: valor do campo "Grau de participacao".
- `bot.selected_operator_code`: codigo da operadora (`5711`, `421715`, `333689`) para a execucao atual.
- `bot.login_mode`: `manual` (aguarda login humano) ou `automatic` (preenche e envia login).
- `bot.login_username` e `bot.login_password`: credenciais para login automatico.
- `bot.login_username_selector`, `bot.login_password_selector`, `bot.login_submit_selector`: seletores da tela de login.
- `ORIZON_LOGIN_USERNAME` e `ORIZON_LOGIN_PASSWORD`: fallback por variaveis de ambiente quando usuario/senha nao estao no JSON.
- `bot.post_login_url`: URL opcional aberta automaticamente apos o login confirmado.
- `bot.post_login_open_new_tab`: quando `true`, abre `post_login_url` em nova aba e continua o fluxo nela.
- `bot.close_notifications_after_login`: tenta fechar popups/notificacoes apos login.
- `bot.use_existing_browser`: conecta ao Chrome ja aberto via CDP (em vez de abrir um Chromium novo).
- `bot.existing_browser_cdp_url`: endpoint CDP do Chrome aberto (padrao `http://127.0.0.1:9222`).
- `bot.start_from_current_page`: quando `true`, usa a aba atual e pula abertura do portal/login/navegacao.
- `bot.execution_mode`: `full` (fluxo completo) ou `header_protocol_only` (preenche apenas Objeto do Recurso + N do Protocolo).
- `bot.notification_close_selectors`: lista de seletores para botoes de fechar notificacao.
- Inclui suporte ao tutorial inicial (`Terminar`) e modais com botao `Fechar`.
- Apos login confirmado, o fluxo aguarda 10 segundos e envia `ESC` para dispensar janelas iniciais.
- O fluxo tambem fecha automaticamente janelas/abas extras e dialogos JS (`alert`, `confirm`, `prompt`) apos o login.
- Na interface, a selecao da operadora e obrigatoria antes de habilitar o botao `Iniciar`.
- Nao versione credenciais reais no repositório.
- `bot.navigation_steps`: lista de seletores para navegar no menu ate a tela de recurso.
- `bot.selectors`: seletores dos campos e botoes do portal.
- `bot.error_mode`: `tolerant` (continua em falhas) ou `strict` (para na primeira falha).
- Para `use_existing_browser=true`, inicie o Chrome com depuracao remota (`--remote-debugging-port=9222`).

`config/config.json` ja vem com seletores validados para o fluxo:
`+Serviços -> Modulo de Glosas -> Digitacao de Recurso -> Criar nova Guia de Recurso`.
Valide no portal e ajuste conforme o HTML da sua conta/tenant.
