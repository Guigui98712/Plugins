# Instruções para Fix do Botão IFC Estrutural

## Problema
O botão está abrindo uma aba vazia em vez de executar o script.

## Solução (Executar nesta ordem):

### 1. Fechar Revit completamente
- Salve o arquivo se necessário
- Feche o Revit (não apenas minimize)
- Aguarde 5 segundos

### 2. Deletar cache do pyRevit
Abra PowerShell como Administrador e execute:

```powershell
$cachePath = "C:\Users\guica\AppData\Roaming\pyRevit\2025\"
Remove-Item -Path $cachePath -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "✓ Cache deletado"
```

### 3. Reiniciar Revit
- Abra o Revit novamente

### 4. Reload pyRevit
- Abra pyRevit Console
- Execute: `pyrevit.reload()`

### 5. Testar
- Clique no botão "IFC Estrutural"
- Deve aparecer o diálogo para selecionar arquivo .ifc

## Se ainda não funcionar:

Verifique se o arquivo está em:
```
D:\PLUGINS\Estrutural\Estrutural.extension\G-log.tab\Estrutural.panel\IfcEstrutural.pushbutton\script.py
```

E que contém `def main():` no início da função principal.
