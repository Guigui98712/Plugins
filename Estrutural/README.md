# Estrutural pyRevit

Plugin estrutural em fase inicial para leitura de IFC.

## Estrutura

- `Estrutural.extension`
- `G-log.tab`
- `Estrutural.panel`
- `IfcEstrutural.pushbutton`
- `script.py`

## Objetivo inicial

- Validar se o arquivo IFC possui mais levels/storeys do que o modelo Revit atual.
- Alertar o usuario antes da conversao estrutural.
- Importar pilares e vigas do IFC nas posicoes equivalentes do Revit.
