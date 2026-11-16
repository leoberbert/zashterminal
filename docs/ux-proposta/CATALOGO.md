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

Cada item tem: problema, mudança, arquivos principais e critério de aceite.

---

## Fase 1 — rápido, alto impacto

### UX-01 `[ ]` Ícones do cabeçalho sem ambiguidade
- **Problema:** painel de sessões usa `computer-symbolic`; IA usa `avatar-default-symbolic` (uma pessoa);
  Gerenciador de Comandos e broadcast usam o mesmo `utilities-terminal-symbolic`.
- **Mudança:** painel → `sidebar-show-symbolic`; IA → ícone próprio; comandos → ícone de lista; broadcast → ícone de transmissão.
- **Arquivos:** `ui/window_ui.py`, `ui/sidebar_manager.py`, `icons/`.
- **Aceite:** cada botão do cabeçalho tem ícone distinto e coerente com o tooltip.

### UX-02 `[ ]` Nomes acessíveis e tooltips em botões só-ícone
- **Problema:** busca ↑/↓ sem tooltip; botões só-ícone sem nome acessível (leitor de tela).
- **Mudança:** `icon_button()`/`icon_image()` passam o tooltip também como `Gtk.AccessibleProperty.LABEL`;
  busca anterior/próxima ganham tooltip.
- **Arquivos:** `utils/icons.py`, `ui/window_ui.py`.
- **Aceite:** todo botão só-ícone do cabeçalho, sidebar e barra de busca tem rótulo acessível.

### UX-03 `[ ]` Broadcast visível no cabeçalho
- **Problema:** botão existe mas fica oculto; só `Ctrl+Shift+B` ou Gerenciador de Comandos.
- **Mudança:** botão de alternância visível, com tooltip + atalho. O fluxo do Gerenciador de Comandos permanece.
- **Arquivos:** `ui/window_ui.py`.
- **Aceite:** clicar abre/fecha a barra de broadcast; estado do botão acompanha a barra.

### UX-04 `[ ]` Confirmação após broadcast
- **Problema:** envio sem feedback.
- **Mudança:** toast "Comando enviado para N terminais" após envio bem-sucedido.
- **Arquivos:** `ui/window_search_broadcast.py`.
- **Aceite:** toast aparece em todos os escopos (painel, grupo, todos, seleção).

### UX-05 `[ ]` Seleção manual de alvos segura
- **Problema:** em "Selecionar…" todos os terminais começam marcados.
- **Mudança:** começa marcado apenas o terminal em foco; botões "Todos"/"Nenhum"; "Lembrar escolha" preservado.
- **Arquivos:** `ui/window_search_broadcast.py`.
- **Aceite:** abrir a seleção sem escolha lembrada marca só o terminal atual.

### UX-06 `[ ]` Barra de abas sempre visível
- **Problema:** com 1 aba o título substitui a barra de abas; com 2+ a barra aparece — o layout "pula".
- **Mudança:** barra de abas visível com qualquer número de abas (≥1); título da janela continua atualizado.
- **Arquivos:** `window.py` (`_update_tab_layout`).
- **Aceite:** abrir/fechar a 2ª aba não muda a posição dos elementos do cabeçalho.

### UX-07 `[ ]` Menu principal reorganizado
- **Problema:** lista plana com 4 importações, TFTP entre Preferências e Sobre, "Cores" abre "Highlight Colors",
  "Start TFTP Server" sem tradução.
- **Mudança (mockup `05-menu-principal.png`):** seções Personalizar / Sessões / Ferramentas; submenu
  "Importar sessões"; "Cores e destaque"; TFTP em Ferramentas com estado. Todos os itens atuais mantidos.
- **Arquivos:** `ui/menus.py`.
- **Aceite:** todas as ações antigas acessíveis pelo menu; imports num submenu.

### UX-08 `[ ]` Atalhos padrão alinhados a outros terminais
- **Problema:** copiar só com `Ctrl+Shift+Insert`; abas só com `Alt+PgUp/PgDn`.
- **Mudança:** acrescentar `Ctrl+Shift+C` (copiar) e `Ctrl+PgUp/PgDn` (abas) como atalhos **adicionais**; os atuais continuam.
- **Arquivos:** `settings/config.py`, onde os aceleradores são registrados.
- **Aceite:** ambos os atalhos funcionam; atalho personalizado pelo usuário continua tendo prioridade.

### UX-09 `[ ]` Diálogo de atalhos: busca e conflito
- **Problema:** sem busca; conflito só é avisado e o atalho duplicado é salvo.
- **Mudança:** campo de busca; ao conflitar, botão salvar desabilitado com a ação conflitante indicada.
- **Arquivos:** `ui/dialogs/shortcuts_dialog.py`.
- **Aceite:** não é possível salvar atalho já usado por outra ação.

### UX-10 `[ ]` Preferência "Copiar ao selecionar"
- **Problema:** selecionar texto sempre sobrescreve a área de transferência, sem opção.
- **Mudança:** preferência `copy_on_select` (padrão **ligado**, preserva comportamento atual).
- **Arquivos:** `settings/config.py`, `terminal/manager.py`, `ui/dialogs/preferences_dialog.py`.
- **Aceite:** desligado, selecionar não altera a área de transferência; Copiar continua funcionando.

### UX-11 `[ ]` Nome consistente da janela
- **Problema:** título "Terminal Zash" × marca "Zashterminal".
- **Mudança:** título da janela usa "Zashterminal".
- **Arquivos:** `window.py`, `ui/window_ui.py`.

### UX-12 `[ ]` Traduções das strings novas
- **Mudança:** toda string nova recebe `msgid`/`msgstr` em **todos** os `locale/*.po` (padrão do repositório:
  blocos anexados ao fim do arquivo com `#: caminho`).
- **Aceite:** `python3 scripts/compile_locales.py` compila sem erro; UI em pt_BR sem textos em inglês novos.

---

## Fase 2 — fluxos principais

### UX-20 `[ ]` Editor de sessão: essencial primeiro
- **Mudança (mockup `04-editor-de-sessao.png`):** Conexão (tipo, nome, host, usuário, porta, autenticação) no topo;
  Organização; Avançado em seções expansíveis; "Destaque (experimental)" por último.
- **Arquivos:** `ui/dialogs/session_edit_dialog.py`.

### UX-21 `[ ]` Painel de sessões
- **Mudança (mockups `01`, `03`):** busca no topo; estado vazio com "Nova sessão" e "Importar…";
  subtítulo `usuário@host` nas sessões SSH.
- **Arquivos:** `ui/window_ui.py`, `sessions/tree.py`, `ui/sidebar_manager.py`.

### UX-22 `[ ]` Seções Conectadas / Favoritas
- **Mudança:** campo `favorite` na sessão; seções no topo do painel. Exige migração compatível do JSON.
- **Arquivos:** `sessions/models.py`, `sessions/storage.py`, `sessions/tree.py`.

### UX-23 `[ ]` Broadcast evidente nos painéis
- **Mudança (mockup `01`):** barra com alvos visíveis ("Enviar para N"); moldura/selo nos painéis-alvo.
- **Arquivos:** `ui/window_search_broadcast.py`, `terminal/tabs.py`, `data/styles/`.

### UX-24 `[ ]` Cabeçalho do painel
- **Mudança:** `>` vira menu "⋯" (Mover para nova aba, Dividir, Fechar); foco com contraste maior.
- **Arquivos:** `terminal/tabs.py`, `data/styles/window.css`.

---

## Fase 3 — estrutura

### UX-30 `[ ]` Paleta de comandos (`Ctrl+Shift+P`)
- Busca única em sessões, comandos salvos, layouts, ações e preferências (mockup `02`).

### UX-31 `[ ]` Preferências como central única
- Navegação lateral; Cores e destaque, IA e Atalhos como páginas; remover duplicação do grupo
  "Cores do papel de parede" (hoje em dois diálogos). Mockup `06`.

---

## Registro

| Data | Item | Commit | Nota |
|---|---|---|---|
| 2026-11-16 | — | — | Avaliação de UX, mockups e catálogo criados |
