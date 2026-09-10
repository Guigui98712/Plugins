using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Plumbing;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Selection;
using RevitHydraulicCalc.Core;
using RevitHydraulicCalc.Engine;
using RevitHydraulicCalc.UI;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Windows;

namespace RevitHydraulicCalc
{
    /// <summary>
    /// Comando principal do Add-in. Executa análise hidráulica completa.
    /// </summary>
    [Transaction(TransactionMode.Manual)]
    [Regeneration(RegenerationOption.Manual)]
    public class HydraulicCommand : IExternalCommand
    {
        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            UIApplication uiApp = commandData.Application;
            UIDocument uiDoc = uiApp.ActiveUIDocument;
            Document doc = uiDoc.Document;

            try
            {
                // === FASE 1: Selecionar Sistema de Tubulação ===
                var reader = new ModelReader(doc);
                var systems = reader.GetAllPipingSystems();

                if (!systems.Any())
                {
                    TaskDialog.Show("Calculadora Hidráulica", 
                        "Nenhum sistema de tubulação encontrado no modelo.\n" +
                        "Certifique-se de que existem sistemas de água fria/quente configurados.");
                    return Result.Cancelled;
                }

                // Se houver múltiplos sistemas, permite seleção
                PipingSystem? selectedSystem = null;
                if (systems.Count == 1)
                {
                    selectedSystem = systems.First();
                }
                else
                {
                    // Diálogo simples de seleção (pode ser substituído por WPF customizado)
                    var systemNames = systems.Select(s => s.Name).ToList();
                    var dialog = new SystemSelectionDialog(systemNames);
                    if (dialog.ShowDialog() != true)
                        return Result.Cancelled;

                    selectedSystem = systems.FirstOrDefault(s => s.Name == dialog.SelectedSystemName);
                }

                if (selectedSystem == null)
                {
                    TaskDialog.Show("Erro", "Sistema não selecionado.");
                    return Result.Cancelled;
                }

                // === FASE 2: Solicitar pressão na origem ===
                double sourcePressureKPa = 200; // padrão
                var pressureDialog = new PressureInputDialog(sourcePressureKPa);
                if (pressureDialog.ShowDialog() == true)
                {
                    sourcePressureKPa = pressureDialog.PressureKPa;
                }

                // === FASE 3: Extrair rede do modelo ===
                var (segments, nodes) = reader.ExtractSystemNetwork(
                    selectedSystem, 
                    defaultFlowLps: 0.5, 
                    sourcePressureKPa: sourcePressureKPa);

                if (!segments.Any())
                {
                    TaskDialog.Show("Calculadora Hidráulica", 
                        $"Nenhum tubo encontrado no sistema '{selectedSystem.Name}'.\n" +
                        "Verifique se o sistema possui tubulações modeladas.");
                    return Result.Cancelled;
                }

                // === FASE 4: Identificar nó de origem ===
                // Por padrão, usa o nó com maior elevação (barrilete/topo)
                // Se o usuário quiser selecionar manualmente, pode implementar pick
                var sourceNode = nodes.FirstOrDefault(n => n.IsSource);
                if (sourceNode == null)
                {
                    TaskDialog.Show("Erro", "Não foi possível identificar o ponto de origem da rede.");
                    return Result.Failed;
                }

                // === FASE 5: Executar cálculo hidráulico ===
                var result = HydraulicEngine.AnalyzeNetwork(
                    segments, nodes, sourceNode.Id, sourcePressureKPa);

                result.SystemName = selectedSystem.Name;

                // === FASE 6: Nomear pontos e gravar parâmetros no Revit ===
                using (var tx = new Transaction(doc, "Nomear Pontos Hidráulicos"))
                {
                    tx.Start();

                    foreach (var node in result.Nodes)
                    {
                        if (!node.RevitElementId.HasValue) continue;

                        var element = doc.GetElement(new ElementId(node.RevitElementId.Value));
                        if (element == null) continue;

                        // Tenta gravar em parâmetros compartilhados ou built-in
                        SetParameter(element, "Pressão Disponível", node.PressureKPa);
                        SetParameter(element, "Pressão Disponível (mca)", node.PressureMca);
                        SetParameter(element, "Status Hidráulico", node.IsCritical ? "CRÍTICO" : "OK");

                        // Nomeia o ponto
                        if (node.IsOutlet)
                        {
                            string pointName = $"PT_{node.Name}_{node.PressureKPa:F0}kPa";
                            node.Name = pointName;
                            SetParameter(element, "Nome do Ponto Hidráulico", pointName);
                        }
                    }

                    tx.Commit();
                }

                // === FASE 7: Exibir relatório ===
                var reportWindow = new ReportWindow(result, doc, uiDoc);
                reportWindow.ShowDialog();

                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                message = $"Erro na análise hidráulica: {ex.Message}\n\n{ex.StackTrace}";
                TaskDialog.Show("Erro", message);
                return Result.Failed;
            }
        }

        /// <summary>
        /// Define valor em parâmetro do elemento (cria se não existir via SharedParameter)
        /// </summary>
        private static void SetParameter(Element element, string paramName, object value)
        {
            try
            {
                var param = element.LookupParameter(paramName);
                if (param == null) return;

                switch (param.StorageType)
                {
                    case StorageType.Double when value is double d:
                        param.Set(d);
                        break;
                    case StorageType.String when value is string s:
                        param.Set(s);
                        break;
                    case StorageType.Integer when value is int i:
                        param.Set(i);
                        break;
                }
            }
            catch { /* ignora erros de parâmetro */ }
        }
    }

    // ==================== DIÁLOGOS AUXILIARES ====================

    /// <summary>
    /// Diálogo para seleção de sistema
    /// </summary>
    public class SystemSelectionDialog : Window
    {
        public string? SelectedSystemName { get; private set; }
        private readonly ComboBox _combo;

        public SystemSelectionDialog(List<string> systemNames)
        {
            Title = "Selecionar Sistema";
            Width = 350;
            Height = 180;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            ResizeMode = ResizeMode.NoResize;

            var panel = new StackPanel { Margin = new Thickness(20) };

            panel.Children.Add(new TextBlock 
            { 
                Text = "Selecione o sistema de tubulação:", 
                Margin = new Thickness(0, 0, 0, 10),
                FontWeight = FontWeights.SemiBold
            });

            _combo = new ComboBox 
            { 
                ItemsSource = systemNames,
                SelectedIndex = 0,
                Margin = new Thickness(0, 0, 0, 20)
            };
            panel.Children.Add(_combo);

            var btnPanel = new StackPanel { Orientation = System.Windows.Controls.Orientation.Horizontal, HorizontalAlignment = System.Windows.HorizontalAlignment.Right };

            var btnOk = new Button 
            { 
                Content = "OK", 
                Width = 80, 
                Height = 28, 
                Margin = new Thickness(0, 0, 10, 0),
                IsDefault = true
            };
            btnOk.Click += (s, e) => { SelectedSystemName = _combo.SelectedItem as string; DialogResult = true; Close(); };

            var btnCancel = new Button 
            { 
                Content = "Cancelar", 
                Width = 80, 
                Height = 28,
                IsCancel = true
            };
            btnCancel.Click += (s, e) => { DialogResult = false; Close(); };

            btnPanel.Children.Add(btnOk);
            btnPanel.Children.Add(btnCancel);
            panel.Children.Add(btnPanel);

            Content = panel;
        }
    }

    /// <summary>
    /// Diálogo para entrada de pressão na origem
    /// </summary>
    public class PressureInputDialog : Window
    {
        public double PressureKPa { get; private set; }
        private readonly System.Windows.Controls.TextBox _txtPressure;

        public PressureInputDialog(double defaultPressure)
        {
            Title = "Pressão na Origem";
            Width = 350;
            Height = 180;
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
            ResizeMode = ResizeMode.NoResize;

            var panel = new StackPanel { Margin = new Thickness(20) };

            panel.Children.Add(new TextBlock 
            { 
                Text = "Informe a pressão disponível na origem (barrilete):",
                Margin = new Thickness(0, 0, 0, 5),
                FontWeight = FontWeights.SemiBold
            });

            panel.Children.Add(new TextBlock 
            { 
                Text = "(em kPa — ex: 200 kPa = 20 mca)",
                Margin = new Thickness(0, 0, 0, 10),
                Foreground = System.Windows.Media.Brushes.Gray,
                FontSize = 11
            });

            _txtPressure = new System.Windows.Controls.TextBox 
            { 
                Text = defaultPressure.ToString("F1"),
                Margin = new Thickness(0, 0, 0, 20)
            };
            panel.Children.Add(_txtPressure);

            var btnPanel = new StackPanel { Orientation = System.Windows.Controls.Orientation.Horizontal, HorizontalAlignment = System.Windows.HorizontalAlignment.Right };

            var btnOk = new Button 
            { 
                Content = "OK", 
                Width = 80, 
                Height = 28, 
                Margin = new Thickness(0, 0, 10, 0),
                IsDefault = true
            };
            btnOk.Click += (s, e) => 
            { 
                if (double.TryParse(_txtPressure.Text, out double p))
                {
                    PressureKPa = p;
                    DialogResult = true;
                    Close();
                }
            };

            var btnCancel = new Button 
            { 
                Content = "Cancelar", 
                Width = 80, 
                Height = 28,
                IsCancel = true
            };
            btnCancel.Click += (s, e) => { DialogResult = false; Close(); };

            btnPanel.Children.Add(btnOk);
            btnPanel.Children.Add(btnCancel);
            panel.Children.Add(btnPanel);

            Content = panel;
        }
    }
}
