# Cotas pyRevit

Extensao inicial para Revit 2025 / pyRevit.

## Objetivo

Criar cotas para comodos fechados e esquadrejados na vista ativa, usando o estilo de cota `SL_PRETO_1,5`.

## Estrutura

- `Cotas.extension`
- `Cotas.tab`
- `Comodos.panel`
- `CotarComodos.pushbutton`
- `script.py`

## Regras atuais

- Executa apenas na vista ativa.
- Processa apenas rooms com contorno fechado.
- Aceita apenas rooms ortogonais / retangulares.
- Tenta aplicar o tipo de cota `SL_PRETO_1,5`.

## Observacao

Esta primeira versao foi preparada para o caso mais simples. Se o contorno do comodo nao for um retangulo ortogonal com paredes, ele sera ignorado e listado no resumo final.