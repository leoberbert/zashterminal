# Zashterminal — instruções do projeto

## Ao abrir o projeto

1. Leia **[docs/ux-proposta/CATALOGO.md](docs/ux-proposta/CATALOGO.md)**: backlog do redesenho de UX
   (itens `UX-xx`, status, arquivos e critérios de aceite).
2. Trabalho de UX acontece na branch `feat/ux-redesign`. **Não faça commits na `main`.**
3. Ao concluir um item: marque `[x]` no catálogo e adicione uma linha em **Registro** (data, item, commit).

## Regras

- Nenhuma funcionalidade existente pode ser removida; apenas realocada, renomeada ou reagrupada.
- Toda string de interface passa por `_()` e recebe tradução em **todos** os `locale/*.po`
  (blocos anexados ao fim do arquivo, com `#: caminho/do/arquivo.py`).
- Mantenha o estilo existente (GTK4 + libadwaita, `utils/icons.py` para ícones, `tooltip_helper` para tooltips).

## Comandos

```bash
PYTHONPATH=src python3 -m unittest discover -s tests      # testes
python3 scripts/compile_locales.py pt                     # compila traduções (msgfmt)
PYTHONPATH=src python3 -m zashterminal                    # executa a partir do checkout
PYTHONPATH=src python3 scripts/capture_gallery.py         # regenera docs/gallery
```
