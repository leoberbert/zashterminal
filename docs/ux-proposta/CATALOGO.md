# Catálogo de melhorias de UX — Zashterminal

Fonte única do que falta fazer no redesenho de UX. **Leia antes de começar qualquer trabalho
nesta frente e atualize ao terminar** (status, data, commit).

- Branch de trabalho: `feat/ux-redesign` (criada a partir de `origin/main`; `main` não recebe commits diretos).
- Regra de ouro: **nenhuma funcionalidade é removida** — só realocada, renomeada ou reagrupada.
- Mockups de referência: imagens `00`–`06` nesta pasta (protótipos GTK/Adwaita, não código do app).

## Legenda

| Status | Significado |
|---|---|
| `[ ]` | pendente |
| `[~]` | em andamento / parcial (ver nota) |
| `[x]` | concluído (com commit) |
| `[?]` | aguardando decisão do mantenedor |

Cada item tem: problema, mudança, arquivos principais e critério de aceite.

---

## Fase 1 — rápido, alto impacto

### UX-01 `[x]` Ícones do cabeçalho sem ambiguidade
- **Problema:** painel de sessões usa `computer-symbolic`; IA usa `avatar-default-symbolic` (uma pessoa);
  Gerenciador de Comandos e broadcast usam o mesmo `utilities-terminal-symbolic`.
- **Mudança:** painel → `sidebar-show-symbolic`; IA → ícone próprio; comandos → ícone de lista; broadcast → ícone de transmissão.
- **Arquivos:** `ui/window_ui.py`, `ui/sidebar_manager.py`, `icons/`.
- **Aceite:** cada botão do cabeçalho tem ícone distinto e coerente com o tooltip.

### UX-02 `[x]` Nomes acessíveis e tooltips em botões só-ícone
- **Problema:** busca ↑/↓ sem tooltip; botões só-ícone sem nome acessível (leitor de tela).
- **Mudança:** `tooltip_helper.add_tooltip*()` e `icon_button(tooltip=…)` definem também
  `Gtk.AccessibleProperty.LABEL` em botões/switches sem texto; busca anterior/próxima ganham tooltip.
- **Arquivos:** `utils/tooltip_helper.py`, `utils/icons.py`, `ui/window_ui.py`.
- **Aceite:** todo botão só-ícone do cabeçalho, sidebar e barra de busca tem rótulo acessível.

### UX-03 `[x]` Broadcast visível no cabeçalho
- **Problema:** botão existe mas fica oculto; só `Ctrl+Shift+B` ou Gerenciador de Comandos.
- **Mudança:** botão de alternância visível, com tooltip + atalho. O fluxo do Gerenciador de Comandos permanece.
- **Arquivos:** `ui/window_ui.py`.
- **Aceite:** clicar abre/fecha a barra de broadcast; estado do botão acompanha a barra.

### UX-04 `[x]` Confirmação após broadcast
- **Problema:** envio sem feedback.
- **Mudança:** toast "Comando enviado para N terminais" após envio bem-sucedido.
- **Arquivos:** `ui/window_search_broadcast.py`.
- **Aceite:** toast aparece em todos os escopos (painel, grupo, todos, seleção).

### UX-05 `[x]` Seleção manual de alvos segura
- **Problema:** em "Selecionar…" todos os terminais começam marcados.
- **Mudança:** começa marcado apenas o terminal em foco; botões "Todos"/"Nenhum"; "Lembrar escolha" preservado.
- **Arquivos:** `ui/window_search_broadcast.py`.
- **Aceite:** abrir a seleção sem escolha lembrada marca só o terminal atual. Escolha lembrada sem terminais
  abertos volta a perguntar (antes enviava para todos).

### UX-06 `[x]` Barra de abas sempre visível
- **Problema:** com 1 aba o título substitui a barra de abas; com 2+ a barra aparece — o layout "pula".
- **Mudança:** barra de abas visível com qualquer número de abas (≥1); título da janela continua atualizado.
- **Arquivos:** `window.py` (`_update_tab_layout`).
- **Aceite:** abrir/fechar a 2ª aba não muda a posição dos elementos do cabeçalho.

### UX-07 `[x]` Menu principal reorganizado
- **Problema:** lista plana com 4 importações, TFTP entre Preferências e Sobre, "Cores" abre "Highlight Colors",
  "Start TFTP Server" sem tradução.
- **Mudança (mockup `05-menu-principal.png`):** seções Personalizar / Sessões / Ferramentas; submenu
  "Importar sessões"; "Cores e destaque"; TFTP em Ferramentas com estado. Todos os itens atuais mantidos.
- **Arquivos:** `ui/menus.py`.
- **Aceite:** todas as ações antigas acessíveis pelo menu; imports num submenu.

### UX-08 `[x]` Atalhos padrão alinhados a outros terminais
- **Problema:** copiar só com `Ctrl+Shift+Insert`; abas só com `Alt+PgUp/PgDn`.
- **Mudança:** acrescentar `Ctrl+Shift+C` (copiar) e `Ctrl+PgUp/PgDn` (abas) como atalhos **adicionais**; os atuais continuam.
- **Arquivos:** `app.py` (`SHORTCUT_ALIASES`, `apply_window_shortcuts`), `helpers.py` (`normalize_accelerator`).
  Teste: `tests/test_shortcut_aliases.py`.
- **Aceite:** ambos os atalhos funcionam; atalho personalizado pelo usuário continua tendo prioridade.

### UX-09 `[x]` Diálogo de atalhos: busca e conflito
- **Problema:** sem busca; conflito só é avisado e o atalho duplicado é salvo.
- **Mudança:** campo de busca; ao conflitar, botão salvar desabilitado com a ação conflitante indicada.
- **Arquivos:** `ui/dialogs/shortcuts_dialog.py`.
- **Aceite:** não é possível salvar atalho já usado por outra ação.

### UX-10 `[x]` Preferência "Copiar ao selecionar"
- **Problema:** selecionar texto sempre sobrescreve a área de transferência, sem opção.
- **Mudança:** preferência `copy_on_select` (padrão **ligado**, preserva comportamento atual).
- **Arquivos:** `settings/config.py`, `terminal/manager.py`, `ui/dialogs/preferences_dialog.py`.
- **Aceite:** desligado, selecionar não altera a área de transferência; Copiar continua funcionando.

### UX-11 `[?]` Nome consistente da janela
- **Problema:** título "Terminal Zash" × marca "Zashterminal".
- **Bloqueio:** "Terminal Zash" é o `Name=` do `.desktop` (menu de aplicativos, 28 idiomas) e `APP_TITLE`.
  Trocar só o título da janela criaria um terceiro nome. Decidir qual nome é o oficial antes de mudar
  `.desktop`, `settings/config.py:APP_TITLE`, `window.py` e `ui/window_ui.py` juntos.

### UX-12 `[x]` Traduções das strings novas
- **Mudança:** toda string nova recebe `msgid`/`msgstr` em **todos** os `locale/*.po` (padrão do repositório:
  blocos anexados ao fim do arquivo com `#: caminho`).
- **Aceite:** strings novas compilam (`python3 scripts/compile_locales.py pt de fr ru zh en`); UI em pt_BR
  sem textos em inglês novos. Ver UX-13 para os erros antigos que impedem compilar todos.

### UX-13 `[x]` Dívida de tradução: placeholders inválidos (anterior a esta branch)
- **Problema:** `msgfmt --check-format` falhava em 14 catálogos (cs, sk, da, el, es, et, hu, it, ja, ko, nl, no,
  sv, tr) por placeholders traduzidos (ex.: `{local_host}` → `{host_local}`).
- **Feito:** 15 `msgstr` corrigidos com os nomes de placeholder do `msgid`.
- **Aceite:** `python3 scripts/compile_locales.py` (todos) termina com código 0.

---

## Fase 2 — fluxos principais

### UX-20 `[~]` Editor de sessão: essencial primeiro
- **Feito:** ordem Nome/Tipo → Conexão → Organização (pasta + cor da aba) → Opções SSH → Avançado
  (grupo de broadcast, transcrição) → Destaque; aviso "Experimental" só aparece ao personalizar.
- **Pendente:** Opções SSH em seções expansíveis (mockup `04`); botão "Salvar e conectar".
- **Decisão:** autenticação padrão continua "Senha" — "Chave SSH" exige arquivo de chave válido para salvar.
- **Arquivos:** `ui/dialogs/session_edit_dialog.py`.

### UX-21 `[x]` Painel de sessões
- **Feito:** busca no topo; estado vazio com "Adicionar Sessão" e "Importar Sessões"; estado "nenhum resultado";
  `usuário@host` esmaecido nas sessões SSH; setas de expansão nas pastas (substitui o ícone de pasta com "+").
- **Arquivos:** `ui/window_ui.py`, `sessions/tree.py`, `data/styles/window.css`.

### UX-22 `[~]` Favoritas
- **Feito:** campo `favorite` em `SessionItem` (JSON antigo sem o campo vira `False`); interruptor "Favorita" em
  Avançado no editor; estrela ★ na árvore; favoritas primeiro na paleta de comandos. Teste: `tests/test_session_favorite.py`.
- **Pendente:** seções "Conectadas" e "Favoritas" fixas no topo do painel (a árvore é um `Gtk.TreeListModel`
  com pastas; seções exigem um modelo de lista separado — decidir se vale a troca).
- **Arquivos:** `sessions/models.py`, `sessions/tree.py`, `ui/dialogs/session_edit_dialog.py`, `ui/command_palette.py`.

### UX-23 `[x]` Broadcast evidente nos painéis
- **Feito:** com a barra aberta, terminais-alvo ganham contorno laranja e a barra mostra "Envia para N: nomes",
  atualizado ao mudar escopo/digitar.
- **Arquivos:** `ui/window_search_broadcast.py`, `ui/window_ui.py`, `data/styles/window.css`.

### UX-24 `[x]` Cabeçalho do painel
- **Feito:** `>` virou menu "⋯" (Mover para nova aba, Dividir esquerda/direita, cima/baixo, Fechar) agindo no
  painel do menu; botão fechar mantido; borda de foco mais forte.
- **Arquivos:** `terminal/tabs.py`, `data/styles/window.css`.

---

## Fase 3 — estrutura

### UX-30 `[x]` Paleta de comandos (`Ctrl+Shift+P`)
- **Feito:** busca única em sessões, layouts, comandos salvos e ações (com atalho exibido). `@` filtra
  sessões/layouts, `>` filtra ações; Enter executa, Esc fecha. Também no menu principal e no diálogo de atalhos.
- **Pendente:** buscar preferências individuais (ex.: "fonte") e ação secundária "Conectar via SFTP".
- **Arquivos:** `ui/command_palette.py`, `ui/actions.py`. Teste: `tests/test_command_palette.py`.

### UX-31 `[~]` Preferências como central única
- **Feito:** todos os grupos de Preferências têm título; página Aparência termina com "Mais configurações"
  (Cores e Destaque, Assistente de IA, Atalhos de Teclado).
- **Constatação:** a duplicação do grupo "Cores do papel de parede" vem de `ui/color_scheme_dialog.py:ColorSchemeDialog`,
  que **não é aberto por nenhum caminho do app** (só por `scripts/capture_gallery.py` e
  `scripts/test_wallpaper_ui.py`). Remover a classe exige adaptar esses scripts e a imagem 06 da galeria.
- **Pendente:** navegação lateral com Cores/IA/Atalhos como páginas internas (mockup `06`).

---

## Registro

| Data | Item | Commit | Nota |
|---|---|---|---|
| 2026-11-16 | — | a75a617 | Avaliação de UX, mockups e catálogo criados |
| 2026-11-16 | UX-01/02/03 | 0946e82 | Ícones, nomes acessíveis, botão de broadcast |
| 2026-11-16 | UX-04/05 | e110b17 | Toast de broadcast, seleção segura |
| 2026-11-16 | UX-06 | c054912 | Barra de abas com 1 aba |
| 2026-11-16 | UX-07 | 197feb5 | Menu principal nativo em seções |
| 2026-11-16 | UX-08/09 | 9ab52ed | Atalhos alternativos, busca e bloqueio de conflito |
| 2026-11-16 | UX-10 | b4e8595 | Copiar ao selecionar (padrão ligado) |
| 2026-11-16 | UX-12 | dd81610 | Traduções da fase 1 (28 idiomas) |
| 2026-11-16 | UX-20 | facbfde | Editor de sessão reordenado (parcial) |
| 2026-11-16 | UX-21 | 3315d74 | Painel de sessões |
| 2026-11-16 | UX-23/24 | 8c9c5b8 | Alvos de broadcast e menu do painel |
| 2026-11-16 | UX-12 | 7702811 | Traduções da fase 2 (28 idiomas) |
| 2026-11-16 | UX-08 | ff25695 | Testes dos atalhos alternativos |
| 2026-11-16 | UX-30 | 9ce81bf | Paleta de comandos |
| 2026-11-16 | UX-31 | f55b628 | Títulos de grupo e links nas Preferências (parcial) |
| 2026-11-16 | UX-12 | ac1e84a | Traduções da fase 3 (28 idiomas) |
| 2026-11-16 | UX-13 | 1da06bb | Placeholders corrigidos; todos os catálogos compilam |
| 2026-11-16 | UX-22 | (ver git log) | Sessões favoritas (parcial) |
