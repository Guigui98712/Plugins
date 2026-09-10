# 🔧 RevitHydraulicCalc — Calculadora de Pressão e Vazão para Revit 2025

Add-in para Autodesk Revit 2025 que realiza análise hidráulica de redes de tubulação predial, calculando perda de carga, pressão disponível em pontos de saída e sinalizando pontos críticos.

---

## 📋 Pré-requisitos

- **Revit 2025**
- **Visual Studio 2022** (ou superior)
- **.NET 8** (incluído no VS 2022)
- Acesso às DLLs do Revit 2025:
  - `C:\Program Files\Autodesk\Revit 2025\RevitAPI.dll`
  - `C:\Program Files\Autodesk\Revit 2025\RevitAPIUI.dll`

---

## 🏗️ Estrutura do Projeto

```
RevitHydraulicCalc/
├── RevitHydraulicCalc.csproj      # Projeto .NET 8 Class Library (WPF)
├── HydraulicCommand.cs            # Comando principal (IExternalCommand)
├── Core/
│   ├── Models.cs                  # Modelos de dados (PipeSegment, HydraulicNode, etc.)
│   └── ModelReader.cs             # Leitura do modelo Revit
├── Engine/
│   └── HydraulicEngine.cs         # Motor de cálculo (Hazen-Williams, perdas)
├── UI/
│   ├── ReportWindow.xaml          # Interface WPF de relatório
│   └── ReportWindow.xaml.cs       # Code-behind + ViewModels
├── Utils/
│   └── UnitConverters.cs          # Conversores imperial ↔ métrico
└── Addin/
    └── RevitHydraulicCalc.addin   # Manifesto de instalação
```

---

## 🚀 Instalação

### 1. Compilar o projeto

```bash
cd RevitHydraulicCalc
dotnet build -c Release
```

Ou compile pelo Visual Studio: **Build → Build Solution** (Ctrl+Shift+B).

### 2. Instalar o Add-in

Copie os seguintes arquivos para a pasta de Add-ins do Revit:

```
%APPDATA%\Autodesk\Revit\Addins\2025\
    ├── RevitHydraulicCalc.addin
    └── RevitHydraulicCalc.dll
```

> **Caminho completo:** `C:\Users\<SEU_USUARIO>\AppData\Roaming\Autodesk\Revit\Addins\2025\`

### 3. Reiniciar o Revit

O comando aparecerá na aba **Add-ins → External Tools**.

---

## ⚙️ Funcionamento

1. **Seleciona o sistema** de tubulação (água fria/quente)
2. **Informa a pressão na origem** (barrilete) em kPa
3. **Lê a rede** do Revit: tubos, diâmetros, materiais, comprimentos
4. **Calcula** perda de carga distribuída (Hazen-Williams) + localizada (comprimento equivalente)
5. **Acumula** perdas do nó de origem até cada ponto de saída
6. **Compara** pressão disponível vs. mínima requerida
7. **Nomeia** os pontos e grava parâmetros no modelo
8. **Exibe** relatório com tabela e permite:
   - Exportar para CSV
   - Destacar pontos críticos em vermelho na vista

---

## 📐 Fórmulas Utilizadas

### Hazen-Williams (perda distribuída)
```
J = 10.67 × Q^1.852 / (C^1.852 × D^4.87)   [m/m]
Δh = J × L                                   [m]
```

Onde:
- **Q** = vazão (m³/s)
- **C** = coeficiente de Hazen-Williams (material)
- **D** = diâmetro interno (m)
- **L** = comprimento do trecho (m)

### Perda localizada
```
Comprimento equivalente dos acessórios (tabela Macintyre)
```

### Perda por elevação
```
Δh_elev = z_final − z_inicial   [m]
```

### Pressão disponível
```
P_disponível = P_origem − Σ(perdas) − Δh_elev × ρ × g   [kPa]
```

---

## 🔴 Pontos Críticos

Um ponto é marcado como **CRÍTICO** quando:
```
Pressão Disponível < Pressão Mínima Requerida
```

Pressões mínimas por tipo (configuráveis):
| Tipo de Saída | Mínimo (kPa) | Mínimo (mca) |
|---------------|-------------|--------------|
| Chuveiro/Ducha | 100 | 10.2 |
| Torneira/Lavatório | 50 | 5.1 |
| Vaso Sanitário | 70 | 7.1 |
| Padrão | 100 | 10.2 |

---

## 🗺️ Roadmap de Fases

| Fase | Descrição | Status |
|------|-----------|--------|
| **Fase 1** | Rede em série simples (1 sistema, sem ramificação) | ✅ MVP |
| **Fase 2** | Redes ramificadas em árvore | ✅ Implementado |
| **Fase 3** | Múltiplos sistemas + seleção pelo usuário | ✅ Implementado |
| **Fase 4** | Exportação CSV + destaque visual de críticos | ✅ Implementado |
| **Fase 5** | Parâmetros compartilhados + tags nativos do Revit | 🔄 Parcial |
| **Fase 6** | Seleção manual do nó de origem (pick no modelo) | ⏳ Futuro |
| **Fase 7** | Cálculo de vazão por simultaneidade (NBR 8160) | ⏳ Futuro |

---

## 📝 Notas Técnicas

- O Revit 2025 usa **.NET 8** — não confundir com .NET Framework 4.8 das versões anteriores
- As DLLs `RevitAPI.dll` e `RevitAPIUI.dll` devem ter **Copy Local = False** (não redistribuir)
- O motor de cálculo (`HydraulicEngine`) é **independente do Revit** — pode ser testado unitariamente
- A tabela de comprimento equivalente é baseada em **Macintyre — Instalações Hidráulicas Prediais**

---

## 🐛 Troubleshooting

| Problema | Solução |
|----------|---------|
| "Nenhum sistema encontrado" | Verifique se o modelo possui sistemas de tubulação (PipingSystem) criados |
| "Nenhum tubo encontrado" | Certifique-se de que o sistema possui elementos do tipo `Pipe` modelados |
| Erro de compilação | Verifique se o caminho das DLLs do Revit está correto no `.csproj` |
| Add-in não aparece | Verifique se o `.addin` e a `.dll` estão na pasta correta de Add-ins |

---

Desenvolvido para Revit 2025 | .NET 8 | C# 12
